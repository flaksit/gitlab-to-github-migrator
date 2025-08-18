#!/usr/bin/env python3
"""
Integration tests for GitLab to GitHub Migration Tool using real APIs

These tests connect to real GitLab and GitHub APIs to ensure the migration
functionality works correctly with actual data.

Test source: GitLab project flaks/jk/jkx
Test target: Temporary GitHub repo in abuflow organization
"""

import pytest
import os
import time
import random
import string
from pathlib import Path

import gitlab
from github import Github
from gitlab_to_github_migrator import GitLabToGitHubMigrator, MigrationError


class TestRealAPIIntegration:
    """Integration tests using real GitLab and GitHub APIs."""
    
    @classmethod
    def setup_class(cls):
        """Setup class-level fixtures."""
        # GitLab project to use as source (read-only)
        cls.source_gitlab_project = "flaks/jk/jkx"
        
        # Generate unique test repo name
        random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        cls.test_repo_name = f"migration-test-{random_suffix}"
        cls.target_github_repo = f"abuflow/{cls.test_repo_name}"
        
        # Initialize API clients with authentication
        # GitLab client - try env var, then pass, then anonymous
        gitlab_token = os.environ.get('GITLAB_TOKEN')
        if not gitlab_token:
            try:
                import subprocess
                result = subprocess.run(['pass', 'gitlab/cli/ro_token'], 
                                      capture_output=True, text=True, check=True)
                gitlab_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access
        
        if gitlab_token:
            cls.gitlab_client = gitlab.Gitlab('https://gitlab.com', private_token=gitlab_token)
        else:
            cls.gitlab_client = gitlab.Gitlab()  # Anonymous access
        
        # GitHub client - try env var, then pass, then anonymous
        github_token = os.environ.get('GITHUB_TOKEN')
        if not github_token:
            try:
                import subprocess
                result = subprocess.run(['pass', 'github/cli/token'], 
                                      capture_output=True, text=True, check=True)
                github_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access
        
        if github_token:
            cls.github_client = Github(github_token)
        else:
            cls.github_client = Github()  # Anonymous access
        
        # Test repo reference (will be set during tests)
        cls.test_github_repo = None
        
        print(f"Test setup - Source: {cls.source_gitlab_project}, Target: {cls.target_github_repo}")
    
    @classmethod
    def teardown_class(cls):
        """Cleanup class-level fixtures."""
        # Delete test repository if it exists
        if cls.test_github_repo:
            try:
                cls.test_github_repo.delete()
                print(f"Cleaned up test repository: {cls.target_github_repo}")
            except Exception as e:
                print(f"Failed to cleanup test repository: {e}")
    
    def test_gitlab_source_project_access(self):
        """Test that we can access the source GitLab project."""
        try:
            project = self.gitlab_client.projects.get(self.source_gitlab_project)
            assert project.name.lower() == "jkx"
            print(f"Successfully accessed GitLab project: {project.name}")
            
            # Test that we can read issues
            issues = project.issues.list(all=True)
            print(f"Found {len(issues)} issues in source project")
            
            # Test that we can read milestones
            milestones = project.milestones.list(all=True)
            print(f"Found {len(milestones)} milestones in source project")
            
            # Test that we can read labels
            labels = project.labels.list(all=True)
            print(f"Found {len(labels)} labels in source project")
            
        except Exception as e:
            pytest.fail(f"Failed to access GitLab source project: {e}")
    
    def test_github_api_access(self):
        """Test that we can access GitHub API."""
        try:
            user = self.github_client.get_user()
            print(f"GitHub API access successful - User: {user.login}")
            
            # Test organization access
            org = self.github_client.get_organization("abuflow")
            print(f"Organization access successful: {org.name}")
            
        except Exception as e:
            pytest.fail(f"Failed to access GitHub API: {e}")
    
    def test_label_translation_functionality(self):
        """Test the label translation with real GitLab labels."""
        from gitlab_to_github_migrator import LabelTranslator
        
        # Get real labels from the source project
        project = self.gitlab_client.projects.get(self.source_gitlab_project)
        real_labels = project.labels.list(all=True)
        
        if not real_labels:
            pytest.skip("No labels found in source project")
        
        # Test translation patterns based on actual labels
        translator = LabelTranslator([
            "comp_*:component: *",
            "prio_*:priority: *",
            "status_*:status: *"
        ])
        
        for label in real_labels[:5]:  # Test first 5 labels
            original_name = label.name
            translated_name = translator.translate(original_name)
            print(f"Label translation: '{original_name}' -> '{translated_name}'")
            
            # Verify translation logic
            if original_name.startswith("comp_"):
                expected = original_name.replace("comp_", "component: ")
                assert translated_name == expected
            elif original_name.startswith("prio_"):
                expected = original_name.replace("prio_", "priority: ")
                assert translated_name == expected
    
    def test_migrator_initialization_with_real_apis(self):
        """Test migrator initialization with real API connections."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=[
                    "priority::*:priority: *",
                    "type::*:type: *"
                ]
            )
            
            # Test API validation
            migrator.validate_api_access()
            print("API validation successful")
            
            # Verify GitLab project details
            assert migrator.gitlab_project.path_with_namespace == self.source_gitlab_project
            print(f"GitLab project loaded: {migrator.gitlab_project.name}")
            
        except Exception as e:
            pytest.fail(f"Migrator initialization failed: {e}")
    
    def test_github_repository_creation_and_cleanup(self):
        """Test GitHub repository creation and cleanup."""
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo
            )
            
            # Create test repository
            migrator.create_github_repository()
            self.test_github_repo = migrator.github_repo  # Store for cleanup
            
            print(f"Test repository created: {self.test_github_repo.html_url}")
            
            # Verify repository properties
            assert self.test_github_repo.name == self.test_repo_name
            assert self.test_github_repo.private is True
            assert self.test_github_repo.has_issues is True
            
            # Test that we can't create it again (should fail)
            with pytest.raises(MigrationError, match="already exists"):
                migrator.create_github_repository()
                
        except Exception as e:
            pytest.fail(f"Repository creation test failed: {e}")
    
    def test_label_migration_with_real_data(self):
        """Test label migration with real GitLab labels."""
        if not self.test_github_repo:
            pytest.skip("Test repository not available")
        
        try:
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=[
                    "priority::*:priority: *",
                    "type::*:type: *"
                ]
            )
            migrator.github_repo = self.test_github_repo
            
            # Get original label count
            original_labels = list(self.test_github_repo.get_labels())
            original_count = len(original_labels)
            
            # Migrate labels
            migrator.handle_labels()
            
            # Verify labels were created
            new_labels = list(self.test_github_repo.get_labels())
            new_count = len(new_labels)
            
            print(f"Labels migrated: {original_count} -> {new_count}")
            print(f"Label mapping created: {len(migrator.label_mapping)} entries")
            
            # Verify some labels were processed
            assert len(migrator.label_mapping) > 0
            assert new_count >= original_count
            
            # Check that translation patterns were applied
            translated_labels = [label.name for label in new_labels if "priority: " in label.name or "type: " in label.name]
            if translated_labels:
                print(f"Successfully translated labels: {translated_labels}")
            
        except Exception as e:
            pytest.fail(f"Label migration test failed: {e}")
    
    def test_milestone_migration_with_real_data(self):
        """Test milestone migration with real GitLab milestones."""
        if not self.test_github_repo:
            pytest.skip("Test repository not available")
        
        try:
            # Get source milestones
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            source_milestones = source_project.milestones.list(all=True)
            
            if not source_milestones:
                pytest.skip("No milestones found in source project")
            
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo
            )
            migrator.github_repo = self.test_github_repo
            
            # Migrate milestones
            migrator.migrate_milestones_with_number_preservation()
            
            # Verify milestones were created
            github_milestones = list(self.test_github_repo.get_milestones(state='all'))
            
            print(f"Milestones migrated: {len(source_milestones)} source -> {len(github_milestones)} target")
            print(f"Milestone mapping: {migrator.milestone_mapping}")
            
            # Verify milestone count (may include placeholders)
            assert len(github_milestones) >= len(source_milestones)
            
            # Check that real milestones have correct titles
            real_milestones = [m for m in github_milestones if m.title != "Placeholder Milestone"]
            assert len(real_milestones) == len(source_milestones)
            
        except Exception as e:
            pytest.fail(f"Milestone migration test failed: {e}")
    
    def test_issue_reading_from_real_source(self):
        """Test reading issues from real GitLab source."""
        try:
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            issues = source_project.issues.list(all=True, per_page=5)  # Limit for testing
            
            if not issues:
                pytest.skip("No issues found in source project")
            
            print(f"Testing with {len(issues)} sample issues")
            
            for issue in issues:
                print(f"Issue #{issue.iid}: {issue.title}")
                print(f"  State: {issue.state}, Labels: {issue.labels}")
                print(f"  Author: {issue.author['name']}")
                
                # Test comment reading
                comments = issue.notes.list(all=True)
                print(f"  Comments: {len(comments)}")
                
                # Test that we can access issue properties needed for migration
                assert hasattr(issue, 'iid')
                assert hasattr(issue, 'title')
                assert hasattr(issue, 'description')
                assert hasattr(issue, 'state')
                assert hasattr(issue, 'labels')
                assert hasattr(issue, 'created_at')
                assert hasattr(issue, 'web_url')
                
        except Exception as e:
            pytest.fail(f"Issue reading test failed: {e}")
    
    def test_partial_migration_simulation(self):
        """Test a partial migration simulation without creating all issues."""
        if not self.test_github_repo:
            pytest.skip("Test repository not available")
        
        try:
            # Get a small sample of source data
            source_project = self.gitlab_client.projects.get(self.source_gitlab_project)
            source_issues = source_project.issues.list(all=True, per_page=2)
            source_milestones = source_project.milestones.list(all=True)
            
            if not source_issues:
                pytest.skip("No issues found for simulation")
            
            migrator = GitLabToGitHubMigrator(
                gitlab_project_path=self.source_gitlab_project,
                github_repo_path=self.target_github_repo,
                label_translations=["priority::*:priority: *"]
            )
            migrator.github_repo = self.test_github_repo
            
            # Migrate labels and milestones (but not issues to avoid spam)
            migrator.handle_labels()
            if source_milestones:
                migrator.migrate_milestones_with_number_preservation()
            
            # Test validation report generation
            report = migrator.validate_migration()
            
            print(f"Migration simulation completed")
            print(f"Report success: {report['success']}")
            print(f"Statistics: {report['statistics']}")
            
            # Should have some data migrated
            assert report['statistics']['labels_migrated'] >= 0
            if source_milestones:
                assert report['statistics']['github_milestones'] >= len(source_milestones)
            
        except Exception as e:
            pytest.fail(f"Partial migration simulation failed: {e}")


if __name__ == '__main__':
    # Run tests with verbose output
    pytest.main([__file__, '-v', '-s', '--tb=short'])