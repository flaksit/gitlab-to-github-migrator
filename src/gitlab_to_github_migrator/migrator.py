"""
Main migration class for GitLab to GitHub migration.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

import github.Issue
import github.Milestone
import github.Repository
import gitlab  # noqa: TC002 - used at runtime, not just for type hints
from github import Github, GithubException
from gitlab.exceptions import GitlabAuthenticationError, GitlabError

from . import git_migration, labels
from . import github_utils as ghu
from . import gitlab_utils as glu
from .attachments import AttachmentHandler
from .exceptions import MigrationError, NumberVerificationError
from .gitlab_utils import get_normal_issue_cross_links
from .issue_builder import build_issue_body, format_timestamp

if TYPE_CHECKING:
    from gitlab.v4.objects import ProjectIssue as GitlabProjectIssue
    from gitlab.v4.objects.projects import Project as GitlabProject

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)


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
        self._attachment_handler: AttachmentHandler | None = None

        # Store label translations for later use
        self._label_translations: list[str] | None = label_translations

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

    @property
    def attachment_handler(self) -> AttachmentHandler:
        """Get or create the attachment handler for this migration (cached)."""
        if self._attachment_handler is None:
            self._attachment_handler = AttachmentHandler(
                self.gitlab_client,
                self.gitlab_project,
                self.github_repo,
            )
        return self._attachment_handler

    def validate_api_access(self) -> None:
        """Validate GitLab and GitHub API access."""
        try:
            # Test GitLab access
            _ = self.gitlab_project.name  # pyright: ignore[reportUnknownVariableType]
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

    def migrate_git_content(self) -> None:
        """Migrate git repository content from GitLab to GitHub."""
        git_migration.migrate_git_content(
            source_http_url=str(self.gitlab_project.http_url_to_repo),  # pyright: ignore[reportUnknownArgumentType]
            target_clone_url=self.github_repo.clone_url,
            source_token=self.gitlab_token,
            target_token=self.github_token,
            local_clone_path=self.local_clone_path,
        )

    def migrate_labels(self) -> None:
        """Migrate and translate labels from GitLab to GitHub."""
        result = labels.migrate_labels(
            self.gitlab_project,
            self.github_repo,
            self._label_translations,
        )
        self.label_mapping = result.label_mapping
        self.initial_github_labels = result.initial_github_labels

    def migrate_milestones_with_number_preservation(self) -> None:
        """Migrate milestones while preserving GitLab milestone numbers."""
        # Get all GitLab milestones sorted by ID
        gitlab_milestones = self.gitlab_project.milestones.list(get_all=True, state="all")
        gitlab_milestones.sort(key=lambda m: m.iid)

        if not gitlab_milestones:
            logger.info("No milestones to migrate")
            return

        max_milestone_number = gitlab_milestones[-1].iid  # This works because sorted by iid
        gitlab_milestone_map = {m.iid: m for m in gitlab_milestones}
        placeholder_milestones: list[github.Milestone.Milestone] = []

        # Create milestones maintaining number sequence
        for milestone_number in range(1, max_milestone_number + 1):
            if milestone_number in gitlab_milestone_map:
                # Real milestone exists
                gitlab_milestone = gitlab_milestone_map[milestone_number]

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
                placeholder_milestones.append(placeholder_milestone)

        for milestone in placeholder_milestones:
            milestone.delete()
            logger.debug(f"Deleted placeholder milestone #{milestone.number}")

        logger.info(f"Migrated {len(self.milestone_mapping)} milestones")

    def _create_migrated_issue(
        self,
        gitlab_issue: GitlabProjectIssue,
    ) -> tuple[github.Issue.Issue, list[int]]:
        """Create a GitHub issue from a GitLab issue.

        Args:
            gitlab_issue: The GitLab issue to migrate

        Returns:
            The created GitHub issue and list of GitLab Issue IIDs that are blocked by this issue
        """
        # Process description with attachments
        processed_description = ""
        if gitlab_issue.description:
            processed_description = self.attachment_handler.process_content(
                gitlab_issue.description,
                context=f"issue #{gitlab_issue.iid}",
            )

        # Get cross-linked issues and collect relationships
        cross_links = get_normal_issue_cross_links(
            gitlab_issue,
            self.gitlab_project_path,
        )

        # Build issue body using the issue_builder module
        issue_body = build_issue_body(
            gitlab_issue,
            processed_description=processed_description,
            cross_links_text=cross_links.cross_links_text,
        )

        # Prepare labels
        issue_labels = [
            self.label_mapping[label_name] for label_name in gitlab_issue.labels if label_name in self.label_mapping
        ]

        # Prepare milestone
        milestone = None
        if gitlab_issue.milestone and gitlab_issue.milestone["id"] in self.milestone_mapping:
            milestone_number = self.milestone_mapping[gitlab_issue.milestone["id"]]
            milestone = self.github_repo.get_milestone(milestone_number)

        # Create GitHub issue
        if milestone:
            github_issue = self.github_repo.create_issue(
                title=gitlab_issue.title, body=issue_body, labels=issue_labels, milestone=milestone
            )
        else:
            github_issue = self.github_repo.create_issue(
                title=gitlab_issue.title, body=issue_body, labels=issue_labels
            )

        return github_issue, cross_links.blocked_issue_iids

    def _create_placeholder_issue(self, expected_number: int) -> github.Issue.Issue:
        """Create a placeholder issue to preserve issue numbering."""
        placeholder_issue = self.github_repo.create_issue(
            title="Placeholder", body="Placeholder to preserve issue numbering - will be deleted"
        )

        if placeholder_issue.number != expected_number:
            msg = f"Placeholder issue number mismatch: expected {expected_number}, got {placeholder_issue.number}"
            raise NumberVerificationError(msg)

        placeholder_issue.edit(state="closed")
        logger.debug(f"Created placeholder issue #{expected_number}")
        return placeholder_issue

    def _create_issues(
        self,
        gitlab_issues: list[GitlabProjectIssue],
    ) -> tuple[dict[int, github.Issue.Issue], dict[int, list[int]]]:
        """First pass: Create issues maintaining number sequence.

        Returns:
            Tuple of (gitlab_issue_map, pending_blocking_relations), where
                - gitlab_to_github_issue_map maps GitLab issue IID to created GitHub issue
                - gitlab_blocks_links is a map {blocking GitLab IID: [blocked GitLab IIDs]}
        """
        gitlab_issue_map: dict[int, GitlabProjectIssue] = {i.iid: i for i in gitlab_issues}
        gitlab_to_github_issue_map: dict[int, github.Issue.Issue] = {}
        gitlab_blocks_links: dict[int, list[int]] = {}
        max_issue_number: int = max(gitlab_issue_map)
        github_placeholder_issues: list[github.Issue.Issue] = []

        for issue_number in range(1, max_issue_number + 1):
            if issue_number in gitlab_issue_map:
                gitlab_issue = gitlab_issue_map[issue_number]

                github_issue, gitlab_blocked_issue_iids = self._create_migrated_issue(gitlab_issue)
                # Verify issue number
                if github_issue.number != issue_number:
                    msg = f"Issue number mismatch: expected {issue_number}, got {github_issue.number}"
                    raise NumberVerificationError(msg)

                gitlab_to_github_issue_map[gitlab_issue.iid] = github_issue
                logger.debug(f"Added issue #{gitlab_issue.iid} to github_issue_dict")

                # Migrate comments
                self.migrate_issue_comments(gitlab_issue, github_issue)

                # Close issue if needed
                if gitlab_issue.state == "closed":
                    github_issue.edit(state="closed")

                if gitlab_blocked_issue_iids:
                    gitlab_blocks_links[gitlab_issue.iid] = gitlab_blocked_issue_iids

                logger.debug(f"Created issue #{issue_number}: {gitlab_issue.title}")
            else:
                github_issue = self._create_placeholder_issue(issue_number)
                github_placeholder_issues.append(github_issue)

        for issue in github_placeholder_issues:
            # Delete placeholder issues using GraphQL API
            ghu.delete_issue(self.github_client, issue.node_id)
            logger.debug(f"Deleted placeholder issue #{issue.number}")

        return gitlab_to_github_issue_map, gitlab_blocks_links

    def _create_parent_child_relations(
        self,
        github_issue_map: dict[int, github.Issue.Issue],
    ) -> None:
        """Second pass: Create parent-child relationships as GitHub sub-issues."""
        for parent_gitlab_iid, parent_github_issue in github_issue_map.items():
            for child_gitlab_iid in glu.get_work_item_children(
                self.gitlab_graphql_client, self.gitlab_project_path, parent_gitlab_iid
            ):
                logger.debug(f"Looking for child issue #{child_gitlab_iid}")

                if child_gitlab_iid not in github_issue_map:
                    logger.warning(f"Child issue #{child_gitlab_iid} not found for parent #{parent_gitlab_iid}")
                    continue

                child_github_issue = github_issue_map[child_gitlab_iid]

                parent_github_issue.add_sub_issue(child_github_issue.id)
                logger.debug(f"Linked issue #{child_gitlab_iid} as sub-issue of #{parent_gitlab_iid}")

    def _create_blocking_relations(
        self,
        gitlab_blocking_links: dict[int, list[int]],
        github_issue_dict: dict[int, github.Issue.Issue],
    ) -> None:
        """Third pass: Create blocking relationships as GitHub issue dependencies."""
        if not gitlab_blocking_links:
            return

        logger.info(f"Processing {len(gitlab_blocking_links)} blocking relationships...")
        owner, repo = self.github_repo_path.split("/")

        for source_gitlab_iid, relations in gitlab_blocking_links.items():
            for target_gitlab_iid in relations:
                # GitLab "blocks" means: source blocks target -> target is blocked by source
                # GitLab "is_blocked_by" means: source is blocked by target
                # We receive each relation twice (once per direction), so skip the reverse direction
                if source_gitlab_iid not in github_issue_dict:
                    logger.warning(f"Source issue #{source_gitlab_iid} not found for blocking relationship")
                    continue
                if target_gitlab_iid not in github_issue_dict:
                    logger.warning(f"Target issue #{target_gitlab_iid} not found for blocking relationship")
                    continue

                source_github_issue = github_issue_dict[source_gitlab_iid]
                target_github_issue = github_issue_dict[target_gitlab_iid]

                success = ghu.create_issue_dependency(
                    self.github_client, owner, repo, target_github_issue.number, source_github_issue.id
                )

                if success:
                    logger.debug(f"Created blocking relationship: #{source_gitlab_iid} blocks #{target_gitlab_iid}")

    def migrate_issues_with_number_preservation(self) -> None:
        """Migrate issues while preserving GitLab issue numbers."""
        gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
        if not gitlab_issues:
            logger.info("No issues to migrate")
            return

        gitlab_to_github_issue_map, gitlab_blocks_links = self._create_issues(gitlab_issues)
        self._create_parent_child_relations(gitlab_to_github_issue_map)
        if gitlab_blocks_links:
            self._create_blocking_relations(gitlab_blocks_links, gitlab_to_github_issue_map)

        logger.info(f"Migrated {len(gitlab_issues)} issues")

    def migrate_issue_comments(self, gitlab_issue: GitlabProjectIssue, github_issue: github.Issue.Issue) -> None:
        """Migrate comments for an issue."""
        notes = gitlab_issue.notes.list(get_all=True)
        notes.sort(key=lambda n: n.created_at)

        for note in notes:
            if note.system:
                comment_body = f"**System note:** {note.body}"
            else:
                comment_body = (
                    f"**Comment by** {note.author['name']} (@{note.author['username']}) "
                    f"**on** {format_timestamp(note.created_at)}\n\n"
                )
                comment_body += "---\n\n"

                if note.body:
                    updated_body = self.attachment_handler.process_content(
                        note.body,
                        context=f"issue #{gitlab_issue.iid} note {note.id}",
                    )
                    comment_body += updated_body

            github_issue.create_comment(comment_body)
            logger.debug(f"Migrated comment by {note.author['username']}")

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

            # Count GitHub items with state breakdown
            # No need to filter placeholders since they are now deleted via GraphQL
            github_issues = list(self.github_repo.get_issues(state="all"))
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

            # Validation
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
