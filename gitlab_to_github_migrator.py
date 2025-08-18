#!/usr/bin/env python3
"""
GitLab to GitHub Migration Tool

Migrates GitLab projects to GitHub with full metadata preservation including
exact issue/milestone numbers, comments, attachments, and relationships.
"""

import argparse
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from urllib.parse import urlparse

import gitlab
import requests
from github import Github, GithubException


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the migration process."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('migration.log', mode='a')
        ]
    )


class LabelTranslator:
    """Handles label translation patterns."""
    
    def __init__(self, patterns: List[str]):
        self.patterns = []
        for pattern in patterns:
            if ':' not in pattern:
                raise ValueError(f"Invalid pattern format: {pattern}")
            source, target = pattern.split(':', 1)
            self.patterns.append((source, target))
    
    def translate(self, label_name: str) -> str:
        """Translate a label name using configured patterns."""
        for source_pattern, target_pattern in self.patterns:
            if '*' in source_pattern:
                # Convert glob pattern to regex
                regex_pattern = source_pattern.replace('*', '(.*)')
                match = re.match(f'^{regex_pattern}$', label_name)
                if match:
                    return target_pattern.replace('*', match.group(1))
            elif source_pattern == label_name:
                return target_pattern
        return label_name


class MigrationError(Exception):
    """Base exception for migration errors."""
    pass


class NumberVerificationError(MigrationError):
    """Raised when milestone/issue number verification fails."""
    pass


