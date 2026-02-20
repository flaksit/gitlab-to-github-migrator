#!/usr/bin/env python
"""Create GitLab test project for migration testing.

This script creates a GitLab project with test data covering all edge cases
for the gitlab-to-github-migrator. Manual steps for attachments are printed
at the end.

Prerequisites:
- SOURCE_GITLAB_TOKEN environment variable set, or token stored in pass at gitlab/api/rw_token
- git installed and configured for SSH access to GitLab
"""

import argparse
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from gitlab import Gitlab, GraphQL
from gitlab.exceptions import GitlabCreateError, GitlabDeleteError, GitlabGetError

from . import gitlab_utils as glu
from .issue_builder import LAST_EDITED_THRESHOLD_SECONDS
from .utils import setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from gitlab.v4.objects import Project

GITLAB_URL = "https://gitlab.com"

logger = logging.getLogger(__name__)


def get_gitlab_token(pass_path: str | None = None) -> str:
    """Get GitLab token from environment or pass. Requires write access."""
    token = glu.get_readwrite_token(pass_path=pass_path)
    if token:
        return token
    msg = (
        f"GitLab token required. Set {glu.GITLAB_TOKEN_ENV_VAR} "
        f"or store in pass at {glu.DEFAULT_GITLAB_RW_TOKEN_PASS_PATH}"
    )
    raise ValueError(msg)


