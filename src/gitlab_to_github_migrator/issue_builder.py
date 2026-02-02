"""Build GitHub issue body from GitLab issue data."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gitlab.v4.objects import ProjectIssue

# Minimum time difference (in seconds) to consider showing "last edited" timestamp
LAST_EDITED_THRESHOLD_SECONDS = 60


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO 8601 timestamp to human-readable format.

    Args:
        iso_timestamp: ISO 8601 formatted timestamp string

    Returns:
        Formatted timestamp (e.g., "2024-01-15 10:30:45Z").
        Returns original value if parsing fails.
    """
    if not iso_timestamp:
        return iso_timestamp

    try:
        timestamp_dt = dt.datetime.fromisoformat(iso_timestamp)
        formatted = timestamp_dt.isoformat(sep=" ", timespec="seconds")
        return formatted.replace("+00:00", "Z")
    except (ValueError, AttributeError):
        return iso_timestamp


def should_show_last_edited(created_at: str, updated_at: str) -> bool:
    """Check if last edited timestamp should be shown.

    Args:
        created_at: ISO 8601 formatted creation timestamp
        updated_at: ISO 8601 formatted update timestamp

    Returns:
        True if updated_at differs from created_at by more than LAST_EDITED_THRESHOLD_SECONDS
    """
    if not created_at or not updated_at:
        return False

    try:
        created_dt = dt.datetime.fromisoformat(created_at)
        updated_dt = dt.datetime.fromisoformat(updated_at)
    except (ValueError, AttributeError):
        return False

    diff = abs((updated_dt - created_dt).total_seconds())
    return diff > LAST_EDITED_THRESHOLD_SECONDS


def build_issue_body(
    gitlab_issue: ProjectIssue,
    *,
    processed_description: str | None = None,
    cross_links_text: str | None = None,
) -> str:
    """Build complete GitHub issue body with migration header.

    Args:
        gitlab_issue: GitLab issue object
        processed_description: Description with some text already adapted for GitHub (if any)
        cross_links_text: Formatted cross-links section (may be empty)

    Returns:
        Complete issue body for GitHub
    """
    body = f"**Migrated from GitLab issue #{gitlab_issue.iid}**\n"
    body += f"**Original Author:** {gitlab_issue.author['name']} ({gitlab_issue.author['username']})\n"
    body += f"**Created:** {format_timestamp(gitlab_issue.created_at)}\n"
    body += f"**GitLab URL:** {gitlab_issue.web_url}\n\n"
    body += "---\n\n"
    body += processed_description or gitlab_issue.description
    body += cross_links_text or ""
    return body
