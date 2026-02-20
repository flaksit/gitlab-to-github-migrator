"""
Tests for GitHub utilities module.
"""

from unittest.mock import Mock, patch

import pytest
from github import GithubException

from gitlab_to_github_migrator import MigrationError
from gitlab_to_github_migrator.github_utils import create_repo, set_default_branch


@pytest.mark.unit
class TestSetDefaultBranch:
    """Test default branch setting functionality."""

    def test_set_default_branch_success(self) -> None:
        """Test that set_default_branch successfully sets the branch."""
        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"
        mock_repo.edit = Mock()

        set_default_branch(mock_repo, "develop")

        mock_repo.edit.assert_called_once_with(default_branch="develop")

    def test_set_default_branch_github_error(self) -> None:
        """Test that set_default_branch raises MigrationError on GitHub API error."""
        mock_repo = Mock()
        mock_repo.full_name = "owner/repo"
        mock_repo.edit = Mock(side_effect=GithubException(404, "Branch not found", None))

        with pytest.raises(MigrationError, match=r"Failed to set default branch to 'nonexistent'"):
            set_default_branch(mock_repo, "nonexistent")


@pytest.mark.unit
class TestCreateRepo:
    """Test repository creation functionality."""

    def test_create_repo_invalid_path_no_slash(self) -> None:
        """Test that create_repo raises MigrationError when repo path has no slash."""
        mock_client = Mock()

        with pytest.raises(
            MigrationError, match=r"Invalid GitHub repository path.*Expected format: 'owner/repository'"
        ):
            create_repo(mock_client, "just-owner", "Test description")

    def test_create_repo_invalid_path_empty_string(self) -> None:
        """Test that create_repo raises MigrationError when repo path is empty."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Invalid GitHub repository path"):
            create_repo(mock_client, "", "Test description")

    def test_create_repo_invalid_path_only_spaces(self) -> None:
        """Test that create_repo raises MigrationError when repo path is only spaces."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Invalid GitHub repository path"):
            create_repo(mock_client, "   ", "Test description")

    def test_create_repo_invalid_path_multiple_slashes(self) -> None:
        """Test that create_repo raises MigrationError when repo path has multiple slashes."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Invalid GitHub repository path"):
            create_repo(mock_client, "owner/repo/extra", "Test description")

    def test_create_repo_invalid_path_leading_slash(self) -> None:
        """Test that create_repo raises MigrationError when repo path has leading slash (empty owner)."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Both owner and repository name must be non-empty"):
            create_repo(mock_client, "/repo", "Test description")

    def test_create_repo_invalid_path_trailing_slash(self) -> None:
        """Test that create_repo raises MigrationError when repo path has trailing slash (empty repo)."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Both owner and repository name must be non-empty"):
            create_repo(mock_client, "owner/", "Test description")

    def test_create_repo_invalid_path_double_slash(self) -> None:
        """Test that create_repo raises MigrationError when repo path has double slash."""
        mock_client = Mock()

        with pytest.raises(MigrationError, match="Invalid GitHub repository path"):
            create_repo(mock_client, "owner//repo", "Test description")

    @patch("gitlab_to_github_migrator.github_utils.get_repo")
    def test_create_repo_valid_path_already_exists(self, mock_get_repo: Mock) -> None:
        """Test that create_repo raises MigrationError when repo already exists."""
        mock_client = Mock()
        mock_get_repo.return_value = Mock()  # Simulate existing repo

        with pytest.raises(MigrationError, match="already exists"):
            create_repo(mock_client, "owner/repo", "Test description")

    @patch("gitlab_to_github_migrator.github_utils.get_repo")
    def test_create_repo_valid_path_organization(self, mock_get_repo: Mock) -> None:
        """Test that create_repo successfully creates repo for organization."""
        mock_client = Mock()
        mock_get_repo.return_value = None  # No existing repo

        mock_org = Mock()
        mock_repo = Mock()
        mock_org.create_repo.return_value = mock_repo
        mock_client.get_organization.return_value = mock_org

        result = create_repo(mock_client, "myorg/myrepo", "Test description")

        assert result == mock_repo
        mock_org.create_repo.assert_called_once()
        args = mock_org.create_repo.call_args
        assert args.kwargs["name"] == "myrepo"
        assert args.kwargs["description"] == "Test description"

    @patch("gitlab_to_github_migrator.github_utils.get_repo")
    def test_create_repo_valid_path_with_surrounding_spaces(self, mock_get_repo: Mock) -> None:
        """Test that create_repo strips surrounding spaces from repo path."""
        mock_client = Mock()
        mock_get_repo.return_value = None  # No existing repo

        mock_org = Mock()
        mock_repo = Mock()
        mock_org.create_repo.return_value = mock_repo
        mock_client.get_organization.return_value = mock_org

        result = create_repo(mock_client, "  myorg/myrepo  ", "Test description")

        assert result == mock_repo
        mock_org.create_repo.assert_called_once()
        args = mock_org.create_repo.call_args
        assert args.kwargs["name"] == "myrepo"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
