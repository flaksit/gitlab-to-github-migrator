"""
Tests for CLI module.
"""

from unittest.mock import Mock, call, patch

import pytest

from gitlab_to_github_migrator.cli import _print_validation_report


@pytest.mark.unit
class TestPrintValidationReport:
    """Test validation report printing functionality."""

    def test_successful_validation_report(self) -> None:
        """Test printing a successful validation report."""
        report = {
            "gitlab_project": "test-org/test-project",
            "github_repo": "github-org/test-repo",
            "success": True,
            "errors": [],
            "statistics": {
                "gitlab_issues_total": 10,
                "gitlab_issues_open": 3,
                "gitlab_issues_closed": 7,
                "github_issues_total": 10,
                "github_issues_open": 3,
                "github_issues_closed": 7,
                "gitlab_milestones_total": 5,
                "gitlab_milestones_open": 2,
                "gitlab_milestones_closed": 3,
                "github_milestones_total": 5,
                "github_milestones_open": 2,
                "github_milestones_closed": 3,
                "gitlab_labels_total": 15,
                "github_labels_existing": 5,
                "github_labels_created": 10,
                "labels_translated": 8,
            },
        }

        with patch("gitlab_to_github_migrator.cli.logger") as mock_logger:
            _print_validation_report(report)

            # Verify that logger.info and logger.error were called
            assert mock_logger.info.called
            
            # Check key outputs
            calls = [str(c) for c in mock_logger.info.call_args_list]
            output = " ".join(calls)
            
            assert "test-org/test-project" in output
            assert "github-org/test-repo" in output
            assert "PASSED" in output
            assert "gitlab_issues_total=10" in output or "Total=10" in output

    def test_failed_validation_report(self) -> None:
        """Test printing a failed validation report with errors."""
        report = {
            "gitlab_project": "test-org/test-project",
            "github_repo": "github-org/test-repo",
            "success": False,
            "errors": [
                "Issue count mismatch: GitLab 10, GitHub 9",
                "Milestone count mismatch: GitLab 5, GitHub 4",
            ],
            "statistics": {
                "gitlab_issues_total": 10,
                "gitlab_issues_open": 3,
                "gitlab_issues_closed": 7,
                "github_issues_total": 9,
                "github_issues_open": 3,
                "github_issues_closed": 6,
                "gitlab_milestones_total": 5,
                "gitlab_milestones_open": 2,
                "gitlab_milestones_closed": 3,
                "github_milestones_total": 4,
                "github_milestones_open": 2,
                "github_milestones_closed": 2,
                "gitlab_labels_total": 15,
                "github_labels_existing": 5,
                "github_labels_created": 10,
                "labels_translated": 8,
            },
        }

        with patch("gitlab_to_github_migrator.cli.logger") as mock_logger:
            _print_validation_report(report)

            # Verify that errors are printed
            assert mock_logger.error.called
            
            # Check that error messages are included
            error_calls = [str(c) for c in mock_logger.error.call_args_list]
            error_output = " ".join(error_calls)
            
            assert "FAILED" in error_output
            assert "Issue count mismatch" in error_output
            assert "Milestone count mismatch" in error_output

    def test_empty_statistics(self) -> None:
        """Test printing report with empty statistics."""
        report = {
            "gitlab_project": "test-org/test-project",
            "github_repo": "github-org/test-repo",
            "success": True,
            "errors": [],
            "statistics": {},
        }

        with patch("gitlab_to_github_migrator.cli.logger") as mock_logger:
            # Should not raise an error
            _print_validation_report(report)
            assert mock_logger.info.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