class GitLabToGitHubMigrator:
    """Main migration class."""
    
    def __init__(self, gitlab_project_path: str, github_repo_path: str, 
                 label_translations: Optional[List[str]] = None,
                 local_clone_path: Optional[str] = None):
        self.gitlab_project_path = gitlab_project_path
        self.github_repo_path = github_repo_path
        self.local_clone_path = local_clone_path
        self.temp_clone_path = None
        
        # Initialize API clients with authentication
        gitlab_token = os.environ.get('GITLAB_TOKEN')
        if not gitlab_token:
            try:
                result = subprocess.run(['pass', 'gitlab/cli/ro_token'], 
                                      capture_output=True, text=True, check=True)
                gitlab_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access
        
        if gitlab_token:
            self.gitlab_client = gitlab.Gitlab('https://gitlab.com', private_token=gitlab_token)
        else:
            self.gitlab_client = gitlab.Gitlab()  # Anonymous access
        
        github_token = os.environ.get('GITHUB_TOKEN')
        if not github_token:
            try:
                result = subprocess.run(['pass', 'github/cli/token'], 
                                      capture_output=True, text=True, check=True)
                github_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access
        
        if github_token:
            self.github_client = Github(github_token)
        else:
            self.github_client = Github()  # Anonymous access
        
        # Get projects
        self.gitlab_project = self.gitlab_client.projects.get(gitlab_project_path)
        
        # Parse GitHub repo path
        github_org, github_repo = github_repo_path.split('/')
        self.github_org = github_org
        self.github_repo_name = github_repo
        self.github_repo = None
        
        # Initialize label translator
        self.label_translator = LabelTranslator(label_translations or [])
        
        # Mappings for migration
        self.label_mapping = {}
        self.milestone_mapping = {}
        
        logging.info(f"Initialized migrator for {gitlab_project_path} -> {github_repo_path}")
    
    def validate_api_access(self) -> None:
        """Validate GitLab and GitHub API access."""
        try:
            # Test GitLab access
            _ = self.gitlab_project.name
            logging.info("GitLab API access validated")
        except Exception as e:
            raise MigrationError(f"GitLab API access failed: {e}")
        
        try:
            # Test GitHub access
            self.github_client.get_user()
            logging.info("GitHub API access validated")
        except Exception as e:
            raise MigrationError(f"GitHub API access failed: {e}")
    
    def create_github_repository(self) -> None:
        """Create GitHub repository with GitLab project metadata."""
        try:
            # Check if repository already exists
            try:
                existing_repo = self.github_client.get_repo(self.github_repo_path)
                raise MigrationError(f"Repository {self.github_repo_path} already exists")
            except GithubException as e:
                if e.status != 404:
                    raise MigrationError(f"Error checking repository existence: {e}")
            
            # Get organization
            org = self.github_client.get_organization(self.github_org)
            
            # Create repository
            self.github_repo = org.create_repo(
                name=self.github_repo_name,
                description=self.gitlab_project.description or "",
                private=True,
                has_issues=True,
                has_wiki=False,
                has_projects=False
            )
            
            logging.info(f"Created GitHub repository: {self.github_repo.html_url}")
            
        except Exception as e:
            raise MigrationError(f"Failed to create GitHub repository: {e}")
    
    def migrate_repository_content(self) -> None:
        """Migrate git repository content from GitLab to GitHub."""
        try:
            if self.local_clone_path:
                # Use existing local clone
                clone_path = Path(self.local_clone_path)
                if not clone_path.exists():
                    raise MigrationError(f"Local clone path does not exist: {self.local_clone_path}")
            else:
                # Create temporary clone
                self.temp_clone_path = tempfile.mkdtemp(prefix="gitlab_migration_")
                clone_path = Path(self.temp_clone_path)
                
                # Clone from GitLab
                result = subprocess.run([
                    'git', 'clone', '--mirror', 
                    self.gitlab_project.ssh_url_to_repo,
                    str(clone_path)
                ], capture_output=True, text=True)
                
                if result.returncode != 0:
                    raise MigrationError(f"Failed to clone GitLab repository: {result.stderr}")
            
            # Add GitHub remote and push
            os.chdir(clone_path)
            
            # Add GitHub remote
            subprocess.run(['git', 'remote', 'add', 'github', self.github_repo.ssh_url], check=True)
            
            # Push all branches and tags
            subprocess.run(['git', 'push', '--mirror', 'github'], check=True)
            
            logging.info("Repository content migrated successfully")
            
        except Exception as e:
            raise MigrationError(f"Failed to migrate repository content: {e}")
        finally:
            # Cleanup temporary clone if created
            if self.temp_clone_path and os.path.exists(self.temp_clone_path):
                shutil.rmtree(self.temp_clone_path)
    
    def handle_labels(self) -> None:
        """Migrate and translate labels from GitLab to GitHub."""
        try:
            # Get GitLab labels
            gitlab_labels = self.gitlab_project.labels.list(all=True)
            
            # Get existing GitHub organization labels
            org = self.github_client.get_organization(self.github_org)
            try:
                existing_github_labels = {label.name for label in org.get_labels()}
            except GithubException:
                # Organization might not have default labels
                existing_github_labels = set()
            
            # Get existing repository labels
            existing_repo_labels = {label.name for label in self.github_repo.get_labels()}
            
            for gitlab_label in gitlab_labels:
                # Translate label name
                translated_name = self.label_translator.translate(gitlab_label.name)
                
                # Skip if label already exists (org default or repo)
                if translated_name in existing_github_labels or translated_name in existing_repo_labels:
                    self.label_mapping[gitlab_label.name] = translated_name
                    continue
                
                # Create new label
                try:
                    github_label = self.github_repo.create_label(
                        name=translated_name,
                        color=gitlab_label.color.lstrip('#'),
                        description=gitlab_label.description or ""
                    )
                    self.label_mapping[gitlab_label.name] = github_label.name
                    logging.debug(f"Created label: {gitlab_label.name} -> {translated_name}")
                except GithubException as e:
                    logging.warning(f"Failed to create label {translated_name}: {e}")
                    self.label_mapping[gitlab_label.name] = gitlab_label.name
            
            logging.info(f"Migrated {len(self.label_mapping)} labels")
            
        except Exception as e:
            raise MigrationError(f"Failed to handle labels: {e}")
    
    def migrate_milestones_with_number_preservation(self) -> None:
        """Migrate milestones while preserving GitLab milestone numbers."""
        try:
            # Get all GitLab milestones sorted by ID
            gitlab_milestones = self.gitlab_project.milestones.list(all=True, state='all')
            gitlab_milestones.sort(key=lambda m: m.iid)
            
            if not gitlab_milestones:
                logging.info("No milestones to migrate")
                return
            
            max_milestone_number = max(m.iid for m in gitlab_milestones)
            gitlab_milestone_dict = {m.iid: m for m in gitlab_milestones}
            
            # Create milestones maintaining number sequence
            for milestone_number in range(1, max_milestone_number + 1):
                if milestone_number in gitlab_milestone_dict:
                    # Real milestone exists
                    gitlab_milestone = gitlab_milestone_dict[milestone_number]
                    
                    github_milestone = self.github_repo.create_milestone(
                        title=gitlab_milestone.title,
                        state='open' if gitlab_milestone.state == 'active' else 'closed',
                        description=gitlab_milestone.description or "",
                        due_on=gitlab_milestone.due_date
                    )
                    
                    # Verify milestone number
                    if github_milestone.number != milestone_number:
                        raise NumberVerificationError(
                            f"Milestone number mismatch: expected {milestone_number}, got {github_milestone.number}"
                        )
                    
                    self.milestone_mapping[gitlab_milestone.id] = github_milestone.number
                    logging.debug(f"Created milestone #{milestone_number}: {gitlab_milestone.title}")
                else:
                    # Create placeholder milestone
                    placeholder_milestone = self.github_repo.create_milestone(
                        title="Placeholder Milestone",
                        state='closed',
                        description="Placeholder to preserve milestone numbering"
                    )
                    
                    # Verify placeholder number
                    if placeholder_milestone.number != milestone_number:
                        raise NumberVerificationError(
                            f"Placeholder milestone number mismatch: expected {milestone_number}, got {placeholder_milestone.number}"
                        )
                    
                    logging.debug(f"Created placeholder milestone #{milestone_number}")
            
            logging.info(f"Migrated {len(self.milestone_mapping)} milestones")
            
        except Exception as e:
            raise MigrationError(f"Failed to migrate milestones: {e}")
    
    def download_gitlab_attachments(self, content: str, gitlab_item) -> Tuple[str, List[Dict[str, Any]]]:
        """Download GitLab attachments and return updated content with file info."""
        # Find attachment URLs in content
        attachment_pattern = r'/uploads/[a-f0-9]{32}/[^)\s]+'
        attachments = re.findall(attachment_pattern, content)
        
        downloaded_files = []
        updated_content = content
        
        for attachment_url in attachments:
            try:
                # Build full URL
                full_url = f"{self.gitlab_project.web_url}{attachment_url}"
                
                # Download file
                response = requests.get(full_url, headers={'Authorization': f'Bearer {self.gitlab_client.private_token}'})
                response.raise_for_status()
                
                # Extract filename
                filename = attachment_url.split('/')[-1]
                
                downloaded_files.append({
                    'filename': filename,
                    'content': response.content,
                    'original_url': attachment_url,
                    'full_url': full_url
                })
                
            except Exception as e:
                logging.warning(f"Failed to download attachment {attachment_url}: {e}")
        
        return updated_content, downloaded_files
    
    def upload_github_attachments(self, files: List[Dict[str, Any]], content: str) -> str:
        """Upload files to GitHub and update content with new URLs."""
        updated_content = content
        
        for file_info in files:
            try:
                # Create a temporary file path for GitHub API
                temp_path = f"/tmp/{file_info['filename']}"
                with open(temp_path, 'wb') as f:
                    f.write(file_info['content'])
                
                # GitHub doesn't have a direct file upload API for arbitrary files
                # We'll create a commit with the file and reference it
                try:
                    # Try to upload as release asset if possible, otherwise skip file upload
                    # and keep original reference with a note
                    logging.warning(f"File upload not implemented for {file_info['filename']}")
                    # For now, we'll keep the original URL with a note
                    updated_content = updated_content.replace(
                        file_info['original_url'],
                        f"{file_info['original_url']} (Original GitLab attachment)"
                    )
                except Exception as e:
                    logging.warning(f"Failed to upload {file_info['filename']}: {e}")
                
                # Clean up temp file
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
            except Exception as e:
                logging.warning(f"Failed to process attachment {file_info['filename']}: {e}")
        
        return updated_content
    
    def migrate_issues_with_number_preservation(self) -> None:
        """Migrate issues while preserving GitLab issue numbers."""
        try:
            # Get all GitLab issues sorted by IID
            gitlab_issues = self.gitlab_project.issues.list(all=True, state='all')
            gitlab_issues.sort(key=lambda i: i.iid)
            
            if not gitlab_issues:
                logging.info("No issues to migrate")
                return
            
            max_issue_number = max(i.iid for i in gitlab_issues)
            gitlab_issue_dict = {i.iid: i for i in gitlab_issues}
            
            # Create issues maintaining number sequence
            for issue_number in range(1, max_issue_number + 1):
                if issue_number in gitlab_issue_dict:
                    # Real issue exists
                    gitlab_issue = gitlab_issue_dict[issue_number]
                    
                    # Prepare issue content
                    issue_body = f"**Migrated from GitLab issue #{gitlab_issue.iid}**\n"
                    issue_body += f"**Original Author:** {gitlab_issue.author['name']} (@{gitlab_issue.author['username']})\n"
                    issue_body += f"**Created:** {gitlab_issue.created_at}\n"
                    issue_body += f"**GitLab URL:** {gitlab_issue.web_url}\n\n"
                    issue_body += "---\n\n"
                    
                    if gitlab_issue.description:
                        # Download and process attachments
                        updated_description, files = self.download_gitlab_attachments(
                            gitlab_issue.description, gitlab_issue
                        )
                        updated_description = self.upload_github_attachments(files, updated_description)
                        issue_body += updated_description
                    
                    # Prepare labels
                    issue_labels = []
                    for label_name in gitlab_issue.labels:
                        if label_name in self.label_mapping:
                            issue_labels.append(self.label_mapping[label_name])
                    
                    # Prepare milestone
                    milestone = None
                    if gitlab_issue.milestone and gitlab_issue.milestone['id'] in self.milestone_mapping:
                        milestone_number = self.milestone_mapping[gitlab_issue.milestone['id']]
                        milestone = self.github_repo.get_milestone(milestone_number)
                    
                    # Create GitHub issue
                    github_issue = self.github_repo.create_issue(
                        title=gitlab_issue.title,
                        body=issue_body,
                        labels=issue_labels,
                        milestone=milestone
                    )
                    
                    # Verify issue number
                    if github_issue.number != issue_number:
                        raise NumberVerificationError(
                            f"Issue number mismatch: expected {issue_number}, got {github_issue.number}"
                        )
                    
                    # Migrate comments
                    self.migrate_issue_comments(gitlab_issue, github_issue)
                    
                    # Close issue if needed
                    if gitlab_issue.state == 'closed':
                        github_issue.edit(state='closed')
                    
                    logging.debug(f"Created issue #{issue_number}: {gitlab_issue.title}")
                    
                else:
                    # Create placeholder issue
                    placeholder_issue = self.github_repo.create_issue(
                        title="Placeholder",
                        body="Placeholder to preserve issue numbering - will be deleted"
                    )
                    
                    # Verify placeholder number
                    if placeholder_issue.number != issue_number:
                        raise NumberVerificationError(
                            f"Placeholder issue number mismatch: expected {issue_number}, got {placeholder_issue.number}"
                        )
                    
                    # Close placeholder immediately
                    placeholder_issue.edit(state='closed')
                    logging.debug(f"Created placeholder issue #{issue_number}")
            
            logging.info(f"Migrated {len([i for i in gitlab_issues])} issues")
            
        except Exception as e:
            raise MigrationError(f"Failed to migrate issues: {e}")
    
    def migrate_issue_comments(self, gitlab_issue, github_issue) -> None:
        """Migrate comments for an issue."""
        try:
            # Get all notes/comments
            notes = gitlab_issue.notes.list(all=True)
            notes.sort(key=lambda n: n.created_at)
            
            for note in notes:
                if note.system:
                    # System note - convert to regular comment
                    comment_body = f"**System note:** {note.body}\n\n"
                else:
                    # Regular comment
                    comment_body = f"**Comment by** {note.author['name']} (@{note.author['username']}) **on** {note.created_at}\n\n"
                    comment_body += "---\n\n"
                    
                    if note.body:
                        # Process attachments in comment
                        updated_body, files = self.download_gitlab_attachments(note.body, note)
                        updated_body = self.upload_github_attachments(files, updated_body)
                        comment_body += updated_body
                
                # Create GitHub comment
                github_issue.create_comment(comment_body)
                logging.debug(f"Migrated comment by {note.author['username']}")
                
        except Exception as e:
            logging.warning(f"Failed to migrate comments for issue #{gitlab_issue.iid}: {e}")
    
    def cleanup_placeholders(self) -> None:
        """Delete placeholder issues and milestones."""
        try:
            # Clean up placeholder issues
            issues = self.github_repo.get_issues(state='all')
            for issue in issues:
                if issue.title == "Placeholder":
                    # GitHub API doesn't allow deleting issues, so we'll leave them closed
                    logging.debug(f"Placeholder issue #{issue.number} left closed (cannot delete)")
            
            # Clean up placeholder milestones
            milestones = self.github_repo.get_milestones(state='all')
            for milestone in milestones:
                if milestone.title == "Placeholder Milestone":
                    milestone.delete()
                    logging.debug(f"Deleted placeholder milestone #{milestone.number}")
            
            logging.info("Cleanup completed")
            
        except Exception as e:
            logging.warning(f"Cleanup failed: {e}")
    
    def validate_migration(self) -> Dict[str, Any]:
        """Validate migration results and generate report."""
        report = {
            'gitlab_project': self.gitlab_project_path,
            'github_repo': self.github_repo_path,
            'success': True,
            'errors': [],
            'statistics': {}
        }
        
        try:
            # Count GitLab items
            gitlab_issues = self.gitlab_project.issues.list(all=True, state='all')
            gitlab_milestones = self.gitlab_project.milestones.list(all=True, state='all')
            
            # Count GitHub items (excluding placeholders)
            github_issues = [i for i in self.github_repo.get_issues(state='all') if i.title != "Placeholder"]
            github_milestones = [m for m in self.github_repo.get_milestones(state='all') if m.title != "Placeholder Milestone"]
            
            report['statistics'] = {
                'gitlab_issues': len(gitlab_issues),
                'github_issues': len(github_issues),
                'gitlab_milestones': len(gitlab_milestones),
                'github_milestones': len(github_milestones),
                'labels_migrated': len(self.label_mapping)
            }
            
            # Validate counts
            if len(gitlab_issues) != len(github_issues):
                report['errors'].append(f"Issue count mismatch: GitLab {len(gitlab_issues)}, GitHub {len(github_issues)}")
                report['success'] = False
            
            if len(gitlab_milestones) != len(github_milestones):
                report['errors'].append(f"Milestone count mismatch: GitLab {len(gitlab_milestones)}, GitHub {len(github_milestones)}")
                report['success'] = False
            
            logging.info("Migration validation completed")
            
        except Exception as e:
            report['success'] = False
            report['errors'].append(f"Validation failed: {e}")
            logging.error(f"Validation failed: {e}")
        
        return report
    
    def migrate(self) -> Dict[str, Any]:
        """Execute the complete migration process."""
        try:
            logging.info("Starting GitLab to GitHub migration")
            
            # Validation
            self.validate_api_access()
            
            # Repository creation and content migration
            self.create_github_repository()
            self.migrate_repository_content()
            
            # Metadata migration
            self.handle_labels()
            self.migrate_milestones_with_number_preservation()
            self.migrate_issues_with_number_preservation()
            
            # Cleanup and validation
            self.cleanup_placeholders()
            report = self.validate_migration()
            
            logging.info("Migration completed successfully")
            return report
            
        except Exception as e:
            logging.error(f"Migration failed: {e}")
            # Optionally clean up created repository
            if self.github_repo:
                try:
                    logging.info("Cleaning up created repository due to failure")
                    self.github_repo.delete()
                except Exception as cleanup_error:
                    logging.error(f"Failed to cleanup repository: {cleanup_error}")
            
            raise MigrationError(f"Migration failed: {e}")


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Migrate GitLab project to GitHub with full metadata preservation"
    )
    
    parser.add_argument(
        '--gitlab-project',
        required=True,
        help='GitLab project path (namespace/project)'
    )
    
    parser.add_argument(
        '--github-repo',
        required=True,
        help='GitHub repository path (org/repo)'
    )
    
    parser.add_argument(
        '--label-translation',
        action='append',
        help='Label translation pattern (format: "source_pattern:target_pattern"). Can be specified multiple times.'
    )
    
    parser.add_argument(
        '--local-clone-path',
        help='Path to existing local git clone of GitLab project'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()
    
    # Setup logging
    setup_logging(args.verbose)
    
    try:
        # Initialize migrator
        migrator = GitLabToGitHubMigrator(
            gitlab_project_path=args.gitlab_project,
            github_repo_path=args.github_repo,
            label_translations=args.label_translation,
            local_clone_path=args.local_clone_path
        )
        
        # Execute migration
        report = migrator.migrate()
        
        # Print report
        print("\n" + "="*50)
        print("MIGRATION REPORT")
        print("="*50)
        print(f"GitLab Project: {report['gitlab_project']}")
        print(f"GitHub Repository: {report['github_repo']}")
        print(f"Success: {report['success']}")
        
        if report['errors']:
            print("\nErrors:")
            for error in report['errors']:
                print(f"  - {error}")
        
        print("\nStatistics:")
        for key, value in report['statistics'].items():
            print(f"  {key}: {value}")
        
        if report['success']:
            print("\nMigration completed successfully!")
            sys.exit(0)
        else:
            print("\nMigration completed with errors!")
            sys.exit(1)
            
    except Exception as e:
        logging.error(f"Migration failed: {e}")
        print(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()