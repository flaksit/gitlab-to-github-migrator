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

    def _create_mock_milestone(self, iid: int, state: str = "active", due_date: str | None = None) -> Mock:
        """Create a mock GitLab milestone with standard attributes."""
        from datetime import UTC, datetime, timedelta

        base = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
        created = base + timedelta(hours=iid)
        updated = created + timedelta(minutes=30)

        mock = Mock()
        mock.iid = iid
        mock.id = 100 + iid
        mock.title = f"Milestone {iid}"
        mock.state = state
        mock.description = f"Milestone {iid} description"
        mock.due_date = due_date
        mock.created_at = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        mock.updated_at = updated.strftime("%Y-%m-%dT%H:%M:%SZ")
        return mock

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
        mock_milestone1 = self._create_mock_milestone(1)
        mock_milestone3 = self._create_mock_milestone(3, state="closed", due_date="2024-03-01")
        mock_milestone5 = self._create_mock_milestone(5)
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
        from unittest.mock import Mock, patch

        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"deleteIssue": {"clientMutationId": None}}}

        with patch("gitlab_to_github_migrator.github_utils.requests.post", return_value=mock_response) as mock_post:
            # Should not raise any exception
            delete_issue("fake_token", "gid_123")

            # Verify the request was made correctly
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "https://api.github.com/graphql"
            assert call_args[1]["headers"]["Authorization"] == "Bearer fake_token"

    def test_raises_exception_on_http_error(self) -> None:
        from unittest.mock import Mock, patch

        from gitlab_to_github_migrator.exceptions import MigrationError
        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Not found"

        with (
            patch("gitlab_to_github_migrator.github_utils.requests.post", return_value=mock_response),
            pytest.raises(MigrationError),
        ):
            delete_issue("fake_token", "gid_123")

    def test_raises_exception_on_graphql_error(self) -> None:
        from unittest.mock import Mock, patch

        from gitlab_to_github_migrator.exceptions import MigrationError
        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"errors": [{"message": "Issue not found"}]}

        with (
            patch("gitlab_to_github_migrator.github_utils.requests.post", return_value=mock_response),
            pytest.raises(MigrationError),
        ):
            delete_issue("fake_token", "gid_123")

    def test_raises_error_on_unexpected_response(self) -> None:
        from unittest.mock import Mock, patch

        from gitlab_to_github_migrator.exceptions import MigrationError
        from gitlab_to_github_migrator.github_utils import delete_issue

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"unexpected": "response"}}

        with (
            patch("gitlab_to_github_migrator.github_utils.requests.post", return_value=mock_response),
            pytest.raises(MigrationError),
        ):
            delete_issue("fake_token", "gid_123")


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


