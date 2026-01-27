"""
Main migration class for GitLab to GitHub migration.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import github.GitRelease
import github.Issue
import github.Repository
import gitlab  # noqa: TC002 - used at runtime, not just for type hints
import requests
from github import Github, GithubException
from gitlab.exceptions import GitlabAuthenticationError, GitlabError

from . import github_utils as ghu
from . import gitlab_utils as glu
from .exceptions import MigrationError, NumberVerificationError
from .label_translator import LabelTranslator

if TYPE_CHECKING:
    from gitlab.v4.objects import ProjectIssue as GitlabProjectIssue
    from gitlab.v4.objects.projects import Project as GitlabProject

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class DownloadedFile:
    """Represents a downloaded file from GitLab."""

    filename: str
    content: bytes
    short_gitlab_url: str
    full_gitlab_url: str


class GitlabToGithubMigrator:
    """Main migration class."""

    def __init__(
        self,
        gitlab_project_path: str,
        github_repo_path: str,
        *,
        label_translations: list[str] | None = None,
        local_clone_path: str | None = None,
        gitlab_token: str | None = None,
        github_token: str,
    ) -> None:
        self.gitlab_project_path: str = gitlab_project_path
        self.github_repo_path: str = github_repo_path
        self.local_clone_path: Path | None = Path(local_clone_path) if local_clone_path else None

        # Store tokens for direct API access
        self.gitlab_token: str | None = gitlab_token
        self.github_token: str = github_token

        # Initialize API clients with authentication. This falls back to anonymous access if no token is provided.
        self.gitlab_client: gitlab.Gitlab = glu.get_client(token=gitlab_token)
        self.github_client: Github = ghu.get_client(github_token)

        # Get project
        self.gitlab_project: GitlabProject = self.gitlab_client.projects.get(gitlab_project_path)

        # Initialize GitLab GraphQL client using the gitlab.GraphQL class
        self.gitlab_graphql_client: gitlab.GraphQL = glu.get_graphql_client(token=gitlab_token)

        self._github_repo: github.Repository.Repository | None = None
        self._attachments_release: github.GitRelease.GitRelease | None = None

        # Cache for uploaded attachments: GitLab short URL -> GitHub download URL
        self._uploaded_attachments: dict[str, str] = {}

        # Initialize label translator
        self.label_translator: LabelTranslator = LabelTranslator(label_translations)

        # Mappings for migration
        self.label_mapping: dict[str, str] = {}
        # From GitLab milestone ID (not iid!) to GitHub milestone number
        self.milestone_mapping: dict[int, int] = {}

        # Track initial repository state for reporting (lowercase name -> actual name)
        self.initial_github_labels: dict[str, str] = {}

        logger.info(f"Initialized migrator for {gitlab_project_path} -> {github_repo_path}")

    @property
    def github_repo(self) -> github.Repository.Repository:
        if self._github_repo is None:
            msg = "GitHub repository not loaded yet. Call create_github_repo() first."
            raise MigrationError(msg)
        return self._github_repo

    @github_repo.setter
    def github_repo(self, value: github.Repository.Repository) -> None:
        self._github_repo = value

    @staticmethod
    def _format_timestamp(iso_timestamp: str) -> str:
        """Format ISO 8601 timestamp to a human-readable format.

        Args:
            iso_timestamp: ISO 8601 formatted timestamp string (e.g., "2024-01-15T10:30:45.123Z")

        Returns:
            Formatted timestamp string (e.g., "2024-01-15 10:30:45Z" for UTC, "2024-01-15 10:30:45+05:30" for other timezones).
            If the timestamp cannot be parsed, returns the original value unchanged.
        """
        if not iso_timestamp:
            # Return empty string as-is if provided
            return iso_timestamp

        try:
            # Parse the ISO format string to datetime
            timestamp_dt = dt.datetime.fromisoformat(iso_timestamp)
            # Format with space separator and seconds precision
            formatted = timestamp_dt.isoformat(sep=" ", timespec="seconds")
            # Replace +00:00 with Z for cleaner UTC representation
            return formatted.replace("+00:00", "Z")
        except (ValueError, AttributeError):
            # If parsing fails, return the original timestamp unchanged
            return iso_timestamp

    def validate_api_access(self) -> None:
        """Validate GitLab and GitHub API access."""
        try:
            # Test GitLab access
            _ = self.gitlab_project.name
            logger.info("GitLab API access validated")
        except (GitlabError, GitlabAuthenticationError) as e:
            msg = f"GitLab API access failed: {e}"
            raise MigrationError(msg) from e

        try:
            # Test GitHub access
            self.github_client.get_user()
            logger.info("GitHub API access validated")
        except GithubException as e:
            msg = f"GitHub API access failed: {e}"
            raise MigrationError(msg) from e

    def _make_graphql_request(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GraphQL request to GitLab API using python-gitlab's native GraphQL support."""
        try:
            # Use python-gitlab's native GraphQL support via the dedicated GraphQL client
            # Note: gql's HTTPXTransport expects 'variable_values' not 'variables'
            response = self.gitlab_graphql_client.execute(query, variable_values=variables or {})

            # Check for errors in the response
            if "errors" in response:
                msg = f"GraphQL errors: {response['errors']}"
                raise MigrationError(msg)

            return response.get("data", {})

        except GitlabError as e:
            msg = f"GraphQL request failed: {e}"
            raise MigrationError(msg) from e

    def get_work_item_children(self, issue_iid: int) -> list[dict[str, Any]]:
        """Get child work items for a given issue using GraphQL Work Items API.

        Args:
            issue_iid: The internal ID of the issue

        Returns:
            List of child work item information including IID, title, and relationship type
        """
        # Get the project's full path for GraphQL query
        project_path = self.gitlab_project_path

        # GraphQL query to get work item with its children
        # Note: workItem is under namespace, not project
        query = """
        query GetWorkItemWithChildren($fullPath: ID!, $iid: String!) {
            namespace(fullPath: $fullPath) {
                workItem(iid: $iid) {
                    id
                    iid
                    title
                    workItemType {
                        name
                    }
                    widgets {
                        type
                        ... on WorkItemWidgetHierarchy {
                            children {
                                nodes {
                                    id
                                    iid
                                    title
                                    state
                                    workItemType {
                                        name
                                    }
                                    webUrl
                                }
                            }
                        }
                    }
                }
            }
        }
        """

        variables = {"fullPath": project_path, "iid": str(issue_iid)}

        try:
            data = self._make_graphql_request(query, variables)

            namespace = data.get("namespace")
            if not namespace:
                logger.debug(f"Namespace {project_path} not found in GraphQL response")
                return []

            work_item = namespace.get("workItem")
            if not work_item:
                logger.debug(f"Work item {issue_iid} not found in project {project_path}")
                return []

            # Find the hierarchy widget to get children
            children = []
            widgets = work_item.get("widgets", [])

            for widget in widgets:
                if widget.get("type") == "HIERARCHY":
                    child_nodes = widget.get("children", {}).get("nodes", [])

                    for child in child_nodes:
                        child_info = {
                            "iid": child.get("iid"),
                            "title": child.get("title"),
                            "state": child.get("state"),
                            "type": child.get("workItemType", {}).get("name"),
                            "web_url": child.get("webUrl"),
                            "relationship_type": "child_of",  # This is a child relationship
                        }
                        children.append(child_info)

            logger.debug(f"Found {len(children)} child work items for issue #{issue_iid}")

        except GitlabError as e:
            msg = f"Failed to get work item children for issue #{issue_iid}: {e}"
            raise MigrationError(msg) from e
        else:
            return children

    def migrate_git_content(self) -> None:
        """Migrate git repository content from GitLab to GitHub."""
        temp_clone_path: str | None = None
        try:
            if self.local_clone_path:
                # Use existing local clone
                clone_path = self.local_clone_path
                if not clone_path.exists():
                    msg = f"Local clone path does not exist: {self.local_clone_path}"
                    raise MigrationError(msg)
            else:
                # Create temporary clone
                temp_clone_path = tempfile.mkdtemp(prefix="gitlab_migration_")
                clone_path = temp_clone_path

                # Clone from GitLab
                result = subprocess.run(  # noqa: S603
                    [
                        "git",
                        "clone",
                        "--mirror",
                        self.gitlab_project.ssh_url_to_repo,
                        temp_clone_path,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                if result.returncode != 0:
                    msg = f"Failed to clone GitLab repository: {result.stderr}"
                    raise MigrationError(msg)

            # Add GitHub remote
            _ = subprocess.run(  # noqa: S603
                ["git", "remote", "add", "github", self.github_repo.ssh_url], cwd=clone_path, check=True
            )

            # Push all branches and tags
            _ = subprocess.run(["git", "push", "--mirror", "github"], cwd=clone_path, check=True)

            logger.info("Repository content migrated successfully")

        except (subprocess.CalledProcessError, OSError) as e:
            msg = f"Failed to migrate repository content: {e}"
            raise MigrationError(msg) from e
        finally:
            # Cleanup temporary clone if created
            if temp_clone_path and Path(temp_clone_path).exists():
                shutil.rmtree(temp_clone_path)

    def migrate_labels(self) -> None:
        """Migrate and translate labels from GitLab to GitHub.

        Matching with existing GitHub labels is case-insensitive (GitHub treats
        "Bug" and "bug" as the same label). When a translated label matches an
        existing label, the existing label's name is used in the mapping.

        Handles race condition where GitHub organization default labels may be
        applied asynchronously after repository creation.
        """
        try:
            # Get existing GitHub labels (case-insensitive lookup: lowercase -> actual name)
            self.initial_github_labels = {
                label.name.lower(): label.name for label in self.github_repo.get_labels()
            }

            # Get GitLab labels
            gitlab_labels = self.gitlab_project.labels.list(get_all=True)

            for gitlab_label in gitlab_labels:
                # Translate label name
                translated_name = self.label_translator.translate(gitlab_label.name)

                # Skip if label already exists (case-insensitive, as GitHub labels are)
                existing_label = self.initial_github_labels.get(translated_name.lower())
                if existing_label is not None:
                    self.label_mapping[gitlab_label.name] = existing_label
                    logger.debug(
                        f"Using existing label: {gitlab_label.name} -> {existing_label}"
                    )
                    continue

                # Create new label
                try:
                    github_label = self.github_repo.create_label(
                        name=translated_name,
                        color=gitlab_label.color.lstrip("#"),
                        description=gitlab_label.description or "",
                    )
                    self.label_mapping[gitlab_label.name] = github_label.name
                    logger.debug(f"Created label: {gitlab_label.name} -> {translated_name}")
                except GithubException as e:
                    msg = f"Failed to create label {translated_name}"
                    raise MigrationError(msg) from e

            logger.info(f"Migrated {len(self.label_mapping)} labels")

        except (GitlabError, GithubException) as e:
            msg = f"Failed to migrate labels: {e}"
            raise MigrationError(msg) from e

    def migrate_milestones_with_number_preservation(self) -> None:
        """Migrate milestones while preserving GitLab milestone numbers."""
        try:
            # Get all GitLab milestones sorted by ID
            gitlab_milestones = self.gitlab_project.milestones.list(get_all=True, state="all")
            gitlab_milestones.sort(key=lambda m: m.iid)

            if not gitlab_milestones:
                logger.info("No milestones to migrate")
                return

            max_milestone_number = gitlab_milestones[-1].iid  # This works because sorted by iid
            gitlab_milestone_dict = {m.iid: m for m in gitlab_milestones}

            # Create milestones maintaining number sequence
            for milestone_number in range(1, max_milestone_number + 1):
                if milestone_number in gitlab_milestone_dict:
                    # Real milestone exists
                    gitlab_milestone = gitlab_milestone_dict[milestone_number]

                    # Create milestone parameters, only include due_on if it exists
                    milestone_params = {
                        "title": gitlab_milestone.title,
                        "state": "open" if gitlab_milestone.state == "active" else "closed",
                        "description": gitlab_milestone.description or "",
                    }
                    if gitlab_milestone.due_date:
                        milestone_params["due_on"] = dt.datetime.strptime(gitlab_milestone.due_date, "%Y-%m-%d").date()  # noqa: DTZ007

                    github_milestone = self.github_repo.create_milestone(**milestone_params)  # pyright: ignore[reportArgumentType]

                    # Verify milestone number
                    if github_milestone.number != milestone_number:
                        msg = f"Milestone number mismatch: expected {milestone_number}, got {github_milestone.number}"
                        raise NumberVerificationError(msg)

                    self.milestone_mapping[gitlab_milestone.id] = github_milestone.number
                    logger.debug(f"Created milestone #{milestone_number}: {gitlab_milestone.title}")
                else:
                    # Create placeholder milestone
                    placeholder_milestone = self.github_repo.create_milestone(
                        title="Placeholder Milestone",
                        state="closed",
                        description="Placeholder to preserve milestone numbering",
                    )

                    # Verify placeholder number
                    if placeholder_milestone.number != milestone_number:
                        msg = f"Placeholder milestone number mismatch: expected {milestone_number}, got {placeholder_milestone.number}"
                        raise NumberVerificationError(msg)

                    logger.debug(f"Created placeholder milestone #{milestone_number}")

            logger.info(f"Migrated {len(self.milestone_mapping)} milestones")

        except (GitlabError, GithubException) as e:
            msg = f"Failed to migrate milestones: {e}"
            raise MigrationError(msg) from e

    def download_gitlab_attachments(self, content: str) -> tuple[list[DownloadedFile], str]:
        """Download GitLab attachments and return files plus content with cached URLs replaced.

        Uses the GitLab REST API endpoint (GitLab 17.4+) to download uploads
        by secret and filename, avoiding Cloudflare blocks on web URLs.

        Returns:
            Tuple of (list of newly downloaded files, content with cached URLs replaced)
        """
        # Find attachment URLs in content: /uploads/<secret>/<filename>
        attachment_pattern = r"/uploads/([a-f0-9]{32})/([^)\s]+)"
        attachments = re.findall(attachment_pattern, content)

        downloaded_files: list[DownloadedFile] = []
        updated_content = content

        for secret, filename in attachments:
            short_url = f"/uploads/{secret}/{filename}"

            # If already uploaded, just replace the URL in content (skip download)
            if short_url in self._uploaded_attachments:
                github_url = self._uploaded_attachments[short_url]
                updated_content = updated_content.replace(short_url, github_url)
                logger.debug(f"Reusing cached attachment {filename}: {github_url}")
                continue

            full_url = f"{self.gitlab_project.web_url}{short_url}"
            try:
                # Use GitLab REST API endpoint instead of web URL to avoid Cloudflare
                # API: GET /projects/:id/uploads/:secret/:filename (GitLab 17.4+)
                api_path = f"/projects/{self.gitlab_project.id}/uploads/{secret}/{filename}"

                # http_get with raw=True returns requests.Response (type stubs are incorrect)
                response = cast(
                    requests.Response,
                    self.gitlab_client.http_get(api_path, raw=True, timeout=30),
                )
                response.raise_for_status()

                attachment_content = response.content
                content_type = response.headers.get("Content-Type", "unknown")
                logger.debug(f"Downloaded {filename}: {len(attachment_content)} bytes, Content-Type: {content_type}")

                if attachment_content:
                    downloaded_files.append(
                        DownloadedFile(
                            filename=filename,
                            content=attachment_content,
                            short_gitlab_url=short_url,
                            full_gitlab_url=full_url,
                        )
                    )
                else:
                    logger.warning(
                        f"GitLab returned empty content for attachment {short_url} "
                        f"(status: {response.status_code}, Content-Type: {content_type})"
                    )

            except (requests.RequestException, OSError) as e:
                msg = f"Failed to download attachment {short_url}: {e}"
                raise MigrationError(msg) from e

        return downloaded_files, updated_content

    @property
    def attachments_release(self) -> github.GitRelease.GitRelease:
        """Get or create the 'gitlab-issue-attachments' release for storing attachment files (cached)."""
        if self._attachments_release is None:
            release_tag = "gitlab-issue-attachments"
            release_name = "GitLab issue attachments"

            # Draft releases can't be found by tag, so list all releases and find by name
            release = None
            for r in self.github_repo.get_releases():
                if r.name == release_name:
                    release = r
                    logger.debug(f"Using existing attachments release: {release.name}")
                    break

            if release is None:
                # Release doesn't exist, create it
                logger.info(f"Creating new '{release_name}' release for storing attachment files")
                release = self.github_repo.create_git_release(
                    tag=release_tag,
                    name=release_name,
                    message="Storage for migrated GitLab attachments. Do not delete.",
                    draft=True,  # Keep it as a draft to minimize visibility
                )
                logger.info(f"Created attachments release: {release.name}")

            self._attachments_release = release

        return self._attachments_release

    def upload_github_attachments(self, files: list[DownloadedFile], content: str, context: str = "") -> str:
        """Upload files to GitHub release assets and update content with new URLs.

        Args:
            files: List of downloaded files to upload.
            content: The content containing attachment URLs to replace.
            context: Context string for log messages (e.g., "issue #5" or "issue #5 note 123").

        Files are cached by their GitLab URL to avoid duplicate uploads when the
        same attachment appears in multiple issues or comments.
        """
        if not files:
            return content

        updated_content = content

        # Get or create the attachments release (cached property)
        release = self.attachments_release

        for file_info in files:
            # Check if this file was already uploaded (same GitLab URL)
            if file_info.short_gitlab_url in self._uploaded_attachments:
                download_url = self._uploaded_attachments[file_info.short_gitlab_url]
                updated_content = updated_content.replace(file_info.short_gitlab_url, download_url)
                logger.debug(f"Reusing cached attachment {file_info.filename}: {download_url}")
                continue

            # Skip empty files - GitHub rejects uploads with 0 bytes (Bad Content-Length error)
            if not file_info.content:
                ctx = f" in {context}" if context else ""
                logger.warning(
                    f"Skipping empty attachment {file_info.filename} (0 bytes){ctx} - "
                    f"GitLab URL: {file_info.full_gitlab_url}"
                )
                continue

            temp_path = None
            try:
                # Create a temporary file for GitHub API
                # Use only the file extension for the suffix to avoid filesystem issues
                file_ext = Path(file_info.filename).suffix if file_info.filename else ""
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(file_info.content)

                # Make filename unique by prepending the GitLab secret hash
                # This handles different files with the same filename
                # Extract secret from short_gitlab_url: /uploads/<secret>/<filename>
                url_parts = file_info.short_gitlab_url.split("/")
                secret = url_parts[2] if len(url_parts) >= 3 else ""
                unique_filename = f"{secret[:8]}_{file_info.filename}" if secret else file_info.filename

                # Upload file as release asset
                asset = release.upload_asset(path=temp_path, name=unique_filename)
                # Get the download URL for the asset
                download_url = asset.browser_download_url

                # Cache the URL for future references to the same file
                self._uploaded_attachments[file_info.short_gitlab_url] = download_url

                # Replace the GitLab URL with the GitHub URL in content
                updated_content = updated_content.replace(file_info.short_gitlab_url, download_url)
                logger.debug(f"Uploaded {file_info.filename} to release assets: {download_url}")

            except (GithubException, OSError):
                logger.exception(f"Failed to process attachment {file_info.filename}")
                raise
            finally:
                # Clean up temp file
                if temp_path:
                    temp_file_path = Path(temp_path)
                    if temp_file_path.exists():
                        temp_file_path.unlink()

        return updated_content

    def create_github_sub_issue(
        self, parent_github_issue: github.Issue.Issue, sub_issue_title: str, sub_issue_body: str
    ) -> None:
        """Create a GitHub sub-issue using PyGithub's native sub-issue support.

        This uses GitHub's sub-issues API introduced in December 2024, now supported
        natively by PyGithub.
        """
        # First create a regular issue
        sub_issue = self.github_repo.create_issue(title=sub_issue_title, body=sub_issue_body)

        # Add the issue as a sub-issue to the parent using PyGithub's native support
        # Note: PyGithub requires the issue ID (not number) for sub-issue operations
        parent_github_issue.add_sub_issue(sub_issue.id)

        logger.debug(f"Created sub-issue #{sub_issue.number} under parent #{parent_github_issue.number}")

    def create_github_issue_dependency(self, blocked_issue_number: int, blocking_issue_id: int) -> bool:
        """Create a GitHub issue dependency using PyGithub's requester.

        GitHub's issue dependencies API (August 2025) is not yet supported by PyGithub's
        classes, so we use the requester to make raw API calls while benefiting from
        PyGithub's authentication and rate limiting.

        Args:
            blocked_issue_number: The issue number that is blocked
            blocking_issue_id: The issue ID (not number) that is blocking

        Returns:
            True if successful, False otherwise
        """
        # Parse owner and repo from github_repo_path
        owner, repo = self.github_repo_path.split("/")

        # The API endpoint adds a "blocked by" relationship to an issue.
        # POST /repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by
        # with body: {"issue_id": <blocking_issue_id>}
        endpoint = f"/repos/{owner}/{repo}/issues/{blocked_issue_number}/dependencies/blocked_by"
        payload = {"issue_id": blocking_issue_id}

        try:
            # Use PyGithub's requester for consistent auth and rate limiting
            status, _, data = self.github_client.requester.requestJson("POST", endpoint, input=payload)
        except GithubException as e:
            if e.status == 422:
                # Dependency may already exist or be invalid
                logger.debug(f"Could not create dependency (may already exist): {e.status} - {e.data}")
                return False
            # Re-raise other GitHub exceptions as they represent real errors
            raise

        if status == 201:
            logger.debug(
                f"Created issue dependency: issue #{blocked_issue_number} blocked by issue ID {blocking_issue_id}"
            )
            return True

        msg = f"Failed to create issue dependency: {status} - {data}"
        raise MigrationError(msg)

    def get_issue_cross_links(  # noqa: PLR0912 - complex categorization logic
        self,
        gitlab_issue: Any,  # noqa: ANN401 - gitlab has no type stubs
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Get cross-linked issues and separate different relationship types.

        This method uses both GitLab's Work Items GraphQL API and REST API to properly
        detect and categorize issue relationships:
        - Parent-child relationships (work item hierarchy) -> GitHub sub-issues
        - Blocking relationships (blocks/is_blocked_by) -> GitHub issue dependencies
        - Other relationships (relates_to) -> Text in issue description

        Returns:
            tuple: (
                cross_links_text: str - For relates_to links in description,
                parent_child_relations: list[dict] - For GitHub sub-issues,
                blocking_relations: list[dict] - For GitHub issue dependencies
            )
        """
        # Step 1: Get child tasks using GraphQL Work Items API (using python-gitlab's native GraphQL support)
        child_work_items = []
        child_work_items = self.get_work_item_children(gitlab_issue.iid)
        logger.debug(f"Found {len(child_work_items)} tasks via GraphQL for issue #{gitlab_issue.iid}")

        # Step 2: Get regular issue links from REST API
        regular_links = []
        links = gitlab_issue.links.list(get_all=True)

        for link in links:
            # Determine the relationship type and target
            if hasattr(link, "link_type"):
                link_type = link.link_type
            else:
                link_type = "relates_to"  # Default

            # Get target issue information (data is directly on link object, not in nested dict)
            target_issue_iid = link.iid
            target_issue_title = getattr(link, "title", "Unknown Title")
            # Extract project path from references or web_url
            references = getattr(link, "references", {})
            target_project_path = references.get("full", "").rsplit("#", 1)[0] if references else None
            target_web_url = getattr(link, "web_url", "")

            # Log the link type for debugging
            logger.debug(
                f"Issue #{gitlab_issue.iid} has link_type '{link_type}' to issue #{target_issue_iid} from project {target_project_path}"
            )
            target_project_path = target_project_path or self.gitlab_project_path

            link_info = {
                "type": link_type,
                "target_iid": target_issue_iid,
                "target_title": target_issue_title,
                "target_project_path": target_project_path,
                "target_web_url": target_web_url,
                "is_same_project": target_project_path == self.gitlab_project_path,
            }
            regular_links.append(link_info)

        # Step 3: Categorize relationships into three groups
        parent_child_relations = []
        blocking_relations = []
        relates_to_links = []

        # Add child work items as parent-child relationships
        parent_child_relations = [
            {
                "type": "child_of",
                "target_iid": child["iid"],
                "target_title": child["title"],
                "target_project_path": self.gitlab_project_path,
                "target_web_url": child["web_url"],
                "is_same_project": True,
                "source": "graphql_work_items",
            }
            for child in child_work_items
        ]

        # Process regular issue links (blocks, is_blocked_by, relates_to)
        for link_info in regular_links:
            link_type = link_info["type"]

            # Categorize by relationship type
            if link_type in ("blocks", "is_blocked_by"):
                # Blocking relationships - will be migrated to GitHub issue dependencies
                # Only same-project links can be migrated natively
                if link_info["is_same_project"]:
                    blocking_relations.append(link_info)
                else:
                    # Cross-project blocking links fall back to description text
                    relates_to_links.append(("Blocked by" if link_type == "is_blocked_by" else "Blocks", link_info))
            elif link_type == "relates_to":
                relates_to_links.append(("Related to", link_info))
            else:
                relates_to_links.append((f"Linked ({link_type})", link_info))

        # Step 4: Format cross-links text for relates_to relationships only
        # (blocking relationships are handled natively via GitHub API)
        cross_links_text = ""
        if relates_to_links:
            cross_links_text = "\n\n---\n\n**Cross-linked Issues:**\n\n"

            for relationship, link_info in relates_to_links:
                if link_info["is_same_project"]:
                    # Same project - will be migrated to GitHub issue numbers
                    cross_links_text += (
                        f"- **{relationship}**: #{link_info['target_iid']} - {link_info['target_title']}\n"
                    )
                else:
                    # External project - keep GitLab reference
                    cross_links_text += f"- **{relationship}**: [{link_info['target_project_path']}#{link_info['target_iid']}]({link_info['target_web_url']}) - {link_info['target_title']}\n"

        # Log summary
        logger.debug(
            f"Issue #{gitlab_issue.iid} summary: {len(parent_child_relations)} parent-child, "
            f"{len(blocking_relations)} blocking, {len(relates_to_links)} relates_to links"
        )

        return cross_links_text, parent_child_relations, blocking_relations

    def migrate_issues_with_number_preservation(self) -> None:  # noqa: PLR0912, PLR0915
        """Migrate issues while preserving GitLab issue numbers."""
        try:
            # Get all GitLab issues sorted by IID
            gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
            gitlab_issues.sort(key=lambda i: i.iid)

            if not gitlab_issues:
                logger.info("No issues to migrate")
                return

            max_issue_number = max(i.iid for i in gitlab_issues)
            gitlab_issue_dict: dict[int, GitlabProjectIssue] = {i.iid: i for i in gitlab_issues}
            github_issue_dict: dict[int, github.Issue.Issue] = {}  # Maps GitLab IID to GitHub issue
            pending_parent_child_relations = []  # Store parent-child relations for second pass
            pending_blocking_relations: list[dict[str, Any]] = []  # Store blocking relations for second pass

            # First pass: Create issues maintaining number sequence
            for issue_number in range(1, max_issue_number + 1):
                if issue_number in gitlab_issue_dict:
                    # Real issue exists
                    gitlab_issue = gitlab_issue_dict[issue_number]

                    # Prepare issue content
                    issue_body = f"**Migrated from GitLab issue #{gitlab_issue.iid}**\n"
                    issue_body += (
                        f"**Original Author:** {gitlab_issue.author['name']} (@{gitlab_issue.author['username']})\n"
                    )
                    issue_body += f"**Created:** {self._format_timestamp(gitlab_issue.created_at)}\n"
                    issue_body += f"**GitLab URL:** {gitlab_issue.web_url}\n\n"
                    issue_body += "---\n\n"

                    if gitlab_issue.description:
                        # Download and process attachments (cached URLs already replaced)
                        files, description_with_cached = self.download_gitlab_attachments(gitlab_issue.description)
                        updated_description = self.upload_github_attachments(
                            files, description_with_cached, context=f"issue #{gitlab_issue.iid}"
                        )
                        issue_body += updated_description

                    # Add cross-linked issues to the description and collect relationships
                    cross_links_text, parent_child_relations, blocking_relations = self.get_issue_cross_links(
                        gitlab_issue
                    )
                    if cross_links_text:
                        issue_body += cross_links_text

                    # Store parent-child relations for second pass (after all issues are created)
                    if parent_child_relations:
                        pending_parent_child_relations.extend(
                            {"parent_gitlab_iid": gitlab_issue.iid, "relation": relation}
                            for relation in parent_child_relations
                        )

                    # Store blocking relations for second pass
                    if blocking_relations:
                        pending_blocking_relations.extend(
                            {"source_gitlab_iid": gitlab_issue.iid, "relation": relation}
                            for relation in blocking_relations
                        )

                    # Prepare labels
                    issue_labels = [
                        self.label_mapping[label_name]
                        for label_name in gitlab_issue.labels
                        if label_name in self.label_mapping
                    ]

                    # Prepare milestone
                    milestone = None
                    if gitlab_issue.milestone and gitlab_issue.milestone["id"] in self.milestone_mapping:
                        milestone_number = self.milestone_mapping[gitlab_issue.milestone["id"]]
                        milestone = self.github_repo.get_milestone(milestone_number)

                    # Create GitHub issue (only pass milestone if it exists)
                    if milestone:
                        github_issue = self.github_repo.create_issue(
                            title=gitlab_issue.title, body=issue_body, labels=issue_labels, milestone=milestone
                        )
                    else:
                        github_issue = self.github_repo.create_issue(
                            title=gitlab_issue.title, body=issue_body, labels=issue_labels
                        )

                    # Verify issue number
                    if github_issue.number != issue_number:
                        msg = f"Issue number mismatch: expected {issue_number}, got {github_issue.number}"
                        raise NumberVerificationError(msg)

                    # Store GitHub issue for parent-child relationship handling
                    github_issue_dict[gitlab_issue.iid] = github_issue

                    # Migrate comments
                    self.migrate_issue_comments(gitlab_issue, github_issue)

                    # Close issue if needed
                    if gitlab_issue.state == "closed":
                        github_issue.edit(state="closed")

                    logger.debug(f"Created issue #{issue_number}: {gitlab_issue.title}")

                else:
                    # Create placeholder issue
                    placeholder_issue = self.github_repo.create_issue(
                        title="Placeholder", body="Placeholder to preserve issue numbering - will be deleted"
                    )

                    # Verify placeholder number
                    if placeholder_issue.number != issue_number:
                        msg = f"Placeholder issue number mismatch: expected {issue_number}, got {placeholder_issue.number}"
                        raise NumberVerificationError(msg)

                    # Close placeholder immediately
                    placeholder_issue.edit(state="closed")
                    logger.debug(f"Created placeholder issue #{issue_number}")

            # Second pass: Create parent-child relationships as GitHub sub-issues
            if pending_parent_child_relations:
                logger.info(f"Processing {len(pending_parent_child_relations)} parent-child relationships...")

                for pending_relation in pending_parent_child_relations:
                    parent_gitlab_iid = pending_relation["parent_gitlab_iid"]
                    child_relation = pending_relation["relation"]

                    # Get the parent GitHub issue
                    if parent_gitlab_iid in github_issue_dict:
                        parent_github_issue = github_issue_dict[parent_gitlab_iid]

                        # Get the child issue info
                        child_gitlab_iid = child_relation["target_iid"]
                        if child_gitlab_iid in github_issue_dict:
                            child_github_issue = github_issue_dict[child_gitlab_iid]

                            # Create sub-issue relationship
                            # Note: This will attempt to use GitHub's new sub-issues API
                            self.create_github_sub_issue(
                                parent_github_issue,
                                f"Link to #{child_github_issue.number}",
                                f"This issue is linked as a child of #{parent_github_issue.number}.\n\nOriginal GitLab relationship: {child_relation['type']}",
                            )

                            logger.debug(f"Linked issue #{child_gitlab_iid} as sub-issue of #{parent_gitlab_iid}")
                        else:
                            logger.warning(f"Child issue #{child_gitlab_iid} not found for parent-child relationship")
                    else:
                        logger.warning(f"Parent issue #{parent_gitlab_iid} not found for parent-child relationship")

            # Third pass: Create blocking relationships as GitHub issue dependencies
            if pending_blocking_relations:
                logger.info(f"Processing {len(pending_blocking_relations)} blocking relationships...")

                for pending_relation in pending_blocking_relations:
                    source_gitlab_iid = pending_relation["source_gitlab_iid"]
                    relation = pending_relation["relation"]
                    link_type = relation["type"]
                    target_gitlab_iid = relation["target_iid"]

                    # Get both GitHub issues
                    if source_gitlab_iid not in github_issue_dict:
                        logger.warning(f"Source issue #{source_gitlab_iid} not found for blocking relationship")
                        continue
                    if target_gitlab_iid not in github_issue_dict:
                        logger.warning(f"Target issue #{target_gitlab_iid} not found for blocking relationship")
                        continue

                    source_github_issue = github_issue_dict[source_gitlab_iid]
                    target_github_issue = github_issue_dict[target_gitlab_iid]

                    # Determine which issue is blocked and which is blocking based on GitLab link type
                    # GitLab "blocks" means: source blocks target -> target is blocked by source
                    # GitLab "is_blocked_by" means: source is blocked by target -> source is blocked by target
                    if link_type == "blocks":
                        # Source blocks target: target is blocked by source
                        blocked_issue_number = target_github_issue.number
                        blocking_issue_id = source_github_issue.id
                    else:  # is_blocked_by
                        # Source is blocked by target: source is blocked by target
                        blocked_issue_number = source_github_issue.number
                        blocking_issue_id = target_github_issue.id

                    success = self.create_github_issue_dependency(blocked_issue_number, blocking_issue_id)

                    if success:
                        logger.debug(
                            f"Created blocking relationship: #{source_gitlab_iid} {link_type} #{target_gitlab_iid}"
                        )

            logger.info(f"Migrated {len(gitlab_issues)} issues")

        except (GitlabError, GithubException) as e:
            msg = f"Failed to migrate issues: {e}"
            raise MigrationError(msg) from e

    def migrate_issue_comments(self, gitlab_issue: GitlabProjectIssue, github_issue: github.Issue.Issue) -> None:
        """Migrate comments for an issue."""
        # Get all notes/comments
        notes = gitlab_issue.notes.list(get_all=True)
        notes.sort(key=lambda n: n.created_at)

        for note in notes:
            if note.system:
                # System note - convert to regular comment
                comment_body = f"**System note:** {note.body}"
            else:
                # Regular comment
                comment_body = f"**Comment by** {note.author['name']} (@{note.author['username']}) **on** {self._format_timestamp(note.created_at)}\n\n"
                comment_body += "---\n\n"

                if note.body:
                    # Process attachments in comment (cached URLs already replaced)
                    files, body_with_cached = self.download_gitlab_attachments(note.body)
                    updated_body = self.upload_github_attachments(
                        files, body_with_cached, context=f"issue #{gitlab_issue.iid} note {note.id}"
                    )
                    comment_body += updated_body

            # Create GitHub comment
            github_issue.create_comment(comment_body)
            logger.debug(f"Migrated comment by {note.author['username']}")

    def cleanup_placeholders(self) -> None:
        """Delete placeholder issues and milestones."""
        # Clean up placeholder issues
        issues = self.github_repo.get_issues(state="all")
        for issue in issues:
            if issue.title == "Placeholder":
                # GitHub API doesn't allow deleting issues, so we'll leave them closed
                logger.debug(f"Placeholder issue #{issue.number} left closed (cannot delete)")

        # Clean up placeholder milestones
        milestones = self.github_repo.get_milestones(state="all")
        for milestone in milestones:
            if milestone.title == "Placeholder Milestone":
                milestone.delete()
                logger.debug(f"Deleted placeholder milestone #{milestone.number}")

        logger.info("Cleanup completed")

    def validate_migration(self) -> dict[str, Any]:
        """Validate migration results and generate report."""
        errors: list[str] = []
        statistics: dict[str, int] = {}
        report: dict[str, Any] = {
            "gitlab_project": self.gitlab_project_path,
            "github_repo": self.github_repo_path,
            "success": True,
            "errors": errors,
            "statistics": statistics,
        }

        try:
            # Count GitLab items with state breakdown
            gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
            gitlab_issues_open = [i for i in gitlab_issues if i.state == "opened"]
            gitlab_issues_closed = [i for i in gitlab_issues if i.state == "closed"]

            gitlab_milestones = self.gitlab_project.milestones.list(get_all=True, state="all")
            gitlab_milestones_open = [m for m in gitlab_milestones if m.state == "active"]
            gitlab_milestones_closed = [m for m in gitlab_milestones if m.state == "closed"]

            gitlab_labels = self.gitlab_project.labels.list(get_all=True)

            # Count GitHub items (excluding placeholders) with state breakdown
            github_issues_all = list(self.github_repo.get_issues(state="all"))
            github_issues = [i for i in github_issues_all if i.title != "Placeholder"]
            github_issues_open = [i for i in github_issues if i.state == "open"]
            github_issues_closed = [i for i in github_issues if i.state == "closed"]

            github_milestones_all = list(self.github_repo.get_milestones(state="all"))
            github_milestones = [m for m in github_milestones_all if m.title != "Placeholder Milestone"]
            github_milestones_open = [m for m in github_milestones if m.state == "open"]
            github_milestones_closed = [m for m in github_milestones if m.state == "closed"]

            # Count label statistics
            github_labels_all = list(self.github_repo.get_labels())

            # Use the initial label count we captured at repository creation
            labels_created = len(github_labels_all) - len(self.initial_github_labels)

            statistics.update(
                {
                    "gitlab_issues_total": len(gitlab_issues),
                    "gitlab_issues_open": len(gitlab_issues_open),
                    "gitlab_issues_closed": len(gitlab_issues_closed),
                    "github_issues_total": len(github_issues),
                    "github_issues_open": len(github_issues_open),
                    "github_issues_closed": len(github_issues_closed),
                    "gitlab_milestones_total": len(gitlab_milestones),
                    "gitlab_milestones_open": len(gitlab_milestones_open),
                    "gitlab_milestones_closed": len(gitlab_milestones_closed),
                    "github_milestones_total": len(github_milestones),
                    "github_milestones_open": len(github_milestones_open),
                    "github_milestones_closed": len(github_milestones_closed),
                    "gitlab_labels_total": len(gitlab_labels),
                    "github_labels_existing": len(self.initial_github_labels),
                    "github_labels_created": max(0, labels_created),
                    "labels_translated": len(self.label_mapping),
                }
            )

            # Validate counts
            if len(gitlab_issues) != len(github_issues):
                errors.append(f"Issue count mismatch: GitLab {len(gitlab_issues)}, GitHub {len(github_issues)}")
                report["success"] = False

            if len(gitlab_milestones) != len(github_milestones):
                errors.append(
                    f"Milestone count mismatch: GitLab {len(gitlab_milestones)}, GitHub {len(github_milestones)}"
                )
                report["success"] = False

            logger.info("Migration validation completed")

        except (GitlabError, GithubException) as e:
            report["success"] = False
            errors.append(f"Validation failed: {e}")
            logger.exception("Validation failed")

        return report

    def create_github_repo(self) -> None:
        self._github_repo = ghu.create_repo(
            self.github_client,
            self.github_repo_path,
            self.gitlab_project.description,  # pyright: ignore[reportUnknownArgumentType]
        )

    def migrate(self) -> dict[str, Any]:
        """Execute the complete migration process."""
        try:
            logger.info("Starting GitLab to GitHub migration")

            # Validation
            self.validate_api_access()

            # Repository creation and content migration
            self.create_github_repo()
            self.migrate_git_content()

            # Metadata migration
            self.migrate_labels()
            self.migrate_milestones_with_number_preservation()
            self.migrate_issues_with_number_preservation()

            # Cleanup and validation
            self.cleanup_placeholders()
            report = self.validate_migration()

            logger.info("Migration completed successfully")

        except (GitlabError, GithubException, subprocess.CalledProcessError, OSError) as e:
            logger.exception("Migration failed")
            # Optionally clean up created repository
            if self._github_repo:
                try:
                    logger.info("Cleaning up created repository due to failure")
                    self._github_repo.delete()
                except GithubException:
                    logger.exception("Failed to cleanup repository")

            msg = f"Migration failed: {e}"
            raise MigrationError(msg) from e

        return report
