"""
Cleanup script for orphaned test repositories.

This script identifies and deletes test repositories with names starting with 
"migration-test-" or "deletion-test-" from the abuflow GitHub organization.

Usage:
    uv run delete_test_repos [pass_path]

Args:
    pass_path: Optional path to 'pass' entry containing GitHub token with admin rights.
               If not provided, will try GITHUB_TOKEN env var or github/api/token.

TODO add required command line positional arg to pass the github repo. Do not hardcode a default.
TODO make the pass_path a "flag" argument instead of positional
"""

import argparse
import os
import sys
import textwrap
from typing import Final

from github import Github

from .utils import PassError, get_pass_value, setup_logging

GITHUB_TOKEN_ENV_VAR: Final[str] = "GITHUB_TOKEN"  # noqa: S105
DEFAULT_GITHUB_TOKEN_PASS_PATH: Final[str] = "github/api/token"  # noqa: S105


def _get_github_token(pass_path: str | None = None) -> str:
    """Get GitHub token from pass path, env var GITHUB_TOKEN, or default pass location."""
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
    except PassError as e:
        msg = "No GitHub token specified nor found. Please specify correct pass path or set GITHUB_TOKEN environment variable."
        raise ValueError(msg) from e



def delete_test_repositories(pass_path: str | None = None) -> None:
    """Find and delete test repositories."""
    # TODO refactor to use the github_utils
    token = _get_github_token(pass_path)
    if not token:
        path_info = f" at '{pass_path}'" if pass_path else " at 'github/api/token'"
        print(f"❌ No GitHub token found. Set GITHUB_TOKEN env var or use 'pass' to store token{path_info}")
        sys.exit(1)

    github_client = Github(token)
    
    try:
        org = github_client.get_organization("abuflow")
        print(f"🔍 Scanning repositories in {org.login} organization...")
        
        repos = list(org.get_repos())
        test_repos = [
            repo for repo in repos 
            if repo.name.startswith("migration-test-") or repo.name.startswith("deletion-test-")
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
        failed_repos = []
        
        for repo in test_repos:
            try:
                repo.delete()
                print(f"✅ Deleted: {repo.name}")
                success_count += 1
            except Exception as e:
                print(f"❌ Failed to delete {repo.name}: {e}")
                failed_repos.append((repo.name, str(e)))
        
        print(f"\n📊 Cleanup Summary:")
        print(f"  ✅ Successfully deleted: {success_count}")
        print(f"  ❌ Failed to delete: {len(failed_repos)}")
        
        if failed_repos:
            print(f"\n❌ Failed repositories:")
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
              uv run delete_test_repos                    # Use default token path
              uv run delete_test_repos github/admin/token # Use admin token from pass
        """)
    )
    parser.add_argument(
        "pass_path", 
        nargs="?", 
        help="Path to 'pass' entry containing GitHub token with admin rights"
    )
    
    args = parser.parse_args()
    delete_test_repositories(args.pass_path)


if __name__ == "__main__":
    main()
