"""Tests for issue body building functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gitlab_to_github_migrator.issue_builder import build_issue_body, format_timestamp, should_show_last_edited


@pytest.mark.unit
class TestFormatTimestamp:
    def test_format_with_z_suffix(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45.123Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_with_utc_offset(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45.123456+00:00")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_without_microseconds(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_with_non_utc_timezone(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45+05:30")
        assert result == "2024-01-15 10:30:45+05:30"

    def test_empty_string_returns_as_is(self) -> None:
        result = format_timestamp("")
        assert result == ""

    def test_invalid_format_returns_original(self) -> None:
        result = format_timestamp("invalid-timestamp")
        assert result == "invalid-timestamp"


@pytest.mark.unit
class TestShouldShowLastEdited:
    def test_no_difference(self) -> None:
        created = "2024-01-15T10:30:45Z"
        updated = "2024-01-15T10:30:45Z"
        assert not should_show_last_edited(created, updated)

    def test_difference_less_than_one_minute(self) -> None:
        created = "2024-01-15T10:30:45Z"
        updated = "2024-01-15T10:31:30Z"  # 45 seconds
        assert not should_show_last_edited(created, updated)

    def test_difference_exactly_one_minute(self) -> None:
        created = "2024-01-15T10:30:45Z"
        updated = "2024-01-15T10:31:45Z"  # exactly 60 seconds
        assert not should_show_last_edited(created, updated)

    def test_difference_more_than_one_minute(self) -> None:
        created = "2024-01-15T10:30:45Z"
        updated = "2024-01-15T10:32:00Z"  # 75 seconds
        assert should_show_last_edited(created, updated)

    def test_difference_hours(self) -> None:
        created = "2024-01-15T10:30:45Z"
        updated = "2024-01-15T12:30:45Z"  # 2 hours
        assert should_show_last_edited(created, updated)

    def test_empty_created_at(self) -> None:
        assert not should_show_last_edited("", "2024-01-15T10:30:45Z")

    def test_empty_updated_at(self) -> None:
        assert not should_show_last_edited("2024-01-15T10:30:45Z", "")

    def test_invalid_timestamps(self) -> None:
        assert not should_show_last_edited("invalid", "also-invalid")


@pytest.mark.unit
class TestBuildIssueBody:
    def test_basic_issue_body(self) -> None:
        issue = MagicMock(
            iid=42,
            author={"name": "John Doe", "username": "johndoe"},
            created_at="2024-01-15T10:30:45Z",
            updated_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            description="Issue description here",
        )
        result = build_issue_body(
            issue,
            processed_description="Issue description here",
            cross_links_text="",
        )
        assert "**Migrated from GitLab issue #42**" in result
        assert "**Original Author:** John Doe (@johndoe)" in result
        assert "**Created:** 2024-01-15 10:30:45Z" in result
        assert "**Last Edited:**" not in result  # Last edited not shown for issues
        assert "**GitLab URL:** https://gitlab.com/org/proj/-/issues/42" in result
        assert "Issue description here" in result

    def test_issue_body_with_cross_links(self) -> None:
        issue = MagicMock(
            iid=42,
            author={"name": "John Doe", "username": "johndoe"},
            created_at="2024-01-15T10:30:45Z",
            updated_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            description="Description",
        )
        result = build_issue_body(
            issue,
            processed_description="Description",
            cross_links_text="\n\n**Related:** #123",
        )
        assert "**Related:** #123" in result

    def test_issue_body_with_empty_description(self) -> None:
        issue = MagicMock(
            iid=42,
            author={"name": "Jane", "username": "jane"},
            created_at="2024-01-15T10:30:45Z",
            updated_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            description="",
        )
        result = build_issue_body(
            issue,
            processed_description="",
            cross_links_text="",
        )
        assert "**Migrated from GitLab issue #42**" in result
        assert "---" in result
