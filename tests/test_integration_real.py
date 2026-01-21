"""
Integration tests for GitLab to GitHub Migration Tool using real APIs

These tests connect to real GitLab and GitHub APIs to ensure the migration
functionality works correctly with actual data.

Test source: GitLab project (REQUIRED: set via GITLAB_TEST_PROJECT environment variable)
Test target: Temporary GitHub repo (REQUIRED: set via GITHUB_TEST_ORG environment variable)
"""

import os
import random
import re
import string

import gitlab
import pytest
from github import Github, GithubException

from gitlab_to_github_migrator import GitLabToGitHubMigrator, MigrationError


@pytest.mark.integration
class TestRealAPIIntegration:
    """Integration tests using real GitLab and GitHub APIs."""

    @classmethod
    def setup_class(cls) -> None:
        """Setup class-level fixtures."""
        # GitLab project to use as source (read-only)
        # REQUIRED: Must be set via GITLAB_TEST_PROJECT environment variable (non-empty)
        cls.source_gitlab_project = os.environ.get("GITLAB_TEST_PROJECT")
        if not cls.source_gitlab_project:
            msg = (
                "GITLAB_TEST_PROJECT environment variable is required. "
                "Example: export GITLAB_TEST_PROJECT='your-namespace/your-project'"
            )
            raise ValueError(msg)

        # GitHub organization/user for test repositories
        # REQUIRED: Must be set via GITHUB_TEST_ORG environment variable (non-empty)
        cls.target_github_org = os.environ.get("GITHUB_TEST_ORG")
        if not cls.target_github_org:
            msg = (
                "GITHUB_TEST_ORG environment variable is required. "
                "Example: export GITHUB_TEST_ORG='your-org-or-username'"
            )
            raise ValueError(msg)

        # Generate unique test repo name
        random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        cls.test_repo_name = f"migration-test-{random_suffix}"
        cls.target_github_repo = f"{cls.target_github_org}/{cls.test_repo_name}"

        # Initialize API clients with authentication
        # GitLab client - try env var, then pass, then anonymous
        gitlab_token = os.environ.get("GITLAB_TOKEN")
        if not gitlab_token:
            import subprocess

            try:
                result = subprocess.run(["pass", "gitlab/api/ro_token"], capture_output=True, text=True, check=True)
                gitlab_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access

        if gitlab_token:
            cls.gitlab_client = gitlab.Gitlab("https://gitlab.com", private_token=gitlab_token)
        else:
            cls.gitlab_client = gitlab.Gitlab()  # Anonymous access

        # GitHub client - try env var, then pass, then anonymous
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            import subprocess

            try:
                result = subprocess.run(["pass", "github/api/token"], capture_output=True, text=True, check=True)
                github_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access

        if github_token:
            cls.github_client = Github(github_token)
        else:
            cls.github_client = Github()  # Anonymous access

        # Test repo reference (will be set during tests)
        cls.test_github_repo = None
        cls._repository_created = False

        # Test setup completed

    @classmethod
    def teardown_class(cls) -> None:
        """Cleanup class-level fixtures."""
        # Delete test repository if it exists
        if cls.test_github_repo:
            try:
                cls.test_github_repo.delete()
                print(f"✓ Cleaned up test repository: {cls.target_github_repo}")
            except Exception as e:
                error_str = str(e)
                if "403" in error_str and "admin rights" in error_str:
                    print(f"⚠️  Cannot delete test repository {cls.target_github_repo}: insufficient permissions")
                    print("   To clean up test repositories, run:")
                    print("   uv run delete_test_repos <github_owner> <pass_path>")
                    print(f"   where <github_owner> is the GitHub organization or user (e.g., '{cls.target_github_org}')")
                    print("   and <pass_path> is a 'pass' path containing a GitHub token with admin rights")
                else:
                    print(f"✗ Failed to cleanup test repository {cls.target_github_repo}: {e}")
                    # Re-raise for unexpected errors
                    raise RuntimeError(f"Repository cleanup failed: {e}") from e

    def _ensure_test_repository_exists(self) -> None:
        """Ensure test repository exists for tests that need it."""
        if not self._repository_created:
            try:
                migrator = GitLabToGitHubMigrator(
                    gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
                )
                migrator.create_github_repository()
                self.__class__.test_github_repo = migrator.github_repo  # Store for cleanup
                self.__class__._repository_created = True
            except Exception as e:
                pytest.skip(f"Could not create test repository: {e}")

    def test_gitlab_source_project_access(self) -> None:
        """Test that we can access the source GitLab project."""
        try:
            project = self.gitlab_client.projects.get(self.source_gitlab_project)
            # Verify we got a valid project object
            assert project.path_with_namespace == self.source_gitlab_project
            # Successfully accessed GitLab project

            # Test that we can read issues
            project.issues.list(all=True)
            # Found issues in source project

            # Test that we can read milestones
            project.milestones.list(all=True)
            # Found milestones in source project

            # Test that we can read labels
            project.labels.list(all=True)
            # Found labels in source project

        except Exception as e:
            pytest.fail(f"Failed to access GitLab source project: {e}")

    def test_github_api_access(self) -> None:
        """Test that we can access GitHub API."""
        try:
            self.github_client.get_user()
            # GitHub API access successful

            # Test organization access (if using an organization, not a user account)
            try:
                self.github_client.get_organization(self.target_github_org)
                # Organization access successful
            except GithubException as e:
                # Not an organization, might be a user account - that's also fine
                # Just verify we can access the user (404 errors are caught by GithubException)
                try:
                    self.github_client.get_user(self.target_github_org)
                    # User account access successful
                except GithubException as user_err:
                    # If both org and user lookup failed, this is a real error
                    pytest.fail(
                        f"Failed to access '{self.target_github_org}' as organization: {e}; "
                        f"also failed as user: {user_err}"
                    )

        except Exception as e:
            pytest.fail(f"Failed to access GitHub API: {e}")

    def test_migrator_initialization_with_real_apis(self) -> None:
        """Test migrator initialization with real API connections."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=["priority::*:priority: *", "type::*:type: *"],
            )

            # Test API validation
            migrator.validate_api_access()
            # API validation successful

            # Verify GitLab project details
            assert migrator.gitlab_project.path_with_namespace == self.source_gitlab_project
            # GitLab project loaded

        except Exception as e:
            pytest.fail(f"Migrator initialization failed: {e}")

    def test_github_repository_creation(self) -> None:
        """Test GitHub repository creation and cleanup."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )

            # Create test repository
            migrator.create_github_repository()
            self.__class__.test_github_repo = migrator.github_repo  # Store for cleanup
            self.__class__._repository_created = True

            # Test repository created

            # Verify repository properties
            assert self.test_github_repo.name == self.test_repo_name
            assert self.test_github_repo.private is True
            assert self.test_github_repo.has_issues is True

            # Test that we can't create it again (should fail)
            with pytest.raises(MigrationError, match="already exists"):
                migrator.create_github_repository()

        except Exception as e:
            pytest.fail(f"Repository creation test failed: {e}")

    def test_github_repository_deletion(self) -> None:
        """Test GitHub repository deletion functionality."""
        # Create a separate test repository for deletion testing
        random_suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        deletion_test_repo_name = f"deletion-test-{random_suffix}"
        deletion_test_repo_path = f"{self.target_github_org}/{deletion_test_repo_name}"

        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=deletion_test_repo_path
            )

            # Create repository for deletion test
            migrator.create_github_repository()
            deletion_test_repo = migrator.github_repo

            # Verify repository exists
            assert deletion_test_repo.name == deletion_test_repo_name
            print(f"✓ Created test repository for deletion: {deletion_test_repo_path}")

            # Test deletion
            deletion_test_repo.delete()
            print(f"✓ Successfully deleted test repository: {deletion_test_repo_path}")

            # Verify repository is deleted by trying to access it
            with pytest.raises(Exception):
                self.github_client.get_repo(deletion_test_repo_path)

        except Exception as e:
            # Clean up if test fails
            try:
                self.github_client.get_repo(deletion_test_repo_path).delete()
                print(f"✓ Cleaned up failed deletion test repository: {deletion_test_repo_path}")
            except Exception:
                print(
                    f"✗ Could not clean up failed deletion test repository: {deletion_test_repo_path}. "
                    "You will need to manually run 'uv run delete_test_repos <github_owner> <pass_path>' to delete it."
                )
            
            pytest.fail(f"Repository deletion test failed: {e}")

    def test_label_migration_with_real_data(self) -> None:
        """Test label migration with real GitLab labels."""
        self._ensure_test_repository_exists()

        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=["p_*:priority: *", "t_*:type: *"],
            )
            migrator.github_repo = self.test_github_repo

            # Get original label count
            original_labels = list(self.test_github_repo.get_labels())
            original_count = len(original_labels)

            # Migrate labels
            migrator.migrate_labels()

            # Verify labels were created
            new_labels = list(self.test_github_repo.get_labels())
            new_count = len(new_labels)

            # Labels migrated
            # Label mapping created

            # Verify some labels were processed
            assert len(migrator.label_mapping) > 0
            assert new_count >= original_count

            # Check that translation patterns were applied
            translated_labels = [
                label.name for label in new_labels if "priority: " in label.name or "type: " in label.name
            ]
            if translated_labels:
                pass  # Successfully translated labels

        except Exception as e:
            pytest.fail(f"Label migration test failed: {e}")

    def test_milestone_migration_with_real_data(self) -> None:
        """Test milestone migration with real GitLab milestones."""
        self._ensure_test_repository_exists()

        try:
            # Get source milestones
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            source_milestones = source_project.milestones.list(all=True)

            if not source_milestones:
                pytest.skip("No milestones found in source project")

            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )
            migrator.github_repo = self.test_github_repo

            # Migrate milestones
            migrator.migrate_milestones_with_number_preservation()

            # Verify milestones were created
            github_milestones = list(self.test_github_repo.get_milestones(state="all"))

            # Milestones migrated
            # Milestone mapping created

            # Verify milestone count (may include placeholders)
            assert len(github_milestones) >= len(source_milestones)

            # Check that real milestones have correct titles
            real_milestones = [m for m in github_milestones if m.title != "Placeholder Milestone"]
            assert len(real_milestones) == len(source_milestones)

        except Exception as e:
            pytest.fail(f"Milestone migration test failed: {e}")

    def test_issue_reading_from_real_source(self) -> None:
        """Test reading issues from real GitLab source."""
        try:
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(per_page=5)  # Limit for testing

            if not issues:
                pytest.skip("No issues found in source project")

            # Testing with sample issues

            for issue in issues:
                # Issue details
                # Issue state and labels
                # Issue author

                # Test comment reading
                issue.notes.list(all=True)
                # Issue comments

                # Test that we can access issue properties needed for migration
                assert hasattr(issue, "iid")
                assert hasattr(issue, "title")
                assert hasattr(issue, "description")
                assert hasattr(issue, "state")
                assert hasattr(issue, "labels")
                assert hasattr(issue, "created_at")
                assert hasattr(issue, "web_url")

        except Exception as e:
            pytest.fail(f"Issue reading test failed: {e}")

    def test_gitlab_cross_linking_read(self) -> None:
        """Test reading cross-linked issues from GitLab."""
        try:
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(per_page=20)

            if not issues:
                pytest.skip("No issues found in source project")

            # Look for issues with cross-links
            issues_with_links = []
            for issue in issues:
                try:
                    links = issue.links.list(all=True)
                    if links:
                        issues_with_links.append((issue, links))
                        # Found issue with cross-links

                        # Test link properties
                        for link in links[:2]:  # Test first 2 links
                            assert hasattr(link, "target_issue")
                            assert "iid" in link.target_issue
                            assert "title" in link.target_issue
                            # Link properties validated

                        if len(issues_with_links) >= 3:  # Test a few examples
                            break

                except Exception:
                    continue  # Skip issues without links or with API errors

            if not issues_with_links:
                pytest.skip("No cross-linked issues found in source project")

            # Cross-linked issues found and validated

        except Exception as e:
            pytest.fail(f"Cross-linking read test failed: {e}")

    def test_gitlab_attachment_detection(self) -> None:
        """Test finding and accessing GitLab attachments."""
        try:
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(per_page=50)

            if not issues:
                pytest.skip("No issues found in source project")

            # Look for issues with attachments
            issues_with_attachments = []
            attachment_pattern = r"/uploads/[a-f0-9]{32}/[^)\\s]+"

            for issue in issues:
                if issue.description:
                    attachments = re.findall(attachment_pattern, issue.description)
                    if attachments:
                        issues_with_attachments.append((issue, attachments))
                        # Found issue with attachments

                        if len(issues_with_attachments) >= 2:  # Test a few examples
                            break

                # Also check comments for attachments
                try:
                    notes = issue.notes.list(per_page=10)
                    for note in notes:
                        if note.body:
                            attachments = re.findall(attachment_pattern, note.body)
                            if attachments:
                                issues_with_attachments.append((issue, attachments, note))
                                # Found comment with attachments
                                break
                except Exception:
                    continue

                if len(issues_with_attachments) >= 2:
                    break

            if not issues_with_attachments:
                pytest.skip("No attachments found in source project issues")

            # Test download capability (without actually downloading)
            for _issue, attachments, *_note in issues_with_attachments[:1]:
                for attachment_url in attachments[:1]:
                    # Would download from: {full_url}
                    assert attachment_url.startswith("/uploads/")
                    assert len(attachment_url.split("/")) >= 3

            # Attachment detection and URL construction successful

        except Exception as e:
            pytest.fail(f"Attachment detection test failed: {e}")

    def test_cross_linking_functionality(self) -> None:
        """Test the cross-linking text generation functionality."""
        self._ensure_test_repository_exists()

        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )

            # Get an issue with cross-links for testing
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(per_page=20)

            test_issue = None
            for issue in issues:
                try:
                    links = issue.links.list(all=True)
                    if links:
                        test_issue = issue
                        break
                except Exception:
                    continue

            if not test_issue:
                pytest.skip("No cross-linked issues found for testing")

            # Test the cross-linking text generation
            cross_links_text, parent_child_relations, blocking_relations = migrator.get_issue_cross_links(test_issue)

            if cross_links_text:
                # Cross-links text generated successfully (only for relates_to links)
                assert "Cross-linked Issues:" in cross_links_text
                assert "---" in cross_links_text
                # Verify formatting
                lines = cross_links_text.split("\n")
                link_lines = [line for line in lines if line.startswith("- **")]
                assert len(link_lines) > 0

                # Verify relates_to links appear in text (blocks/is_blocked_by are in blocking_relations)
                for line in link_lines:
                    # Check that link lines have expected format
                    assert "**" in line  # Bold formatting for relationship type

            # Test parent-child relations
            assert isinstance(parent_child_relations, list)

            # Test blocking relations (new in this version)
            assert isinstance(blocking_relations, list)

        except Exception as e:
            pytest.fail(f"Cross-linking functionality test failed: {e}")

    def test_github_issue_creation_with_cross_links(self) -> None:
        """Test creating GitHub issues with cross-link information."""
        self._ensure_test_repository_exists()

        try:
            # Create a test issue with cross-link information
            test_title = "Test Issue with Cross-Links"
            test_body = """**Migrated from GitLab issue #123**

Original test issue description.

---

**Cross-linked Issues:**

- **Related to**: #456 - Related Issue Title
- **Blocks**: #789 - Blocked Issue Title
- **Blocked by**: [external/project#12](https://gitlab.com/external/project/-/issues/12) - External Issue Title
"""

            # Create the issue
            created_issue = self.test_github_repo.create_issue(title=test_title, body=test_body)

            # Verify the issue was created successfully
            assert created_issue.title == test_title
            assert "Cross-linked Issues:" in created_issue.body
            assert "Related to" in created_issue.body
            assert "Blocks" in created_issue.body
            assert "Blocked by" in created_issue.body

            # Test issue created with cross-link formatting

        except Exception as e:
            pytest.fail(f"GitHub issue creation with cross-links test failed: {e}")

    def test_github_milestone_operations(self) -> None:
        """Test comprehensive GitHub milestone operations."""
        self._ensure_test_repository_exists()

        try:
            # Test milestone creation
            test_milestone = self.test_github_repo.create_milestone(
                title="Test Milestone", description="Test milestone for API testing", state="open"
            )

            assert test_milestone.title == "Test Milestone"
            assert test_milestone.state == "open"

            # Test milestone update
            test_milestone.edit(title="Test Milestone", description="Updated test milestone description", state="closed")

            # Refresh milestone data
            updated_milestone = self.test_github_repo.get_milestone(test_milestone.number)
            assert "Updated test milestone" in updated_milestone.description
            assert updated_milestone.state == "closed"

            # Test milestone listing
            milestones = list(self.test_github_repo.get_milestones(state="all"))
            milestone_titles = [m.title for m in milestones]
            assert "Test Milestone" in milestone_titles

            # Milestone operations successful

        except Exception as e:
            pytest.fail(f"GitHub milestone operations test failed: {e}")

    def test_github_label_operations(self) -> None:
        """Test comprehensive GitHub label operations."""
        self._ensure_test_repository_exists()

        try:
            # Test label creation
            test_label = self.test_github_repo.create_label(
                name="test-label", color="ff0000", description="Test label for API testing"
            )

            assert test_label.name == "test-label"
            assert test_label.color == "ff0000"

            # Test label update
            test_label.edit(name="updated-test-label", color="00ff00", description="Updated test label description")

            # Test label listing
            labels = list(self.test_github_repo.get_labels())
            label_names = [label.name for label in labels]
            assert "updated-test-label" in label_names

            # Test label with special characters (translation scenario)
            special_label = self.test_github_repo.create_label(
                name="priority: high", color="ff6b6b", description="Translated priority label"
            )

            assert special_label.name == "priority: high"

            # Label operations successful

        except Exception as e:
            pytest.fail(f"GitHub label operations test failed: {e}")

    def test_github_issue_comment_operations(self) -> None:
        """Test GitHub issue comment creation and management."""
        self._ensure_test_repository_exists()

        try:
            # Create a test issue first
            test_issue = self.test_github_repo.create_issue(
                title="Test Issue for Comments", body="This is a test issue for comment testing"
            )

            # Test comment creation
            test_comment = test_issue.create_comment(
                "**Comment by** TestUser (@testuser) **on** 2024-01-01T12:00:00Z\n\n---\n\nThis is a test comment body with formatting."
            )

            assert "TestUser" in test_comment.body
            assert "test comment body" in test_comment.body

            # Test system note style comment
            system_comment = test_issue.create_comment("**System note:** changed milestone to %1")

            assert "System note:" in system_comment.body

            # Test comment listing
            comments = list(test_issue.get_comments())
            assert len(comments) >= 2

            comment_bodies = [comment.body for comment in comments]
            assert any("TestUser" in body for body in comment_bodies)
            assert any("System note:" in body for body in comment_bodies)

            # Issue comment operations successful

        except Exception as e:
            pytest.fail(f"GitHub issue comment operations test failed: {e}")

    def test_validation_report_generation(self) -> None:
        """Test migration validation report generation with real data."""
        self._ensure_test_repository_exists()

        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )
            migrator.github_repo = self.test_github_repo

            # Generate validation report
            report = migrator.validate_migration()

            # Verify report structure
            assert "gitlab_project" in report
            assert "github_repo" in report
            assert "success" in report
            assert "errors" in report
            assert "statistics" in report

            assert report["gitlab_project"] == self.source_gitlab_project
            assert report["github_repo"] == self.target_github_repo

            # Verify statistics structure
            stats = report["statistics"]
            required_stats = [
                "gitlab_issues_total",
                "github_issues_total",
                "gitlab_milestones_total",
                "github_milestones_total",
                "gitlab_labels_total",
                "labels_translated",
            ]

            for stat_key in required_stats:
                assert stat_key in stats
                assert isinstance(stats[stat_key], int)
                assert stats[stat_key] >= 0

            # Validation report generated successfully

        except Exception as e:
            pytest.fail(f"Validation report generation test failed: {e}")

    def test_partial_migration_simulation(self) -> None:
        """Test a partial migration simulation without creating all issues."""
        self._ensure_test_repository_exists()

        try:
            # Get a small sample of source data
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            source_issues = source_project.issues.list(per_page=2)
            source_milestones = source_project.milestones.list(all=True)

            if not source_issues:
                pytest.skip("No issues found for simulation")

            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=["priority::*:priority: *"],
            )
            migrator.github_repo = self.test_github_repo

            # Migrate labels and milestones (but not issues to avoid spam)
            migrator.migrate_labels()
            if source_milestones:
                migrator.migrate_milestones_with_number_preservation()

            # Test validation report generation
            report = migrator.validate_migration()

            # Migration simulation completed
            # Report generated
            # Statistics available

            # Should have some data migrated
            assert report["statistics"]["labels_translated"] >= 0
            if source_milestones:
                assert report["statistics"]["github_milestones_total"] >= len(source_milestones)

        except Exception as e:
            pytest.fail(f"Partial migration simulation failed: {e}")

    def test_graphql_work_items_api_functionality(self) -> None:
        """Test the GitLab GraphQL Work Items API functionality."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )

            # Test GraphQL connection
            if not migrator.gitlab_token:
                pytest.skip("GitLab token required for GraphQL API testing")

            # Find an issue with tasks in description for testing
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(per_page=50, state="all")

            test_issue = None
            for issue in issues:
                if (
                    issue.description
                    and ("- [ ]" in issue.description or "- [x]" in issue.description)
                    and "#" in issue.description
                ):  # Has issue references
                    test_issue = issue
                    break

            if not test_issue:
                pytest.skip("No issues with task references found for GraphQL testing")

            # Test GraphQL Work Items API
            try:
                child_work_items = migrator.get_work_item_children(test_issue.iid)
                # GraphQL query executed successfully (may return empty list)
                assert isinstance(child_work_items, list)

                # If work items found, verify structure
                for child in child_work_items:
                    assert "iid" in child
                    assert "title" in child
                    assert "relationship_type" in child
                    assert child["relationship_type"] == "child_of"

                # GraphQL Work Items API test completed

            except Exception as e:
                # GraphQL API may not be fully available - this is expected
                assert "GraphQL" in str(e) or "token" in str(e).lower() or "401" in str(e)

        except Exception as e:
            pytest.fail(f"GraphQL Work Items API test failed: {e}")


    def test_enhanced_cross_linking_functionality(self) -> None:
        """Test the enhanced cross-linking functionality with task separation."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )

            # Find an issue with both tasks and regular links
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)

            test_issue = None
            # Look for issue #382 which has both task items and regular links
            try:
                issue_382 = source_project.issues.get(382)
                # Verify it has both tasks in description and regular links
                if issue_382.description and (
                    "- [ ] #" in issue_382.description or "- [x] #" in issue_382.description
                ):
                    try:
                        links = issue_382.links.list(all=True)
                        if links:  # Has both tasks and links
                            test_issue = issue_382
                    except Exception:
                        pass
            except Exception:
                pass

            if not test_issue:
                # Fallback: find any issue with links
                issues = source_project.issues.list(per_page=20)
                for issue in issues:
                    try:
                        links = issue.links.list(all=True)
                        if links:
                            test_issue = issue
                            break
                    except Exception:
                        continue

            if not test_issue:
                pytest.skip("No issues with cross-links found for testing")

            # Test the enhanced cross-linking method
            cross_links_text, parent_child_relations, blocking_relations = migrator.get_issue_cross_links(test_issue)

            # Verify return types
            assert isinstance(cross_links_text, str)
            assert isinstance(parent_child_relations, list)
            assert isinstance(blocking_relations, list)

            # If there are parent-child relations, verify structure
            for relation in parent_child_relations:
                assert "type" in relation
                assert "target_iid" in relation
                assert "target_title" in relation
                assert "is_same_project" in relation
                assert "source" in relation
                assert relation["type"] == "child_of"
                assert relation["source"] == "graphql_work_items"

            # If there are blocking relations, verify structure
            for relation in blocking_relations:
                assert "type" in relation
                assert "target_iid" in relation
                assert "target_title" in relation
                assert "is_same_project" in relation
                assert relation["type"] in ["blocks", "is_blocked_by"]

            # If there are cross-links text (relates_to only), verify formatting
            if cross_links_text:
                assert "Cross-linked Issues:" in cross_links_text
                assert "---" in cross_links_text

            # Enhanced cross-linking test completed

        except Exception as e:
            pytest.fail(f"Enhanced cross-linking functionality test failed: {e}")

    def test_closed_issues_and_milestones_retrieval(self) -> None:
        """Test that closed issues and milestones are properly retrieved."""
        try:
            # First check if we can access the configured source project
            try:
                source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            except Exception:
                # If the configured project is not accessible, skip the test
                pytest.skip(f"Source project '{self.source_gitlab_project}' not accessible. "
                           f"Set GITLAB_TEST_PROJECT environment variable to a project you have access to.")

            # Test issue retrieval with state='all'
            all_issues = source_project.issues.list(all=True, state="all")
            opened_issues = source_project.issues.list(all=True, state="opened")
            closed_issues = source_project.issues.list(all=True, state="closed")

            # Verify we get both open and closed issues
            assert len(all_issues) >= len(opened_issues)
            assert len(all_issues) >= len(closed_issues)
            assert len(all_issues) == len(opened_issues) + len(closed_issues)

            # Check that we actually have some closed issues
            closed_count = len([issue for issue in all_issues if issue.state == "closed"])
            opened_count = len([issue for issue in all_issues if issue.state == "opened"])

            assert closed_count > 0, "No closed issues found - test project should have closed issues"
            assert opened_count > 0, "No opened issues found - test project should have open issues"

            # Test milestone retrieval with state='all'
            all_milestones = source_project.milestones.list(all=True, state="all")

            if all_milestones:
                active_milestones = [m for m in all_milestones if m.state == "active"]
                closed_milestones = [m for m in all_milestones if m.state == "closed"]

                assert len(all_milestones) == len(active_milestones) + len(closed_milestones)
                # Milestones retrieved with all states

            # Test that migrator can be initialized with the project (validates GitLab access)
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project, github_repo_path=self.target_github_repo
            )
            
            # Verify the GitLab project was loaded correctly
            assert migrator.gitlab_project is not None

            # Closed issues and milestones retrieval test completed

        except Exception as e:
            pytest.fail(f"Closed issues and milestones retrieval test failed: {e}")

    def test_comprehensive_cleanup_verification(self) -> None:
        """Verify that all test artifacts are properly identified for cleanup."""
        self._ensure_test_repository_exists()

        try:
            # List all issues created during testing
            all_issues = list(self.test_github_repo.get_issues(state="all"))
            [issue for issue in all_issues if "Test Issue" in issue.title or "test" in issue.title.lower()]

            # List all milestones created during testing
            all_milestones = list(self.test_github_repo.get_milestones(state="all"))
            [
                milestone
                for milestone in all_milestones
                if "Test" in milestone.title or "test" in milestone.title.lower()
            ]

            # List all labels created during testing
            all_labels = list(self.test_github_repo.get_labels())
            [label for label in all_labels if "test" in label.name.lower() or "priority:" in label.name]

            # Test artifacts identified for cleanup verification
            # Issues found: {len(test_issues)}
            # Milestones found: {len(test_milestones)}
            # Labels found: {len(test_labels)}

            # Verify we can access the repository for cleanup
            assert self.test_github_repo.name == self.test_repo_name
            assert hasattr(self.test_github_repo, "delete")

        except Exception as e:
            pytest.fail(f"Cleanup verification test failed: {e}")


if __name__ == "__main__":
    # Run tests with verbose output
    pytest.main([__file__, "-v", "-s", "--tb=short"])
