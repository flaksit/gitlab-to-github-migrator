"""
Integration tests for GitLab to GitHub Migration Tool using real APIs

These tests connect to real GitLab and GitHub APIs to ensure the migration
functionality works correctly with actual data.

Test source: GitLab project (REQUIRED: set via SOURCE_GITLAB_TEST_PROJECT environment variable)
Test target: Temporary GitHub repo (REQUIRED: set via TARGET_GITHUB_TEST_OWNER environment variable)

Test structure:
- GitHub access test: Verify GitHub API access (no GitLab repository needed)
- Full migration tests: End-to-end migration with aspect-based assertions (isolated)
"""

import os
import random
import re
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gitlab
import gitlab.v4.objects
import pytest
from github import Auth, Github, GithubException, Repository

from gitlab_to_github_migrator import GitlabToGithubMigrator
from gitlab_to_github_migrator import github_utils as ghu
from gitlab_to_github_migrator import gitlab_utils as glu

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence


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
    return gitlab_project.issues.list(get_all=True, state="all")


@pytest.fixture(scope="module")
def gitlab_labels(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectLabel]:
    return gitlab_project.labels.list(get_all=True)


@pytest.fixture(scope="module")
def gitlab_milestones(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectMilestone]:
    return gitlab_project.milestones.list(get_all=True, state="all")


@pytest.fixture(scope="module")
def gitlab_branches(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectBranch]:
    return gitlab_project.branches.list(get_all=True)


@pytest.fixture(scope="module")
def gitlab_tags(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectTag]:
    return gitlab_project.tags.list(get_all=True)


@pytest.fixture(scope="module")
def gitlab_commits(gitlab_project: gitlab.v4.objects.Project) -> Sequence[gitlab.v4.objects.ProjectCommit]:
    return gitlab_project.commits.list(get_all=True)


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


@dataclass
class MigrationResult:
    """Result of a migration run, providing access to the migrator and its output."""

    migrator: GitlabToGithubMigrator
    report: dict[str, Any]
    github_repo: Repository.Repository
    repo_path: str
    label_translations: list[str]
    expected_label_translations: dict[str, str]


def _create_label_translations(
    gitlab_labels: Sequence[gitlab.v4.objects.ProjectLabel],
) -> tuple[list[str], dict[str, str]]:
    """Dynamically create label translation patterns for testing."""
    label_translations = []
    expected_translations = {}  # Maps source label -> expected target label

    label_names = [label.name for label in gitlab_labels]

    # Strategy 1: Find common prefixes among labels without assuming any specific format
    prefix_matches: dict[str, set[str]] = {}
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


@pytest.fixture(scope="class")
def migration_result(
    gitlab_token: str | None,
    gitlab_project_path: str,
    gitlab_labels: Sequence[gitlab.v4.objects.ProjectLabel],
    github_token: str,
    github_owner: str,
) -> Generator[MigrationResult]:
    """Run migration once and provide result to all test methods in the class."""
    repo_name = _generate_repo_name("full-migration")
    repo_path = f"{github_owner}/{repo_name}"

    label_translations, expected_translations = _create_label_translations(gitlab_labels)

    migrator = GitlabToGithubMigrator(
        gitlab_project_path=gitlab_project_path,
        github_repo_path=repo_path,
        label_translations=label_translations,
        gitlab_token=gitlab_token,
        github_token=github_token,
    )

    report = migrator.migrate()
    print(f"\n Migration completed for: {repo_path}")

    yield MigrationResult(
        migrator=migrator,
        report=report,
        github_repo=migrator.github_repo,
        repo_path=repo_path,
        label_translations=label_translations,
        expected_label_translations=expected_translations,
    )

    # Cleanup message (don't delete for manual inspection)
    print(f"\n Did not delete test repository {repo_path} so that you can inspect it manually.")
    print("   To clean up all test repos, run: uv run python -m gitlab_to_github_migrator.delete_test_repos")