@pytest.mark.unit
class TestCommentMigration:
    """Test comment migration functionality."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.gitlab_project_path: str = "test-org/test-project"
        self.github_repo_path: str = "github-org/test-repo"

    def _create_mock_note(
        self, created_at: str, body: str | None, *, system: bool = False, author: dict[str, str] | None = None
    ) -> Mock:
        """Create a mock GitLab note."""
        note = Mock()
        note.created_at = created_at
        note.updated_at = created_at  # Default to same as created_at
        note.body = body
        note.system = system
        note.id = 1
        if author is None:
            author = {"name": "Test User", "username": "testuser"}
        note.author = author
        return note

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_single_system_note_compact_format(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that a single system note uses compact format."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        # Mock issue
        mock_gitlab_issue = Mock()
        mock_github_issue = Mock()

        # Single system note
        system_note = self._create_mock_note("2026-01-27T20:18:55Z", "marked this issue as related to #1", system=True)
        mock_gitlab_issue.notes.list.return_value = [system_note]

        # Execute
        migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify - single system note should use compact format
        mock_github_issue.create_comment.assert_called_once()
        comment_body = mock_github_issue.create_comment.call_args[0][0]
        assert comment_body.startswith("**System note**")
        assert "2026-01-27 20:18:55Z by testuser" in comment_body
        assert "marked this issue as related to #1" in comment_body

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_consecutive_system_notes_grouped_format(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that consecutive system notes are grouped with markdown header."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        # Mock issue
        mock_gitlab_issue = Mock()
        mock_github_issue = Mock()
        mock_gitlab_issue.iid = 1

        # Three consecutive system notes
        system_notes = [
            self._create_mock_note("2026-01-27T20:18:55Z", "marked this issue as related to #1", system=True),
            self._create_mock_note(
                "2026-01-27T20:19:10Z", "This is a long system note\n- that spans\n- multiple lines", system=True
            ),
            self._create_mock_note("2026-01-27T20:19:22Z", "marked this issue as closed", system=True),
        ]
        mock_gitlab_issue.notes.list.return_value = system_notes

        # Execute
        migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify - single comment with grouped format
        mock_github_issue.create_comment.assert_called_once()
        comment_body = mock_github_issue.create_comment.call_args[0][0]

        # Should have markdown header
        assert comment_body.startswith("### System notes\n")

        # Should have all three notes with timestamps and authors
        assert "2026-01-27 20:18:55Z by testuser: marked this issue as related to #1" in comment_body
        assert (
            "2026-01-27 20:19:10Z by testuser: This is a long system note\n- that spans\n- multiple lines"
            in comment_body
        )
        assert "2026-01-27 20:19:22Z by testuser: marked this issue as closed" in comment_body

        # Should have empty lines between notes (double newlines)
        assert "\n\n" in comment_body

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_non_consecutive_system_notes_separate_comments(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that non-consecutive system notes create separate comments."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        # Mock attachment handler
        mock_attachment_handler = Mock()
        mock_attachment_handler.process_content.return_value = "This is a user comment"
        migrator._attachment_handler = mock_attachment_handler

        # Mock issue
        mock_gitlab_issue = Mock()
        mock_github_issue = Mock()
        mock_gitlab_issue.iid = 1

        # System note, user comment, system note
        notes = [
            self._create_mock_note("2026-01-27T20:18:55Z", "marked this issue as related to #1", system=True),
            self._create_mock_note("2026-01-27T20:19:00Z", "This is a user comment", system=False),
            self._create_mock_note("2026-01-27T20:19:22Z", "marked this issue as closed", system=True),
        ]
        mock_gitlab_issue.notes.list.return_value = notes

        # Execute
        migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify - should create 3 separate comments
        assert mock_github_issue.create_comment.call_count == 3

        # First comment: single system note (compact format)
        first_comment = mock_github_issue.create_comment.call_args_list[0][0][0]
        assert first_comment.startswith("**System note**")
        assert "marked this issue as related to #1" in first_comment

        # Second comment: user comment
        second_comment = mock_github_issue.create_comment.call_args_list[1][0][0]
        assert "**Comment by**" in second_comment
        assert "This is a user comment" in second_comment

        # Third comment: single system note (compact format)
        third_comment = mock_github_issue.create_comment.call_args_list[2][0][0]
        assert third_comment.startswith("**System note**")
        assert "marked this issue as closed" in third_comment

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_mixed_consecutive_and_non_consecutive_system_notes(self, mock_github_class, mock_gitlab_class) -> None:
        """Test mixed scenario: consecutive system notes, user comment, more consecutive system notes."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        # Mock attachment handler
        mock_attachment_handler = Mock()
        mock_attachment_handler.process_content.return_value = "Great work!"
        migrator._attachment_handler = mock_attachment_handler

        # Mock issue
        mock_gitlab_issue = Mock()
        mock_github_issue = Mock()
        mock_gitlab_issue.iid = 1

        # Two system notes, user comment, two more system notes
        notes = [
            self._create_mock_note("2026-01-27T20:18:55Z", "marked this issue as related to #1", system=True),
            self._create_mock_note("2026-01-27T20:19:10Z", "added label priority:high", system=True),
            self._create_mock_note("2026-01-27T20:19:15Z", "Great work!", system=False),
            self._create_mock_note("2026-01-27T20:19:20Z", "removed label priority:high", system=True),
            self._create_mock_note("2026-01-27T20:19:22Z", "marked this issue as closed", system=True),
        ]
        mock_gitlab_issue.notes.list.return_value = notes

        # Execute
        migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify - should create 3 comments
        assert mock_github_issue.create_comment.call_count == 3

        # First comment: grouped system notes (2 consecutive)
        first_comment = mock_github_issue.create_comment.call_args_list[0][0][0]
        assert first_comment.startswith("### System notes\n")
        assert "marked this issue as related to #1" in first_comment
        assert "added label priority:high" in first_comment

        # Second comment: user comment
        second_comment = mock_github_issue.create_comment.call_args_list[1][0][0]
        assert "**Comment by**" in second_comment
        assert "Great work!" in second_comment

        # Third comment: grouped system notes (2 consecutive)
        third_comment = mock_github_issue.create_comment.call_args_list[2][0][0]
        assert third_comment.startswith("### System notes\n")
        assert "removed label priority:high" in third_comment
        assert "marked this issue as closed" in third_comment

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_empty_system_note_body(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that empty system note bodies are handled with '(empty note)' placeholder."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        # Mock issue
        mock_gitlab_issue = Mock()
        mock_github_issue = Mock()
        mock_gitlab_issue.iid = 1

        # System notes with empty bodies
        notes = [
            self._create_mock_note("2026-01-27T20:18:55Z", "", system=True),  # Empty string
            self._create_mock_note("2026-01-27T20:19:10Z", None, system=True),  # None
            self._create_mock_note("2026-01-27T20:19:22Z", "marked this issue as closed", system=True),
        ]
        mock_gitlab_issue.notes.list.return_value = notes

        # Execute
        migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify - single comment with grouped format
        mock_github_issue.create_comment.assert_called_once()
        comment_body = mock_github_issue.create_comment.call_args[0][0]

        # Should have markdown header
        assert comment_body.startswith("### System notes\n")

        # Empty notes should show "(empty note)" with author
        assert "2026-01-27 20:18:55Z by testuser: (empty note)" in comment_body
        assert "2026-01-27 20:19:10Z by testuser: (empty note)" in comment_body
        assert "2026-01-27 20:19:22Z by testuser: marked this issue as closed" in comment_body

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_attachment_counting_in_comments(self, mock_github_class: Mock, mock_gitlab_class: Mock) -> None:
        """Test that attachments in comments are counted correctly."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_project = Mock()
        mock_gitlab_project.id = 12345
        mock_gitlab_project.name = "test-project"
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Create migrator
        migrator = GitlabToGithubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            github_token="test_token",
        )

        mock_github_issue = Mock()

        # Mock GitLab issue with no description
        mock_gitlab_issue = Mock()
        mock_gitlab_issue.iid = 1
        mock_gitlab_issue.description = None

        # Mock attachment handler to return different content for each call
        mock_attachment_handler = Mock()
        # First call: 2 attachments, second call: 0 attachments
        mock_attachment_handler.process_content.side_effect = [
            "This has [file1](/releases/download/GitLab-issue-attachments/file1.png) and "
            "[file2](/releases/download/GitLab-issue-attachments/file2.pdf) attachments",
            "Plain comment without attachments",
        ]
        migrator._attachment_handler = mock_attachment_handler

        # Mock notes - first with attachments, second without
        note_with_attachments = self._create_mock_note(
            "2026-01-27T20:18:55Z", "Comment with attachments", system=False
        )
        note_without_attachments = self._create_mock_note("2026-01-27T20:19:55Z", "Plain comment", system=False)

        mock_gitlab_issue.notes.list.return_value = [note_with_attachments, note_without_attachments]

        # Execute
        user_comment_count, attachment_count = migrator.migrate_issue_comments(mock_gitlab_issue, mock_github_issue)

        # Verify
        assert user_comment_count == 2  # Two user comments
        assert attachment_count == 2  # Two attachments from first comment, zero from second
        assert mock_attachment_handler.process_content.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
