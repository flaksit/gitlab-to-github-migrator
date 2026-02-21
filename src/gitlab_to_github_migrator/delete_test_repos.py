"""
Cleanup script for orphaned test repositories.

This script identifies and deletes test repositories matching the pattern
"gl2ghmigr-(.+-)?test" from a specified GitHub owner (organization or user).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import textwrap
from typing import TYPE_CHECKING

from github import Auth, Github, GithubException, UnknownObjectException
from github.AuthenticatedUser import AuthenticatedUser

from . import github_utils as ghu
from .utils import setup_logging

if TYPE_CHECKING:
    from github.Organization import Organization
    from github.Repository import Repository

logger = logging.getLogger(__name__)


def get_owner_repos(client: Github, owner_name: str) -> tuple[str, list[Repository]]:
    """
    Get repositories for a GitHub owner (organization or user).

    Args:
        client: Authenticated GitHub client
        owner_name: GitHub organization or user name

    Returns:
        Tuple of (owner_type, repositories) where owner_type is "organization" or "user"

    Raises:
        UnknownObjectException: If owner is not found or not accessible
        ValueError: If owner is a user but doesn't match authenticated user
    """
    # Try to get as organization first, fall back to user
    try:
        org: Organization = client.get_organization(owner_name)
    except UnknownObjectException as e:
        if e.status == 404 and e.message == "Not Found":
            # Not an organization, validate it's the authenticated user
            authenticated_user = client.get_user()
            assert isinstance(authenticated_user, AuthenticatedUser)  # always true
            if owner_name != authenticated_user.login:
                msg = (
                    f"Cannot access repositories for '{owner_name}'. "
                    "The specified owner is not an organization and does not match "
                    f"the authenticated user '{authenticated_user.login}'. "
                    "You can only access repositories for organizations you have access to "
                    "or for your own user account."
                )
                raise ValueError(msg) from None

            # Get repositories for authenticated user
            repos = list(authenticated_user.get_repos())
            return "user", repos
        raise
    else:
        repos = list(org.get_repos())
        return "organization", repos


def delete_test_repositories(github_owner: str, github_token_pass_path: str | None) -> None:
    """Find and delete test repositories for the specified GitHub owner."""
    token = ghu.get_token(pass_path=github_token_pass_path)
    github_client = Github(auth=Auth.Token(token))

    try:
        owner_type, repos = get_owner_repos(github_client, github_owner)
        logger.info(f"🔍 Scanning repositories for {github_owner} ({owner_type})...")

        test_repo_pattern = re.compile(r"gl2ghmigr-(.+-)?test\b")
        test_repos = [repo for repo in repos if test_repo_pattern.match(repo.name)]

        if not test_repos:
            logger.info("✅ No test repositories found to cleanup")
            return

        logger.info(f"📋 Found {len(test_repos)} test repositories:")
        for repo in test_repos:
            logger.info(f"  - {repo.name} (created: {repo.created_at})")

        # Auto-confirm deletion since this is a cleanup script
        logger.info(f"\n🚀 Proceeding to delete all {len(test_repos)} test repositories...")

        logger.info("\n🗑️  Deleting repositories...")
        success_count = 0
        failed_repos: list[tuple[str, str]] = []

        for repo in test_repos:
            try:
                repo.delete()
                logger.info(f"✅ Deleted: {repo.name}")
                success_count += 1
            except GithubException as e:
                logger.exception(f"❌ Failed to delete {repo.name}")
                failed_repos.append((repo.name, str(e)))

        logger.info("\n📊 Cleanup Summary:")
        logger.info(f"  ✅ Successfully deleted: {success_count}")
        logger.info(f"  ❌ Failed to delete: {len(failed_repos)}")

        if failed_repos:
            logger.error("\n❌ Failed repositories:")
            for repo_name, error in failed_repos:
                logger.error(f"  - {repo_name}: {error}")

        if success_count > 0:
            logger.info(f"\n🎉 Cleanup completed! Deleted {success_count} test repositories.")

    except GithubException, ValueError:
        logger.exception("❌ Error during cleanup")
        sys.exit(1)


def main() -> None:
    """Main entry point for the cleanup script."""
    parser = argparse.ArgumentParser(
        description="Cleanup orphaned test repositories from GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              uv run delete_test_repos your-org                              # Delete from organization
              uv run delete_test_repos your-user                             # Delete from user account
              uv run delete_test_repos                                       # Uses TARGET_GITHUB_TEST_OWNER env var
              uv run delete_test_repos your-org --github-token-pass-path github/admin/token
        """),
    )
    parser.add_argument(
        "github_owner",
        nargs="?",
        help="GitHub organization or user to search for test repositories. "
        "If not provided, uses TARGET_GITHUB_TEST_OWNER environment variable.",
    )
    parser.add_argument(
        "--github-token-pass-path",
        help=f"Path for GitHub token in pass utility. If not set, will use {ghu.GITHUB_TOKEN_ENV_VAR} env var, "
        f"or fall back to default pass path {ghu.DEFAULT_GITHUB_TOKEN_PASS_PATH}.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()
    setup_logging(verbose=args.verbose)

    # Determine github_owner: use argument if provided, otherwise use env var
    github_owner = args.github_owner
    if not github_owner:
        github_owner = os.environ.get("TARGET_GITHUB_TEST_OWNER")
        if not github_owner:
            parser.error(
                "github_owner argument is required when TARGET_GITHUB_TEST_OWNER environment variable is not set.\n"
                "Either provide github_owner as an argument or set TARGET_GITHUB_TEST_OWNER environment variable."
            )

    github_token_pass_path: str | None = getattr(args, "github_token_pass_path", None)
    delete_test_repositories(github_owner, github_token_pass_path)


if __name__ == "__main__":
    main()
