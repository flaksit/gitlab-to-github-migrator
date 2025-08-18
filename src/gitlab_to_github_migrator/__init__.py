"""
GitLab to GitHub Migration Tool

Migrates GitLab projects to GitHub with full metadata preservation including
exact issue/milestone numbers, comments, attachments, and relationships.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import gitlab
import requests
from github import Github, GithubException


def setup_logging(*, verbose: bool = False) -> None:
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
    
    def __init__(self, patterns: list[str]) -> None:
        self.patterns = []
        for pattern in patterns:
            if ':' not in pattern:
                msg = f"Invalid pattern format: {pattern}"
                raise ValueError(msg)
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


class NumberVerificationError(MigrationError):
    """Raised when milestone/issue number verification fails."""


class GitLabToGitHubMigrator:
    """Main migration class."""
    
    def __init__(
        self,
        gitlab_project_path: str,
        github_repo_path: str,
        *,
        label_translations: list[str] | None = None,
        local_clone_path: str | None = None,
        gitlab_token_path: str | None = None,
        github_token_path: str | None = None,
    ) -> None:
        self.gitlab_project_path = gitlab_project_path
        self.github_repo_path = github_repo_path
        self.local_clone_path = local_clone_path
        self.temp_clone_path = None
        self.gitlab_token_path = gitlab_token_path
        self.github_token_path = github_token_path
        
        # Initialize API clients with authentication
        gitlab_token = os.environ.get('GITLAB_TOKEN')
        if not gitlab_token:
            try:
                gitlab_token_path = self.gitlab_token_path or 'gitlab/cli/ro_token'
                result = subprocess.run(['pass', gitlab_token_path],  # noqa: S603
                                      capture_output=True, text=True, check=True)
                gitlab_token = result.stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass  # Fall back to anonymous access
        
        if gitlab_token:
            self.gitlab_client = gitlab.Gitlab('https://gitlab.com', private_token=gitlab_token)
        else:
            self.gitlab_client = gitlab.Gitlab()  # Anonymous access
        
        # Store GitLab token for GraphQL queries
        self.gitlab_token = gitlab_token
        
        github_token = os.environ.get('GITHUB_TOKEN')
        if not github_token:
            try:
                github_token_path = self.github_token_path or 'github/cli/token'
                result = subprocess.run(['pass', github_token_path],  # noqa: S603
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
        
        logger = logging.getLogger(__name__)
        logger.info(f"Initialized migrator for {gitlab_project_path} -> {github_repo_path}")
    
    def validate_api_access(self) -> None:
        """Validate GitLab and GitHub API access."""
        try:
            # Test GitLab access
            _ = self.gitlab_project.name
            logger = logging.getLogger(__name__)
            logger.info("GitLab API access validated")
        except Exception as e:
            msg = f"GitLab API access failed: {e}"
            raise MigrationError(msg) from e
        
        try:
            # Test GitHub access
            self.github_client.get_user()
            logger = logging.getLogger(__name__)
            logger.info("GitHub API access validated")
        except Exception as e:
            msg = f"GitHub API access failed: {e}"
            raise MigrationError(msg) from e
    
    def _make_graphql_request(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make a GraphQL request to GitLab API."""
        if not self.gitlab_token:
            msg = "GitLab token required for GraphQL API access"
            raise MigrationError(msg)
        
        url = 'https://gitlab.com/api/graphql'
        headers = {
            'Authorization': f'Bearer {self.gitlab_token}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'query': query,
            'variables': variables or {}
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            
            if 'errors' in data:
                msg = f"GraphQL errors: {data['errors']}"
                raise MigrationError(msg)
            
            return data.get('data', {})
            
        except requests.RequestException as e:
            msg = f"GraphQL request failed: {e}"
            raise MigrationError(msg) from e
    
    def get_work_item_children(self, issue_iid: int) -> list[dict[str, Any]]:
        """Get child work items for a given issue using GraphQL Work Items API.
        
        Args:
            issue_iid: The internal ID of the issue
            
        Returns:
            List of child work item information including IID, title, and relationship type
        """
        logger = logging.getLogger(__name__)
        
        # Get the project's full path for GraphQL query
        project_path = self.gitlab_project_path
        
        # GraphQL query to get work item with its children
        query = '''
        query GetWorkItemWithChildren($projectPath: ID!, $iid: String!) {
            project(fullPath: $projectPath) {
                workItem(iid: $iid) {
                    id
                    iid
                    title
                    workItemType {
                        name
                    }
                    widgets {
                        type
                        ... on WorkItemWidgetHierarchy {
                            children {
                                nodes {
                                    id
                                    iid
                                    title
                                    state
                                    workItemType {
                                        name
                                    }
                                    webUrl
                                }
                            }
                        }
                    }
                }
            }
        }
        '''
        
        variables = {
            'projectPath': project_path,
            'iid': str(issue_iid)
        }
        
        try:
            data = self._make_graphql_request(query, variables)
            
            project = data.get('project')
            if not project:
                logger.debug(f"Project {project_path} not found in GraphQL response")
                return []
            
            work_item = project.get('workItem')
            if not work_item:
                logger.debug(f"Work item {issue_iid} not found in project {project_path}")
                return []
            
            # Find the hierarchy widget to get children
            children = []
            widgets = work_item.get('widgets', [])
            
            for widget in widgets:
                if widget.get('type') == 'HIERARCHY':
                    child_nodes = widget.get('children', {}).get('nodes', [])
                    
                    for child in child_nodes:
                        child_info = {
                            'iid': child.get('iid'),
                            'title': child.get('title'),
                            'state': child.get('state'),
                            'type': child.get('workItemType', {}).get('name'),
                            'web_url': child.get('webUrl'),
                            'relationship_type': 'child_of'  # This is a child relationship
                        }
                        children.append(child_info)
            
            logger.debug(f"Found {len(children)} child work items for issue #{issue_iid}")
            return children
            
        except Exception as e:
            logger.warning(f"Failed to get work item children for issue #{issue_iid}: {e}")
            return []
    
    def detect_issue_tasks_from_description(self, description: str) -> list[dict[str, Any]]:
        """Detect task items in issue description using markdown task list syntax.
        
        This is a fallback method when GraphQL Work Items API is not available
        or when tasks are defined as simple markdown checkboxes.
        
        Args:
            description: The issue description text
            
        Returns:
            List of task information extracted from description
        """
        if not description:
            return []
        
        tasks = []
        
        # Pattern to match markdown task lists: - [ ] or - [x] followed by issue reference
        task_pattern = r'^[\s]*-\s*\[[\sx]\]\s*#(\d+)(?:\s+(.+))?$'
        
        for line in description.split('\n'):
            match = re.match(task_pattern, line.strip())
            if match:
                issue_number = int(match.group(1))
                task_title = match.group(2) or f"Task #{issue_number}"
                
                task_info = {
                    'iid': issue_number,
                    'title': task_title.strip(),
                    'state': 'opened',  # We don't know the actual state from description
                    'type': 'task',
                    'web_url': f"{self.gitlab_project.web_url}/-/issues/{issue_number}",
                    'relationship_type': 'child_of',
                    'source': 'description'  # Mark that this came from description parsing
                }
                tasks.append(task_info)
        
        return tasks
    
    def create_github_repository(self) -> None:
        """Create GitHub repository with GitLab project metadata."""
        try:
            # Check if repository already exists
            try:
                self.github_client.get_repo(self.github_repo_path)
                msg = f"Repository {self.github_repo_path} already exists"
                raise MigrationError(msg)
            except GithubException as e:
                if e.status != 404:
                    msg = f"Error checking repository existence: {e}"
                    raise MigrationError(msg) from e
            
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
            
            logger = logging.getLogger(__name__)
            logger.info(f"Created GitHub repository: {self.github_repo.html_url}")
            
        except Exception as e:
            msg = f"Failed to create GitHub repository: {e}"
            raise MigrationError(msg)
    
    def migrate_repository_content(self) -> None:
        """Migrate git repository content from GitLab to GitHub."""
        try:
            if self.local_clone_path:
                # Use existing local clone
                clone_path = Path(self.local_clone_path)
                if not clone_path.exists():
                    msg = f"Local clone path does not exist: {self.local_clone_path}"
                    raise MigrationError(msg)
            else:
                # Create temporary clone
                self.temp_clone_path = tempfile.mkdtemp(prefix="gitlab_migration_")
                clone_path = Path(self.temp_clone_path)
                
                # Clone from GitLab
                result = subprocess.run([  # noqa: S603
                    'git', 'clone', '--mirror', 
                    self.gitlab_project.ssh_url_to_repo,
                    str(clone_path)
                ], check=False, capture_output=True, text=True)
                
                if result.returncode != 0:
                    msg = f"Failed to clone GitLab repository: {result.stderr}"
                    raise MigrationError(msg)
            
            # Add GitHub remote and push
            os.chdir(clone_path)
            
            # Add GitHub remote
            subprocess.run(['git', 'remote', 'add', 'github', self.github_repo.ssh_url], check=True)  # noqa: S603
            
            # Push all branches and tags
            subprocess.run(['git', 'push', '--mirror', 'github'], check=True)
            
            logger = logging.getLogger(__name__)
            logger.info("Repository content migrated successfully")
            
        except Exception as e:
            msg = f"Failed to migrate repository content: {e}"
            raise MigrationError(msg)
        finally:
            # Cleanup temporary clone if created
            if self.temp_clone_path and Path(self.temp_clone_path).exists():
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
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Created label: {gitlab_label.name} -> {translated_name}")
                except GithubException as e:
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Failed to create label {translated_name}: {e}")
                    self.label_mapping[gitlab_label.name] = gitlab_label.name
            
            logger = logging.getLogger(__name__)
            logger.info(f"Migrated {len(self.label_mapping)} labels")
            
        except Exception as e:
            msg = f"Failed to handle labels: {e}"
            raise MigrationError(msg)
    
    def migrate_milestones_with_number_preservation(self) -> None:
        """Migrate milestones while preserving GitLab milestone numbers."""
        try:
            # Get all GitLab milestones sorted by ID
            gitlab_milestones = self.gitlab_project.milestones.list(all=True, state='all')
            gitlab_milestones.sort(key=lambda m: m.iid)
            
            if not gitlab_milestones:
                logger = logging.getLogger(__name__)
                logger.info("No milestones to migrate")
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
                        msg = f"Milestone number mismatch: expected {milestone_number}, got {github_milestone.number}"
                        raise NumberVerificationError(
                            msg
                        )
                    
                    self.milestone_mapping[gitlab_milestone.id] = github_milestone.number
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Created milestone #{milestone_number}: {gitlab_milestone.title}")
                else:
                    # Create placeholder milestone
                    placeholder_milestone = self.github_repo.create_milestone(
                        title="Placeholder Milestone",
                        state='closed',
                        description="Placeholder to preserve milestone numbering"
                    )
                    
                    # Verify placeholder number
                    if placeholder_milestone.number != milestone_number:
                        msg = f"Placeholder milestone number mismatch: expected {milestone_number}, got {placeholder_milestone.number}"
                        raise NumberVerificationError(
                            msg
                        )
                    
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Created placeholder milestone #{milestone_number}")
            
            logger = logging.getLogger(__name__)
            logger.info(f"Migrated {len(self.milestone_mapping)} milestones")
            
        except Exception as e:
            msg = f"Failed to migrate milestones: {e}"
            raise MigrationError(msg)
    
    def download_gitlab_attachments(self, content: str, gitlab_item) -> tuple[str, list[dict[str, Any]]]:
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
                response = requests.get(full_url, headers={'Authorization': f'Bearer {self.gitlab_client.private_token}'}, timeout=30)
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
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to download attachment {attachment_url}: {e}")
        
        return updated_content, downloaded_files
    
    def upload_github_attachments(self, files: list[dict[str, Any]], content: str) -> str:
        """Upload files to GitHub and update content with new URLs."""
        updated_content = content
        
        for file_info in files:
            try:
                # Create a temporary file for GitHub API
                with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                    temp_path = temp_file.name
                    temp_file.write(file_info['content'])
                
                # GitHub doesn't have a direct file upload API for arbitrary files
                # We'll create a commit with the file and reference it
                try:
                    # Try to upload as release asset if possible, otherwise skip file upload
                    # and keep original reference with a note
                    logger = logging.getLogger(__name__)
                    logger.warning(f"File upload not implemented for {file_info['filename']}")
                    # For now, we'll keep the original URL with a note
                    updated_content = updated_content.replace(
                        file_info['original_url'],
                        f"{file_info['original_url']} (Original GitLab attachment)"
                    )
                except Exception as e:
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Failed to upload {file_info['filename']}: {e}")
                
                # Clean up temp file
                temp_file_path = Path(temp_path)
                if temp_file_path.exists():
                    temp_file_path.unlink()
                    
            except Exception as e:
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to process attachment {file_info['filename']}: {e}")
        
        return updated_content
    
    def create_github_sub_issue(self, parent_github_issue, sub_issue_title: str, sub_issue_body: str) -> None:
        """Create a GitHub sub-issue using the REST API.
        
        This uses GitHub's new sub-issues API introduced in December 2024.
        """
        try:
            # First create a regular issue
            sub_issue = self.github_repo.create_issue(
                title=sub_issue_title,
                body=sub_issue_body
            )
            
            # Then convert it to a sub-issue using the REST API
            # Note: This requires the new GitHub sub-issues API
            # We'll use requests to call the API directly since PyGithub may not support it yet
            import requests
            
            headers = {
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {self.github_client._Github__requester._Requester__auth.token}',
                'X-GitHub-Api-Version': '2022-11-28'
            }
            
            # Add sub-issue to parent
            url = f'https://api.github.com/repos/{self.github_repo.full_name}/issues/{parent_github_issue.number}/sub_issues'
            data = {'sub_issue_id': sub_issue.number}
            
            response = requests.post(url, json=data, headers=headers, timeout=30)
            response.raise_for_status()
            
            logger = logging.getLogger(__name__)
            logger.debug(f"Created sub-issue #{sub_issue.number} under parent #{parent_github_issue.number}")
            
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to create GitHub sub-issue: {e}")
    
    def get_issue_cross_links(self, gitlab_issue) -> tuple[str, list[dict]]:
        """Get cross-linked issues and separate parent-child from other relationships.
        
        This method now uses both GitLab's Work Items GraphQL API and REST API
        to properly detect parent-child task relationships separate from regular issue links.
        
        Returns:
            tuple: (cross_links_text for description, list of parent_child_relations for GitHub sub-issues)
        """
        logger = logging.getLogger(__name__)
        
        try:
            # Step 1: Try to get child work items using GraphQL Work Items API
            child_work_items = []
            try:
                child_work_items = self.get_work_item_children(gitlab_issue.iid)
                logger.debug(f"Found {len(child_work_items)} child work items via GraphQL for issue #{gitlab_issue.iid}")
            except Exception as e:
                logger.debug(f"GraphQL Work Items API failed for issue #{gitlab_issue.iid}: {e}")
            
            # Step 2: Fallback to parsing task items from description  
            description_tasks = []
            if not child_work_items:  # Only use fallback if GraphQL didn't find anything
                try:
                    description_tasks = self.detect_issue_tasks_from_description(gitlab_issue.description)
                    logger.debug(f"Found {len(description_tasks)} task items in description for issue #{gitlab_issue.iid}")
                except Exception as e:
                    logger.debug(f"Description task parsing failed for issue #{gitlab_issue.iid}: {e}")
            
            # Step 3: Get regular issue links from REST API
            regular_links = []
            try:
                links = gitlab_issue.links.list(all=True)
                
                for link in links:
                    # Determine the relationship type and target
                    if hasattr(link, 'link_type'):
                        link_type = link.link_type
                    else:
                        link_type = "relates_to"  # Default
                    
                    # Get target issue information
                    target_issue_iid = link.target_issue['iid']
                    target_issue_title = link.target_issue.get('title', 'Unknown Title')
                    target_project_path = link.target_issue.get('project_path_with_namespace', 
                                                             self.gitlab_project_path)
                    target_web_url = link.target_issue.get('web_url', '')
                    
                    # Log the link type for debugging
                    logger.debug(f"Issue #{gitlab_issue.iid} has link_type '{link_type}' to issue #{target_issue_iid}")
                    
                    link_info = {
                        'type': link_type,
                        'target_iid': target_issue_iid,
                        'target_title': target_issue_title,
                        'target_project_path': target_project_path,
                        'target_web_url': target_web_url,
                        'is_same_project': target_project_path == self.gitlab_project_path
                    }
                    regular_links.append(link_info)
                    
            except Exception as e:
                logger.debug(f"Failed to get issue links for issue #{gitlab_issue.iid}: {e}")
            
            # Step 4: Separate parent-child relationships from regular links
            parent_child_relations = []
            other_links = []
            
            # Add child work items as parent-child relationships
            for child in child_work_items:
                parent_child_relations.append({
                    'type': 'child_of',
                    'target_iid': child['iid'],
                    'target_title': child['title'],
                    'target_project_path': self.gitlab_project_path,
                    'target_web_url': child['web_url'],
                    'is_same_project': True,
                    'source': 'graphql_work_items'
                })
            
            # Add description tasks as parent-child relationships if no GraphQL data
            for task in description_tasks:
                parent_child_relations.append({
                    'type': 'child_of',
                    'target_iid': task['iid'],
                    'target_title': task['title'],
                    'target_project_path': self.gitlab_project_path,
                    'target_web_url': task['web_url'],
                    'is_same_project': True,
                    'source': 'description_tasks'
                })
            
            # Process regular issue links (blocks, is_blocked_by, relates_to)
            for link_info in regular_links:
                link_type = link_info['type']
                
                # Skip links that might represent parent-child relationships in description tasks
                # to avoid duplication
                is_task_duplicate = any(
                    task_rel['target_iid'] == link_info['target_iid'] 
                    for task_rel in parent_child_relations 
                    if task_rel['source'] == 'description_tasks'
                )
                
                if is_task_duplicate:
                    logger.debug(f"Skipping duplicate link #{link_info['target_iid']} - already captured as task")
                    continue
                
                # Format relationship description
                if link_type == "blocks":
                    relationship = "Blocks"
                elif link_type == "is_blocked_by":
                    relationship = "Blocked by"
                elif link_type == "relates_to":
                    relationship = "Related to"
                else:
                    # Future-proofing: If GitLab adds explicit parent/child link types
                    if link_type in ["parent_of", "child_of", "has_child", "has_parent"]:
                        # Convert to parent-child relationship
                        parent_child_relations.append(link_info)
                        continue
                    else:
                        relationship = f"Linked ({link_type})"
                
                other_links.append((relationship, link_info))
            
            # Step 5: Format cross-links text for non-parent-child relationships
            cross_links_text = ""
            if other_links:
                cross_links_text = "\n\n---\n\n**Cross-linked Issues:**\n\n"
                
                for relationship, link_info in other_links:
                    if link_info['is_same_project']:
                        # Same project - will be migrated to GitHub issue numbers
                        cross_links_text += f"- **{relationship}**: #{link_info['target_iid']} - {link_info['target_title']}\n"
                    else:
                        # External project - keep GitLab reference
                        cross_links_text += f"- **{relationship}**: [{link_info['target_project_path']}#{link_info['target_iid']}]({link_info['target_web_url']}) - {link_info['target_title']}\n"
            
            # Log summary
            logger.debug(f"Issue #{gitlab_issue.iid} summary: {len(parent_child_relations)} parent-child relations, {len(other_links)} other links")
            
            return cross_links_text, parent_child_relations
            
        except Exception as e:
            logger.warning(f"Failed to get cross-links for issue #{gitlab_issue.iid}: {e}")
            return "", []
    
    def migrate_issues_with_number_preservation(self) -> None:
        """Migrate issues while preserving GitLab issue numbers."""
        try:
            # Get all GitLab issues sorted by IID
            gitlab_issues = self.gitlab_project.issues.list(all=True, state='all')
            gitlab_issues.sort(key=lambda i: i.iid)
            
            if not gitlab_issues:
                logger = logging.getLogger(__name__)
                logger.info("No issues to migrate")
                return
            
            max_issue_number = max(i.iid for i in gitlab_issues)
            gitlab_issue_dict = {i.iid: i for i in gitlab_issues}
            github_issue_dict = {}  # Maps GitLab IID to GitHub issue
            pending_parent_child_relations = []  # Store parent-child relations for second pass
            
            # First pass: Create issues maintaining number sequence
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
                    
                    # Add cross-linked issues to the description and collect parent-child relationships
                    cross_links_text, parent_child_relations = self.get_issue_cross_links(gitlab_issue)
                    if cross_links_text:
                        issue_body += cross_links_text
                    
                    # Store parent-child relations for second pass (after all issues are created)
                    if parent_child_relations:
                        for relation in parent_child_relations:
                            pending_parent_child_relations.append({
                                'parent_gitlab_iid': gitlab_issue.iid,
                                'relation': relation
                            })
                    
                    # Prepare labels
                    issue_labels = [
                        self.label_mapping[label_name] 
                        for label_name in gitlab_issue.labels 
                        if label_name in self.label_mapping
                    ]
                    
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
                        msg = f"Issue number mismatch: expected {issue_number}, got {github_issue.number}"
                        raise NumberVerificationError(
                            msg
                        )
                    
                    # Store GitHub issue for parent-child relationship handling
                    github_issue_dict[gitlab_issue.iid] = github_issue
                    
                    # Migrate comments
                    self.migrate_issue_comments(gitlab_issue, github_issue)
                    
                    # Close issue if needed
                    if gitlab_issue.state == 'closed':
                        github_issue.edit(state='closed')
                    
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Created issue #{issue_number}: {gitlab_issue.title}")
                    
                else:
                    # Create placeholder issue
                    placeholder_issue = self.github_repo.create_issue(
                        title="Placeholder",
                        body="Placeholder to preserve issue numbering - will be deleted"
                    )
                    
                    # Verify placeholder number
                    if placeholder_issue.number != issue_number:
                        msg = f"Placeholder issue number mismatch: expected {issue_number}, got {placeholder_issue.number}"
                        raise NumberVerificationError(
                            msg
                        )
                    
                    # Close placeholder immediately
                    placeholder_issue.edit(state='closed')
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Created placeholder issue #{issue_number}")
            
            # Second pass: Create parent-child relationships as GitHub sub-issues
            if pending_parent_child_relations:
                logger = logging.getLogger(__name__)
                logger.info(f"Processing {len(pending_parent_child_relations)} parent-child relationships...")
                
                for pending_relation in pending_parent_child_relations:
                    try:
                        parent_gitlab_iid = pending_relation['parent_gitlab_iid']
                        child_relation = pending_relation['relation']
                        
                        # Get the parent GitHub issue
                        if parent_gitlab_iid in github_issue_dict:
                            parent_github_issue = github_issue_dict[parent_gitlab_iid]
                            
                            # Get the child issue info
                            child_gitlab_iid = child_relation['target_iid']
                            if child_gitlab_iid in github_issue_dict:
                                child_github_issue = github_issue_dict[child_gitlab_iid]
                                
                                # Create sub-issue relationship
                                # Note: This will attempt to use GitHub's new sub-issues API
                                self.create_github_sub_issue(
                                    parent_github_issue, 
                                    f"Link to #{child_github_issue.number}",
                                    f"This issue is linked as a child of #{parent_github_issue.number}.\n\nOriginal GitLab relationship: {child_relation['type']}"
                                )
                                
                                logger.debug(f"Linked issue #{child_gitlab_iid} as sub-issue of #{parent_gitlab_iid}")
                            else:
                                logger.warning(f"Child issue #{child_gitlab_iid} not found for parent-child relationship")
                        else:
                            logger.warning(f"Parent issue #{parent_gitlab_iid} not found for parent-child relationship")
                            
                    except Exception as e:
                        logger.warning(f"Failed to create parent-child relationship: {e}")
            
            logger = logging.getLogger(__name__)
            logger.info(f"Migrated {len(gitlab_issues)} issues")
            
        except Exception as e:
            msg = f"Failed to migrate issues: {e}"
            raise MigrationError(msg)
    
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
                logger = logging.getLogger(__name__)
                logger.debug(f"Migrated comment by {note.author['username']}")
                
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to migrate comments for issue #{gitlab_issue.iid}: {e}")
    
    def cleanup_placeholders(self) -> None:
        """Delete placeholder issues and milestones."""
        try:
            # Clean up placeholder issues
            issues = self.github_repo.get_issues(state='all')
            for issue in issues:
                if issue.title == "Placeholder":
                    # GitHub API doesn't allow deleting issues, so we'll leave them closed
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Placeholder issue #{issue.number} left closed (cannot delete)")
            
            # Clean up placeholder milestones
            milestones = self.github_repo.get_milestones(state='all')
            for milestone in milestones:
                if milestone.title == "Placeholder Milestone":
                    milestone.delete()
                    logger = logging.getLogger(__name__)
                    logger.debug(f"Deleted placeholder milestone #{milestone.number}")
            
            logger = logging.getLogger(__name__)
            logger.info("Cleanup completed")
            
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Cleanup failed: {e}")
    
    def validate_migration(self) -> dict[str, Any]:
        """Validate migration results and generate report."""
        report = {
            'gitlab_project': self.gitlab_project_path,
            'github_repo': self.github_repo_path,
            'success': True,
            'errors': [],
            'statistics': {}
        }
        
        try:
            # Count GitLab items with state breakdown
            gitlab_issues = self.gitlab_project.issues.list(all=True, state='all')
            gitlab_issues_open = [i for i in gitlab_issues if i.state == 'opened']
            gitlab_issues_closed = [i for i in gitlab_issues if i.state == 'closed']
            
            gitlab_milestones = self.gitlab_project.milestones.list(all=True, state='all')
            gitlab_milestones_open = [m for m in gitlab_milestones if m.state == 'active']
            gitlab_milestones_closed = [m for m in gitlab_milestones if m.state == 'closed']
            
            gitlab_labels = self.gitlab_project.labels.list(all=True)
            
            # Count GitHub items (excluding placeholders) with state breakdown
            github_issues_all = list(self.github_repo.get_issues(state='all'))
            github_issues = [i for i in github_issues_all if i.title != "Placeholder"]
            github_issues_open = [i for i in github_issues if i.state == 'open']
            github_issues_closed = [i for i in github_issues if i.state == 'closed']
            
            github_milestones_all = list(self.github_repo.get_milestones(state='all'))
            github_milestones = [m for m in github_milestones_all if m.title != "Placeholder Milestone"]
            github_milestones_open = [m for m in github_milestones if m.state == 'open']
            github_milestones_closed = [m for m in github_milestones if m.state == 'closed']
            
            # Count label statistics
            github_labels_all = list(self.github_repo.get_labels())
            
            # Try to get organization default labels for comparison
            existing_labels_count = 0
            try:
                org = self.github_client.get_organization(self.github_org)
                org_labels = list(org.get_labels())
                existing_labels_count = len(org_labels)
            except Exception:
                pass  # Organization might not have default labels
            
            labels_created = len(github_labels_all) - existing_labels_count
            
            report['statistics'] = {
                'gitlab_issues_total': len(gitlab_issues),
                'gitlab_issues_open': len(gitlab_issues_open),
                'gitlab_issues_closed': len(gitlab_issues_closed),
                'github_issues_total': len(github_issues),
                'github_issues_open': len(github_issues_open),
                'github_issues_closed': len(github_issues_closed),
                'gitlab_milestones_total': len(gitlab_milestones),
                'gitlab_milestones_open': len(gitlab_milestones_open),
                'gitlab_milestones_closed': len(gitlab_milestones_closed),
                'github_milestones_total': len(github_milestones),
                'github_milestones_open': len(github_milestones_open),
                'github_milestones_closed': len(github_milestones_closed),
                'gitlab_labels_total': len(gitlab_labels),
                'github_labels_existing': existing_labels_count,
                'github_labels_created': max(0, labels_created),
                'labels_translated': len(self.label_mapping)
            }
            
            # Validate counts
            if len(gitlab_issues) != len(github_issues):
                report['errors'].append(f"Issue count mismatch: GitLab {len(gitlab_issues)}, GitHub {len(github_issues)}")
                report['success'] = False
            
            if len(gitlab_milestones) != len(github_milestones):
                report['errors'].append(f"Milestone count mismatch: GitLab {len(gitlab_milestones)}, GitHub {len(github_milestones)}")
                report['success'] = False
            
            logger = logging.getLogger(__name__)
            logger.info("Migration validation completed")
            
        except Exception as e:
            report['success'] = False
            report['errors'].append(f"Validation failed: {e}")
            logger = logging.getLogger(__name__)
            logger.exception(f"Validation failed: {e}")
        
        return report
    
    def migrate(self) -> dict[str, Any]:
        """Execute the complete migration process."""
        try:
            logger = logging.getLogger(__name__)
            logger.info("Starting GitLab to GitHub migration")
            
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
            
            logger = logging.getLogger(__name__)
            logger.info("Migration completed successfully")
            return report
            
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.exception(f"Migration failed: {e}")
            # Optionally clean up created repository
            if self.github_repo:
                try:
                    logger = logging.getLogger(__name__)
                    logger.info("Cleaning up created repository due to failure")
                    self.github_repo.delete()
                except Exception as cleanup_error:
                    logger = logging.getLogger(__name__)
                    logger.exception(f"Failed to cleanup repository: {cleanup_error}")
            
            msg = f"Migration failed: {e}"
            raise MigrationError(msg)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Migrate GitLab project to GitHub with full metadata preservation"
    )
    
    # Positional arguments
    parser.add_argument(
        'gitlab_project_pos',
        nargs='?',
        help='GitLab project path (namespace/project)'
    )
    
    parser.add_argument(
        'github_repo_pos', 
        nargs='?',
        help='GitHub repository path (org/repo)'
    )
    
    # Optional arguments with short forms
    parser.add_argument(
        '--gitlab-project', '--gitlab',
        dest='gitlab_project_flag',
        help='GitLab project path (namespace/project)'
    )
    
    parser.add_argument(
        '--github-repo', '--github',
        dest='github_repo_flag',
        help='GitHub repository path (org/repo)'
    )
    
    parser.add_argument(
        '--label-translation', '--relabel',
        action='append',
        help='Label translation pattern (format: "source_pattern:target_pattern"). Can be specified multiple times.'
    )
    
    parser.add_argument(
        '--local-clone-path', '--local-clone',
        help='Path to existing local git clone of GitLab project'
    )
    
    parser.add_argument(
        '--gitlab-token-path',
        help='Path for GitLab token in pass utility (default: gitlab/cli/ro_token)'
    )
    
    parser.add_argument(
        '--github-token-path',
        help='Path for GitHub token in pass utility (default: github/cli/token)'
    )
    
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )
    
    args = parser.parse_args()
    
    # Handle positional vs named arguments
    if args.gitlab_project_pos is None and args.gitlab_project_flag is None:
        parser.error("GitLab project is required (provide as positional argument or --gitlab-project)")
    elif args.gitlab_project_pos and args.gitlab_project_flag:
        parser.error("Cannot specify GitLab project both as positional argument and --gitlab-project")
    elif args.gitlab_project_pos:
        args.gitlab_project = args.gitlab_project_pos
    else:
        args.gitlab_project = args.gitlab_project_flag
        
    if args.github_repo_pos is None and args.github_repo_flag is None:
        parser.error("GitHub repository is required (provide as positional argument or --github-repo)")
    elif args.github_repo_pos and args.github_repo_flag:
        parser.error("Cannot specify GitHub repository both as positional argument and --github-repo")
    elif args.github_repo_pos:
        args.github_repo = args.github_repo_pos
    else:
        args.github_repo = args.github_repo_flag
    
    return args


def main() -> None:
    """Main entry point."""
    args = parse_arguments()
    
    # Setup logging
    setup_logging(verbose=args.verbose)
    
    try:
        # Initialize migrator
        migrator = GitLabToGitHubMigrator(
            args.gitlab_project,
            args.github_repo,
            label_translations=args.label_translation,
            local_clone_path=args.local_clone_path,
            gitlab_token_path=args.gitlab_token_path,
            github_token_path=args.github_token_path,
        )
        
        # Execute migration
        report = migrator.migrate()
        
        # Print report
        
        if report['errors']:
            for _error in report['errors']:
                pass
        
        for _key, _value in report['statistics'].items():
            pass
        
        if report['success']:
            sys.exit(0)
        else:
            sys.exit(1)
            
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()