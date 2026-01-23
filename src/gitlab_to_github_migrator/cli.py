"""
Command-line interface for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging import Logger
from typing import Any, Final

from .exceptions import MigrationError
from .migrator import GitLabToGitHubMigrator
from .utils import PassError, get_pass_value, setup_logging

logger: Logger = logging.getLogger(__name__)

GITLAB_TOKEN_ENV_VAR: Final[str] = "GITLAB_TOKEN"  # noqa: S105
DEFAULT_GITLAB_TOKEN_PASS_PATH: Final[str] = "gitlab/api/ro_token"  # noqa: S105
GITHUB_TOKEN_ENV_VAR: Final[str] = "GITHUB_TOKEN"  # noqa: S105
DEFAULT_GITHUB_TOKEN_PASS_PATH: Final[str] = "github/api/token"  # noqa: S105


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Migrate GitLab project to GitHub with full metadata preservation")

    # Positional arguments
    _ = parser.add_argument("gitlab_project", help="GitLab project path (namespace/project)")
    _ = parser.add_argument("github_repo", help="GitHub repository path (owner/repo)")

    # Optional arguments with short forms
    _ = parser.add_argument(
        "--relabel",
        "-l",
        action="append",
        help='Label translation pattern (format: "source_pattern:target_pattern"). Can be specified multiple times. Supports * as a glob-style wildcard. Example: "p_*:prio: *" translates "p_high" to "prio: high"',
    )

    _ = parser.add_argument("--local-clone", help="Path to existing local git clone of GitLab project")

    _ = parser.add_argument(
        "--gitlab-token-pass-path",
        help="Path for GitLab token in pass utility. If not set, will use GITLAB_TOKEN env var, or fall back to default pass path gitlab/api/ro_token. ",
    )

    _ = parser.add_argument(
        "--github-token-pass-path",
        help="Path for GitHub token in pass utility. If not set, will use GITHUB_TOKEN env var, or fall back to default pass path github/api/token.",
    )

    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    return parser.parse_args()


def _get_gitlab_token(pass_path: str | None = None) -> str | None:
    """Get GitLab token from pass path, env var GITLAB_TOKEN_ENV_VAR, or default pass location."""
    # Try pass path first
    if pass_path:
        return get_pass_value(pass_path)

    # Try environment variable
    token: str | None = os.environ.get(GITLAB_TOKEN_ENV_VAR)
    if token:
        return token

    # Try default pass path
    try:
        return get_pass_value(DEFAULT_GITLAB_TOKEN_PASS_PATH)
    except PassError:
        logger.warning(
            f"No GitLab token specified nor found. If non-anonymous access is required, specify correct pass path or set {GITLAB_TOKEN_ENV_VAR} environment variable."
        )
        return None


def _get_github_token(pass_path: str | None = None) -> str:
    """Get GitHub token from pass path, env var GITHUB_TOKEN_ENV_VAR, or default pass location."""
    # Try pass path first
    if pass_path:
        return get_pass_value(pass_path)

    # Try environment variable
    token: str | None = os.environ.get(GITHUB_TOKEN_ENV_VAR)
    if token:
        return token

    # Try default pass path
    try:
        return get_pass_value(DEFAULT_GITHUB_TOKEN_PASS_PATH)
    except PassError:
        msg = f"No GitHub token specified nor found. Specify correct pass path or set {GITHUB_TOKEN_ENV_VAR} environment variable."
        raise MigrationError(msg) from None


def _print_validation_report(report: dict[str, Any]) -> None:
    """Print the validation report in a readable format."""
    logger.info("=" * 80)
    logger.info("MIGRATION VALIDATION REPORT")
    logger.info("=" * 80)
    logger.info("")
    
    # Print project info
    logger.info(f"GitLab Project: {report['gitlab_project']}")
    logger.info(f"GitHub Repository: {report['github_repo']}")
    logger.info("")
    
    # Print validation status
    if report["success"]:
        logger.info("✓ Validation Status: PASSED")
    else:
        logger.error("✗ Validation Status: FAILED")
    logger.info("")
    
    # Print errors if any
    if report["errors"]:
        logger.error("ERRORS:")
        for error in report["errors"]:
            logger.error(f"  • {error}")
        logger.info("")
    
    # Print statistics
    logger.info("MIGRATION STATISTICS:")
    logger.info("")
    
    stats = report["statistics"]
    
    # Issues section
    logger.info("Issues:")
    logger.info(f"  GitLab:  Total={stats.get('gitlab_issues_total', 0)}, "
                f"Open={stats.get('gitlab_issues_open', 0)}, "
                f"Closed={stats.get('gitlab_issues_closed', 0)}")
    logger.info(f"  GitHub:  Total={stats.get('github_issues_total', 0)}, "
                f"Open={stats.get('github_issues_open', 0)}, "
                f"Closed={stats.get('github_issues_closed', 0)}")
    logger.info("")
    
    # Milestones section
    logger.info("Milestones:")
    logger.info(f"  GitLab:  Total={stats.get('gitlab_milestones_total', 0)}, "
                f"Open={stats.get('gitlab_milestones_open', 0)}, "
                f"Closed={stats.get('gitlab_milestones_closed', 0)}")
    logger.info(f"  GitHub:  Total={stats.get('github_milestones_total', 0)}, "
                f"Open={stats.get('github_milestones_open', 0)}, "
                f"Closed={stats.get('github_milestones_closed', 0)}")
    logger.info("")
    
    # Labels section
    logger.info("Labels:")
    logger.info(f"  GitLab:  Total={stats.get('gitlab_labels_total', 0)}")
    logger.info(f"  GitHub:  Existing={stats.get('github_labels_existing', 0)}, "
                f"Created={stats.get('github_labels_created', 0)}, "
                f"Translated={stats.get('labels_translated', 0)}")
    logger.info("")
    
    logger.info("=" * 80)


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    verbose: bool = getattr(args, "verbose", False)
    setup_logging(verbose=verbose)
    global logger  # noqa: PLW0603
    logger = logging.getLogger(__name__)

    # Initialize migrator
    label_translation: list[str] | None = getattr(args, "label_translation", None)
    local_clone_path: str | None = getattr(args, "local_clone_path", None)
    gitlab_token_pass_path: str | None = getattr(args, "gitlab_token_pass_path", None)
    github_token_pass_path: str | None = getattr(args, "github_token_pass_path", None)

    migrator = GitLabToGitHubMigrator(
        args.gitlab_project,
        args.github_repo,
        label_translations=label_translation,
        local_clone_path=local_clone_path,
        gitlab_token=_get_gitlab_token(gitlab_token_pass_path),
        github_token=_get_github_token(github_token_pass_path),
    )

    # Execute migration
    report = migrator.migrate()

    # Print validation report
    _print_validation_report(report)

    if report["success"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
