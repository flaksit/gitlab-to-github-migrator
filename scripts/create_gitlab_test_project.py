#!/usr/bin/env python
"""Create GitLab test project for migration testing.

This script creates a GitLab project with test data covering all edge cases
for the gitlab-to-github-migrator. Manual steps for attachments are printed
at the end.

Prerequisites:
- GITLAB_TOKEN environment variable set, or token stored in pass at gitlab/api/rw_token
- git installed and configured for SSH access to GitLab
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from gitlab import Gitlab, GraphQL
from gitlab.exceptions import GitlabCreateError, GitlabDeleteError, GitlabGetError

if TYPE_CHECKING:
    from gitlab.v4.objects import Project

GITLAB_URL = "https://gitlab.com"


def get_gitlab_token() -> str:
    """Get GitLab token from environment or pass. Requires write access."""
    token = os.environ.get("GITLAB_TOKEN")
    if not token:
        try:
            result = subprocess.run(
                ["pass", "gitlab/api/rw_token"],
                capture_output=True,
                text=True,
                check=True,
            )
            token = result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            msg = "GitLab token required. Set GITLAB_TOKEN or store in pass at gitlab/api/rw_token"
            raise ValueError(msg) from e
    return token


def run_git(cmd: list[str], cwd: Path | None = None) -> None:
    """Run a git command."""
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=cwd)


def get_or_create_project(gl: Gitlab, project_path: str) -> Project:
    """Create the test project or get it if it already exists."""
    print("\n[1/8] Creating project...")

    # Handle nested namespaces like "group/subgroup/project"
    path_parts = project_path.split("/")
    namespace = "/".join(path_parts[:-1])
    project_name = path_parts[-1]

    try:
        namespaces = gl.namespaces.list(search=namespace)
        if not namespaces:
            print(f"ERROR: Namespace '{namespace}' not found")
            sys.exit(1)
        namespace_id = namespaces[0].id

        project = gl.projects.create({
            "name": project_name,
            "namespace_id": namespace_id,
            "visibility": "public",
            "description": "Test project for gitlab-to-github-migrator (https://github.com/flaksit/gitlab-to-github-migrator)",
            "default_branch": "main",
        })
        print(f"    Created project: {project.web_url}")
    except GitlabCreateError as e:
        if "has already been taken" in str(e):
            print("    Project already exists, fetching...")
            project = gl.projects.get(project_path)
            print(f"    Found project: {project.web_url}")
        else:
            raise

    print(f"    Project ID: {project.id}")
    return project


def create_labels(project: Project) -> None:
    """Create test labels."""
    print("\n[2/8] Creating labels...")
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
            print(f"    Created label: {name}")
        except GitlabCreateError:
            print(f"    Label already exists: {name}")


def create_milestones(project: Project) -> tuple[int, int]:
    """Create test milestones with a gap at #2. Returns (ms1_id, ms3_id)."""
    print("\n[3/8] Creating milestones (with gap at #2)...")

    # Milestone v1.0
    try:
        ms1 = project.milestones.create({"title": "v1.0", "due_date": "2024-06-15"})
        print(f"    Created milestone: v1.0 (iid={ms1.iid})")
    except GitlabCreateError:
        ms1 = next(m for m in project.milestones.list(get_all=True) if m.title == "v1.0")
        print(f"    Milestone already exists: v1.0 (iid={ms1.iid})")

    # Dummy milestone to create gap
    try:
        ms_dummy = project.milestones.create({"title": "DELETE-ME"})
        print(f"    Created placeholder milestone: DELETE-ME (iid={ms_dummy.iid})")
        ms_dummy.delete()
        print("    Deleted placeholder milestone to create gap")
    except GitlabCreateError:
        print("    Placeholder milestone already handled")

    # Milestone v2.0
    try:
        ms3 = project.milestones.create({"title": "v2.0", "due_date": "2025-12-31"})
        print(f"    Created milestone: v2.0 (iid={ms3.iid})")
    except GitlabCreateError:
        ms3 = next(m for m in project.milestones.list(get_all=True) if m.title == "v2.0")
        print(f"    Milestone already exists: v2.0 (iid={ms3.iid})")

    # Close v1.0
    ms1.state_event = "close"
    ms1.save()
    print("    Closed milestone v1.0")

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


def create_task_with_parent(
    gql: GraphQL, project_path: str, title: str, description: str, parent_iid: int
) -> None:
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

    result = gql.execute(mutation, variable_values={
        "projectPath": project_path,
        "title": title,
        "description": description,
        "workItemTypeId": task_type_id,
        "parentId": parent_id,
    })
    errors = result.get("workItemCreate", {}).get("errors", [])
    if errors:
        msg = f"GraphQL errors creating task: {errors}"
        raise RuntimeError(msg)
    work_item = result.get("workItemCreate", {}).get("workItem", {})
    print(f"    Created task: #{work_item.get('iid')} {work_item.get('title')} (child of #{parent_iid})")