def run_git(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a git command."""
    logger.info(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd)


def get_or_create_project(gl: Gitlab, project_path: str) -> Project:
    """Create the test project or get it if it already exists."""
    logger.info("\n[1/8] Creating project...")

    # Handle nested namespaces like "group/subgroup/project"
    path_parts = project_path.split("/")
    namespace = "/".join(path_parts[:-1])
    project_name = path_parts[-1]

    try:
        namespaces = gl.namespaces.list(search=namespace)
        if not namespaces:
            logger.error(f"Namespace '{namespace}' not found")
            sys.exit(1)
        namespace_id = namespaces[0].id

        project = gl.projects.create(
            {
                "name": project_name,
                "namespace_id": namespace_id,
                "visibility": "public",
                "description": "Test project for gitlab-to-github-migrator (https://github.com/flaksit/gitlab-to-github-migrator)",
                "default_branch": "main",
            }
        )
        logger.info(f"    Created project: {project.web_url}")
    except GitlabCreateError as e:
        if "has already been taken" in str(e):
            logger.info("    Project already exists, fetching...")
            project = gl.projects.get(project_path)
            logger.info(f"    Found project: {project.web_url}")
        else:
            raise

    logger.info(f"    Project ID: {project.id}")
    return project


def create_labels(project: Project) -> None:
    """Create test labels."""
    logger.info("\n[2/8] Creating labels...")
    labels = [
        ("p_high", "#FF0000", "High priority"),
        ("p_low", "#00FF00", "Low priority"),
        ("bug", "#D73A4A", "Bug"),
        ("feature", "#0075CA", "Feature"),
        ("documentation", "#0052CC", "Documentation"),
    ]
    for name, color, desc in labels:
        try:
            project.labels.create({"name": name, "color": color, "description": desc})
            logger.info(f"    Created label: {name}")
        except GitlabCreateError:
            logger.info(f"    Label already exists: {name}")


def create_milestones(project: Project) -> tuple[int, int]:
    """Create test milestones with a gap at #2. Returns (ms1_id, ms3_id)."""
    logger.info("\n[3/8] Creating milestones (with gap at #2)...")

    # Milestone v1.0
    try:
        ms1 = project.milestones.create({"title": "v1.0", "due_date": "2024-06-15"})
        logger.info(f"    Created milestone: v1.0 (iid={ms1.iid})")
    except GitlabCreateError:
        ms1 = next(m for m in project.milestones.list(get_all=True) if m.title == "v1.0")
        logger.info(f"    Milestone already exists: v1.0 (iid={ms1.iid})")

    # Dummy milestone to create gap
    try:
        ms_dummy = project.milestones.create({"title": "DELETE-ME"})
        logger.info(f"    Created placeholder milestone: DELETE-ME (iid={ms_dummy.iid})")
        ms_dummy.delete()
        logger.info("    Deleted placeholder milestone to create gap")
    except GitlabCreateError:
        logger.info("    Placeholder milestone already handled")

    # Milestone v2.0
    try:
        ms3 = project.milestones.create({"title": "v2.0", "due_date": "2025-12-31"})
        logger.info(f"    Created milestone: v2.0 (iid={ms3.iid})")
    except GitlabCreateError:
        ms3 = next(m for m in project.milestones.list(get_all=True) if m.title == "v2.0")
        logger.info(f"    Milestone already exists: v2.0 (iid={ms3.iid})")

    # Close v1.0
    ms1.state_event = "close"
    ms1.save()
    logger.info("    Closed milestone v1.0")

    return ms1.id, ms3.id


def get_task_type_id(gql: GraphQL, project_path: str) -> str:
    """Get the Task work item type ID for the project."""
    query = """
    query GetWorkItemTypes($fullPath: ID!) {
        namespace(fullPath: $fullPath) {
            workItemTypes {
                nodes {
                    id
                    name
                }
            }
        }
    }
    """
    data = gql.execute(query, variable_values={"fullPath": project_path})
    types = data.get("namespace", {}).get("workItemTypes", {}).get("nodes", [])
    for t in types:
        if t.get("name") == "Task":
            task_id = t.get("id")
            if task_id:
                return task_id
    msg = f"Could not find Task work item type for project {project_path}"
    raise RuntimeError(msg)


def get_work_item_id(gql: GraphQL, project_path: str, iid: int) -> str:
    """Get the work item ID for an issue by IID."""
    query = """
    query GetWorkItemId($fullPath: ID!, $iid: String!) {
        namespace(fullPath: $fullPath) {
            workItem(iid: $iid) {
                id
            }
        }
    }
    """
    data = gql.execute(query, variable_values={"fullPath": project_path, "iid": str(iid)})
    work_item_id = data.get("namespace", {}).get("workItem", {}).get("id")
    if not work_item_id:
        msg = f"Could not find work item ID for issue #{iid} in project {project_path}"
        raise RuntimeError(msg)
    return work_item_id


def create_task_with_parent(gql: GraphQL, project_path: str, title: str, description: str, parent_iid: int) -> None:
    """Create a Task work item with a parent issue using GraphQL."""
    task_type_id = get_task_type_id(gql, project_path)
    parent_id = get_work_item_id(gql, project_path, parent_iid)

    mutation = """
    mutation CreateTaskWithParent(
        $projectPath: ID!,
        $title: String!,
        $description: String!,
        $workItemTypeId: WorkItemsTypeID!,
        $parentId: WorkItemID!
    ) {
        workItemCreate(input: {
            namespacePath: $projectPath,
            title: $title,
            descriptionWidget: { description: $description },
            workItemTypeId: $workItemTypeId,
            hierarchyWidget: { parentId: $parentId }
        }) {
            workItem {
                iid
                title
            }
            errors
        }
    }
    """

    result = gql.execute(
        mutation,
        variable_values={
            "projectPath": project_path,
            "title": title,
            "description": description,
            "workItemTypeId": task_type_id,
            "parentId": parent_id,
        },
    )
    errors = result.get("workItemCreate", {}).get("errors", [])
    if errors:
        msg = f"GraphQL errors creating task: {errors}"
        raise RuntimeError(msg)
    work_item = result.get("workItemCreate", {}).get("workItem", {})
    logger.info(f"    Created task: #{work_item.get('iid')} {work_item.get('title')} (child of #{parent_iid})")


def create_issues(project: Project, gql: GraphQL, project_path: str, ms1_id: int, ms3_id: int) -> None:
    """Create test issues with a gap at #4, tasks at #5-6, and issues at #7-8."""
    logger.info("\n[4/8] Creating issues (with gap at #4, tasks at #5-6)...")

    existing_issues = {i.title: i for i in project.issues.list(get_all=True, state="all")}

    # First batch: issues #1-4
    first_batch: list[tuple[str, str, list[str], int | None]] = [
        ("Basic issue", "A basic test issue for migration testing.", ["bug"], ms1_id),
        ("Issue with attachments", "REPLACE_WITH_ATTACHMENTS", ["documentation"], None),
        ("Parent issue", "This is a parent issue with child work items.", ["feature"], ms3_id),
        ("DELETE-ME", "Placeholder", [], None),
    ]

    for title, desc, issue_labels, milestone_id in first_batch:
        if title in existing_issues:
            logger.info(f"    Issue already exists: #{existing_issues[title].iid} {title}")
        else:
            issue_data: dict[str, object] = {
                "title": title,
                "description": desc,
                "labels": issue_labels,
            }
            if milestone_id:
                issue_data["milestone_id"] = milestone_id
            issue = project.issues.create(issue_data)
            logger.info(f"    Created issue: #{issue.iid} {title}")

    # Delete dummy issue #4 if it exists
    try:
        dummy_issue = project.issues.get(4)
        if dummy_issue.title == "DELETE-ME":
            dummy_issue.delete()
            logger.info("    Deleted placeholder issue #4 to create gap")
    except (GitlabGetError, GitlabDeleteError):
        logger.info("    Placeholder issue #4 already deleted or doesn't exist")

    # Create Tasks #5 and #6 (children of issue #3) via GraphQL
    tasks_to_create = [
        ("Child task 1 (open)", "REPLACE_WITH_TASK_ATTACHMENT"),
        ("Child task 2 (closed)", "Closed task child of #3"),
    ]
    for task_title, task_desc in tasks_to_create:
        if task_title not in existing_issues:
            create_task_with_parent(gql, project_path, task_title, task_desc, parent_iid=3)
        else:
            logger.info(f"    Task already exists: #{existing_issues[task_title].iid} {task_title}")

    # Close task #6
    task6 = project.issues.get(6)
    if task6.state != "closed":
        task6.state_event = "close"
        task6.save()
        logger.info("    Closed task #6")
    else:
        logger.info("    Task #6 already closed")

    # Second batch: issues #7-8
    second_batch: list[tuple[str, str, list[str], int | None]] = [
        ("Blocking issue", "This issue blocks #8", ["p_low"], None),
        ("Blocked issue", "This issue is blocked by #7", ["feature"], None),
    ]

    for title, desc, issue_labels, milestone_id in second_batch:
        if title in existing_issues:
            logger.info(f"    Issue already exists: #{existing_issues[title].iid} {title}")
        else:
            issue_data = {
                "title": title,
                "description": desc,
                "labels": issue_labels,
            }
            if milestone_id:
                issue_data["milestone_id"] = milestone_id
            issue = project.issues.create(issue_data)
            logger.info(f"    Created issue: #{issue.iid} {title}")


def setup_issue_relationships(project: Project) -> None:
    """Set up issue links (blocking, related). Parent-child is set during Task creation."""
    logger.info("\n[5/8] Setting up issue relationships...")

    project_id = int(project.id)  # pyright: ignore[reportUnknownArgumentType]
    issue1 = project.issues.get(1)
    issue2 = project.issues.get(2)
    issue7 = project.issues.get(7)
    issue8 = project.issues.get(8)

    # Note: Parent-child relationship (#3 -> #5, #3 -> #6) is created during Task creation in create_issues()

    # Regular issue links (blocks, relates_to) via REST API
    links_to_create = [
        (issue7, issue8.iid, "blocks", "#7 blocks #8"),
        (issue1, issue2.iid, "relates_to", "#1 relates to #2"),
    ]

    for source_issue, target_iid, link_type, description in links_to_create:
        try:
            source_issue.links.create(
                {
                    "target_project_id": project_id,
                    "target_issue_iid": target_iid,
                    "link_type": link_type,
                }
            )
            logger.info(f"    Created link: {description}")
        except GitlabCreateError as e:
            if "already exists" in str(e).lower():
                logger.info(f"    Link already exists: {description}")
            elif link_type == "blocks" and "not available" in str(e).lower():
                # Fall back to relates_to if blocking not available (requires premium license)
                logger.info(f"    Blocking not available, falling back to relates_to for {description}")
                source_issue.links.create(
                    {
                        "target_project_id": project_id,
                        "target_issue_iid": target_iid,
                        "link_type": "relates_to",
                    }
                )
                logger.info("    Created link: #7 relates to #8 (fallback)")
            else:
                raise


def add_comments_and_close_issue(project: Project) -> None:
    """Add a comment to issue #1 and close it."""
    logger.info("\n[6/8] Adding comments and system notes...")

    issue1 = project.issues.get(1)

    # Check if comment already exists
    notes = issue1.notes.list(get_all=True)
    has_comment = any("This is a regular comment" in (n.body or "") for n in notes)
    if not has_comment:
        issue1.notes.create({"body": "This is a regular comment on the basic issue."})
        logger.info("    Added comment to issue #1")
    else:
        logger.info("    Comment already exists on issue #1")

    # Close issue #1 to generate system note
    if issue1.state != "closed":
        issue1.state_event = "close"
        issue1.save()
        logger.info("    Closed issue #1")
    else:
        logger.info("    Issue #1 already closed")


def update_test_data_for_last_edited(project: Project) -> None:
    """Update comments to test last edited timestamp feature.

    Updates are made with smart waiting - only waiting the minimum necessary time
    based on when objects were created to ensure >1 minute difference.
    """
    import datetime as dt

    logger.info("\n[7.5/8] Updating test data for last edited timestamp tests...")

    def parse_time(timestamp: str) -> dt.datetime:
        """Parse GitLab timestamp."""
        return dt.datetime.fromisoformat(timestamp)

    def wait_if_needed(created_at: str, item_name: str) -> None:
        """Wait only if we need to reach threshold + buffer seconds since creation."""
        created = parse_time(created_at)
        now = dt.datetime.now(dt.UTC)
        elapsed = (now - created).total_seconds()

        # Add 5 second buffer to ensure we're well past the threshold
        required_elapsed = LAST_EDITED_THRESHOLD_SECONDS + 5
        if elapsed < required_elapsed:
            wait_time = int(required_elapsed - elapsed) + 1
            logger.info(f"    Waiting {wait_time} seconds before updating {item_name}...")
            time.sleep(wait_time)
        else:
            logger.info(f"    No wait needed for {item_name} (created {int(elapsed)} seconds ago)")

    # Update comment only (not milestones or issues)
    _update_comment(project, wait_if_needed)


def _update_comment(project: Project, wait_if_needed: Callable[[str, str], None]) -> None:
    """Update comment test data."""
    issue1 = project.issues.get(1)
    notes = issue1.notes.list(get_all=True)
    existing_comment = None
    for note in notes:
        if not note.system and "This is a regular comment" in (note.body or ""):
            existing_comment = note
            break

    if existing_comment:
        if "EDITED" not in existing_comment.body:
            wait_if_needed(existing_comment.created_at, "comment on issue #1")
            existing_comment.body = "This is a regular comment on the basic issue. EDITED after creation."
            existing_comment.save()
            logger.info("    Updated existing comment on issue #1")
        else:
            logger.info("    Comment on issue #1 already updated")
    else:
        logger.info("    No existing comment found on issue #1 to update")


def create_git_content(project_path: str) -> None:
    """Create a feature branch and tag in the repository."""
    logger.info("\n[7/8] Creating git content (branch and tag)...")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        run_git(["git", "clone", f"git@gitlab.com:{project_path}.git", str(repo_path)])

        # Check if main branch exists
        result = subprocess.run(
            ["git", "branch", "-r", "--list", "origin/main"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        main_exists = bool(result.stdout.strip())

        # Create main branch if it doesn't exist
        if not main_exists:
            # Check if we have any local commits (orphan branch scenario)
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=False,
            )
            has_commits = result.returncode == 0

            if has_commits:
                # We have commits but no main - create main from current HEAD
                run_git(["git", "checkout", "-b", "main"], cwd=repo_path)
            else:
                # Empty repo - create initial commit with full README
                run_git(["git", "checkout", "-b", "main"], cwd=repo_path)
                readme_file = repo_path / "README.md"
                project_name = project_path.rsplit("/", maxsplit=1)[-1]
                readme_content = f"""# {project_name}

Test project for [gitlab-to-github-migrator](https://github.com/flaksit/gitlab-to-github-migrator).

This project contains test data for verifying migration functionality:
- Issues with various states (open, closed)
- Milestones (with gaps in numbering)
- Labels
- Issue relationships (parent-child, blocking, related)
- Comments
- Attachments
- Branches and tags
"""
                readme_file.write_text(readme_content)
                run_git(["git", "add", "README.md"], cwd=repo_path)
                run_git(["git", "commit", "-m", "Initial commit"], cwd=repo_path)

            run_git(["git", "push", "-u", "origin", "main"], cwd=repo_path)
            logger.info("    Created and pushed main branch")
        else:
            logger.info("    Branch main already exists")

        # Check if feature branch exists
        result = subprocess.run(
            ["git", "branch", "-r", "--list", "origin/feature/sample"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        feature_branch_exists = bool(result.stdout.strip())

        if not feature_branch_exists:
            run_git(["git", "checkout", "main"], cwd=repo_path)
            run_git(["git", "checkout", "-b", "feature/sample"], cwd=repo_path)
            sample_file = repo_path / "sample.txt"
            sample_file.write_text("Sample feature file for testing branch migration.\n")
            run_git(["git", "add", "sample.txt"], cwd=repo_path)
            run_git(["git", "commit", "-m", "Add sample feature file"], cwd=repo_path)
            run_git(["git", "push", "-u", "origin", "feature/sample"], cwd=repo_path)
            logger.info("    Created and pushed feature/sample branch")
        else:
            logger.info("    Branch feature/sample already exists")

        # Check if tag exists
        result = subprocess.run(
            ["git", "tag", "-l", "v1.0.0"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        tag_exists = bool(result.stdout.strip())

        if not tag_exists:
            run_git(["git", "checkout", "main"], cwd=repo_path)
            run_git(["git", "tag", "-a", "v1.0.0", "-m", "First stable release"], cwd=repo_path)
            run_git(["git", "push", "--tags"], cwd=repo_path)
            logger.info("    Created and pushed v1.0.0 tag")
        else:
            logger.info("    Tag v1.0.0 already exists")


def print_manual_instructions(project_path: str) -> None:
    """Print instructions for manual steps that can't be automated."""
    logger.info("\n[8/8] MANUAL STEPS REQUIRED")
    logger.info("=" * 60)
    logger.info(
        f"""
The following steps must be done manually via the GitLab web UI:

STEP 1: Add attachments to Issue #2
---------------------------------------
1. Go to: https://gitlab.com/{project_path}/-/issues/2
2. Click "Edit"
3. Delete "REPLACE_WITH_ATTACHMENTS" from the description
4. Drag and drop these files into the description:
   - A PNG image (any small test image, e.g., screenshot)
   - A PDF document (any small PDF file)
5. The description should look like:

   This issue has multiple attachments:

   ![test-image](/uploads/abc123.../test-image.png)

   [document.pdf](/uploads/def456.../document.pdf)

6. Click "Save changes"
7. COPY the markdown for the PNG image (you'll need it for Steps 2 and 3)

STEP 2: Add attachment to Task #5
---------------------------------------
1. Go to: https://gitlab.com/{project_path}/-/issues/5
2. Click "Edit"
3. Delete "REPLACE_WITH_TASK_ATTACHMENT" from the description
4. Drag and drop a different image (e.g., another screenshot)
5. Click "Save changes"

This tests that the migrator handles attachments on child tasks.

STEP 3: Add comment with same attachment URL to Issue #8
---------------------------------------
1. Go to: https://gitlab.com/{project_path}/-/issues/8
2. In the comment box, PASTE the exact PNG markdown from Issue #2
   (e.g., ![test-image](/uploads/abc123.../test-image.png))
3. Do NOT drag-and-drop a new file - use the SAME URL
4. Click "Comment"

This tests that the migrator caches attachments by URL.


Project URL: https://gitlab.com/{project_path}
"""
    )
    logger.info("=" * 60)
    logger.info("Script completed successfully!")


def create_test_project(project_path: str, gitlab_token_pass_path: str | None = None) -> None:
    """
    Create a GitLab test project with comprehensive test data.

    This function contains the main business logic for creating a test project
    that can be used for migration testing.

    Args:
        project_path: GitLab project path (e.g., 'namespace/project' or 'group/subgroup/project')
        gitlab_token_pass_path: Optional path in pass utility for GitLab token
    """
    logger.info("=" * 60)
    logger.info("Creating GitLab test project for migration testing")
    logger.info(f"Project: {project_path}")
    logger.info("=" * 60)

    token = get_gitlab_token(pass_path=gitlab_token_pass_path)
    gl = Gitlab(GITLAB_URL, private_token=token)
    gql = GraphQL(GITLAB_URL, token=token)

    project = get_or_create_project(gl, project_path)
    create_labels(project)
    ms1_id, ms3_id = create_milestones(project)
    create_issues(project, gql, project_path, ms1_id, ms3_id)
    setup_issue_relationships(project)
    add_comments_and_close_issue(project)
    create_git_content(project_path)
    update_test_data_for_last_edited(project)
    print_manual_instructions(project_path)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a GitLab test project for migration testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s flaks/migrator-test-project
  %(prog)s mygroup/subgroup/test-project
  %(prog)s flaks/test --gitlab-token-pass-path gitlab/admin/token
""",
    )
    parser.add_argument(
        "project_path",
        help="GitLab project path (e.g., 'namespace/project' or 'group/subgroup/project')",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--gitlab-token-pass-path",
        help=f"Path for GitLab token in pass utility. If not set, will use {glu.GITLAB_TOKEN_ENV_VAR} env var, "
        f"or fall back to default pass path {glu.DEFAULT_GITLAB_RW_TOKEN_PASS_PATH}.",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point - handles argument parsing and logging setup."""
    args = parse_args()
    setup_logging(verbose=args.verbose)
    gitlab_token_pass_path: str | None = getattr(args, "gitlab_token_pass_path", None)
    create_test_project(args.project_path, gitlab_token_pass_path=gitlab_token_pass_path)


if __name__ == "__main__":
    main()
