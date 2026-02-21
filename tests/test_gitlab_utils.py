"""Tests for issue relationship data structures."""

from unittest.mock import Mock

import pytest

from gitlab_to_github_migrator.gitlab_utils import (
    IssueCrossLinks,
    get_normal_issue_cross_links,
    mark_project_as_migrated,
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
class TestMarkProjectAsMigrated:
    def _make_project(self, name: str, description: str | None) -> Mock:
        project = Mock()
        project.name = name
        project.description = description
        project.path_with_namespace = "org/project"
        return project

    def test_appends_suffix_and_prepends_url(self) -> None:
        project = self._make_project("My Project", "Original description")
        mark_project_as_migrated(project, "https://github.com/org/repo")
        assert project.name == "My Project (migrated to GitHub)"
        assert project.description == "Migrated to https://github.com/org/repo\n\nOriginal description"
        project.save.assert_called_once()

    def test_none_description_becomes_url_only(self) -> None:
        project = self._make_project("My Project", None)
        mark_project_as_migrated(project, "https://github.com/org/repo")
        assert project.name == "My Project (migrated to GitHub)"
        assert project.description == "Migrated to https://github.com/org/repo"
        project.save.assert_called_once()

    def test_idempotent_name(self) -> None:
        project = self._make_project(
            "My Project (migrated to GitHub)",
            "Migrated to https://github.com/org/repo\n\nSome description",
        )
        mark_project_as_migrated(project, "https://github.com/org/repo")
        assert project.name == "My Project (migrated to GitHub)"
        assert project.description == "Migrated to https://github.com/org/repo\n\nSome description"


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