def create_issues(project: Project, gql: GraphQL, project_path: str, ms1_id: int, ms3_id: int) -> None:
    """Create test issues with a gap at #4, tasks at #5-6, and issues at #7-8."""
    print("\n[4/8] Creating issues (with gap at #4, tasks at #5-6)...")

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
            print(f"    Issue already exists: #{existing_issues[title].iid} {title}")
        else:
            issue_data: dict[str, object] = {
                "title": title,
                "description": desc,
                "labels": issue_labels,
            }
            if milestone_id:
                issue_data["milestone_id"] = milestone_id
            issue = project.issues.create(issue_data)
            print(f"    Created issue: #{issue.iid} {title}")

    # Delete dummy issue #4 if it exists
    try:
        dummy_issue = project.issues.get(4)
        if dummy_issue.title == "DELETE-ME":
            dummy_issue.delete()
            print("    Deleted placeholder issue #4 to create gap")
    except (GitlabGetError, GitlabDeleteError):
        print("    Placeholder issue #4 already deleted or doesn't exist")

    # Create Tasks #5 and #6 (children of issue #3) via GraphQL
    tasks_to_create = [
        ("Child task 1 (open)", "REPLACE_WITH_TASK_ATTACHMENT"),
        ("Child task 2 (closed)", "Closed task child of #3"),
    ]
    for task_title, task_desc in tasks_to_create:
        if task_title not in existing_issues:
            create_task_with_parent(gql, project_path, task_title, task_desc, parent_iid=3)
        else:
            print(f"    Task already exists: #{existing_issues[task_title].iid} {task_title}")

    # Close task #6
    task6 = project.issues.get(6)
    if task6.state != "closed":
        task6.state_event = "close"
        task6.save()
        print("    Closed task #6")
    else:
        print("    Task #6 already closed")

    # Second batch: issues #7-8
    second_batch: list[tuple[str, str, list[str], int | None]] = [
        ("Blocking issue", "This issue blocks #8", ["p_low"], None),
        ("Blocked issue", "This issue is blocked by #7", ["feature"], None),
    ]

    for title, desc, issue_labels, milestone_id in second_batch:
        if title in existing_issues:
            print(f"    Issue already exists: #{existing_issues[title].iid} {title}")
        else:
            issue_data = {
                "title": title,
                "description": desc,
                "labels": issue_labels,
            }
            if milestone_id:
                issue_data["milestone_id"] = milestone_id
            issue = project.issues.create(issue_data)
            print(f"    Created issue: #{issue.iid} {title}")

def setup_issue_relationships(project: Project) -> None:
    """Set up issue links (blocking, related). Parent-child is set during Task creation."""
    print("\n[5/8] Setting up issue relationships...")

    project_id = project.id
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
            source_issue.links.create({
                "target_project_id": project_id,
                "target_issue_iid": target_iid,
                "link_type": link_type,
            })
            print(f"    Created link: {description}")
        except GitlabCreateError as e:
            if "already exists" in str(e).lower():
                print(f"    Link already exists: {description}")
            elif link_type == "blocks" and "not available" in str(e).lower():
                # Fall back to relates_to if blocking not available (requires premium license)
                print(f"    Blocking not available, falling back to relates_to for {description}")
                source_issue.links.create({
                    "target_project_id": project_id,
                    "target_issue_iid": target_iid,
                    "link_type": "relates_to",
                })
                print(f"    Created link: #7 relates to #8 (fallback)")
            else:
                raise


def add_comments_and_close_issue(project: Project) -> None:
    """Add a comment to issue #1 and close it."""
    print("\n[6/8] Adding comments and system notes...")

    issue1 = project.issues.get(1)

    # Check if comment already exists
    notes = issue1.notes.list(get_all=True)
    has_comment = any("This is a regular comment" in (n.body or "") for n in notes)
    if not has_comment:
        issue1.notes.create({"body": "This is a regular comment on the basic issue."})
        print("    Added comment to issue #1")
    else:
        print("    Comment already exists on issue #1")

    # Close issue #1 to generate system note
    if issue1.state != "closed":
        issue1.state_event = "close"
        issue1.save()
        print("    Closed issue #1")
    else:
        print("    Issue #1 already closed")


def create_git_content(project_path: str) -> None:
    """Create a feature branch and tag in the repository."""
    print("\n[7/8] Creating git content (branch and tag)...")

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
                project_name = project_path.split("/")[-1]
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
            print("    Created and pushed main branch")
        else:
            print("    Branch main already exists")

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
            print("    Created and pushed feature/sample branch")
        else:
            print("    Branch feature/sample already exists")

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
            print("    Created and pushed v1.0.0 tag")
        else:
            print("    Tag v1.0.0 already exists")


def print_manual_instructions(project_path: str) -> None:
    """Print instructions for manual steps that can't be automated."""
    print("\n[8/8] MANUAL STEPS REQUIRED")
    print("=" * 60)
    print(
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
    print("=" * 60)
    print("Script completed successfully!")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Create a GitLab test project for migration testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s flaks/migrator-test-project
  %(prog)s mygroup/subgroup/test-project
""",
    )
    parser.add_argument(
        "project_path",
        help="GitLab project path (e.g., 'namespace/project' or 'group/subgroup/project')",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_path = args.project_path

    print("=" * 60)
    print("Creating GitLab test project for migration testing")
    print(f"Project: {project_path}")
    print("=" * 60)

    token = get_gitlab_token()
    gl = Gitlab(GITLAB_URL, private_token=token)
    gql = GraphQL(GITLAB_URL, token=token)

    project = get_or_create_project(gl, project_path)
    create_labels(project)
    ms1_id, ms3_id = create_milestones(project)
    create_issues(project, gql, project_path, ms1_id, ms3_id)
    setup_issue_relationships(project)
    add_comments_and_close_issue(project)
    create_git_content(project_path)
    print_manual_instructions(project_path)


if __name__ == "__main__":
    main()
