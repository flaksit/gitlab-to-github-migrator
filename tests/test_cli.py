"""
Tests for CLI module.
"""

import logging

import pytest

from gitlab_to_github_migrator.cli import _print_validation_report
from gitlab_to_github_migrator.utils import setup_logging


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


@pytest.mark.unit
class TestSetupLogging:
    """Test setup_logging verbose behavior."""

    def test_verbose_enables_debug_on_console(self) -> None:
        """With verbose=True, the console handler level should be DEBUG."""
        root_logger = logging.getLogger()
        # Remove existing handlers to isolate the test
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()

        try:
            setup_logging(verbose=True)
            console_handlers = [
                h
                for h in root_logger.handlers
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            ]
            assert console_handlers, "Expected at least one console StreamHandler"
            assert console_handlers[0].level == logging.DEBUG
        finally:
            # Restore original handlers
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers

    def test_non_verbose_shows_only_warnings_on_console(self) -> None:
        """With verbose=False, the console handler level should be WARNING."""
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()

        try:
            setup_logging(verbose=False)
            console_handlers = [
                h
                for h in root_logger.handlers
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
            ]
            assert console_handlers, "Expected at least one console StreamHandler"
            assert console_handlers[0].level == logging.WARNING
        finally:
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
