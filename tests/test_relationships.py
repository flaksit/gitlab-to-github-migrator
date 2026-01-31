"""Tests for issue relationship data structures."""

from unittest.mock import Mock

import pytest

from gitlab_to_github_migrator.relationships import (
    IssueCrossLinks,
    IssueLinkInfo,
    WorkItemChild,
)


@pytest.mark.unit
class TestWorkItemChild:
    def test_creation(self) -> None:
        child = WorkItemChild(
            iid=123,
            title="Child task",
            state="opened",
            type="Task",
            web_url="https://gitlab.com/org/proj/-/issues/123",
        )
        assert child.iid == 123
        assert child.title == "Child task"
        assert child.state == "opened"
        assert child.type == "Task"


@pytest.mark.unit
class TestIssueLinkInfo:
    def test_creation_with_defaults(self) -> None:
        link = IssueLinkInfo(
            type="blocks",
            target_iid=456,
            target_title="Blocked issue",
            target_project_path="org/project",
            target_web_url="https://gitlab.com/org/project/-/issues/456",
            is_same_project=True,
        )
        assert link.type == "blocks"
        assert link.source == "rest_api"  # default

    def test_creation_with_custom_source(self) -> None:
        link = IssueLinkInfo(
            type="child_of",
            target_iid=789,
            target_title="Child",
            target_project_path="org/project",
            target_web_url="https://gitlab.com/org/project/-/issues/789",
            is_same_project=True,
            source="graphql_work_items",
        )
        assert link.source == "graphql_work_items"


@pytest.mark.unit
class TestIssueCrossLinks:
    def test_creation(self) -> None:
        cross_links = IssueCrossLinks(
            cross_links_text="**Related:** #123",
            parent_child_relations=[],
            blocking_relations=[],
        )
        assert cross_links.cross_links_text == "**Related:** #123"
        assert cross_links.parent_child_relations == []
        assert cross_links.blocking_relations == []


@pytest.mark.unit
class TestGetIssueCrossLinks:
    def test_returns_empty_when_no_links(self) -> None:
        from gitlab_to_github_migrator.relationships import get_issue_cross_links

        mock_issue = Mock()
        mock_issue.iid = 42
        mock_issue.links.list.return_value = []

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {"namespace": {"workItem": {"widgets": []}}}

        result = get_issue_cross_links(mock_issue, "org/project", mock_graphql)

        assert result.cross_links_text == ""
        assert result.parent_child_relations == []
        assert result.blocking_relations == []

    def test_categorizes_blocking_links(self) -> None:
        from gitlab_to_github_migrator.relationships import get_issue_cross_links

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

        result = get_issue_cross_links(mock_issue, "org/project", mock_graphql)

        assert len(result.blocking_relations) == 1
        assert result.blocking_relations[0].type == "blocks"
        assert result.blocking_relations[0].target_iid == 100
