"""
Cleanup script for orphaned test repositories.

This script identifies and deletes test repositories with names starting with 
"migration-test-" or "deletion-test-" from the abuflow GitHub organization.

Usage:
    uv run cleanup_test_repos [pass_path]

Args:
    pass_path: Optional path to 'pass' entry containing GitHub token with admin rights.
               If not provided, will try GITHUB_TOKEN env var or github/cli/token.

TODO add required command line positional arg to pass the github repo. Do not hardcode a default.
TODO make the pass_path a "flag" argument instead of positional
"""

import os
import subprocess
import sys

from github import Github


def get_github_token(pass_path: str | None = None) -> str | None:
    """Get GitHub token from environment, pass path, or default pass location."""
    # Try environment variable first
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    
    # Try specified pass path or default
    pass_command_path = pass_path if pass_path else "github/cli/token"
    
    try:
        result = subprocess.run(
            ["pass", pass_command_path],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def cleanup_test_repositories(pass_path: str | None = None) -> None:
    """Find and delete test repositories."""
    token = get_github_token(pass_path)
    if not token:
        path_info = f" at '{pass_path}'" if pass_path else " at 'github/cli/token'"
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


def main():
    """Main entry point for the cleanup script."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Cleanup orphaned test repositories from GitHub",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run cleanup_test_repos                    # Use default token path
  uv run cleanup_test_repos github/admin/token # Use admin token from pass
        """
    )
    parser.add_argument(
        "pass_path", 
        nargs="?", 
        help="Path to 'pass' entry containing GitHub token with admin rights"
    )
    
    args = parser.parse_args()
    cleanup_test_repositories(args.pass_path)


if __name__ == "__main__":
    main()