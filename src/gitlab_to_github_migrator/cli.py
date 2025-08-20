"""
Command-line interface for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .migrator import GitLabToGitHubMigrator
from .utils import setup_logging


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
        help='Label translation pattern (format: "source_pattern:target_pattern"). Can be specified multiple times.',
    )

    _ = parser.add_argument(
        "--local-clone", help="Path to existing local git clone of GitLab project"
    )

    _ = parser.add_argument(
        "--gitlab-pass-token", help="Path for GitLab token in pass utility (default: gitlab/cli/ro_token)"
    )

    _ = parser.add_argument(
        "--github-pass-token", help="Path for GitHub token in pass utility (default: github/cli/token)"
    )

    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    verbose: bool = getattr(args, "verbose", False)
    setup_logging(verbose=verbose)

    try:
        # Initialize migrator
        label_translation: list[str] | None = getattr(args, "label_translation", None)
        local_clone_path: str | None = getattr(args, "local_clone_path", None)
        gitlab_token_path: str | None = getattr(args, "gitlab_token_path", None)
        github_token_path: str | None = getattr(args, "github_token_path", None)

        migrator = GitLabToGitHubMigrator(
            args.gitlab_project,
            args.github_repo,
            label_translations=label_translation,
            local_clone_path=local_clone_path,
            gitlab_token_path=gitlab_token_path,
            github_token_path=github_token_path,
        )

        # Execute migration
        report = migrator.migrate()

        # Print report
        errors = report["errors"]
        statistics = report["statistics"]

        if errors:
            for _error in errors:
                pass

        for _key, _value in statistics.items():
            pass

        if report["success"]:
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception:
        logger = logging.getLogger(__name__)
        logger.exception("Migration failed")
        sys.exit(1)
