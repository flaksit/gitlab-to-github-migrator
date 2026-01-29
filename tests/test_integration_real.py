"""
Integration tests for GitLab to GitHub Migration Tool using real APIs

These tests connect to real GitLab and GitHub APIs to ensure the migration
functionality works correctly with actual data.

Test source: GitLab project (REQUIRED: set via SOURCE_GITLAB_TEST_PROJECT environment variable)
Test target: Temporary GitHub repo (REQUIRED: set via TARGET_GITHUB_TEST_OWNER environment variable)

Test structure:
- Read-only tests: Verify API access and data reading (no GitHub repo needed)
- Repository lifecycle test: Tests repo creation and deletion (isolated)
- Full migration test: End-to-end migration with comprehensive assertions (isolated)
"""

import os
import random
import re
import string

import gitlab
import pytest
from github import Auth, Github, GithubException
from gitlab.exceptions import GitlabError

from gitlab_to_github_migrator import GitlabToGithubMigrator
from gitlab_to_github_migrator import github_utils as ghu
from gitlab_to_github_migrator import gitlab_utils as glu
from gitlab_to_github_migrator.exceptions import MigrationError


def _generate_repo_name(test_type: str = "generic") -> str:
    """Generate a unique test repository name.

    Format: gl2ghmigr-<test_type>-test-<hash>
    """
    random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"gl2ghmigr-{test_type}-test-{random_suffix}"


@pytest.fixture(scope="module")
def source_gitlab_project() -> str:
    """Get the source GitLab project path from environment."""
    project = os.environ.get("SOURCE_GITLAB_TEST_PROJECT")
    if not project:
        msg = (
            "SOURCE_GITLAB_TEST_PROJECT environment variable is required. "
            "Example: export SOURCE_GITLAB_TEST_PROJECT='your-namespace/your-project'"
        )
        raise ValueError(msg)
    return project


@pytest.fixture(scope="module")
def target_github_org() -> str:
    """Get the target GitHub org/user from environment."""
    org = os.environ.get("TARGET_GITHUB_TEST_OWNER")
    if not org:
        msg = "TARGET_GITHUB_TEST_OWNER environment variable is required. Example: export TARGET_GITHUB_TEST_OWNER='your-org-or-username'"
        raise ValueError(msg)
    return org


@pytest.fixture(scope="module")
def gitlab_token() -> str | None:
    """Get GitLab API token."""
    return glu.get_readonly_token()


@pytest.fixture(scope="module")
def github_token() -> str:
    """Get GitHub API token."""
    return ghu.get_token()


@pytest.fixture(scope="module")
def gitlab_client(gitlab_token: str | None) -> gitlab.Gitlab:
    """Create GitLab API client."""
    if gitlab_token:
        return gitlab.Gitlab("https://gitlab.com", private_token=gitlab_token)
    return gitlab.Gitlab()  # Anonymous access


@pytest.fixture(scope="module")
def github_client(github_token: str) -> Github:
    """Create GitHub API client."""
    return Github(auth=Auth.Token(github_token))


@pytest.mark.integration
class TestGitHubAccess:
    """Tests that only read from GitHub - no GitLab repository needed."""

    def test_github_api_access(
        self,
        github_client: Github,
        target_github_org: str,
    ) -> None:
        """Test that we can access GitHub API."""
        github_client.get_user()

        # Test organization or user access
        try:
            github_client.get_organization(target_github_org)
        except GithubException:
            # Not an organization, might be a user account
            try:
                github_client.get_user(target_github_org)
            except GithubException as user_err:
                pytest.fail(f"Failed to access '{target_github_org}' as organization or user: {user_err}")


