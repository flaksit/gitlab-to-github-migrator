"""
Tests for CLI module.
"""

import logging
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest

from gitlab_to_github_migrator.cli import _print_validation_report, main
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
    """Test setup_logging verbosity levels."""

    def _get_console_handler(self, root_logger: logging.Logger) -> logging.StreamHandler[Any]:
        console_handlers = [
            h
            for h in root_logger.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert console_handlers, "Expected at least one console StreamHandler"
        return console_handlers[0]

    def test_default_shows_only_warnings_on_console(self) -> None:
        """With verbosity=0 (default), the console handler level should be WARNING."""
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()

        try:
            setup_logging(verbosity=0)
            assert self._get_console_handler(root_logger).level == logging.WARNING
        finally:
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers

    def test_verbose_shows_info_on_console(self) -> None:
        """With verbosity=1 (-v), the console handler level should be INFO."""
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()

        try:
            setup_logging(verbosity=1)
            assert self._get_console_handler(root_logger).level == logging.INFO
        finally:
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers

    def test_extra_verbose_shows_debug_on_console(self) -> None:
        """With verbosity=2 (-vv), the console handler level should be DEBUG."""
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers[:]
        root_logger.handlers.clear()

        try:
            setup_logging(verbosity=2)
            assert self._get_console_handler(root_logger).level == logging.DEBUG
        finally:
            for h in root_logger.handlers:
                h.close()
            root_logger.handlers = original_handlers


@pytest.mark.unit
class TestLabelTranslationForwarding:
    """Test that -l / --relabel patterns are forwarded to the migrator."""

    def test_label_translations_passed_to_migrator(self) -> None:
        """Label translation patterns supplied via -l are forwarded to the migrator."""
        mock_report = {
            "gitlab_project": "ns/proj",
            "github_repo": "owner/repo",
            "success": True,
            "errors": [],
            "statistics": {},
        }

        with (
            patch("sys.argv", ["prog", "-l", "bug:new-bug", "-l", "p*:p-*", "ns/proj", "owner/repo"]),
            patch("gitlab_to_github_migrator.cli.glu.get_readonly_token", return_value="gl-token"),
            patch("gitlab_to_github_migrator.cli.ghu.get_token", return_value="gh-token"),
            patch("gitlab_to_github_migrator.cli.GitlabToGithubMigrator") as mock_migrator,
        ):
            mock_instance = MagicMock()
            mock_instance.migrate.return_value = mock_report
            mock_migrator.return_value = mock_instance

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

            mock_migrator.assert_called_once()
            _, kwargs = mock_migrator.call_args
            assert kwargs["label_translations"] == ["bug:new-bug", "p*:p-*"]

    def test_no_label_translations_when_flag_omitted(self) -> None:
        """When -l is not supplied, label_translations is None."""
        mock_report = {
            "gitlab_project": "ns/proj",
            "github_repo": "owner/repo",
            "success": True,
            "errors": [],
            "statistics": {},
        }

        with (
            patch("sys.argv", ["prog", "ns/proj", "owner/repo"]),
            patch("gitlab_to_github_migrator.cli.glu.get_readonly_token", return_value="gl-token"),
            patch("gitlab_to_github_migrator.cli.ghu.get_token", return_value="gh-token"),
            patch("gitlab_to_github_migrator.cli.GitlabToGithubMigrator") as mock_migrator,
        ):
            mock_instance = MagicMock()
            mock_instance.migrate.return_value = mock_report
            mock_migrator.return_value = mock_instance

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

            mock_migrator.assert_called_once()
            _, kwargs = mock_migrator.call_args
            assert kwargs["label_translations"] is None


@pytest.mark.unit
class TestSkipFlags:
    """Test that --skip-labels, --skip-milestones, --skip-issues are forwarded to the migrator."""

    _mock_report: ClassVar[dict[str, object]] = {
        "gitlab_project": "ns/proj",
        "github_repo": "owner/repo",
        "success": True,
        "errors": [],
        "statistics": {},
    }

    def _run_main_with_args(self, extra_args: list[str]) -> tuple[MagicMock, MagicMock]:
        with (
            patch("sys.argv", ["prog", *extra_args, "ns/proj", "owner/repo"]),
            patch("gitlab_to_github_migrator.cli.glu.get_readonly_token", return_value="gl-token"),
            patch("gitlab_to_github_migrator.cli.ghu.get_token", return_value="gh-token"),
            patch("gitlab_to_github_migrator.cli.GitlabToGithubMigrator") as mock_migrator,
        ):
            mock_instance = MagicMock()
            mock_instance.migrate.return_value = self._mock_report
            mock_migrator.return_value = mock_instance

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

            return mock_migrator, mock_instance

    def test_skip_flags_default_to_false(self) -> None:
        """When skip flags are omitted, they default to False."""
        mock_migrator, _ = self._run_main_with_args([])
        _, kwargs = mock_migrator.call_args
        assert kwargs["skip_labels"] is False
        assert kwargs["skip_milestones"] is False
        assert kwargs["skip_issues"] is False

    def test_skip_labels_passed_to_migrator(self) -> None:
        """--skip-labels is forwarded to the migrator as skip_labels=True."""
        mock_migrator, _ = self._run_main_with_args(["--skip-labels"])
        _, kwargs = mock_migrator.call_args
        assert kwargs["skip_labels"] is True
        assert kwargs["skip_milestones"] is False
        assert kwargs["skip_issues"] is False

    def test_skip_milestones_passed_to_migrator(self) -> None:
        """--skip-milestones is forwarded to the migrator as skip_milestones=True."""
        mock_migrator, _ = self._run_main_with_args(["--skip-milestones"])
        _, kwargs = mock_migrator.call_args
        assert kwargs["skip_labels"] is False
        assert kwargs["skip_milestones"] is True
        assert kwargs["skip_issues"] is False

    def test_skip_issues_passed_to_migrator(self) -> None:
        """--skip-issues is forwarded to the migrator as skip_issues=True."""
        mock_migrator, _ = self._run_main_with_args(["--skip-issues"])
        _, kwargs = mock_migrator.call_args
        assert kwargs["skip_labels"] is False
        assert kwargs["skip_milestones"] is False
        assert kwargs["skip_issues"] is True

    def test_all_skip_flags_combined(self) -> None:
        """All three skip flags can be used together."""
        mock_migrator, _ = self._run_main_with_args(["--skip-labels", "--skip-milestones", "--skip-issues"])
        _, kwargs = mock_migrator.call_args
        assert kwargs["skip_labels"] is True
        assert kwargs["skip_milestones"] is True
        assert kwargs["skip_issues"] is True
