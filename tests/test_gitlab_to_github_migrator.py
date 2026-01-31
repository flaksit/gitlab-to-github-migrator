"""
Tests for GitLab to GitHub Migration Tool
"""

from unittest.mock import Mock, PropertyMock, patch

import pytest
from github import GithubException
from gitlab.exceptions import GitlabError

from gitlab_to_github_migrator import GitlabToGithubMigrator, MigrationError
from gitlab_to_github_migrator.gitlab_utils import get_work_item_children


@pytest.mark.unit
class TestGitlabToGithubMigrator:
    """Test main migration functionality."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.gitlab_project_path: str = "test-org/test-project"
        self.github_repo_path: str = "github-org/test-repo"

        # Mock GitLab project
        self.mock_gitlab_project: Mock = Mock()
        self.mock_gitlab_project.id = 12345
        self.mock_gitlab_project.name = "test-project"
        self.mock_gitlab_project.description = "Test project description"
        self.mock_gitlab_project.web_url = "https://gitlab.com/test-org/test-project"
        self.mock_gitlab_project.ssh_url_to_repo = "git@gitlab.com:test-org/test-project.git"

        # Mock GitHub repo
        self.mock_github_repo: Mock = Mock()
        self.mock_github_repo.html_url = "https://github.com/github-org/test-repo"
        self.mock_github_repo.ssh_url = "git@github.com:github-org/test-repo.git"

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_init(self, mock_github_class, mock_gitlab_class) -> None:
        """Test migrator initialization."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            label_translations=["p_*:priority: *"],
            github_token="test_token",
        )

        assert migrator.gitlab_project_path == self.gitlab_project_path
        assert migrator.github_repo_path == self.github_repo_path
        assert migrator._label_translations == ["p_*:priority: *"]

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_validate_api_access_success(self, mock_github_class, mock_gitlab_class) -> None:
        """Test successful API validation."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project
        mock_github_client.get_user.return_value = Mock()

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")

        # Should not raise an exception
        migrator.validate_api_access()

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_validate_api_access_gitlab_failure(self, mock_github_class, mock_gitlab_class) -> None:
        """Test GitLab API validation failure."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        # Make GitLab project accessible during init but fail during validation
        mock_gitlab_project = Mock()
        mock_gitlab_project.name = "test-project"  # Works during init
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")

        # Now make the name property fail by replacing the project
        failing_project = Mock()
        type(failing_project).name = PropertyMock(side_effect=GitlabError("GitLab API error"))
        migrator.gitlab_project = failing_project

        with pytest.raises(MigrationError, match="GitLab API access failed"):
            migrator.validate_api_access()

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_handle_labels(self, mock_github_class, mock_gitlab_class) -> None:
        """Test label handling and translation."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        # Mock GitLab labels
        mock_label1 = Mock()
        mock_label1.name = "p_high"
        mock_label1.color = "#ff0000"
        mock_label1.description = "High priority"

        mock_label2 = Mock()
        mock_label2.name = "bug"
        mock_label2.color = "#00ff00"
        mock_label2.description = "Bug report"

        self.mock_gitlab_project.labels.list.return_value = [mock_label1, mock_label2]

        # Mock GitHub organization (no default labels)
        mock_org = Mock()
        mock_org.get_labels.side_effect = GithubException(404, {}, headers={})
        mock_github_client.get_organization.return_value = mock_org

        # Mock GitHub repo labels
        self.mock_github_repo.get_labels.return_value = []

        def create_label_side_effect(**kwargs):
            label = Mock()
            label.name = kwargs["name"]
            return label

        self.mock_github_repo.create_label.side_effect = create_label_side_effect

        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            label_translations=["p_*:priority: *"],
            github_token="test_token",
        )
        migrator.github_repo = self.mock_github_repo

        migrator.migrate_labels()

        # Check label mapping
        assert "p_high" in migrator.label_mapping
        assert "bug" in migrator.label_mapping
        assert migrator.label_mapping["p_high"] == "priority: high"
        assert migrator.label_mapping["bug"] == "bug"

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_migrate_milestones_with_gaps(self, mock_github_class, mock_gitlab_class) -> None:
        """Test milestone migration with gaps in numbering."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        # Mock GitLab milestones with gaps (1, 3, 5)
        mock_milestone1 = Mock()
        mock_milestone1.iid = 1
        mock_milestone1.id = 101
        mock_milestone1.title = "Milestone 1"
        mock_milestone1.state = "active"
        mock_milestone1.description = "First milestone"
        mock_milestone1.due_date = None

        mock_milestone3 = Mock()
        mock_milestone3.iid = 3
        mock_milestone3.id = 103
        mock_milestone3.title = "Milestone 3"
        mock_milestone3.state = "closed"
        mock_milestone3.description = "Third milestone"
        mock_milestone3.due_date = None

        mock_milestone5 = Mock()
        mock_milestone5.iid = 5
        mock_milestone5.id = 105
        mock_milestone5.title = "Milestone 5"
        mock_milestone5.state = "active"
        mock_milestone5.description = "Fifth milestone"
        mock_milestone5.due_date = None

        self.mock_gitlab_project.milestones.list.return_value = [mock_milestone1, mock_milestone3, mock_milestone5]

        # Mock GitHub milestone creation
        created_milestones = []

        def create_milestone_side_effect(**kwargs):
            milestone = Mock()
            milestone.number = len(created_milestones) + 1
            milestone.title = kwargs["title"]
            milestone.state = kwargs["state"]
            created_milestones.append(milestone)
            return milestone

        self.mock_github_repo.create_milestone.side_effect = create_milestone_side_effect

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator.github_repo = self.mock_github_repo

        migrator.migrate_milestones_with_number_preservation()

        # Should create 5 milestones (3 real, 2 placeholders)
        assert self.mock_github_repo.create_milestone.call_count == 5

        # Check milestone mapping for real milestones
        assert 101 in migrator.milestone_mapping  # milestone1.id -> 1
        assert 103 in migrator.milestone_mapping  # milestone3.id -> 3
        assert 105 in migrator.milestone_mapping  # milestone5.id -> 5

        assert migrator.milestone_mapping[101] == 1
        assert migrator.milestone_mapping[103] == 3
        assert migrator.milestone_mapping[105] == 5

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_validation_report_success(self, mock_github_class, mock_gitlab_class) -> None:
        """Test successful validation report generation."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client
        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator.gitlab_project = self.mock_gitlab_project
        migrator.github_repo = self.mock_github_repo
        migrator.label_mapping = {"label1": "label1", "label2": "label2"}

        # Mock GitLab items
        self.mock_gitlab_project.issues.list.return_value = [Mock(), Mock()]  # 2 issues
        self.mock_gitlab_project.milestones.list.return_value = [Mock()]  # 1 milestone
        self.mock_gitlab_project.labels.list.return_value = [Mock(), Mock()]  # 2 labels

        # Mock GitHub items (no placeholders)
        github_issues = [Mock(), Mock()]
        for issue in github_issues:
            issue.title = "Real Issue"
        self.mock_github_repo.get_issues.return_value = github_issues

        github_milestones = [Mock()]
        for milestone in github_milestones:
            milestone.title = "Real Milestone"
        self.mock_github_repo.get_milestones.return_value = github_milestones

        # Mock GitHub labels
        self.mock_github_repo.get_labels.return_value = []

        report = migrator.validate_migration()

        assert report["success"] is True
        assert len(report["errors"]) == 0
        assert report["statistics"]["gitlab_issues_total"] == 2
        assert report["statistics"]["github_issues_total"] == 2
        assert report["statistics"]["gitlab_milestones_total"] == 1
        assert report["statistics"]["github_milestones_total"] == 1
        assert report["statistics"]["labels_translated"] == 2

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_validation_report_failure(self, mock_github_class, mock_gitlab_class) -> None:
        """Test validation report with mismatched counts."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client
        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator.gitlab_project = self.mock_gitlab_project
        migrator.github_repo = self.mock_github_repo
        migrator.label_mapping = {}

        # Mock mismatched counts
        self.mock_gitlab_project.issues.list.return_value = [Mock(), Mock()]  # 2 issues
        self.mock_gitlab_project.milestones.list.return_value = [Mock()]  # 1 milestone
        self.mock_gitlab_project.labels.list.return_value = []  # No labels

        # Mock GitHub with different counts
        github_issues = [Mock()]  # Only 1 issue
        for issue in github_issues:
            issue.title = "Real Issue"
        self.mock_github_repo.get_issues.return_value = github_issues

        github_milestones = [Mock(), Mock()]  # 2 milestones
        for milestone in github_milestones:
            milestone.title = "Real Milestone"
        self.mock_github_repo.get_milestones.return_value = github_milestones

        # Mock GitHub labels
        self.mock_github_repo.get_labels.return_value = []

        report = migrator.validate_migration()

        assert report["success"] is False
        assert len(report["errors"]) == 2  # Issue and milestone count mismatches
        assert "Issue count mismatch" in report["errors"][0]
        assert "Milestone count mismatch" in report["errors"][1]


