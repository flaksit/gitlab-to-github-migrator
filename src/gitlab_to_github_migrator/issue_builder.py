"""Build GitHub issue body from GitLab issue data."""

from __future__ import annotations

import datetime as dt


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


def build_issue_body(
    *,
    iid: int,
    author_name: str,
    author_username: str,
    created_at: str,
    web_url: str,
    processed_description: str,
    cross_links_text: str,
) -> str:
    """Build complete GitHub issue body with migration header.

    Args:
        iid: GitLab issue IID
        author_name: Original author's display name
        author_username: Original author's username
        created_at: ISO timestamp of issue creation
        web_url: GitLab issue URL
        processed_description: Description with attachments already processed
        cross_links_text: Formatted cross-links section (may be empty)

    Returns:
        Complete issue body for GitHub
    """
    body = f"**Migrated from GitLab issue #{iid}**\n"
    body += f"**Original Author:** {author_name} (@{author_username})\n"
    body += f"**Created:** {format_timestamp(created_at)}\n"
    body += f"**GitLab URL:** {web_url}\n\n"
    body += "---\n\n"
    body += processed_description
    body += cross_links_text
    return body
