"""Data models for migration between source and target systems.

These models represent the normalized data exchanged between SourceSystem,
TargetSystem, and the Migrator orchestrator. They are intentionally simple
and system-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class Label:
    """A label/tag that can be applied to issues."""

    name: str
    color: str  # Hex color without '#' prefix (e.g., "ff0000")
    description: str = ""


@dataclass
class Milestone:
    """A milestone for grouping issues.

    Not all source systems have milestones (e.g., Azure DevOps doesn't),
    and not all targets may support them. The Migrator handles this gracefully.
    """

    source_number: int
    title: str
    description: str = ""
    state: Literal["open", "closed"] = "open"
    due_date: datetime | None = None


@dataclass
class Attachment:
    """An attachment to be migrated.

    The source system extracts these from content, providing the bytes.
    The target system uploads them and returns the new URL.
    """

    source_url: str  # URL pattern as it appears in source content
    filename: str
    content: bytes


@dataclass
class Issue:
    """An issue/work item from the source system.

    The body_markdown may still contain source-specific URLs and issue
    references. The Migrator uses SourceSystem.transform_content() to
    convert these before passing to the target.
    """

    source_number: int
    title: str
    body_markdown: str
    state: Literal["open", "closed"]
    labels: list[str] = field(default_factory=list)
    milestone_title: str | None = None
    created_at: datetime | None = None
    closed_at: datetime | None = None
    author: str = ""  # Display name for attribution in migrated content


@dataclass
class Comment:
    """A comment on an issue.

    Like Issue.body_markdown, the body may contain source-specific URLs
    and references that need transformation.
    """

    body_markdown: str
    created_at: datetime | None = None
    author: str = ""


@dataclass
class Relationship:
    """A relationship between two issues in the source system.

    The Migrator translates source numbers to target numbers before
    passing to the target system.

    Relationship kinds:
    - parent/child: Hierarchical relationship (child is sub-task of parent)
    - blocks/blocked_by: Dependency relationship
    - related: General association (often rendered as markdown links, not API)
    """

    from_number: int  # Source issue number
    to_number: int  # Source issue number
    kind: Literal["parent", "child", "blocks", "blocked_by", "related"]
