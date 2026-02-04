"""Migration orchestrator that coordinates source and target systems.

The Migrator class is the central coordinator for migration. It:
1. Manages the flow between SourceSystem and TargetSystem
2. Builds and maintains the issue number mapping
3. Coordinates content transformation (attachments, issue references)
4. Handles error recovery and reporting

Migration Flow
--------------
The migration proceeds in phases, designed to minimize round-trips
between source and target while handling dependencies correctly:

Phase 1: Preparation
    - Validate API access to both source and target
    - Create target repository (or validate it exists)
    - Push git content from source to target
    - Pre-fetch issue numbers from source to build number_map
      (This enables all content transformation in a single pass)

Phase 2: Labels
    - Fetch all labels from source
    - Create labels in target
    - One-way batch operation, no dependencies

Phase 3: Milestones (optional)
    - Fetch milestones from source (may be empty for some sources)
    - Create milestones in target, building milestone_map
    - For targets with number preservation, may create placeholders

Phase 4: Issues and Comments
    For each issue (in migration order):
        a. Fetch issue details from source
        b. Fetch comments for this issue
        c. For issue body and each comment body:
           - Extract attachments from content
           - Upload attachments to target
           - Build attachment_url_map for this content
           - Transform content (replace issue refs + attachment URLs)
        d. Create issue in target with transformed body
        e. Create comments in target with transformed bodies

    The number_map (computed in Phase 1) allows transforming issue
    references without needing a second pass.

Phase 5: Relationships
    - Fetch all relationships from source
    - Translate source numbers to target numbers using number_map
    - Create relationships in target (parent-child, dependencies)
    - Must happen after all issues exist in target

Attachment Flow Detail
----------------------
Attachments require coordination between source and target:

    content (with source URLs)
           │
           ▼
    ┌──────────────────┐
    │ Source.extract_  │ ──► list[Attachment]
    │ attachments()    │     (with downloaded bytes)
    └──────────────────┘
           │
           ▼
    ┌──────────────────┐
    │ Target.upload_   │ ──► url_map: {source_url: target_url}
    │ attachment()     │
    └──────────────────┘
           │
           ▼
    ┌──────────────────┐
    │ Source.transform_│ ──► content (with target URLs)
    │ content()        │
    └──────────────────┘

This is the only unavoidable round-trip between source and target,
because:
- Source knows how to find its URL patterns in content
- Target knows where to store attachments and what URLs they get
- Source knows how to replace its URL patterns

Number Mapping
--------------
Issue numbers differ between source and target:
- GitLab: Issues start at 1, may have gaps from deleted issues
- Azure DevOps: Work items have high numbers (e.g., 7001+), sparse
- GitHub (target): Issues are sequential from 1

The number_map translates source → target:
    {7001: 1, 7005: 2, 7006: 3, ...}

This is computed upfront from source.get_issue_numbers(), enabling
issue reference transformation without a second pass.

Error Handling
--------------
- API errors: Logged and optionally retried (configurable)
- Number verification: Fails fast if target numbers don't match expected
- Partial migration: State can be inspected for recovery/retry
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocols import SourceSystem, TargetSystem

logger = logging.getLogger(__name__)


@dataclass
class MigrationStats:
    """Statistics collected during migration."""

    labels_created: int = 0
    milestones_created: int = 0
    issues_created: int = 0
    comments_created: int = 0
    attachments_uploaded: int = 0
    relationships_created: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class MigrationResult:
    """Result of a migration run."""

    success: bool
    stats: MigrationStats
    number_map: dict[int, int]  # source_number -> target_number
    milestone_map: dict[int, int]  # source_milestone_number -> target_milestone_number


class Migrator:
    """Orchestrates migration from a source system to a target system.

    Usage:
        source = GitLabSource(gitlab_client, project)
        target = GitHubTarget(github_client, repo)
        migrator = Migrator(source, target)
        result = migrator.migrate()

    The migrator is stateless between runs - all state is returned in
    MigrationResult. This allows inspection and potential retry logic.
    """

    _source: SourceSystem
    _target: TargetSystem

    def __init__(self, source: SourceSystem, target: TargetSystem) -> None:
        """Initialize the migrator.

        Args:
            source: Source system to migrate from
            target: Target system to migrate to
        """
        self._source = source
        self._target = target

    def migrate(
        self,
        *,
        include_git: bool = True,
        include_milestones: bool = True,
        include_relationships: bool = True,
    ) -> MigrationResult:
        """Execute the full migration.

        Args:
            include_git: Whether to push git content
            include_milestones: Whether to migrate milestones
            include_relationships: Whether to create issue relationships

        Returns:
            MigrationResult with statistics and mappings

        Raises:
            MigrationError: If a critical error occurs that prevents continuation
        """
        raise NotImplementedError("Migration logic not yet implemented")

    def _build_number_map(self) -> dict[int, int]:
        """Build mapping from source issue numbers to target numbers.

        Target numbers are sequential starting from 1, in migration order.
        """
        raise NotImplementedError

    def _migrate_labels(self, stats: MigrationStats) -> None:
        """Migrate all labels from source to target."""
        raise NotImplementedError

    def _migrate_milestones(self, stats: MigrationStats) -> dict[int, int]:
        """Migrate milestones, returning source->target number mapping."""
        raise NotImplementedError

    def _migrate_issue(
        self,
        source_number: int,
        target_number: int,
        number_map: dict[int, int],
        milestone_map: dict[int, int],
        stats: MigrationStats,
    ) -> None:
        """Migrate a single issue with its comments."""
        raise NotImplementedError

    def _transform_and_upload_content(
        self,
        content: str,
        number_map: dict[int, int],
        context: str,
    ) -> str:
        """Transform content: upload attachments, replace URLs and issue refs.

        This is the core content transformation that coordinates between
        source and target for attachments.

        Args:
            content: Original content from source
            number_map: Issue number mapping for reference replacement
            context: Context string for logging (e.g., "issue #7001")

        Returns:
            Transformed content ready for target
        """
        raise NotImplementedError

    def _migrate_relationships(
        self,
        number_map: dict[int, int],
        stats: MigrationStats,
    ) -> None:
        """Migrate all issue relationships."""
        raise NotImplementedError
