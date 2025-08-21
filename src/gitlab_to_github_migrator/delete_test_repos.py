"""
Cleanup script for orphaned test repositories.

This script identifies and deletes test repositories with names starting with
"migration-test-" or "deletion-test-" from a specified GitHub owner (organization or user).

Usage:
    uv run delete_test_repos <github_owner> <pass_path>

Args:
    github_owner: GitHub organization or user to search for test repositories
    pass_path: Path to 'pass' entry containing GitHub token with admin rights
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from typing import TYPE_CHECKING

from github import Github, UnknownObjectException
from github.AuthenticatedUser import AuthenticatedUser

from .utils import get_pass_value, setup_logging

if TYPE_CHECKING:
    from github.Organization import Organization
    from github.Repository import Repository


def _get_github_token(pass_path: str) -> str:
    """Get GitHub token from specified pass path."""
    return get_pass_value(pass_path)


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


def delete_test_repositories(github_owner: str, pass_path: str) -> None:
    """Find and delete test repositories for the specified GitHub owner."""
    token = _get_github_token(pass_path)
    github_client = Github(token)

    try:
        owner_type, repos = get_owner_repos(github_client, github_owner)
        print(f"🔍 Scanning repositories for {github_owner} ({owner_type})...")

        test_repos = [
            repo for repo in repos if repo.name.startswith("migration-test-") or repo.name.startswith("deletion-test-")
        ]

        if not test_repos:
            print("✅ No test repositories found to cleanup")
            return

        print(f"📋 Found {len(test_repos)} test repositories:")
        for repo in test_repos:
            print(f"  - {repo.name} (created: {repo.created_at})")

        # Auto-confirm deletion since this is a cleanup script
        print(f"\n🚀 Proceeding to delete all {len(test_repos)} test repositories...")

        print("\n🗑️  Deleting repositories...")
        success_count = 0
        failed_repos: list[tuple[str, str]] = []

        for repo in test_repos:
            try:
                repo.delete()
                print(f"✅ Deleted: {repo.name}")
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to delete {repo.name}: {e}")
                failed_repos.append((repo.name, str(e)))

        print("\n📊 Cleanup Summary:")
        print(f"  ✅ Successfully deleted: {success_count}")
        print(f"  ❌ Failed to delete: {len(failed_repos)}")

        if failed_repos:
            print("\n❌ Failed repositories:")
            for repo_name, error in failed_repos:
                print(f"  - {repo_name}: {error}")

        if success_count > 0:
            print(f"\n🎉 Cleanup completed! Deleted {success_count} test repositories.")

    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        sys.exit(1)


def main() -> None:
    """Main entry point for the cleanup script."""

    setup_logging(verbose=True)

    parser = argparse.ArgumentParser(
        description="Cleanup orphaned test repositories from GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Examples:
              uv run delete_test_repos abuflow github/api/token        # Delete from organization
              uv run delete_test_repos myuser github/admin/token       # Delete from user account
        """),
    )
    parser.add_argument("github_owner", help="GitHub organization or user to search for test repositories")
    parser.add_argument("pass_path", help="Path to 'pass' entry containing GitHub token with admin rights")

    args = parser.parse_args()
    delete_test_repositories(args.github_owner, args.pass_path)


if __name__ == "__main__":
    main()
