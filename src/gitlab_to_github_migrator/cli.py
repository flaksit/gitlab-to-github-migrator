"""
Command-line interface for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging import Logger
from typing import Any

from . import git_utils
from . import github_utils as ghu
from . import gitlab_utils as glu
from .migrator import GitlabToGithubMigrator
from .utils import setup_logging

logger: Logger = logging.getLogger(__name__)


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

    _ = parser.add_argument(
        "--gitlab-token-pass-path",
        help=f"Path for GitLab token in pass utility. If not set, will try {glu.GITLAB_TOKEN_ENV_VAR} env var first, then fall back to default pass path {glu.DEFAULT_GITLAB_RO_TOKEN_PASS_PATH}.",
    )

    _ = parser.add_argument(
        "--github-token-pass-path",
        help=f"Path for GitHub token in pass utility. If not set, will try {ghu.GITHUB_TOKEN_ENV_VAR} env var first, then fall back to default pass path {ghu.DEFAULT_GITHUB_TOKEN_PASS_PATH}.",
    )

    _ = parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    _ = parser.add_argument(
        "--no-update-remotes",
        action="store_true",
        default=False,
        help=(
            "Do not update git remotes in the current working directory after a successful migration. "
            "By default, if the current directory is a git workdir for the migrated GitLab project, "
            "the remote pointing to GitLab is updated to point to the new GitHub repository and the "
            "old GitLab URL is kept as a backup remote."
        ),
    )

    return parser.parse_args()


def _print_validation_report(report: dict[str, Any]) -> None:
    """Print the validation report in a readable format."""
    print()
    print("=" * 80)
    print("MIGRATION VALIDATION REPORT")
    print("=" * 80)
    print()

    # Print project info
    print(f"GitLab Project: {report['gitlab_project']}")
    print(f"GitHub Repository: {report['github_repo']}")
    print()

    # Print validation status
    if report["success"]:
        print("✓ Validation Status: PASSED")
    else:
        print("✗ Validation Status: FAILED")
    print()

    # Print errors if any
    if report["errors"]:
        print("ERRORS:")
        for error in report["errors"]:
            print(f"  • {error}")
        print()

    # Print statistics
    print("MIGRATION STATISTICS:")
    print()

    stats = report["statistics"]

    # Git Repository section
    print("Git Repository:")
    print(
        f"  GitLab:  Branches={stats.get('gitlab_branches', 0)}, "
        f"Tags={stats.get('gitlab_tags', 0)}, "
        f"Commits={stats.get('gitlab_commits', 0)}"
    )
    print(
        f"  GitHub:  Branches={stats.get('github_branches', 0)}, "
        f"Tags={stats.get('github_tags', 0)}, "
        f"Commits={stats.get('github_commits', 0)}"
    )
    print()

    # Labels section
    print("Labels:")
    print(f"  GitLab:  Total={stats.get('gitlab_labels_total', 0)}")
    print(
        f"  GitHub:  Existing={stats.get('github_labels_existing', 0)}, "
        f"Created={stats.get('github_labels_created', 0)}, "
        f"Translated={stats.get('labels_translated', 0)}"
    )
    print()

    # Milestones section
    print("Milestones:")
    print(
        f"  GitLab:  Total={stats.get('gitlab_milestones_total', 0)}, "
        f"Open={stats.get('gitlab_milestones_open', 0)}, "
        f"Closed={stats.get('gitlab_milestones_closed', 0)}"
    )
    print(
        f"  GitHub:  Total={stats.get('github_milestones_total', 0)}, "
        f"Open={stats.get('github_milestones_open', 0)}, "
        f"Closed={stats.get('github_milestones_closed', 0)}"
    )
    print()

    # Issues section
    print("Issues:")
    print(
        f"  GitLab:  Total={stats.get('gitlab_issues_total', 0)}, "
        f"Open={stats.get('gitlab_issues_open', 0)}, "
        f"Closed={stats.get('gitlab_issues_closed', 0)}"
    )
    print(
        f"  GitHub:  Total={stats.get('github_issues_total', 0)}, "
        f"Open={stats.get('github_issues_open', 0)}, "
        f"Closed={stats.get('github_issues_closed', 0)}"
    )
    print()

    # Comments section
    print("Comments:")
    print(f"  Migrated: {stats.get('comments_migrated', 0)}")
    print()

    # Attachments section
    print("Attachments:")
    print(f"  Uploaded files: {stats.get('attachments_uploaded', 0)}")
    print(f"  Total references: {stats.get('attachments_referenced', 0)}")
    print()

    print("=" * 80)


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
    gitlab_token_pass_path: str | None = getattr(args, "gitlab_token_pass_path", None)
    github_token_pass_path: str | None = getattr(args, "github_token_pass_path", None)

    gitlab_token = glu.get_readonly_token(pass_path=gitlab_token_pass_path)
    if gitlab_token is None:
        logger.warning(
            f"No GitLab token found. If non-anonymous access is required, "
            f"set {glu.GITLAB_TOKEN_ENV_VAR} environment variable or configure pass at {glu.DEFAULT_GITLAB_RO_TOKEN_PASS_PATH}."
        )

    migrator = GitlabToGithubMigrator(
        args.gitlab_project,
        args.github_repo,
        label_translations=label_translation,
        gitlab_token=gitlab_token,
        github_token=ghu.get_token(pass_path=github_token_pass_path),
    )

    # Execute migration
    report = migrator.migrate()

    # Print validation report
    _print_validation_report(report)

    # Update git remotes in the current working directory (unless disabled).
    if report["success"] and not getattr(args, "no_update_remotes", False):
        updated = git_utils.update_remotes_after_migration(args.gitlab_project, args.github_repo)
        if updated:
            print()
            print("Git remotes updated:")
            for entry in updated:
                print(f"  {entry.remote_name}: {entry.old_url} → {entry.new_url}")
                print(f"  {entry.backup_name}: {entry.old_url} (backup)")

    if report["success"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