@pytest.mark.unit
class TestCreateIssueDependency:
    def test_creates_dependency_successfully(self) -> None:
        from unittest.mock import Mock

        from gitlab_to_github_migrator.github_utils import create_issue_dependency

        mock_client = Mock()
        mock_client.requester.requestJson.return_value = (201, {}, {"id": 123})

        result = create_issue_dependency(mock_client, "owner", "repo", blocked_issue_number=10, blocking_issue_id=999)

        assert result is True
        mock_client.requester.requestJson.assert_called_once_with(
            "POST",
            "/repos/owner/repo/issues/10/dependencies/blocked_by",
            input={"issue_id": 999},
        )

    def test_returns_false_on_422(self) -> None:
        from unittest.mock import Mock

        from gitlab_to_github_migrator.github_utils import create_issue_dependency

        mock_client = Mock()
        mock_client.requester.requestJson.side_effect = GithubException(422, {"message": "Already exists"}, headers={})

        result = create_issue_dependency(mock_client, "owner", "repo", blocked_issue_number=10, blocking_issue_id=999)

        assert result is False


@pytest.mark.unit
class TestDeleteIssue:
    def test_deletes_issue_successfully(self) -> None:
        from unittest.mock import Mock

        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_client = Mock()
        mock_client._Github__requester.graphql_named_mutation.return_value = (
            {},
            {"deleteIssue": {"clientMutationId": None}},
        )

        # Should not raise any exception
        delete_issue(mock_client, "gid_123")

        mock_client._Github__requester.graphql_named_mutation.assert_called_once_with(
            mutation_name="deleteIssue",
            mutation_input={"issueId": "gid_123"},
            output_schema="clientMutationId",
        )

    def test_raises_exception_on_graphql_exception(self) -> None:
        from unittest.mock import Mock

        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_client = Mock()
        mock_client._Github__requester.graphql_named_mutation.side_effect = GithubException(
            404, {"message": "Not found"}, headers={}
        )

        # Should propagate the exception
        with pytest.raises(GithubException):
            delete_issue(mock_client, "gid_123")

    def test_raises_error_on_unexpected_response(self) -> None:
        from unittest.mock import Mock

        from gitlab_to_github_migrator.exceptions import MigrationError
        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_client = Mock()
        mock_client._Github__requester.graphql_named_mutation.return_value = ({}, {"unexpected": "response"})

        # Should raise MigrationError for unexpected response
        with pytest.raises(MigrationError):
            delete_issue(mock_client, "gid_123")


@pytest.mark.unit
class TestGetWorkItemChildren:
    def test_returns_empty_list_when_no_children(self) -> None:
        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {
                "workItem": {
                    "iid": "42",
                    "widgets": [{"type": "HIERARCHY", "children": {"nodes": []}}],
                }
            }
        }

        result = get_work_item_children(mock_graphql, "org/project", 42)
        assert result == []

    def test_returns_children_when_present(self) -> None:
        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {
                "workItem": {
                    "iid": "42",
                    "widgets": [
                        {
                            "type": "HIERARCHY",
                            "children": {
                                "nodes": [
                                    {
                                        "iid": "100",
                                        "title": "Child task",
                                        "state": "opened",
                                        "workItemType": {"name": "Task"},
                                        "webUrl": "https://gitlab.com/org/proj/-/issues/100",
                                    }
                                ]
                            },
                        }
                    ],
                }
            }
        }

        result = get_work_item_children(mock_graphql, "org/project", 42)
        assert len(result) == 1
        assert result[0] == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
