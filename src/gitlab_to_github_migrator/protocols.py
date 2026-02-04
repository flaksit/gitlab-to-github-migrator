"""Protocols defining the contracts for source and target systems.

The migration architecture separates concerns into three components:

1. SourceSystem: Extracts data from the source (GitLab, Azure DevOps, etc.)
2. TargetSystem: Creates data in the target (GitHub)
3. Migrator: Orchestrates the flow, handles number mapping and content transformation

This separation allows:
- Adding new source systems without changing target or orchestration code
- Testing components in isolation with mock implementations
- Clear boundaries for source-specific logic (URL patterns, API quirks)
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .models import Attachment, Comment, Issue, Label, Milestone, Relationship


class SourceSystem(Protocol):
    """Protocol for extracting data from a source system.

    Implementations handle source-specific API access, data extraction,
    and content transformation. The protocol is designed to:

    1. Provide data in a normalized format (using models.py dataclasses)
    2. Handle source-specific content patterns (attachment URLs, issue refs)
    3. Support pre-computing the issue number list for upfront mapping

    Content Transformation:
        Source systems know their own URL and reference patterns. The Migrator
        calls transform_content() with the number_map and attachment_url_map,
        and the source implementation handles the actual replacements.

    Attachment Flow:
        1. Migrator calls extract_attachments(content) to get Attachment objects
        2. Migrator uploads attachments to target, building url_map
        3. Migrator calls transform_content() which replaces URLs using url_map

    Example implementations:
        - GitLabSource: Handles GitLab REST/GraphQL APIs, `/uploads/` URLs, `#123` refs
        - AzureDevOpsSource: Handles ADO REST API, attachment URLs, `#7001` or `AB#7001` refs
    """

    def get_labels(self) -> Iterator[Label]:
        """Yield all labels from the source system."""
        ...

    def get_milestones(self) -> Iterator[Milestone]:
        """Yield all milestones from the source system.

        May yield nothing if the source system doesn't support milestones
        (e.g., Azure DevOps).
        """
        ...

    def get_issue_numbers(self) -> list[int]:
        """Return all issue numbers in migration order.

        This is called before migration starts to pre-compute the number_map.
        The order determines the target numbers: first issue becomes #1, etc.

        Returns:
            List of source issue numbers in the order they should be migrated.
        """
        ...

    def get_issue(self, source_number: int) -> Issue:
        """Get a single issue by its source number.

        The returned Issue.body_markdown may contain source-specific URLs
        and references that will be transformed by transform_content().
        """
        ...

    def get_comments(self, source_number: int) -> Iterator[Comment]:
        """Yield all comments for an issue in chronological order."""
        ...

    def get_relationships(self) -> Iterator[Relationship]:
        """Yield all relationships between issues.

        Relationships use source issue numbers. The Migrator translates
        these to target numbers before passing to the target system.

        Note: Some sources may represent certain relationships (e.g., "related")
        as text in the issue body rather than structured data. Such relationships
        would be handled via transform_content() instead of this method.
        """
        ...

    def extract_attachments(self, content: str) -> list[Attachment]:
        """Extract attachments from content, downloading their bytes.

        Finds source-specific attachment URL patterns in the content and
        downloads the referenced files.

        Args:
            content: Markdown content that may contain attachment URLs

        Returns:
            List of Attachment objects with downloaded content. The source_url
            field contains the URL pattern as it appears in the content.
        """
        ...

    def transform_content(
        self,
        content: str,
        number_map: dict[int, int],
        attachment_url_map: dict[str, str],
    ) -> str:
        """Transform content by replacing source-specific patterns.

        This method handles:
        1. Issue reference replacement (e.g., #7001 -> #1)
        2. Attachment URL replacement (e.g., source URL -> target URL)
        3. Any other source-specific content transformations

        Args:
            content: Original markdown content
            number_map: Mapping from source issue numbers to target numbers
            attachment_url_map: Mapping from source URLs to target URLs

        Returns:
            Transformed content ready for the target system
        """
        ...


class TargetSystem(Protocol):
    """Protocol for creating data in a target system.

    Implementations handle target-specific API access and resource creation.
    The protocol assumes a GitHub-like model but is generic enough for
    other issue trackers.

    The Migrator calls methods in a specific order:
    1. validate_access() - Ensure API access works
    2. create_repository() - Create or validate target repository
    3. push_git_content() - Mirror git history
    4. create_label() - Create all labels
    5. create_milestone() - Create all milestones (if supported)
    6. upload_attachment() - Upload attachments as issues are processed
    7. create_issue() - Create issues with transformed content
    8. create_comment() - Add comments to issues
    9. create_parent_child() / create_dependency() - Create relationships

    Example implementations:
        - GitHubTarget: Uses PyGithub for REST API, handles rate limiting,
          uses draft releases for attachment storage
    """

    def validate_access(self) -> None:
        """Validate API access to the target system.

        Raises:
            MigrationError: If access validation fails
        """
        ...

    def create_repository(self, name: str, description: str = "", private: bool = True) -> None:
        """Create the target repository or validate it exists.

        Args:
            name: Repository name
            description: Repository description
            private: Whether the repository should be private

        Raises:
            MigrationError: If repository creation/validation fails
        """
        ...

    def push_git_content(self, source_clone_url: str, source_token: str) -> None:
        """Mirror git content from source to target.

        Args:
            source_clone_url: HTTPS clone URL of the source repository
            source_token: Token for authenticating with the source

        Raises:
            MigrationError: If git operations fail
        """
        ...

    def create_label(self, label: Label) -> None:
        """Create a label in the target system.

        Should handle the case where the label already exists gracefully.
        """
        ...

    def create_milestone(self, milestone: Milestone) -> int:
        """Create a milestone and return its target number.

        For systems that preserve milestone numbers (like GitHub), this may
        need to create placeholder milestones to maintain numbering.

        Args:
            milestone: Milestone data from source

        Returns:
            The milestone number in the target system
        """
        ...

    def upload_attachment(self, attachment: Attachment) -> str:
        """Upload an attachment and return its new URL.

        The implementation determines where attachments are stored
        (e.g., GitHub uses draft releases).

        Args:
            attachment: Attachment with content bytes

        Returns:
            The URL where the attachment can be accessed in the target system
        """
        ...

    def create_issue(
        self,
        issue: Issue,
        target_number: int,
        milestone_number: int | None = None,
    ) -> None:
        """Create an issue in the target system.

        The issue.body_markdown should already be transformed (URLs and
        references replaced) by the Migrator before calling this method.

        For systems that support issue number preservation (like GitHub),
        this may need to create placeholder issues to achieve the desired
        target_number.

        Args:
            issue: Issue data with transformed body
            target_number: The issue number this should have in the target
            milestone_number: Target milestone number, if applicable

        Raises:
            NumberVerificationError: If the created issue has wrong number
        """
        ...

    def create_comment(self, target_issue_number: int, comment: Comment) -> None:
        """Add a comment to an issue.

        The comment.body_markdown should already be transformed.

        Args:
            target_issue_number: The issue number in the target system
            comment: Comment data with transformed body
        """
        ...

    def create_parent_child(self, parent_number: int, child_number: int) -> bool:
        """Create a parent-child relationship between issues.

        Args:
            parent_number: Target issue number of the parent
            child_number: Target issue number of the child

        Returns:
            True if relationship was created, False if it couldn't be
            (e.g., target doesn't support sub-issues)
        """
        ...

    def create_dependency(self, blocker_number: int, blocked_number: int) -> bool:
        """Create a blocking dependency between issues.

        Args:
            blocker_number: Target issue number that blocks
            blocked_number: Target issue number that is blocked

        Returns:
            True if dependency was created, False if it couldn't be
        """
        ...
