"""
Tests for GitLab to GitHub Migration Tool
"""

import tempfile
from unittest.mock import Mock, PropertyMock, patch

import pytest
from github import GithubException

from gitlab_to_github_migrator import GitLabToGitHubMigrator, LabelTranslator, MigrationError


@pytest.mark.unit
class TestLabelTranslator:
    """Test label translation functionality."""

    def test_simple_translation(self) -> None:
        translator = LabelTranslator(["p_high:priority: high", "bug:defect"])
        assert translator.translate("p_high") == "priority: high"
        assert translator.translate("bug") == "defect"
        assert translator.translate("unknown") == "unknown"

    def test_wildcard_translation(self) -> None:
        translator = LabelTranslator(["p_*:priority: *", "status_*:status: *"])
        assert translator.translate("p_high") == "priority: high"
        assert translator.translate("p_low") == "priority: low"
        assert translator.translate("status_open") == "status: open"
        assert translator.translate("unmatched") == "unmatched"

    def test_invalid_pattern(self) -> None:
        with pytest.raises(ValueError, match="Invalid pattern format"):
            LabelTranslator(["invalid_pattern"])

    def test_multiple_patterns(self) -> None:
        translator = LabelTranslator(["p_*:priority: *", "comp_*:component: *", "bug:defect"])
        assert translator.translate("p_critical") == "priority: critical"
        assert translator.translate("comp_ui") == "component: ui"
        assert translator.translate("bug") == "defect"


