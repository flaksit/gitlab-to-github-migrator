"""
Tests for GitLab to GitHub Migration Tool
"""

from unittest.mock import Mock, PropertyMock, patch

import pytest
from github import GithubException
from gitlab.exceptions import GitlabError

from gitlab_to_github_migrator import GitlabToGithubMigrator, LabelTranslator, MigrationError


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
class TestTimestampFormatting:
    """Test timestamp formatting functionality."""

    def test_format_timestamp_with_z_suffix(self) -> None:
        """Test formatting timestamp with Z suffix."""
        result = GitlabToGithubMigrator._format_timestamp("2024-01-15T10:30:45.123Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_timestamp_with_timezone(self) -> None:
        """Test formatting timestamp with explicit timezone."""
        result = GitlabToGithubMigrator._format_timestamp("2024-01-15T10:30:45.123456+00:00")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_timestamp_without_microseconds(self) -> None:
        """Test formatting timestamp without microseconds."""
        result = GitlabToGithubMigrator._format_timestamp("2024-01-15T10:30:45Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_timestamp_with_different_timezone(self) -> None:
        """Test formatting timestamp with non-UTC timezone."""
        result = GitlabToGithubMigrator._format_timestamp("2024-01-15T10:30:45+05:30")
        assert result == "2024-01-15 10:30:45+05:30"

    def test_format_timestamp_with_empty_string(self) -> None:
        """Test handling for empty string - returns as-is."""
        result = GitlabToGithubMigrator._format_timestamp("")
        assert result == ""

    def test_format_timestamp_with_invalid_format(self) -> None:
        """Test handling for invalid timestamp format - returns original."""
        result = GitlabToGithubMigrator._format_timestamp("invalid-timestamp")
        assert result == "invalid-timestamp"


@pytest.mark.unit
class TestGitlabToGithubMigrator:
    """Test main migration functionality."""

    def setup_method(self) -> None:
        """Setup test fixtures."""
        self.gitlab_project_path = "test-org/test-project"
        self.github_repo_path = "github-org/test-repo"

        # Mock GitLab project
        self.mock_gitlab_project = Mock()
        self.mock_gitlab_project.id = 12345
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

        migrator = GitlabToGithubMigrator(
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

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")

        content = "Here is an attachment: /uploads/abcdef0123456789abcdef0123456789/file.pdf"
        files, updated_content = migrator.download_gitlab_attachments(content)

        assert len(files) == 1
        assert files[0].filename == "file.pdf"
        assert files[0].content == b"file content"
        assert files[0].short_gitlab_url == "/uploads/abcdef0123456789abcdef0123456789/file.pdf"
        # Content unchanged since no cached URLs
        assert updated_content == content

        # Verify API path is used instead of web URL
        mock_gitlab_client.http_get.assert_called_once_with(
            "/projects/12345/uploads/abcdef0123456789abcdef0123456789/file.pdf",
            raw=True,
            timeout=30,
        )

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_download_gitlab_attachments_cached(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that cached attachments skip download and replace URLs."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client
        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")

        # Pre-populate the cache with an already-uploaded attachment
        cached_url = "/uploads/abcdef0123456789abcdef0123456789/cached.pdf"
        github_url = "https://github.com/releases/download/cached.pdf"
        migrator._uploaded_attachments[cached_url] = github_url

        content = f"Here is a cached attachment: {cached_url}"
        files, updated_content = migrator.download_gitlab_attachments(content)

        # No files should be downloaded (already cached)
        assert len(files) == 0
        # URL should be replaced with GitHub URL
        assert cached_url not in updated_content
        assert github_url in updated_content
        # http_get should NOT be called (skipped download)
        mock_gitlab_client.http_get.assert_not_called()

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

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator._github_repo = self.mock_github_repo

        # Mock the release (found by listing all releases)
        mock_release = Mock()
        mock_release.name = "GitLab issue attachments"
        mock_asset = Mock()
        mock_asset.browser_download_url = "https://github.com/org/repo/releases/download/attachments/test.png"
        mock_release.upload_asset.return_value = mock_asset
        self.mock_github_repo.get_releases.return_value = [mock_release]

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

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator._github_repo = self.mock_github_repo

        content = "No attachments here"
        updated_content = migrator.upload_github_attachments([], content)

        assert updated_content == content

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_attachments_release_existing(self, mock_github_class, mock_gitlab_class) -> None:
        """Test getting existing attachments release."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator._github_repo = self.mock_github_repo

        # Mock existing release found by listing all releases
        mock_release = Mock()
        mock_release.name = "GitLab issue attachments"
        self.mock_github_repo.get_releases.return_value = [mock_release]

        release = migrator.attachments_release

        assert release == mock_release
        self.mock_github_repo.get_releases.assert_called_once()
        self.mock_github_repo.create_git_release.assert_not_called()

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_attachments_release_create_new(self, mock_github_class, mock_gitlab_class) -> None:
        """Test creating new attachments release when it doesn't exist."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator._github_repo = self.mock_github_repo

        # Mock no releases found (empty list)
        self.mock_github_repo.get_releases.return_value = []

        # Mock create release
        mock_release = Mock()
        mock_release.name = "GitLab issue attachments"
        self.mock_github_repo.create_git_release.return_value = mock_release

        release = migrator.attachments_release

        assert release == mock_release
        self.mock_github_repo.create_git_release.assert_called_once()
        call_args = self.mock_github_repo.create_git_release.call_args
        assert call_args.kwargs["tag"] == "gitlab-issue-attachments"
        assert call_args.kwargs["draft"] is True

    @patch("gitlab_to_github_migrator.gitlab_utils.Gitlab")
    @patch("gitlab_to_github_migrator.github_utils.Github")
    def test_attachments_release_cached(self, mock_github_class, mock_gitlab_class) -> None:
        """Test that attachments release is cached after first access."""
        mock_gitlab_client = Mock()
        mock_github_client = Mock()
        mock_gitlab_class.return_value = mock_gitlab_client
        mock_github_class.return_value = mock_github_client

        mock_gitlab_client.projects.get.return_value = self.mock_gitlab_project

        migrator = GitlabToGithubMigrator(self.gitlab_project_path, self.github_repo_path, github_token="test_token")
        migrator._github_repo = self.mock_github_repo

        # Mock existing release found by listing all releases
        mock_release = Mock()
        mock_release.name = "GitLab issue attachments"
        self.mock_github_repo.get_releases.return_value = [mock_release]

        # Access the property twice
        release1 = migrator.attachments_release
        release2 = migrator.attachments_release

        # Should be the same object
        assert release1 == release2
        assert release1 is release2
        # API should only be called once (cached after first call)
        self.mock_github_repo.get_releases.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