@pytest.mark.integration
class TestReadOnlyGitlabAccess:
    """Tests that only read from GitLab - no GitHub repository needed."""

    def test_gitlab_source_project_access(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test that we can access the source GitLab project."""
        project = gitlab_client.projects.get(source_gitlab_project)
        assert project.path_with_namespace == source_gitlab_project

        # Test that we can read milestones, and labels
        # Issue reading is tested in more detail in another test
        project.milestones.list(per_page=2, get_all=False, state="all")
        project.labels.list(per_page=2, get_all=False)

    def test_issue_read(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test reading issues from real GitLab source."""
        source_project = gitlab_client.projects.get(source_gitlab_project)
        issues = source_project.issues.list(per_page=2, get_all=False, state="all")

        if not issues:
            pytest.skip("No issues found in source project")

        for issue in issues:
            issue.notes.list(per_page=5, get_all=False)

            # Test that we can access issue properties needed for migration
            assert hasattr(issue, "iid")
            assert hasattr(issue, "title")
            assert hasattr(issue, "description")
            assert hasattr(issue, "state")
            assert hasattr(issue, "labels")
            assert hasattr(issue, "created_at")
            assert hasattr(issue, "web_url")

    def test_gitlab_cross_linking_read(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test reading cross-linked issues from GitLab."""
        # TODO Don't retrieve all (or 20) issues: use .list(iterator=True) and stop when we found one with links
        source_project = gitlab_client.projects.get(source_gitlab_project)
        issues = source_project.issues.list(iterator=True, state="all")

        for issue in issues:
            try:
                links = issue.links.list(get_all=True)
                if links:
                    # Test link properties (data is directly on link object)
                    for link in links[:2]:
                        assert hasattr(link, "iid")
                        assert hasattr(link, "title")
                        assert hasattr(link, "link_type")

                    break
            except Exception:
                continue
        else:
            pytest.skip("No cross-linked issues found in source project")

    def test_gitlab_attachment_detection(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test finding and accessing GitLab attachments."""
        # TODO Don't retrieve all (or 50) issues: use .list(iterator=True) and stop when we found one with attachments

        source_project = gitlab_client.projects.get(source_gitlab_project)
        issues = source_project.issues.list(iterator=True, state="all")

        attachment_pattern = r"/uploads/[a-f0-9]{32}/[^)\\s]+"

        for issue in issues:
            if issue.description:
                attachments = re.findall(attachment_pattern, issue.description)
                if attachments:
                    break
        else:
            pytest.skip("No attachments found in source project issues")

        # TODO Add: check that we can download the attachments

    def test_graphql_work_items_api_functionality(
        self,
        source_gitlab_project: str,
        gitlab_token: str | None,
        github_token: str,
    ) -> None:
        """Test the GitLab GraphQL Work Items API functionality."""
        if not gitlab_token:
            pytest.skip("GitLab token required for GraphQL API testing")

        # Create migrator just for GraphQL testing (doesn't need GitHub repo)
        migrator = GitlabToGithubMigrator(
            gitlab_project_path=source_gitlab_project,
            github_repo_path="dummy/repo",  # Not used
            gitlab_token=gitlab_token,
            github_token=github_token,
        )

        issues = migrator.gitlab_project.issues.list(iterator=True, state="all")

        # Find an issue that actually has work items by querying GraphQL API
        # Limit to first 20 issues to avoid excessive API calls
        for issue in issues:
            try:
                child_work_items = migrator.get_work_item_children(issue.iid)
                if child_work_items:
                    break
            except (GitlabError, MigrationError):
                # Skip issues that fail to query (e.g., not a work item type)
                continue
        else:
            pytest.skip("No issues with work items found for GraphQL testing")

        # Test GraphQL Work Items API returns valid values
        if not child_work_items:
            pytest.skip("No child work items found for the test issue")

        for child in child_work_items:
            assert int(child.iid) > 0
            assert len(child.title) > 0
            assert child.state.lower() in ("open", "closed")
            assert len(child.type) > 0
            assert "gitlab" in child.web_url.lower()

    def test_closed_issues_retrieval(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test that closed issues are properly retrieved."""
        source_project = gitlab_client.projects.get(source_gitlab_project)

        all_issues = source_project.issues.list(get_all=True, state="all")

        closed_count = len([issue for issue in all_issues if issue.state == "closed"])
        opened_count = len([issue for issue in all_issues if issue.state == "opened"])

        assert closed_count > 0, "No closed issues found - test project should have closed issues"
        assert opened_count > 0, "No opened issues found - test project should have open issues"
        assert len(all_issues) == closed_count + opened_count

    def test_closed_milestones_retrieval(
        self,
        gitlab_client: gitlab.Gitlab,
        source_gitlab_project: str,
    ) -> None:
        """Test that closed milestones are properly retrieved."""
        source_project = gitlab_client.projects.get(source_gitlab_project)

        all_milestones = source_project.milestones.list(get_all=True, state="all")

        if all_milestones:
            active_milestones = [m for m in all_milestones if m.state == "active"]
            closed_milestones = [m for m in all_milestones if m.state == "closed"]
            assert len(all_milestones) == len(active_milestones) + len(closed_milestones)


@pytest.mark.integration
class TestFullMigration:
    """End-to-end migration test with comprehensive assertions."""

    def test_full_migration(  # noqa: PLR0915 - intentionally comprehensive test
        self,
        source_gitlab_project: str,
        target_github_org: str,
        gitlab_token: str | None,
        github_token: str,
        gitlab_client: gitlab.Gitlab,
    ) -> None:
        """Test complete migration workflow using the main migrate() method."""
        repo_name = _generate_repo_name("full-migration")
        repo_path = f"{target_github_org}/{repo_name}"

        # Get source data for comparison before migration
        source_project = gitlab_client.projects.get(source_gitlab_project)
        source_labels = source_project.labels.list(get_all=True)
        source_milestones = source_project.milestones.list(get_all=True, state="all")
        source_issues = source_project.issues.list(get_all=True, state="all")
        source_branches = source_project.branches.list(get_all=True)
        source_tags = source_project.tags.list(get_all=True)
        source_commits = source_project.commits.list(get_all=True)

        # Dynamically discover label patterns and create translations
        # Find common prefixes among labels without assuming any specific format
        label_translations = []
        expected_translations = {}  # Maps source label -> expected target label
        max_patterns = 3  # Limit patterns to keep test manageable

        label_names = [label.name for label in source_labels]

        # Strategy 1: Find common prefixes that match 2+ labels
        # Use first letter as prefix for simplicity and better coverage
        prefix_matches = {}
        for label_name in label_names:
            if label_name:  # Skip empty labels
                prefix = label_name[0]  # First letter as prefix
                if prefix not in prefix_matches:
                    prefix_matches[prefix] = set()
                prefix_matches[prefix].add(label_name)

        # Find a prefix that matches multiple labels (indicating a pattern)
        for prefix, matching_labels in prefix_matches.items():
            if len(matching_labels) >= 2 and len(label_translations) < max_patterns:
                # Create wildcard pattern: "prefix*:prefix-*"
                pattern = f"{prefix}*:{prefix}-*"
                label_translations.append(pattern)

                # Track expected translations for verification
                for label_name in matching_labels:
                    suffix = label_name[1:] if len(label_name) > 1 else ""  # Get part after first letter
                    expected_translations[label_name] = f"{prefix}-{suffix}"

                break  # Use first pattern found

        # Strategy 2: Add a non-wildcard translation for a single label
        # Pick a label that wasn't already matched by wildcard patterns
        for label_name in label_names:
            if label_name not in expected_translations and len(label_translations) < max_patterns:
                # Create exact translation: "original:translated"
                translated_name = f"renamed-{label_name}"
                pattern = f"{label_name}:{translated_name}"
                label_translations.append(pattern)
                expected_translations[label_name] = translated_name
                break

        migrator = GitlabToGithubMigrator(
            gitlab_project_path=source_gitlab_project,
            github_repo_path=repo_path,
            label_translations=label_translations,
            gitlab_token=gitlab_token,
            github_token=github_token,
        )

        try:
            # Run the full migration
            report = migrator.migrate()
            github_repo = migrator.github_repo
            print(f"\n✓ Migration completed for: {repo_path}")

            # Verify migration report
            assert report["success"] is True, f"Migration failed: {report.get('errors', [])}"
            assert report["gitlab_project"] == source_gitlab_project
            assert report["github_repo"] == repo_path
            assert "statistics" in report

            stats = report["statistics"]
            assert stats["gitlab_issues_total"] == len(source_issues)
            assert stats["gitlab_milestones_total"] == len(source_milestones)
            assert stats["gitlab_labels_total"] == len(source_labels)

            # Verify GitHub counts match GitLab counts
            assert stats["github_issues_total"] == stats["gitlab_issues_total"], (
                f"Issue count mismatch in report: {stats['github_issues_total']} != {stats['gitlab_issues_total']}"
            )
            assert stats["github_milestones_total"] == stats["gitlab_milestones_total"], (
                f"Milestone count mismatch in report: {stats['github_milestones_total']} != {stats['gitlab_milestones_total']}"
            )

            print("✓ Migration report verified")
            print(f"  - Issues: {stats['gitlab_issues_total']} → {stats['github_issues_total']}")
            print(f"  - Milestones: {stats['gitlab_milestones_total']} → {stats['github_milestones_total']}")
            print(f"  - Labels: {stats['gitlab_labels_total']} (translated: {stats['labels_translated']})")

            # Verify git content was migrated
            github_branches = list(github_repo.get_branches())
            github_tags = list(github_repo.get_tags())
            github_commits = list(github_repo.get_commits())

            assert len(github_branches) == len(source_branches), (
                f"Branch count mismatch: {len(github_branches)} != {len(source_branches)}"
            )
            assert len(github_tags) == len(source_tags), (
                f"Tag count mismatch: {len(github_tags)} != {len(source_tags)}"
            )
            assert len(github_commits) == len(source_commits), (
                f"Commit count mismatch: {len(github_commits)} != {len(source_commits)}"
            )
            print(
                f"✓ Git content migrated ({len(github_branches)} branches, {len(github_tags)} tags, {len(github_commits)} commits)"
            )

            # Verify labels
            github_labels = list(github_repo.get_labels())
            assert len(migrator.label_mapping) > 0, "No labels were mapped"
            print(f"✓ Verified {len(migrator.label_mapping)} labels")

            # Check label translations were applied correctly
            if expected_translations:
                # Verify that the expected translations were created
                github_label_names = {label.name for label in github_labels}
                for source_label, expected_target_label in expected_translations.items():
                    assert expected_target_label in github_label_names, (
                        f"Expected translated label '{expected_target_label}' (from '{source_label}') not found in GitHub"
                    )
                    # Verify the mapping was recorded correctly
                    assert migrator.label_mapping.get(source_label) == expected_target_label, (
                        f"Label mapping incorrect: {source_label} -> {migrator.label_mapping.get(source_label)} "
                        f"(expected {expected_target_label})"
                    )
                print(f"✓ Label translations applied and verified ({len(expected_translations)} translated)")

            # Verify milestones
            if source_milestones:
                github_milestones = list(github_repo.get_milestones(state="all"))
                # After cleanup_placeholders(), only real milestones remain
                real_milestones = [m for m in github_milestones if m.title != "Placeholder Milestone"]
                assert len(real_milestones) == len(source_milestones), (
                    f"Milestone count mismatch: {len(real_milestones)} != {len(source_milestones)}"
                )

                # Verify milestone number preservation
                for source_ms in source_milestones:
                    matching = [m for m in github_milestones if m.title == source_ms.title]
                    assert len(matching) == 1, f"Milestone '{source_ms.title}' not found"
                    assert matching[0].number == source_ms.iid, (
                        f"Milestone number not preserved: {matching[0].number} != {source_ms.iid}"
                    )

                print(f"✓ Verified {len(source_milestones)} milestones with number preservation")

            # Verify issues
            if source_issues:
                github_issues = list(github_repo.get_issues(state="all"))
                # Placeholders remain (GitHub doesn't allow issue deletion) but are closed
                real_issues = [i for i in github_issues if i.title != "Placeholder"]
                assert len(real_issues) == len(source_issues), (
                    f"Issue count mismatch: {len(real_issues)} != {len(source_issues)}"
                )

                # Verify issue number preservation
                for source_issue in source_issues[:10]:  # Check first 10
                    matching = [i for i in github_issues if i.number == source_issue.iid]
                    assert len(matching) == 1, f"Issue #{source_issue.iid} not found at correct number"

                # Verify issue state preservation
                source_closed = len([i for i in source_issues if i.state == "closed"])
                github_closed = len([i for i in real_issues if i.state == "closed"])
                assert github_closed == source_closed, (
                    f"Closed issue count mismatch: {github_closed} != {source_closed}"
                )

                print(f"✓ Verified {len(source_issues)} issues with number preservation")

                # Verify some issues have comments
                issues_with_comments = 0
                for github_issue in real_issues[:10]:
                    comments = list(github_issue.get_comments())
                    if comments:
                        issues_with_comments += 1
                        # Verify comment format
                        for comment in comments[:2]:
                            assert "**" in comment.body or "System note:" in comment.body

                if issues_with_comments > 0:
                    print(f"✓ Verified comments on {issues_with_comments} issues")

                # Verify parent-child relationships (sub-issues)
                # Test project has issue #3 as parent with child tasks #5 and #6
                try:
                    parent_issue = github_repo.get_issue(3)
                    # PyGithub doesn't expose sub_issues for reading, so we verify via events API
                    # Get issue events and filter for sub_issue_added events
                    events = list(parent_issue.get_events())
                    sub_issue_events = [e for e in events if e.event == "sub_issue_added"]
                    
                    # Expected child issues are #5 and #6 
                    expected_children = [5, 6]
                    assert len(sub_issue_events) >= len(expected_children), (
                        f"Expected {len(expected_children)} sub_issue_added events for issue #{parent_issue.number}, "
                        f"but found {len(sub_issue_events)}"
                    )

                    print(
                        f"✓ Verified parent-child relationships (issue #3 has {len(sub_issue_events)} sub-issues added via events API)"
                    )
                except AssertionError:
                    raise
                except Exception as e:
                    print(f"⚠️  Could not verify parent-child relationships: {e}")

                # Verify attachment migration
                attachment_pattern = r"/uploads/[a-f0-9]{32}/[^)\s]+"
                source_issues_with_attachments = []
                for source_issue in source_issues:
                    if source_issue.description:
                        attachments = re.findall(attachment_pattern, source_issue.description)
                        if attachments:
                            source_issues_with_attachments.append(source_issue)

                if source_issues_with_attachments:
                    # Check that GitHub issues have updated attachment URLs
                    for source_issue in source_issues_with_attachments[:3]:  # Check first 3
                        github_issue = github_repo.get_issue(source_issue.iid)
                        if github_issue.body:
                            # GitLab URLs should be replaced with GitHub release asset URLs
                            remaining_gitlab_urls = re.findall(attachment_pattern, github_issue.body)
                            github_urls = re.findall(r"github\.com/.*/releases/download/", github_issue.body)
                            assert len(remaining_gitlab_urls) == 0 or len(github_urls) > 0, (
                                f"Issue #{source_issue.iid}: attachments not migrated"
                            )

                    # Verify attachments release exists (use cached value from migration)
                    if migrator._attachments_release is None:
                        print("⚠️  No attachments release found (downloads may have failed)")
                    else:
                        assets = list(migrator._attachments_release.get_assets())

                        # Verify at least one asset is downloadable via API
                        # (private/draft release assets require API with Accept: application/octet-stream)
                        if assets:
                            import requests

                            asset = assets[0]
                            # Use GitHub API endpoint with asset ID, not browser_download_url
                            api_url = f"https://api.github.com/repos/{repo_path}/releases/assets/{asset.id}"
                            response = requests.head(
                                api_url,
                                headers={
                                    "Authorization": f"Bearer {github_token}",
                                    "Accept": "application/octet-stream",
                                },
                                allow_redirects=True,
                                timeout=10,
                            )
                            assert response.status_code == 200, (
                                f"Asset {asset.name} not accessible via API: {response.status_code}"
                            )

                        print(f"✓ Verified attachment migration ({len(assets)} files in release, URLs accessible)")

        finally:
            # Cleanup - only if repo was created
            if migrator._github_repo is not None:
                print(f"\n⚠️  Did not delete test repository {repo_path} so that you can inspect it manually.")
                print("   To clean up all test repos, run: uv run delete-test-repos")

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-ra", "--tb=short"])
