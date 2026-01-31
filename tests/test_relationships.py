"""Tests for issue relationship data structures."""

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
