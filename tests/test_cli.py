"""
Tests for CLI module.
"""

import pytest

from gitlab_to_github_migrator.cli import _print_validation_report


@pytest.mark.unit
class TestPrintValidationReport:
    """Test validation report printing functionality."""

    def test_successful_validation_report(self, capsys: pytest.CaptureFixture[str]) -> None:
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

        _print_validation_report(report)
        captured = capsys.readouterr()

        # Check key outputs
        assert "test-org/test-project" in captured.out
        assert "github-org/test-repo" in captured.out
        assert "PASSED" in captured.out
        assert "Total=10" in captured.out

    def test_failed_validation_report(self, capsys: pytest.CaptureFixture[str]) -> None:
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

        _print_validation_report(report)
        captured = capsys.readouterr()

        # Check that error messages are included
        assert "FAILED" in captured.out
        assert "Issue count mismatch" in captured.out
        assert "Milestone count mismatch" in captured.out

    def test_empty_statistics(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test printing report with empty statistics."""
        report = {
            "gitlab_project": "test-org/test-project",
            "github_repo": "github-org/test-repo",
            "success": True,
            "errors": [],
            "statistics": {},
        }

        # Should not raise an error
        _print_validation_report(report)
        captured = capsys.readouterr()
        assert "test-org/test-project" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
