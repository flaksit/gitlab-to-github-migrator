"""Tests for utility scripts (create_gitlab_test_project and delete_test_repos)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, Mock, patch

import pytest

from gitlab_to_github_migrator import create_gitlab_test_project, delete_test_repos


class TestCreateGitlabTestProject:
    """Tests for create_gitlab_test_project module."""

    def test_get_gitlab_token_success(self):
        """Test getting GitLab token from environment/pass."""
        with patch("gitlab_to_github_migrator.create_gitlab_test_project.glu.get_readwrite_token") as mock_get:
            mock_get.return_value = "test-token"
            token = create_gitlab_test_project.get_gitlab_token()
            assert token == "test-token"  # noqa: S105

    def test_get_gitlab_token_failure(self):
        """Test error when GitLab token is not available."""
        with patch("gitlab_to_github_migrator.create_gitlab_test_project.glu.get_readwrite_token") as mock_get:
            mock_get.return_value = None
            with pytest.raises(ValueError, match="GitLab token required"):
                create_gitlab_test_project.get_gitlab_token()

    def test_create_test_project_uses_logging(self, caplog):
        """Test that create_test_project uses logging instead of print."""
        with (
            patch("gitlab_to_github_migrator.create_gitlab_test_project.get_gitlab_token") as mock_token,
            patch("gitlab_to_github_migrator.create_gitlab_test_project.Gitlab"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.GraphQL"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.get_or_create_project") as mock_create,
            patch("gitlab_to_github_migrator.create_gitlab_test_project.create_labels"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.create_milestones") as mock_milestones,
            patch("gitlab_to_github_migrator.create_gitlab_test_project.create_issues"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.setup_issue_relationships"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.add_comments_and_close_issue"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.create_git_content"),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.print_manual_instructions"),
            caplog.at_level(logging.INFO),
        ):
            mock_token.return_value = "test-token"
            mock_project = MagicMock()
            mock_create.return_value = mock_project
            mock_milestones.return_value = (1, 3)

            create_gitlab_test_project.create_test_project("test/project")

            # Verify logging was used
            assert any("Creating GitLab test project" in record.message for record in caplog.records)
            assert any("test/project" in record.message for record in caplog.records)

    def test_main_function_parses_args_and_calls_create(self):
        """Test that main() only handles arg parsing and delegates to create_test_project."""
        test_args = ["test/project", "--verbose"]
        with (
            patch("sys.argv", ["create-gitlab-test-project", *test_args]),
            patch("gitlab_to_github_migrator.create_gitlab_test_project.setup_logging") as mock_setup,
            patch("gitlab_to_github_migrator.create_gitlab_test_project.create_test_project") as mock_create,
        ):
            create_gitlab_test_project.main()

            # Verify setup_logging was called with verbose=True
            mock_setup.assert_called_once_with(verbose=True)
            # Verify create_test_project was called with the project path
            mock_create.assert_called_once_with("test/project")


class TestDeleteTestRepos:
    """Tests for delete_test_repos module."""

    def test_delete_test_repositories_uses_logging(self, caplog):
        """Test that delete_test_repositories uses logging instead of print."""
        mock_repo1 = Mock()
        mock_repo1.name = "gl2ghmigr-test-abc123"
        mock_repo1.created_at = "2024-01-01"

        with (
            patch("gitlab_to_github_migrator.delete_test_repos.get_pass_value") as mock_pass,
            patch("gitlab_to_github_migrator.delete_test_repos.Github"),
            patch("gitlab_to_github_migrator.delete_test_repos.get_owner_repos") as mock_get_repos,
            caplog.at_level(logging.INFO),
        ):
            mock_pass.return_value = "test-token"
            mock_get_repos.return_value = ("organization", [mock_repo1])

            delete_test_repos.delete_test_repositories("test-org", "github/api/token")

            # Verify logging was used
            assert any("Scanning repositories" in record.message for record in caplog.records)
            assert any("test-org" in record.message for record in caplog.records)
            assert any("Found 1 test repositories" in record.message for record in caplog.records)

    def test_delete_test_repositories_no_repos_found(self, caplog):
        """Test behavior when no test repos are found."""
        with (
            patch("gitlab_to_github_migrator.delete_test_repos.get_pass_value") as mock_pass,
            patch("gitlab_to_github_migrator.delete_test_repos.Github"),
            patch("gitlab_to_github_migrator.delete_test_repos.get_owner_repos") as mock_get_repos,
            caplog.at_level(logging.INFO),
        ):
            mock_pass.return_value = "test-token"
            # Return no repos
            mock_get_repos.return_value = ("organization", [])

            delete_test_repos.delete_test_repositories("test-org", "github/api/token")

            # Verify appropriate logging
            assert any("No test repositories found" in record.message for record in caplog.records)

    def test_main_function_calls_setup_logging(self):
        """Test that main() calls setup_logging."""
        test_args = ["test-org", "github/api/token", "--verbose"]
        with (
            patch("sys.argv", ["delete-test-repos", *test_args]),
            patch("gitlab_to_github_migrator.delete_test_repos.setup_logging") as mock_setup,
            patch("gitlab_to_github_migrator.delete_test_repos.delete_test_repositories") as mock_delete,
        ):
            delete_test_repos.main()

            # Verify setup_logging was called with verbose=True
            mock_setup.assert_called_once_with(verbose=True)
            # Verify delete_test_repositories was called
            mock_delete.assert_called_once_with("test-org", "github/api/token")

    def test_main_function_uses_env_var_for_owner(self):
        """Test that main() uses GITHUB_TEST_ORG env var when owner not provided."""
        test_args = ["github/api/token"]
        with (
            patch("sys.argv", ["delete-test-repos", *test_args]),
            patch.dict("os.environ", {"GITHUB_TEST_ORG": "env-org"}),
            patch("gitlab_to_github_migrator.delete_test_repos.setup_logging"),
            patch("gitlab_to_github_migrator.delete_test_repos.delete_test_repositories") as mock_delete,
        ):
            delete_test_repos.main()

            # Verify delete_test_repositories was called with org from env
            mock_delete.assert_called_once_with("env-org", "github/api/token")
