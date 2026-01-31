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
from typing import TYPE_CHECKING

import gitlab
import gitlab.v4.objects
import pytest
from github import Auth, Github, GithubException

from gitlab_to_github_migrator import GitlabToGithubMigrator
from gitlab_to_github_migrator import github_utils as ghu
from gitlab_to_github_migrator import gitlab_utils as glu

if TYPE_CHECKING:
    from collections.abc import Sequence


def _generate_repo_name(test_type: str = "generic") -> str:
    """Generate a unique test repository name.

    Format: gl2ghmigr-<test_type>-test-<hash>
    """
    random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"gl2ghmigr-{test_type}-test-{random_suffix}"


@pytest.fixture(scope="module")
def gitlab_token() -> str | None:
    """Get GitLab API token."""
    return glu.get_readonly_token()


@pytest.fixture(scope="module")
def gitlab_client(gitlab_token: str | None) -> gitlab.Gitlab:
    """Create GitLab API client."""
    if gitlab_token:
        return gitlab.Gitlab("https://gitlab.com", private_token=gitlab_token)
    return gitlab.Gitlab()  # Anonymous access


@pytest.fixture(scope="module")
def gitlab_graphql_client(gitlab_token: str | None) -> gitlab.GraphQL:
    """Create GitLab GraphQL API client."""
    return gitlab.GraphQL(url="https://gitlab.com", token=gitlab_token)


@pytest.fixture(scope="module")
def gitlab_project_path() -> str:
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
def gitlab_project(gitlab_client: gitlab.Gitlab, gitlab_project_path: str) -> gitlab.v4.objects.Project:
    project = gitlab_client.projects.get(gitlab_project_path)
    assert project.path_with_namespace == gitlab_project_path
    return project


@pytest.fixture(scope="module")
def gitlab_issues(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectIssue]:
    issues = gitlab_project.issues.list(get_all=True, state="all")
    assert len(issues) > 0, "No issues found in source project"
    return issues


@pytest.fixture(scope="module")
def github_token() -> str:
    """Get GitHub API token."""
    return ghu.get_token()


@pytest.fixture(scope="module")
def github_client(github_token: str) -> Github:
    """Create GitHub API client."""
    return Github(auth=Auth.Token(github_token))


@pytest.fixture(scope="module")
def github_owner() -> str:
    """Get the target GitHub org/user from environment."""
    owner = os.environ.get("TARGET_GITHUB_TEST_OWNER")
    if not owner:
        msg = "TARGET_GITHUB_TEST_OWNER environment variable is required. Example: export TARGET_GITHUB_TEST_OWNER='your-org-or-username'"
        raise ValueError(msg)
    return owner


@pytest.mark.integration
class TestGitHubAccess:
    """Tests that only read from GitHub - no GitLab repository needed."""

    def test_github_api_access(
        self,
        github_client: Github,
        github_owner: str,
    ) -> None:
        """Test that we can access GitHub API."""
        github_client.get_user()

        # Test organization or user access
        try:
            github_client.get_organization(github_owner)
        except GithubException:
            # Not an organization, might be a user account
            try:
                github_client.get_user(github_owner)
            except GithubException as user_err:
                pytest.fail(f"Failed to access '{github_owner}' as organization or user: {user_err}")