@pytest.mark.integration
class TestFullMigration:
    """End-to-end migration tests split by aspect."""

    def test_migration_report(
        self,
        migration_result: MigrationResult,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
        gitlab_milestones: Sequence[gitlab.v4.objects.ProjectMilestone],
        gitlab_labels: Sequence[gitlab.v4.objects.ProjectLabel],
    ) -> None:
        """Test that migration report is correct and complete."""
        report = migration_result.report

        assert report["success"] is True, f"Migration failed: {report.get('errors', [])}"
        assert report["gitlab_project"] == migration_result.migrator.gitlab_project_path
        assert report["github_repo"] == migration_result.repo_path
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

        print("Migration report verified")
        print(f"  - Issues: {stats['gitlab_issues_total']} -> {stats['github_issues_total']}")
        print(f"  - Milestones: {stats['gitlab_milestones_total']} -> {stats['github_milestones_total']}")
        print(f"  - Labels: {stats['gitlab_labels_total']} (translated: {stats['labels_translated']})")

    def test_git_content(
        self,
        migration_result: MigrationResult,
        gitlab_branches: Sequence[gitlab.v4.objects.ProjectBranch],
        gitlab_tags: Sequence[gitlab.v4.objects.ProjectTag],
        gitlab_commits: Sequence[gitlab.v4.objects.ProjectCommit],
    ) -> None:
        """Test that git content (branches, tags, commits) was migrated correctly."""
        github_repo = migration_result.github_repo

        github_branches = list(github_repo.get_branches())
        github_tags = list(github_repo.get_tags())
        github_commits = list(github_repo.get_commits())

        assert len(github_branches) == len(gitlab_branches), (
            f"Branch count mismatch: {len(github_branches)} != {len(gitlab_branches)}"
        )
        assert len(github_tags) == len(gitlab_tags), f"Tag count mismatch: {len(github_tags)} != {len(gitlab_tags)}"
        assert len(github_commits) == len(gitlab_commits), (
            f"Commit count mismatch: {len(github_commits)} != {len(gitlab_commits)}"
        )

        print(
            f"Git content migrated ({len(github_branches)} branches, {len(github_tags)} tags, {len(github_commits)} commits)"
        )

    def test_labels(
        self,
        migration_result: MigrationResult,
    ) -> None:
        """Test that labels were migrated and translations applied correctly."""
        migrator = migration_result.migrator
        github_repo = migration_result.github_repo
        expected_translations = migration_result.expected_label_translations

        github_labels = list(github_repo.get_labels())
        assert len(migrator.label_mapping) > 0, "No labels were mapped"

        # Check label translations were applied correctly
        if expected_translations:
            github_label_names = {label.name for label in github_labels}
            for source_label, expected_target_label in expected_translations.items():
                assert expected_target_label in github_label_names, (
                    f"Expected translated label '{expected_target_label}' (from '{source_label}') not found in GitHub"
                )
                assert migrator.label_mapping.get(source_label) == expected_target_label, (
                    f"Label mapping incorrect: {source_label} -> {migrator.label_mapping.get(source_label)} "
                    f"(expected {expected_target_label})"
                )

        print(f"Verified {len(migrator.label_mapping)} labels ({len(expected_translations)} translated)")

    def test_milestones(
        self,
        migration_result: MigrationResult,
        gitlab_milestones: Sequence[gitlab.v4.objects.ProjectMilestone],
    ) -> None:
        """Test that milestones were migrated with number preservation."""
        if not gitlab_milestones:
            pytest.skip("No milestones in source project")

        github_repo = migration_result.github_repo
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

        print(f"Verified {len(gitlab_milestones)} milestones with number preservation")

    def test_issues(
        self,
        migration_result: MigrationResult,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test that issues were migrated with number preservation and correct state."""
        if not gitlab_issues:
            pytest.skip("No issues in source project")

        github_repo = migration_result.github_repo
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
        assert gitlab_open == github_open, f"Open issue mismatch: {sorted(github_open)} != {sorted(gitlab_open)}"

        gitlab_closed = {i.iid for i in gitlab_issues if i.state == "closed"}
        github_closed = {i.number for i in github_issues if i.state == "closed"}
        assert github_closed == gitlab_closed, (
            f"Closed issue mismatch: {sorted(github_closed)} != {sorted(gitlab_closed)}"
        )

        print(f"Verified {len(gitlab_issues)} issues with number preservation")

    def test_comments(
        self,
        migration_result: MigrationResult,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
    ) -> None:
        """Test that comments were migrated with correct format."""
        if not gitlab_issues:
            pytest.skip("No issues in source project")

        github_repo = migration_result.github_repo
        github_issues = list(github_repo.get_issues(state="all"))

        issues_with_comments = 0
        for github_issue in github_issues:
            comments = list(github_issue.get_comments())
            if comments:
                issues_with_comments += 1
                # Verify comment format (should have bold text, system note marker, or system notes header)
                for comment in comments:
                    assert (
                        "**" in comment.body
                        or "System note:" in comment.body
                        or "### System notes" in comment.body
                    )

        if issues_with_comments == 0:
            pytest.skip("No comments found in migrated issues")

        print(f"Verified comments on {issues_with_comments} issues")

    def test_parent_child_relationships(
        self,
        migration_result: MigrationResult,
        gitlab_graphql_client: gitlab.GraphQL,
        gitlab_project_path: str,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
        subtests: pytest.Subtests,
    ) -> None:
        """Test that parent-child relationships (sub-issues) were migrated correctly."""
        if not gitlab_issues:
            pytest.skip("No issues in source project")

        github_repo = migration_result.github_repo

        # Find parent-child relationships from GitLab
        gitlab_relationship_count = 0
        relationships_verified = 0

        for gitlab_issue in gitlab_issues:
            child_iids = glu.get_work_item_children(gitlab_graphql_client, gitlab_project_path, gitlab_issue.iid)
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
            pytest.skip("No parent-child relationships found in GitLab project")

        assert relationships_verified > 0, (
            f"Failed to verify any parent-child relationships. Expected to verify {gitlab_relationship_count} relationships."
        )

        print(
            f"Verified {relationships_verified} parent-child relationships (out of {gitlab_relationship_count} total)"
        )

    def test_attachments(
        self,
        migration_result: MigrationResult,
        gitlab_issues: Sequence[gitlab.v4.objects.ProjectIssue],
        github_token: str,
    ) -> None:
        """Test that attachments were migrated with correct URLs."""
        if not gitlab_issues:
            pytest.skip("No issues in source project")

        github_repo = migration_result.github_repo
        migrator = migration_result.migrator
        repo_path = migration_result.repo_path

        # Find issues with attachments
        attachment_pattern = r"/uploads/[a-f0-9]{32}/[^)\s]+"
        source_issues_with_attachments = []
        for source_issue in gitlab_issues:
            if source_issue.description:
                attachments = re.findall(attachment_pattern, source_issue.description)
                if attachments:
                    source_issues_with_attachments.append(source_issue)

        if not source_issues_with_attachments:
            pytest.skip("No attachments found in source project issues")

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
            pytest.skip("No attachments release found (downloads may have failed)")

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
            assert response.status_code == 200, f"Asset {asset.name} not accessible via API: {response.status_code}"

        print(f"Verified attachment migration ({len(assets)} files in release, URLs accessible)")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-ra", "--tb=short"])
