"""
Main migration class for GitLab to GitHub migration.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import github.Issue
import github.Milestone
import github.Repository
import gitlab  # noqa: TC002 - used at runtime, not just for type hints
from github import Github, GithubException
from gitlab.exceptions import GitlabAuthenticationError, GitlabError

from . import git_utils, labels
from . import github_utils as ghu
from . import gitlab_utils as glu
from .attachments import AttachmentHandler
from .exceptions import MigrationError, NumberVerificationError
from .gitlab_utils import get_normal_issue_cross_links
from .issue_builder import build_issue_body, format_timestamp, should_show_last_edited

if TYPE_CHECKING:
    from gitlab.v4.objects import Project as GitlabProject
    from gitlab.v4.objects import ProjectIssue as GitlabProjectIssue

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class MigratedIssue:
    """Result of migrating a single issue."""

    github_issue: github.Issue.Issue
    blocked_issue_iids: list[int]
    attachment_count: int


@dataclass
class CommentMigrationResult:
    """Result of migrating comments for an issue."""

    user_comment_count: int
    attachment_count: int


class GitlabToGithubMigrator:
    """Main migration class."""

    def __init__(
        self,
        gitlab_project_path: str,
        github_repo_path: str,
        *,
        label_translations: list[str] | None = None,
        gitlab_token: str | None = None,
        github_token: str,
    ) -> None:
        self.gitlab_project_path: str = gitlab_project_path
        self.github_repo_path: str = github_repo_path

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

        # Track statistics for reporting
        self.total_comments_migrated: int = 0

        # Store git clone path for efficient operations
        self._git_clone_path: str | None = None

        logger.debug(f"Initialized migrator for {gitlab_project_path} -> {github_repo_path}")

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
        print("Mirroring git repository...")
        self._git_clone_path = git_utils.migrate_git_content(
            source_http_url=str(self.gitlab_project.http_url_to_repo),  # pyright: ignore[reportUnknownArgumentType]
            target_clone_url=self.github_repo.clone_url,
            source_token=self.gitlab_token,
            target_token=self.github_token,
        )

    def set_default_branch(self) -> None:
        """Set the default branch in GitHub to match GitLab's default branch."""
        gitlab_default_branch: str = str(self.gitlab_project.default_branch)  # pyright: ignore[reportUnknownArgumentType]
        logger.debug(f"GitLab default branch: {gitlab_default_branch}")

        # Refresh the repository object to ensure we have the latest state after git push
        self._github_repo = self.github_client.get_repo(self.github_repo.full_name)
        logger.debug(f"Refreshed repository object, current default branch: {self.github_repo.default_branch}")

        print(f"Setting default branch to '{gitlab_default_branch}'...")
        ghu.set_default_branch(self.github_repo, gitlab_default_branch)

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
            print("No milestones to migrate")
            return

        print("Migrating milestones...")
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
                logger.info(f"Created milestone #{milestone_number}: {gitlab_milestone.title}")
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

        print(f"Migrated {len(self.milestone_mapping)} milestones")

    def _create_migrated_issue(
        self,
        gitlab_issue: GitlabProjectIssue,
    ) -> MigratedIssue:
        """Create a GitHub issue from a GitLab issue.

        Args:
            gitlab_issue: The GitLab issue to migrate

        Returns:
            MigratedIssue with the created GitHub issue, blocked issue IIDs, and attachment count
        """
        # Process description with attachments
        processed_description = ""
        attachment_count = 0
        if gitlab_issue.description:
            processed = self.attachment_handler.process_content(
                gitlab_issue.description,
                context=f"issue #{gitlab_issue.iid}",
            )
            processed_description = processed.content
            attachment_count = processed.attachment_count

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

        return MigratedIssue(
            github_issue=github_issue,
            blocked_issue_iids=cross_links.blocked_issue_iids,
            attachment_count=attachment_count,
        )

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

                migrated = self._create_migrated_issue(gitlab_issue)
                # Verify issue number
                if migrated.github_issue.number != issue_number:
                    msg = f"Issue number mismatch: expected {issue_number}, got {migrated.github_issue.number}"
                    raise NumberVerificationError(msg)

                gitlab_to_github_issue_map[gitlab_issue.iid] = migrated.github_issue
                logger.debug(f"Added issue #{gitlab_issue.iid} to github_issue_dict")

                # Migrate comments
                comment_result = self.migrate_issue_comments(gitlab_issue, migrated.github_issue)

                # Close issue if needed
                if gitlab_issue.state == "closed":
                    migrated.github_issue.edit(state="closed")

                if migrated.blocked_issue_iids:
                    gitlab_blocks_links[gitlab_issue.iid] = migrated.blocked_issue_iids

                logger.info(f"Created issue #{issue_number}: {gitlab_issue.title}")

                # Print per-issue output
                details: list[str] = []
                total_attachment_count = migrated.attachment_count + comment_result.attachment_count
                if total_attachment_count > 0:
                    details.append(f"{total_attachment_count} attachment{'s' if total_attachment_count != 1 else ''}")
                if comment_result.user_comment_count > 0:
                    details.append(
                        f"{comment_result.user_comment_count} user comment{'s' if comment_result.user_comment_count != 1 else ''}"
                    )

                if details:
                    print(f"  Issue #{issue_number} migrated with {', '.join(details)}")
                else:
                    print(f"  Issue #{issue_number} migrated")
            else:
                github_issue = self._create_placeholder_issue(issue_number)
                github_placeholder_issues.append(github_issue)

        for issue in github_placeholder_issues:
            ghu.delete_issue(self.github_token, issue.node_id)
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
                logger.info(f"Linked issue #{child_gitlab_iid} as sub-issue of #{parent_gitlab_iid}")

    def _create_blocking_relations(
        self,
        gitlab_blocking_links: dict[int, list[int]],
        github_issue_dict: dict[int, github.Issue.Issue],
    ) -> None:
        """Third pass: Create blocking relationships as GitHub issue dependencies."""
        if not gitlab_blocking_links:
            return

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
                    logger.info(f"Created blocking relationship: #{source_gitlab_iid} blocks #{target_gitlab_iid}")

    def migrate_issues_with_number_preservation(self) -> None:
        """Migrate issues while preserving GitLab issue numbers."""
        gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
        if not gitlab_issues:
            print("No issues to migrate")
            return

        print("Migrating issues...")
        gitlab_to_github_issue_map, gitlab_blocks_links = self._create_issues(gitlab_issues)

        self._create_parent_child_relations(gitlab_to_github_issue_map)

        if gitlab_blocks_links:
            print("Setting up blocking relationships...")
            self._create_blocking_relations(gitlab_blocks_links, gitlab_to_github_issue_map)

        print(f"Migrated {len(gitlab_issues)} issues")

    def migrate_issue_comments(
        self, gitlab_issue: GitlabProjectIssue, github_issue: github.Issue.Issue
    ) -> CommentMigrationResult:
        """Migrate comments for an issue.

        Args:
            gitlab_issue: The GitLab issue
            github_issue: The GitHub issue to add comments to

        Returns:
            CommentMigrationResult with user comment count and total attachment count
        """
        notes = gitlab_issue.notes.list(get_all=True)
        notes.sort(key=lambda n: n.created_at)

        user_comment_count = 0
        comment_attachment_count = 0
        # Group consecutive system notes
        note_index = 0
        while note_index < len(notes):
            note = notes[note_index]

            if note.system:
                # Collect consecutive system notes
                system_notes = [note]
                note_index += 1
                while note_index < len(notes) and notes[note_index].system:
                    system_notes.append(notes[note_index])
                    note_index += 1

                # Format system notes
                if len(system_notes) == 1:
                    # Single system note: use compact format
                    body_text = note.body.strip() if note.body else "(empty note)"
                    author_short = note.author["username"]
                    comment_body = (
                        f"**System note** on {format_timestamp(note.created_at)} by {author_short}: {body_text}"
                    )
                else:
                    # Multiple consecutive system notes: use grouped format
                    note_lines = [
                        f"{format_timestamp(sys_note.created_at)} by {sys_note.author['username']}: {sys_note.body.strip() if sys_note.body else '(empty note)'}"
                        for sys_note in system_notes
                    ]
                    comment_body = "### System notes\n" + "\n\n".join(note_lines) + "\n"

                github_issue.create_comment(comment_body)
                logger.debug(f"Migrated {len(system_notes)} system note(s)")
            else:
                # Build compact comment header on single line
                header = f"**Comment by** {note.author['name']} ({note.author['username']}) **on** {format_timestamp(note.created_at)}"
                if should_show_last_edited(note.created_at, note.updated_at):
                    header += f" — **Last Edited:** {format_timestamp(note.updated_at)}"

                comment_body = header + "\n\n"

                if note.body:
                    processed = self.attachment_handler.process_content(
                        note.body,
                        context=f"issue #{gitlab_issue.iid} note {note.id}",
                    )
                    comment_attachment_count += processed.attachment_count
                    comment_body += processed.content

                github_issue.create_comment(comment_body)
                logger.debug(f"Migrated comment by {note.author['username']}")
                user_comment_count += 1
                note_index += 1

        # Track total comments migrated across all issues
        self.total_comments_migrated += user_comment_count

        return CommentMigrationResult(user_comment_count=user_comment_count, attachment_count=comment_attachment_count)

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
            statistics.update(self._collect_statistics())
            self._validate_counts(statistics, errors, report)
            logger.info("Migration validation completed")

        except (GitlabError, GithubException) as e:
            report["success"] = False
            errors.append(f"Validation failed: {e}")
            logger.exception("Validation failed")

        return report

    def _collect_statistics(self) -> dict[str, int]:
        """Collect all migration statistics from GitLab and GitHub."""
        # Collect GitLab statistics
        gitlab_stats = self._collect_gitlab_statistics()

        # Collect GitHub statistics
        github_stats = self._collect_github_statistics()

        # Collect migration-specific statistics
        migration_stats = self._collect_migration_statistics()

        # Combine all statistics
        return {**gitlab_stats, **github_stats, **migration_stats}

    def _collect_gitlab_statistics(self) -> dict[str, int]:
        """Collect statistics from GitLab."""
        # Count issues with state breakdown
        gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
        gitlab_issues_open = [i for i in gitlab_issues if i.state == "opened"]
        gitlab_issues_closed = [i for i in gitlab_issues if i.state == "closed"]

        # Count milestones with state breakdown
        gitlab_milestones = self.gitlab_project.milestones.list(get_all=True, state="all")
        gitlab_milestones_open = [m for m in gitlab_milestones if m.state == "active"]
        gitlab_milestones_closed = [m for m in gitlab_milestones if m.state == "closed"]

        # Count labels
        gitlab_labels = self.gitlab_project.labels.list(get_all=True)

        # Count git repository items using git CLI (efficient)
        if self._git_clone_path:
            gitlab_branches_count = git_utils.count_branches(self._git_clone_path)
            gitlab_tags_count = git_utils.count_tags(self._git_clone_path)
            gitlab_commits_count = git_utils.count_unique_commits(self._git_clone_path)
        else:
            # Fallback to API if clone not available (shouldn't happen in normal flow)
            gitlab_branches = self.gitlab_project.branches.list(get_all=True)
            gitlab_tags = self.gitlab_project.tags.list(get_all=True)
            gitlab_branches_count = len(gitlab_branches)
            gitlab_tags_count = len(gitlab_tags)
            gitlab_commits_count = glu.count_unique_commits(self.gitlab_project)

        return {
            "gitlab_issues_total": len(gitlab_issues),
            "gitlab_issues_open": len(gitlab_issues_open),
            "gitlab_issues_closed": len(gitlab_issues_closed),
            "gitlab_milestones_total": len(gitlab_milestones),
            "gitlab_milestones_open": len(gitlab_milestones_open),
            "gitlab_milestones_closed": len(gitlab_milestones_closed),
            "gitlab_labels_total": len(gitlab_labels),
            "gitlab_branches": gitlab_branches_count,
            "gitlab_tags": gitlab_tags_count,
            "gitlab_commits": gitlab_commits_count,
        }

    def _collect_github_statistics(self) -> dict[str, int]:
        """Collect statistics from GitHub."""
        # Count issues with state breakdown
        github_issues = list(self.github_repo.get_issues(state="all"))
        github_issues_open = [i for i in github_issues if i.state == "open"]
        github_issues_closed = [i for i in github_issues if i.state == "closed"]

        # Count milestones with state breakdown
        github_milestones_all = list(self.github_repo.get_milestones(state="all"))
        github_milestones = [m for m in github_milestones_all if m.title != "Placeholder Milestone"]
        github_milestones_open = [m for m in github_milestones if m.state == "open"]
        github_milestones_closed = [m for m in github_milestones if m.state == "closed"]

        # Count labels
        github_labels_all = list(self.github_repo.get_labels())
        labels_created = len(github_labels_all) - len(self.initial_github_labels)

        # Count git repository items using git CLI (efficient)
        # Since we pushed to GitHub, the counts should match the source
        if self._git_clone_path:
            github_branches_count = git_utils.count_branches(self._git_clone_path)
            github_tags_count = git_utils.count_tags(self._git_clone_path)
            github_commits_count = git_utils.count_unique_commits(self._git_clone_path)
        else:
            # Fallback to API if clone not available
            github_branches = list(self.github_repo.get_branches())
            github_tags = list(self.github_repo.get_tags())
            github_branches_count = len(github_branches)
            github_tags_count = len(github_tags)
            github_commits_count = ghu.count_unique_commits(self.github_repo)

        return {
            "github_issues_total": len(github_issues),
            "github_issues_open": len(github_issues_open),
            "github_issues_closed": len(github_issues_closed),
            "github_milestones_total": len(github_milestones),
            "github_milestones_open": len(github_milestones_open),
            "github_milestones_closed": len(github_milestones_closed),
            "github_labels_existing": len(self.initial_github_labels),
            "github_labels_created": max(0, labels_created),
            "labels_translated": len(self.label_mapping),
            "github_branches": github_branches_count,
            "github_tags": github_tags_count,
            "github_commits": github_commits_count,
        }

    def _collect_migration_statistics(self) -> dict[str, int]:
        """Collect migration-specific statistics."""
        return {
            "comments_migrated": self.total_comments_migrated,
            "attachments_uploaded": self._attachment_handler.uploaded_files_count if self._attachment_handler else 0,
            "attachments_referenced": self._attachment_handler.total_attachments_referenced
            if self._attachment_handler
            else 0,
        }

    def _validate_counts(self, statistics: dict[str, int], errors: list[str], report: dict[str, Any]) -> None:
        """Validate that GitLab and GitHub counts match."""
        # Validate issue counts
        if statistics["gitlab_issues_total"] != statistics["github_issues_total"]:
            errors.append(
                f"Issue count mismatch: GitLab {statistics['gitlab_issues_total']}, "
                f"GitHub {statistics['github_issues_total']}"
            )
            report["success"] = False

        # Validate milestone counts
        if statistics["gitlab_milestones_total"] != statistics["github_milestones_total"]:
            errors.append(
                f"Milestone count mismatch: GitLab {statistics['gitlab_milestones_total']}, "
                f"GitHub {statistics['github_milestones_total']}"
            )
            report["success"] = False

        # Validate git repository counts
        if statistics["gitlab_branches"] != statistics["github_branches"]:
            errors.append(
                f"Branch count mismatch: GitLab {statistics['gitlab_branches']}, "
                f"GitHub {statistics['github_branches']}"
            )
            report["success"] = False

        if statistics["gitlab_tags"] != statistics["github_tags"]:
            errors.append(
                f"Tag count mismatch: GitLab {statistics['gitlab_tags']}, GitHub {statistics['github_tags']}"
            )
            report["success"] = False

        if statistics["gitlab_commits"] != statistics["github_commits"]:
            errors.append(
                f"Commit count mismatch: GitLab {statistics['gitlab_commits']}, GitHub {statistics['github_commits']}"
            )
            report["success"] = False

    def create_github_repo(self) -> None:
        self._github_repo = ghu.create_repo(
            self.github_client,
            self.github_repo_path,
            self.gitlab_project.description,  # pyright: ignore[reportUnknownArgumentType]
        )

    def mark_gitlab_project_as_migrated(self) -> None:
        """Mark the GitLab project as migrated by updating its title and description."""
        glu.mark_project_as_migrated(self.gitlab_project, self.github_repo.html_url)

    def migrate(self, *, mark_as_migrated: bool = True) -> dict[str, Any]:
        """Execute the complete migration process."""
        try:
            print(f"Starting migration: {self.gitlab_project_path} → {self.github_repo_path}")

            # Validation
            self.validate_api_access()

            # Repository creation and content migration
            self.create_github_repo()
            self.migrate_git_content()
            self.set_default_branch()

            # Metadata migration
            self.migrate_labels()
            self.migrate_milestones_with_number_preservation()
            self.migrate_issues_with_number_preservation()

            # Validation
            report = self.validate_migration()

            if mark_as_migrated:
                self.mark_gitlab_project_as_migrated()

            print("Migration completed successfully")

        except (GitlabError, GithubException, subprocess.CalledProcessError, OSError) as e:
            logger.exception("Migration failed")
            # Optionally clean up created repository
            if self._github_repo:
                try:
                    logger.warning("Cleaning up created repository due to failure")
                    self._github_repo.delete()
                except GithubException:
                    logger.exception("Failed to cleanup repository")

            msg = f"Migration failed: {e}"
            raise MigrationError(msg) from e
        finally:
            # Clean up git clone directory
            if self._git_clone_path:
                git_utils.cleanup_git_clone(self._git_clone_path)

        return report