@pytest.mark.integration
class TestReadOnlyGitlabAccess:
    """Tests that only read from GitLab - no GitHub repository needed."""

    def test_gitlab_source_project_access(
        self,
        gitlab_project: gitlab.v4.objects.Project,
    ) -> None:
        """Test that we can access the source GitLab project."""

        # Test that we can read milestones, and labels
        # Issue reading is tested in more detail in another test
        gitlab_project.milestones.list(per_page=2, get_all=False, state="all")
        gitlab_project.labels.list(per_page=2, get_all=False)

    def test_issue_read(
        self,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test reading issues from real GitLab source."""
        if not gitlab_issues:
            pytest.skip("No issues found in source project")

        for issue in gitlab_issues[:2]:
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
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test reading cross-linked issues from GitLab."""
        for issue in gitlab_issues:
            links = issue.links.list(get_all=True)
            if links:
                # Test link properties (data is directly on link object)
                for link in links[:2]:
                    assert hasattr(link, "iid")
                    assert hasattr(link, "title")
                    assert hasattr(link, "link_type")

                break
        else:
            pytest.skip("No cross-linked issues found in source project")

    def test_gitlab_attachment_detection(
        self,
        gitlab_client: gitlab.Gitlab,
        gitlab_project: gitlab.v4.objects.Project,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test finding and accessing GitLab attachments via the REST API."""
        # Pattern to extract secret and filename from attachment URLs
        attachment_pattern = r"/uploads/([a-f0-9]{32})/([^)\s]+)"

        for issue in gitlab_issues:
            if issue.description:
                attachments = re.findall(attachment_pattern, issue.description)
                if attachments:
                    break
        else:
            pytest.skip("No attachments found in source project issues")

        # Verify that we can download at least one attachment using the GitLab API
        import requests

        download_successful = False
        last_error: Exception | None = None
        for secret, filename in attachments[:3]:  # Test up to 3 attachments
            try:
                content, _content_type = glu.download_attachment(gitlab_client, gitlab_project, secret, filename)
                if len(content) > 0:
                    download_successful = True
                    break
            except requests.RequestException as e:
                last_error = e
                continue

        assert download_successful, (
            f"Could not download any of the {len(attachments)} detected attachments. Last error: {last_error}"
        )

    def test_graphql_work_items_api_functionality(
        self,
        gitlab_project_path: str,
        gitlab_graphql_client: gitlab.GraphQL,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test the GitLab GraphQL Work Items API functionality using gitlab_utils directly."""
        # Use the utility function to get parent-child relationships
        for issue in gitlab_issues:
            child_iids = glu.get_work_item_children(gitlab_graphql_client, gitlab_project_path, issue.iid)
            if child_iids:
                # We found at least one issue with children - verify structure
                for child_iid in child_iids:
                    assert int(child_iid) > 0, f"Invalid child IID: {child_iid} for parent #{issue.iid}"
                break
        else:
            pytest.skip("No parent-child relationships found in GitLab project")

    def test_closed_issues_retrieval(
        self,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test that closed issues are properly retrieved."""
        closed_count = len([issue for issue in gitlab_issues if issue.state == "closed"])
        opened_count = len([issue for issue in gitlab_issues if issue.state == "opened"])

        assert closed_count > 0, "No closed issues found - test project should have closed issues"
        assert opened_count > 0, "No opened issues found - test project should have open issues"
        assert len(gitlab_issues) == closed_count + opened_count

    def test_closed_milestones_retrieval(
        self,
        gitlab_project: gitlab.v4.objects.Project,
    ) -> None:
        """Test that closed milestones are properly retrieved."""
        all_milestones = gitlab_project.milestones.list(get_all=True, state="all")

        if all_milestones:
            active_milestones = [m for m in all_milestones if m.state == "active"]
            closed_milestones = [m for m in all_milestones if m.state == "closed"]
            assert len(all_milestones) == len(active_milestones) + len(closed_milestones)


@pytest.mark.integration
class TestFullMigration:
    """End-to-end migration test with comprehensive assertions."""

    @staticmethod
    def _create_label_translations(
        gitlab_labels: Sequence[gitlab.v4.objects.ProjectLabel],
    ) -> tuple[list[str], dict[str, str]]:
        """Dynamically create label translation patterns for testing."""
        label_translations = []
        expected_translations = {}  # Maps source label -> expected target label

        label_names = [label.name for label in gitlab_labels]

        # Strategy 1: Find common prefixes among labels without assuming any specific format
        prefix_matches = {}
        for label_name in label_names:
            if label_name:  # Skip empty labels
                prefix = label_name[0]  # First letter as prefix
                if prefix not in prefix_matches:
                    prefix_matches[prefix] = set()
                prefix_matches[prefix].add(label_name)

        # Find a prefix that matches multiple labels (indicating a pattern)
        for prefix, matching_labels in prefix_matches.items():
            if len(matching_labels) >= 2:
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
            if label_name not in expected_translations:
                # Create exact translation: "original:translated"
                translated_name = f"renamed-{label_name}"
                pattern = f"{label_name}:{translated_name}"
                label_translations.append(pattern)
                expected_translations[label_name] = translated_name
                break

        return label_translations, expected_translations

    def test_full_migration(  # noqa: PLR0915 - intentionally comprehensive test
        self,
        gitlab_token: str | None,
        gitlab_graphql_client: gitlab.GraphQL,
        gitlab_project_path: str,
        gitlab_project: gitlab.v4.objects.Project,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
        github_token: str,
        github_owner: str,
        subtests: pytest.Subtests,
    ) -> None:
        """Test complete migration workflow using the main migrate() method."""
        repo_name = _generate_repo_name("full-migration")
        repo_path = f"{github_owner}/{repo_name}"

        # Get source data for comparison before migration
        gitlab_labels = gitlab_project.labels.list(get_all=True)
        gitlab_milestones = gitlab_project.milestones.list(get_all=True, state="all")
        gitlab_branches = gitlab_project.branches.list(get_all=True)
        gitlab_tags = gitlab_project.tags.list(get_all=True)
        gitlab_commits = gitlab_project.commits.list(get_all=True)

        # Dynamically discover label patterns and create translations
        # Find common prefixes among labels without assuming any specific format
        label_translations, expected_translations = self._create_label_translations(gitlab_labels)

        migrator = GitlabToGithubMigrator(
            gitlab_project_path=gitlab_project_path,
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
            assert report["gitlab_project"] == gitlab_project_path
            assert report["github_repo"] == repo_path
            assert "statistics" in report

            stats = report["statistics"]
            assert stats["gitlab_issues_total"] == len(gitlab_issues)
            assert stats["gitlab_milestones_total"] == len(gitlab_milestones)
            assert stats["gitlab_labels_total"] == len(gitlab_labels)

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

            assert len(github_branches) == len(gitlab_branches), (
                f"Branch count mismatch: {len(github_branches)} != {len(gitlab_branches)}"
            )
            assert len(github_tags) == len(gitlab_tags), (
                f"Tag count mismatch: {len(github_tags)} != {len(gitlab_tags)}"
            )
            assert len(github_commits) == len(gitlab_commits), (
                f"Commit count mismatch: {len(github_commits)} != {len(gitlab_commits)}"
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
            if gitlab_milestones:
                github_milestones = list(github_repo.get_milestones(state="all"))
                # After cleanup_placeholders(), only real milestones remain
                real_milestones = [m for m in github_milestones if m.title != "Placeholder Milestone"]
                assert len(real_milestones) == len(gitlab_milestones), (
                    f"Milestone count mismatch: {len(real_milestones)} != {len(gitlab_milestones)}"
                )

                # Verify milestone number preservation
                for source_ms in gitlab_milestones:
                    matching = [m for m in github_milestones if m.title == source_ms.title]
                    assert len(matching) == 1, f"Milestone '{source_ms.title}' not found"
                    assert matching[0].number == source_ms.iid, (
                        f"Milestone number not preserved: {matching[0].number} != {source_ms.iid}"
                    )

                print(f"✓ Verified {len(gitlab_milestones)} milestones with number preservation")

            # Verify issues
            if gitlab_issues:
                github_issues = list(github_repo.get_issues(state="all"))
                # Placeholders are now deleted via GraphQL API, so all issues should be real
                assert len(github_issues) == len(gitlab_issues), (
                    f"Issue count mismatch: {len(github_issues)} != {len(gitlab_issues)}"
                )

                # Verify issue number preservation
                for source_issue in gitlab_issues:
                    matching = [i for i in github_issues if i.number == source_issue.iid]
                    assert len(matching) == 1, f"GitLab issue #{source_issue.iid} not found in GitHub"
                for github_issue in github_issues:
                    matching = [i for i in gitlab_issues if i.iid == github_issue.number]
                    assert len(matching) == 1, f"GitHub issue #{github_issue.number} has no matching GitLab issue"

                # Verify issue state preservation
                gitlab_open = {i.iid for i in gitlab_issues if i.state == "opened"}
                github_open = {i.number for i in github_issues if i.state == "open"}
                assert gitlab_open == github_open, (
                    f"Open issue mismatch: {sorted(github_open)} != {sorted(gitlab_open)}"
                )
                gitlab_closed = {i.iid for i in gitlab_issues if i.state == "closed"}
                github_closed = {i.number for i in github_issues if i.state == "closed"}
                assert github_closed == gitlab_closed, (
                    f"Closed issue mismatch: {sorted(github_closed)} != {sorted(gitlab_closed)}"
                )

                print(f"✓ Verified {len(gitlab_issues)} issues with number preservation")

                # Verify some issues have comments
                # TODO extended verification of all comments on all issues
                issues_with_comments = 0
                for github_issue in github_issues:
                    comments = list(github_issue.get_comments())
                    if comments:
                        issues_with_comments += 1
                        # Verify comment format
                        for comment in comments:
                            assert "**" in comment.body or "System note:" in comment.body

                if issues_with_comments > 0:
                    print(f"✓ Verified comments on {issues_with_comments} issues")

                # Verify parent-child relationships (sub-issues)
                # Dynamically get parent-child relationships from GitLab and verify they exist in GitHub
                gitlab_relationship_count = 0
                relationships_verified = 0
                for gitlab_issue in gitlab_issues:
                    child_iids = glu.get_work_item_children(
                        gitlab_graphql_client, gitlab_project_path, gitlab_issue.iid
                    )
                    if child_iids:
                        gitlab_relationship_count += len(child_iids)

                        # Get corresponding GitHub issue
                        parent_github_issue = github_repo.get_issue(int(gitlab_issue.iid))

                        # Get sub-issues directly from PyGithub
                        github_sub_issues = list(parent_github_issue.get_sub_issues())
                        github_sub_issue_numbers = {sub.number for sub in github_sub_issues}

                        with subtests.test(f"Parent #{gitlab_issue.iid} has children {child_iids}"):
                            # Verify all children exist in GitHub
                            missing_children = set(child_iids) - github_sub_issue_numbers
                            assert not missing_children, (
                                f"Parent issue #{gitlab_issue.iid} missing sub-issues {missing_children} in GitHub"
                            )
                            relationships_verified += len(child_iids)

                if not gitlab_relationship_count:
                    # TODO Skip rather than fail if no relationships exist.
                    #       However, we should not skip the entire full migration test, so we should rather isolate
                    #       this into its own test case. Or use subtests.
                    print("⚠️  No parent-child relationships found in GitLab project")
                else:
                    assert relationships_verified > 0, (
                        f"Failed to verify any parent-child relationships. "
                        f"Expected to verify {gitlab_relationship_count} relationships."
                    )

                    print(
                        f"\n✓ Verified {relationships_verified} parent-child relationships "
                        f"(out of {gitlab_relationship_count} total)"
                    )

                # Verify attachment migration
                attachment_pattern = r"/uploads/[a-f0-9]{32}/[^)\s]+"
                source_issues_with_attachments = []
                for source_issue in gitlab_issues:
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
                    if migrator.attachment_handler._release is None:
                        print("⚠️  No attachments release found (downloads may have failed)")
                    else:
                        assets = list(migrator.attachment_handler._release.get_assets())

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