@pytest.mark.unit
class TestGitLabToGitHubMigrator:
    """Test main migration functionality."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.gitlab_project_path = "test-org/test-project"
        self.github_repo_path = "github-org/test-repo"

        # Mock GitLab project
        self.mock_gitlab_project = Mock()
        self.mock_gitlab_project.name = "test-project"
        self.mock_gitlab_project.description = "Test project description"
        self.mock_gitlab_project.web_url = "https://gitlab.com/test-org/test-project"
        self.mock_gitlab_project.ssh_url_to_repo = "git@gitlab.com:test-org/test-project.git"

        # Mock GitHub repo
        self.mock_github_repo = Mock()
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

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path,
            self.github_repo_path,
            label_translations=["p_*:priority: *"],
            github_token="test_token",
        )

        assert migrator.gitlab_project_path == self.gitlab_project_path
        assert migrator.github_repo_path == self.github_repo_path
        assert len(migrator.label_translator.patterns) == 1

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

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )

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

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )

        # Now make the name property fail by replacing the project
        failing_project = Mock()
        type(failing_project).name = PropertyMock(side_effect=Exception("GitLab API error"))
        migrator.gitlab_project = failing_project

        with pytest.raises(MigrationError, match="GitLab API access failed"):
            migrator.validate_api_access()

    @pytest.mark.skip(reason="create_github_repository method moved to github_utils.create_repo")
    def test_create_github_repository_success(self) -> None:
        """Test successful GitHub repository creation."""

    @pytest.mark.skip(reason="create_github_repository method moved to github_utils.create_repo")
    def test_create_repository_for_user(self) -> None:
        """Test creating a repository for a user (not organization)."""

    @pytest.mark.skip(reason="create_github_repository method moved to github_utils.create_repo")
    def test_create_repository_user_mismatch_error(self) -> None:
        """Test error when github_owner doesn't match authenticated user."""

    @pytest.mark.skip(reason="create_github_repository method moved to github_utils.create_repo")
    def test_create_repository_organization_error(self) -> None:
        """Test error handling when getting organization fails with non-404 error."""

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

        migrator = GitLabToGitHubMigrator(
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

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
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
    def test_download_gitlab_attachments(self, mock_github_class, mock_gitlab_class) -> None:
        """Test GitLab attachment download."""
        # Mock successful response
        mock_response = Mock()
        mock_response.content = b"file content"
        mock_response.raise_for_status.return_value = None

        # Setup GitLab/GitHub mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client
        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project
        
        # Mock the http_get method
        mock_gitlab_client.http_get.return_value = mock_response

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )

        content = "Here is an attachment: /uploads/abcdef0123456789abcdef0123456789/file.pdf"
        files = migrator.download_gitlab_attachments(content)

        assert len(files) == 1
        assert files[0].filename == "file.pdf"
        assert files[0].content == b"file content"
        assert files[0].short_gitlab_url == "/uploads/abcdef0123456789abcdef0123456789/file.pdf"

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_validation_report_success(self, mock_github_class, mock_gitlab_class) -> None:
        """Test successful validation report generation."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client
        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
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

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
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

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_upload_github_attachments_success(self, mock_github_class, mock_gitlab_class) -> None:
        """Test successful attachment upload to GitHub release."""
        from gitlab_to_github_migrator.migrator import DownloadedFile
        
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
        migrator._github_repo = self.mock_github_repo

        # Mock the release
        mock_release = Mock()
        mock_asset = Mock()
        mock_asset.browser_download_url = "https://github.com/org/repo/releases/download/attachments/test.png"
        mock_release.upload_asset.return_value = mock_asset
        self.mock_github_repo.get_release.return_value = mock_release

        # Create test file
        test_file = DownloadedFile(
            filename="test.png",
            content=b"fake image data",
            short_gitlab_url="/uploads/abc123/test.png",
            full_gitlab_url="https://gitlab.com/test/project/uploads/abc123/test.png",
        )

        # Test content update
        content = "Here is an image: ![test](/uploads/abc123/test.png)"
        updated_content = migrator.upload_github_attachments([test_file], content)

        # Verify the URL was replaced
        assert "/uploads/abc123/test.png" not in updated_content
        assert "https://github.com/org/repo/releases/download/attachments/test.png" in updated_content
        assert mock_release.upload_asset.called

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_upload_github_attachments_empty_list(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that empty file list returns original content unchanged."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
        migrator._github_repo = self.mock_github_repo

        content = "No attachments here"
        updated_content = migrator.upload_github_attachments([], content)

        assert updated_content == content

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_get_or_create_attachments_release_existing(self, mock_github_class, mock_gitlab_class) -> None:
        """Test getting existing attachments release."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
        migrator._github_repo = self.mock_github_repo

        # Mock existing release
        mock_release = Mock()
        mock_release.tag_name = "attachments"
        self.mock_github_repo.get_release.return_value = mock_release

        release = migrator._get_or_create_attachments_release()

        assert release == mock_release
        self.mock_github_repo.get_release.assert_called_once_with("attachments")
        self.mock_github_repo.create_git_release.assert_not_called()

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_get_or_create_attachments_release_create_new(self, mock_github_class, mock_gitlab_class) -> None:
        """Test creating new attachments release when it doesn't exist."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitLabToGitHubMigrator(
            self.gitlab_project_path, self.github_repo_path, github_token="test_token"
        )
        migrator._github_repo = self.mock_github_repo

        # Mock 404 error when getting release
        mock_error = GithubException(404, "Not Found", headers={})
        self.mock_github_repo.get_release.side_effect = mock_error

        # Mock create release
        mock_release = Mock()
        mock_release.tag_name = "attachments"
        self.mock_github_repo.create_git_release.return_value = mock_release

        release = migrator._get_or_create_attachments_release()

        assert release == mock_release
        self.mock_github_repo.create_git_release.assert_called_once()
        call_args = self.mock_github_repo.create_git_release.call_args
        assert call_args.kwargs["tag"] == "attachments"
        assert call_args.kwargs["draft"] is True


@pytest.mark.unit
class TestIntegration:
    """Integration tests for the full migration process."""

    @pytest.mark.skip(reason="Needs rework - complex mocking of github_utils.create_repo")
    @patch("gitlab_to_github_migrator.migrator.subprocess.run")
    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_full_migration_dry_run(self, mock_github_class, mock_gitlab_class, mock_subprocess) -> None:
        """Test a simplified full migration flow."""
        # Setup mocks
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        # Mock GitLab project
        mock_gitlab_project = Mock()
        mock_gitlab_project.name = "test-project"
        mock_gitlab_project.description = "Test description"
        mock_gitlab_project.web_url = "https://gitlab.com/test/project"
        mock_gitlab_project.ssh_url_to_repo = "git@gitlab.com:test/project.git"
        mock_gitlab_project.labels.list.return_value = []
        mock_gitlab_project.milestones.list.return_value = []
        mock_gitlab_project.issues.list.return_value = []
        mock_gitlab_client.projects.get.return_value = mock_gitlab_project

        # Mock GitHub
        mock_org = Mock()
        mock_github_repo = Mock()
        mock_github_repo.html_url = "https://github.com/org/repo"
        mock_github_repo.ssh_url = "git@github.com:org/repo.git"
        mock_github_repo.get_labels.return_value = []
        mock_github_repo.get_issues.return_value = []
        mock_github_repo.get_milestones.return_value = []

        mock_github_client.get_organization.return_value = mock_org
        mock_github_client.get_repo.side_effect = GithubException(404, {}, headers={})
        mock_github_client.get_user.return_value = Mock()
        mock_org.create_repo.return_value = mock_github_repo
        mock_org.get_labels.side_effect = GithubException(404, {}, headers={})

        # Mock subprocess for git operations
        mock_subprocess.return_value = Mock(returncode=0)

        # Create temporary directory for clone simulation
        with tempfile.TemporaryDirectory() as temp_dir:
            migrator = GitLabToGitHubMigrator(
                "test/project", "org/repo", local_clone_path=temp_dir, github_token="test_token"
            )

            # This should complete without errors for empty project
            report = migrator.migrate()

            assert report["success"] is True
            assert report["statistics"]["gitlab_issues_total"] == 0
            assert report["statistics"]["github_issues_total"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
