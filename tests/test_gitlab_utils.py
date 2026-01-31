"""Tests for issue relationship data structures."""

from unittest.mock import Mock

import pytest

from gitlab_to_github_migrator.gitlab_utils import (
    IssueCrossLinks,
    get_normal_issue_cross_links,
)


@pytest.mark.unit
class TestIssueCrossLinks:
    def test_creation(self) -> None:
        cross_links = IssueCrossLinks(
            cross_links_text="**Related:** #123",
            blocked_issue_iids=[],
        )
        assert cross_links.cross_links_text == "**Related:** #123"
        assert cross_links.blocked_issue_iids == []


@pytest.mark.unit
class TestGetIssueCrossLinks:
    def test_returns_empty_when_no_links(self) -> None:
        mock_issue = Mock()
        mock_issue.iid = 42
        mock_issue.links.list.return_value = []

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {"namespace": {"workItem": {"widgets": []}}}

        result = get_normal_issue_cross_links(mock_issue, "org/project")

        assert result.cross_links_text == ""
        assert result.blocked_issue_iids == []

    def test_categorizes_blocking_links(self) -> None:
        mock_issue = Mock()
        mock_issue.iid = 42

        mock_link = Mock()
        mock_link.link_type = "blocks"
        mock_link.iid = 100
        mock_link.title = "Blocked issue"
        mock_link.references = {"full": "org/project#100"}
        mock_link.web_url = "https://gitlab.com/org/project/-/issues/100"
        mock_issue.links.list.return_value = [mock_link]

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {"namespace": {"workItem": {"widgets": []}}}

        result = get_normal_issue_cross_links(mock_issue, "org/project")

        assert len(result.blocked_issue_iids) == 1
        assert result.blocked_issue_iids[0] == 100
