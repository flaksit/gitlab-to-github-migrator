# Migrator.py Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract distinct responsibilities from migrator.py into separate modules, removing PLR0912/PLR0915 linter suppressions.

**Architecture:** Create 4 new modules (git_migration.py, attachments.py, relationships.py, issue_builder.py) plus additions to existing utils. Migrator becomes a thin orchestrator delegating to these modules.

**Tech Stack:** Python 3.14+, pytest, ruff, basedpyright

---

## Task 1: Create relationships.py with dataclasses

**Files:**
- Create: `src/gitlab_to_github_migrator/relationships.py`
- Create: `tests/test_relationships.py`

**Step 1: Write the test file**

```python
# tests/test_relationships.py
"""Tests for issue relationship data structures."""

import pytest

from gitlab_to_github_migrator.relationships import (
    IssueCrossLinks,
    IssueLinkInfo,
    WorkItemChild,
)


@pytest.mark.unit
class TestWorkItemChild:
    def test_creation(self) -> None:
        child = WorkItemChild(
            iid=123,
            title="Child task",
            state="opened",
            type="Task",
            web_url="https://gitlab.com/org/proj/-/issues/123",
        )
        assert child.iid == 123
        assert child.title == "Child task"
        assert child.state == "opened"
        assert child.type == "Task"


@pytest.mark.unit
class TestIssueLinkInfo:
    def test_creation_with_defaults(self) -> None:
        link = IssueLinkInfo(
            type="blocks",
            target_iid=456,
            target_title="Blocked issue",
            target_project_path="org/project",
            target_web_url="https://gitlab.com/org/project/-/issues/456",
            is_same_project=True,
        )
        assert link.type == "blocks"
        assert link.source == "rest_api"  # default

    def test_creation_with_custom_source(self) -> None:
        link = IssueLinkInfo(
            type="child_of",
            target_iid=789,
            target_title="Child",
            target_project_path="org/project",
            target_web_url="https://gitlab.com/org/project/-/issues/789",
            is_same_project=True,
            source="graphql_work_items",
        )
        assert link.source == "graphql_work_items"


@pytest.mark.unit
class TestIssueCrossLinks:
    def test_creation(self) -> None:
        cross_links = IssueCrossLinks(
            cross_links_text="**Related:** #123",
            parent_child_relations=[],
            blocking_relations=[],
        )
        assert cross_links.cross_links_text == "**Related:** #123"
        assert cross_links.parent_child_relations == []
        assert cross_links.blocking_relations == []
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_relationships.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'gitlab_to_github_migrator.relationships'"

**Step 3: Create the module with dataclasses**

```python
# src/gitlab_to_github_migrator/relationships.py
"""Issue relationship data structures and detection logic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorkItemChild:
    """Child work item from GraphQL Work Items API."""

    iid: int
    title: str
    state: str
    type: str
    web_url: str


@dataclass
class IssueLinkInfo:
    """Information about a linked issue."""

    type: str
    target_iid: int
    target_title: str
    target_project_path: str
    target_web_url: str
    is_same_project: bool
    source: str = "rest_api"


@dataclass
class IssueCrossLinks:
    """Cross-linked issues separated by relationship type."""

    cross_links_text: str
    parent_child_relations: list[IssueLinkInfo]
    blocking_relations: list[IssueLinkInfo]
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_relationships.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/relationships.py tests/test_relationships.py
git commit -m "feat: extract relationship dataclasses to relationships.py

Move WorkItemChild, IssueLinkInfo, IssueCrossLinks from migrator.py.
First step of migrator restructuring.

Refs #57"
```

---

## Task 2: Create issue_builder.py

**Files:**
- Create: `src/gitlab_to_github_migrator/issue_builder.py`
- Create: `tests/test_issue_builder.py`

**Step 1: Write the test file**

```python
# tests/test_issue_builder.py
"""Tests for issue body building functions."""

import pytest

from gitlab_to_github_migrator.issue_builder import build_issue_body, format_timestamp


@pytest.mark.unit
class TestFormatTimestamp:
    def test_format_with_z_suffix(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45.123Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_with_utc_offset(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45.123456+00:00")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_without_microseconds(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45Z")
        assert result == "2024-01-15 10:30:45Z"

    def test_format_with_non_utc_timezone(self) -> None:
        result = format_timestamp("2024-01-15T10:30:45+05:30")
        assert result == "2024-01-15 10:30:45+05:30"

    def test_empty_string_returns_as_is(self) -> None:
        result = format_timestamp("")
        assert result == ""

    def test_invalid_format_returns_original(self) -> None:
        result = format_timestamp("invalid-timestamp")
        assert result == "invalid-timestamp"


@pytest.mark.unit
class TestBuildIssueBody:
    def test_basic_issue_body(self) -> None:
        result = build_issue_body(
            iid=42,
            author_name="John Doe",
            author_username="johndoe",
            created_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            processed_description="Issue description here",
            cross_links_text="",
        )
        assert "**Migrated from GitLab issue #42**" in result
        assert "**Original Author:** John Doe (@johndoe)" in result
        assert "**Created:** 2024-01-15 10:30:45Z" in result
        assert "**GitLab URL:** https://gitlab.com/org/proj/-/issues/42" in result
        assert "Issue description here" in result

    def test_issue_body_with_cross_links(self) -> None:
        result = build_issue_body(
            iid=42,
            author_name="John Doe",
            author_username="johndoe",
            created_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            processed_description="Description",
            cross_links_text="\n\n**Related:** #123",
        )
        assert "**Related:** #123" in result

    def test_issue_body_with_empty_description(self) -> None:
        result = build_issue_body(
            iid=42,
            author_name="Jane",
            author_username="jane",
            created_at="2024-01-15T10:30:45Z",
            web_url="https://gitlab.com/org/proj/-/issues/42",
            processed_description="",
            cross_links_text="",
        )
        assert "**Migrated from GitLab issue #42**" in result
        assert "---" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_issue_builder.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Create the module**

```python
# src/gitlab_to_github_migrator/issue_builder.py
"""Build GitHub issue body from GitLab issue data."""

from __future__ import annotations

import datetime as dt


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO 8601 timestamp to human-readable format.

    Args:
        iso_timestamp: ISO 8601 formatted timestamp string

    Returns:
        Formatted timestamp (e.g., "2024-01-15 10:30:45Z").
        Returns original value if parsing fails.
    """
    if not iso_timestamp:
        return iso_timestamp

    try:
        timestamp_dt = dt.datetime.fromisoformat(iso_timestamp)
        formatted = timestamp_dt.isoformat(sep=" ", timespec="seconds")
        return formatted.replace("+00:00", "Z")
    except (ValueError, AttributeError):
        return iso_timestamp


def build_issue_body(
    *,
    iid: int,
    author_name: str,
    author_username: str,
    created_at: str,
    web_url: str,
    processed_description: str,
    cross_links_text: str,
) -> str:
    """Build complete GitHub issue body with migration header.

    Args:
        iid: GitLab issue IID
        author_name: Original author's display name
        author_username: Original author's username
        created_at: ISO timestamp of issue creation
        web_url: GitLab issue URL
        processed_description: Description with attachments already processed
        cross_links_text: Formatted cross-links section (may be empty)

    Returns:
        Complete issue body for GitHub
    """
    body = f"**Migrated from GitLab issue #{iid}**\n"
    body += f"**Original Author:** {author_name} (@{author_username})\n"
    body += f"**Created:** {format_timestamp(created_at)}\n"
    body += f"**GitLab URL:** {web_url}\n\n"
    body += "---\n\n"
    body += processed_description
    body += cross_links_text
    return body
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_issue_builder.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/issue_builder.py tests/test_issue_builder.py
git commit -m "feat: extract issue body building to issue_builder.py

Move format_timestamp() and add build_issue_body() for constructing
GitHub issue content from GitLab data.

Refs #57"
```

---

## Task 3: Create git_migration.py

**Files:**
- Create: `src/gitlab_to_github_migrator/git_migration.py`
- Create: `tests/test_git_migration.py`

**Step 1: Write the test file**

```python
# tests/test_git_migration.py
"""Tests for git migration functions."""

import pytest

from gitlab_to_github_migrator.git_migration import _inject_token, _sanitize_error


@pytest.mark.unit
class TestInjectToken:
    def test_inject_gitlab_token(self) -> None:
        url = "https://gitlab.com/org/repo.git"
        result = _inject_token(url, "my_token", prefix="oauth2:")
        assert result == "https://oauth2:my_token@gitlab.com/org/repo.git"

    def test_inject_github_token(self) -> None:
        url = "https://github.com/org/repo.git"
        result = _inject_token(url, "gh_token", prefix="")
        assert result == "https://gh_token@github.com/org/repo.git"

    def test_no_token_returns_original(self) -> None:
        url = "https://gitlab.com/org/repo.git"
        result = _inject_token(url, None, prefix="oauth2:")
        assert result == url

    def test_non_https_returns_original(self) -> None:
        url = "git@gitlab.com:org/repo.git"
        result = _inject_token(url, "token", prefix="oauth2:")
        assert result == url


@pytest.mark.unit
class TestSanitizeError:
    def test_sanitize_single_token(self) -> None:
        error = "Failed to clone https://oauth2:secret123@gitlab.com/repo"
        result = _sanitize_error(error, ["secret123"])
        assert "secret123" not in result
        assert "***TOKEN***" in result

    def test_sanitize_multiple_tokens(self) -> None:
        error = "Error: token1 and token2 exposed"
        result = _sanitize_error(error, ["token1", "token2"])
        assert "token1" not in result
        assert "token2" not in result

    def test_sanitize_with_none_tokens(self) -> None:
        error = "Some error message"
        result = _sanitize_error(error, [None, "token"])
        assert result == "Some error message".replace("token", "***TOKEN***")

    def test_empty_tokens_list(self) -> None:
        error = "Some error"
        result = _sanitize_error(error, [])
        assert result == "Some error"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_git_migration.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Create the module with helpers first**

```python
# src/gitlab_to_github_migrator/git_migration.py
"""Git repository mirroring from source to target."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .exceptions import MigrationError

logger: logging.Logger = logging.getLogger(__name__)


def _inject_token(url: str, token: str | None, prefix: str = "") -> str:
    """Inject authentication token into HTTPS URL.

    Args:
        url: The URL to modify
        token: Token to inject (if None, returns original URL)
        prefix: Prefix before token (e.g., "oauth2:" for GitLab)

    Returns:
        URL with token injected, or original if not HTTPS or no token
    """
    if not token or not url.startswith("https://"):
        return url
    return url.replace("https://", f"https://{prefix}{token}@")


def _sanitize_error(error: str, tokens: list[str | None]) -> str:
    """Remove tokens from error message to prevent leakage.

    Args:
        error: Error message that may contain tokens
        tokens: List of tokens to redact (None values are ignored)

    Returns:
        Error message with tokens replaced by ***TOKEN***
    """
    result = error
    for token in tokens:
        if token:
            result = result.replace(token, "***TOKEN***")
    return result


def migrate_git_content(
    source_http_url: str,
    target_clone_url: str,
    source_token: str | None,
    target_token: str,
    local_clone_path: Path | None = None,
) -> None:
    """Mirror git repository from source to target.

    Args:
        source_http_url: Source repository HTTPS URL (e.g., GitLab)
        target_clone_url: Target repository HTTPS URL (e.g., GitHub)
        source_token: Authentication token for source (may be None for public repos)
        target_token: Authentication token for target
        local_clone_path: Optional existing local clone to use

    Raises:
        MigrationError: If cloning or pushing fails
    """
    temp_clone_path: str | None = None
    tokens = [source_token, target_token]

    try:
        if local_clone_path:
            clone_path: Path | str = local_clone_path
            if not local_clone_path.exists():
                msg = f"Local clone path does not exist: {local_clone_path}"
                raise MigrationError(msg)
        else:
            temp_clone_path = tempfile.mkdtemp(prefix="gitlab_migration_")
            clone_path = temp_clone_path

            source_url = _inject_token(source_http_url, source_token, prefix="oauth2:")

            result = subprocess.run(  # noqa: S603, S607
                ["git", "clone", "--mirror", source_url, temp_clone_path],
                check=False,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                msg = f"Failed to clone repository: {_sanitize_error(result.stderr, tokens)}"
                raise MigrationError(msg)

        # Add target remote with token
        target_url = _inject_token(target_clone_url, target_token, prefix="")

        try:
            subprocess.run(  # noqa: S603, S607
                ["git", "remote", "add", "github", target_url],
                cwd=clone_path,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            msg = f"Failed to add remote: {_sanitize_error(str(e), tokens)}"
            raise MigrationError(msg) from e

        # Push all branches and tags
        subprocess.run(  # noqa: S603, S607
            ["git", "push", "--mirror", "github"],
            cwd=clone_path,
            check=True,
        )

        # Clean up remote to remove token from git config
        try:
            subprocess.run(  # noqa: S603, S607
                ["git", "remote", "remove", "github"],
                cwd=clone_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # Ignore cleanup errors

        logger.info("Repository content migrated successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        msg = f"Failed to migrate repository content: {_sanitize_error(str(e), tokens)}"
        raise MigrationError(msg) from e
    finally:
        if temp_clone_path and Path(temp_clone_path).exists():
            shutil.rmtree(temp_clone_path)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_git_migration.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/git_migration.py tests/test_git_migration.py
git commit -m "feat: extract git mirroring to git_migration.py

Standalone function for cloning from source and pushing to target.
Includes token injection and error sanitization helpers.

Refs #57"
```

---

## Task 4: Add get_work_item_children to gitlab_utils.py

**Files:**
- Modify: `src/gitlab_to_github_migrator/gitlab_utils.py`
- Modify: `tests/test_gitlab_to_github_migrator.py` (or create new test file)

**Step 1: Write the test**

Add to `tests/test_gitlab_to_github_migrator.py` or create `tests/test_gitlab_utils.py`:

```python
# Add to existing test file or create tests/test_gitlab_utils.py
@pytest.mark.unit
class TestGetWorkItemChildren:
    def test_returns_empty_list_when_no_children(self) -> None:
        from unittest.mock import Mock
        from gitlab_to_github_migrator.gitlab_utils import get_work_item_children

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {
                "workItem": {
                    "iid": "42",
                    "widgets": [
                        {"type": "HIERARCHY", "children": {"nodes": []}}
                    ]
                }
            }
        }

        result = get_work_item_children(mock_graphql, "org/project", 42)
        assert result == []

    def test_returns_children_when_present(self) -> None:
        from unittest.mock import Mock
        from gitlab_to_github_migrator.gitlab_utils import get_work_item_children
        from gitlab_to_github_migrator.relationships import WorkItemChild

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {
                "workItem": {
                    "iid": "42",
                    "widgets": [
                        {
                            "type": "HIERARCHY",
                            "children": {
                                "nodes": [
                                    {
                                        "iid": "100",
                                        "title": "Child task",
                                        "state": "opened",
                                        "workItemType": {"name": "Task"},
                                        "webUrl": "https://gitlab.com/org/proj/-/issues/100",
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        }

        result = get_work_item_children(mock_graphql, "org/project", 42)
        assert len(result) == 1
        assert isinstance(result[0], WorkItemChild)
        assert result[0].iid == 100
        assert result[0].title == "Child task"
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gitlab_to_github_migrator.py::TestGetWorkItemChildren -v` (or appropriate path)
Expected: FAIL with "cannot import name 'get_work_item_children'"

**Step 3: Add the function to gitlab_utils.py**

Add to `src/gitlab_to_github_migrator/gitlab_utils.py`:

```python
# Add import at top
from .relationships import WorkItemChild

# Add function
def get_work_item_children(
    graphql_client: GraphQL,
    project_path: str,
    issue_iid: int,
) -> list[WorkItemChild]:
    """Get child work items for an issue using GraphQL Work Items API.

    Args:
        graphql_client: GitLab GraphQL client
        project_path: Full project path (e.g., "namespace/project")
        issue_iid: The internal ID of the issue

    Returns:
        List of child work items
    """
    query = """
    query GetWorkItemWithChildren($fullPath: ID!, $iid: String!) {
        namespace(fullPath: $fullPath) {
            workItem(iid: $iid) {
                iid
                widgets {
                    type
                    ... on WorkItemWidgetHierarchy {
                        children {
                            nodes {
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
    """

    variables = {"fullPath": project_path, "iid": str(issue_iid)}

    try:
        response = graphql_client.execute(query, variable_values=variables)

        namespace = response.get("namespace")
        if not namespace:
            logger.debug(f"Namespace {project_path} not found in GraphQL response")
            return []

        work_item = namespace.get("workItem")
        if not work_item:
            logger.debug(f"Work item {issue_iid} not found in project {project_path}")
            return []

        children: list[WorkItemChild] = []
        widgets = work_item.get("widgets", [])

        for widget in widgets:
            if widget.get("type") == "HIERARCHY":
                child_nodes = widget.get("children", {}).get("nodes", [])
                for child in child_nodes:
                    child_info = WorkItemChild(
                        iid=int(child.get("iid")),
                        title=child.get("title"),
                        state=child.get("state"),
                        type=child.get("workItemType", {}).get("name"),
                        web_url=child.get("webUrl"),
                    )
                    children.append(child_info)

        logger.debug(f"Found {len(children)} child work items for issue #{issue_iid}")
        return children

    except Exception as e:
        logger.debug(f"Could not get children for issue #{issue_iid}: {e}")
        return []
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gitlab_to_github_migrator.py::TestGetWorkItemChildren -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/gitlab_utils.py tests/test_gitlab_to_github_migrator.py
git commit -m "feat: add get_work_item_children to gitlab_utils.py

Extract GraphQL work item children query from migrator.
Uses WorkItemChild dataclass from relationships module.

Refs #57"
```

---

## Task 5: Add create_issue_dependency to github_utils.py

**Files:**
- Modify: `src/gitlab_to_github_migrator/github_utils.py`

**Step 1: Write the test**

Add to test file:

```python
@pytest.mark.unit
class TestCreateIssueDependency:
    def test_creates_dependency_successfully(self) -> None:
        from unittest.mock import Mock
        from gitlab_to_github_migrator.github_utils import create_issue_dependency

        mock_client = Mock()
        mock_client.requester.requestJson.return_value = (201, {}, {"id": 123})

        result = create_issue_dependency(
            mock_client, "owner", "repo", blocked_issue_number=10, blocking_issue_id=999
        )

        assert result is True
        mock_client.requester.requestJson.assert_called_once_with(
            "POST",
            "/repos/owner/repo/issues/10/dependencies/blocked_by",
            input={"issue_id": 999},
        )

    def test_returns_false_on_422(self) -> None:
        from unittest.mock import Mock
        from github import GithubException
        from gitlab_to_github_migrator.github_utils import create_issue_dependency

        mock_client = Mock()
        mock_client.requester.requestJson.side_effect = GithubException(
            422, {"message": "Already exists"}, headers={}
        )

        result = create_issue_dependency(
            mock_client, "owner", "repo", blocked_issue_number=10, blocking_issue_id=999
        )

        assert result is False
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_gitlab_to_github_migrator.py::TestCreateIssueDependency -v`
Expected: FAIL with "cannot import name 'create_issue_dependency'"

**Step 3: Add the function to github_utils.py**

Add to `src/gitlab_to_github_migrator/github_utils.py`:

```python
def create_issue_dependency(
    client: Github,
    owner: str,
    repo: str,
    blocked_issue_number: int,
    blocking_issue_id: int,
) -> bool:
    """Create GitHub issue dependency (blocked-by relationship).

    Uses raw API call since PyGithub doesn't support this yet (August 2025 API).

    Args:
        client: PyGithub client
        owner: Repository owner
        repo: Repository name
        blocked_issue_number: The issue number that is blocked
        blocking_issue_id: The issue ID (not number) that is blocking

    Returns:
        True if created, False if already exists or invalid
    """
    endpoint = f"/repos/{owner}/{repo}/issues/{blocked_issue_number}/dependencies/blocked_by"
    payload = {"issue_id": blocking_issue_id}

    try:
        status, _, _ = client.requester.requestJson("POST", endpoint, input=payload)
    except GithubException as e:
        if e.status == 422:
            logger.debug(f"Could not create dependency (may already exist): {e.status} - {e.data}")
            return False
        raise

    if status == 201:
        logger.debug(
            f"Created issue dependency: issue #{blocked_issue_number} blocked by issue ID {blocking_issue_id}"
        )
        return True

    return False
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_gitlab_to_github_migrator.py::TestCreateIssueDependency -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/github_utils.py tests/test_gitlab_to_github_migrator.py
git commit -m "feat: add create_issue_dependency to github_utils.py

Extract GitHub issue dependency creation from migrator.
Uses raw API since PyGithub doesn't support this yet.

Refs #57"
```

---

## Task 6: Create attachments.py

**Files:**
- Create: `src/gitlab_to_github_migrator/attachments.py`
- Create: `tests/test_attachments.py`

**Step 1: Write the test file**

```python
# tests/test_attachments.py
"""Tests for attachment handling."""

from unittest.mock import Mock, patch

import pytest

from gitlab_to_github_migrator.attachments import AttachmentHandler, DownloadedFile


@pytest.mark.unit
class TestDownloadedFile:
    def test_creation(self) -> None:
        f = DownloadedFile(
            filename="test.png",
            content=b"image data",
            short_gitlab_url="/uploads/abc123/test.png",
            full_gitlab_url="https://gitlab.com/org/proj/uploads/abc123/test.png",
        )
        assert f.filename == "test.png"
        assert f.content == b"image data"


@pytest.mark.unit
class TestAttachmentHandler:
    def setup_method(self) -> None:
        self.mock_gitlab_client = Mock()
        self.mock_gitlab_project = Mock()
        self.mock_gitlab_project.id = 12345
        self.mock_gitlab_project.web_url = "https://gitlab.com/org/project"
        self.mock_github_repo = Mock()

    def test_process_content_no_attachments(self) -> None:
        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        content = "No attachments here"
        result = handler.process_content(content)

        assert result == content

    def test_process_content_with_cached_attachment(self) -> None:
        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        # Pre-populate cache
        handler._uploaded_cache["/uploads/abcdef0123456789abcdef0123456789/cached.pdf"] = (
            "https://github.com/releases/cached.pdf"
        )

        content = "See attachment: /uploads/abcdef0123456789abcdef0123456789/cached.pdf"
        result = handler.process_content(content)

        assert "/uploads/abcdef0123456789abcdef0123456789/cached.pdf" not in result
        assert "https://github.com/releases/cached.pdf" in result

    @patch("gitlab_to_github_migrator.attachments.glu.download_attachment")
    def test_process_content_downloads_and_uploads(self, mock_download) -> None:
        # Setup download mock
        mock_download.return_value = (b"file content", "application/pdf")

        # Setup upload mock (release)
        mock_release = Mock()
        mock_asset = Mock()
        mock_asset.browser_download_url = "https://github.com/releases/download/file.pdf"
        mock_release.upload_asset.return_value = mock_asset
        self.mock_github_repo.get_releases.return_value = [mock_release]
        mock_release.name = "GitLab issue attachments"

        handler = AttachmentHandler(
            self.mock_gitlab_client,
            self.mock_gitlab_project,
            self.mock_github_repo,
        )

        content = "File: /uploads/abcdef0123456789abcdef0123456789/doc.pdf"
        result = handler.process_content(content, context="issue #1")

        assert "/uploads/abcdef0123456789abcdef0123456789/doc.pdf" not in result
        assert "https://github.com/releases/download/file.pdf" in result
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_attachments.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Create the module**

```python
# src/gitlab_to_github_migrator/attachments.py
"""Attachment migration between GitLab and GitHub."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from github import GithubException

from . import gitlab_utils as glu

if TYPE_CHECKING:
    import github.GitRelease
    import github.Repository
    from gitlab import Gitlab
    from gitlab.v4.objects.projects import Project as GitlabProject

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class DownloadedFile:
    """Represents a downloaded file from GitLab."""

    filename: str
    content: bytes
    short_gitlab_url: str
    full_gitlab_url: str


class AttachmentHandler:
    """Downloads attachments from GitLab and uploads to GitHub releases."""

    def __init__(
        self,
        gitlab_client: Gitlab,
        gitlab_project: GitlabProject,
        github_repo: github.Repository.Repository,
    ) -> None:
        self._gitlab_client = gitlab_client
        self._gitlab_project = gitlab_project
        self._github_repo = github_repo
        self._uploaded_cache: dict[str, str] = {}
        self._release: github.GitRelease.GitRelease | None = None

    @property
    def attachments_release(self) -> github.GitRelease.GitRelease:
        """Get or create the draft release for storing attachments (cached)."""
        if self._release is None:
            release_tag = "gitlab-issue-attachments"
            release_name = "GitLab issue attachments"

            # Find existing release by name (draft releases can't be found by tag)
            for r in self._github_repo.get_releases():
                if r.name == release_name:
                    self._release = r
                    logger.debug(f"Using existing attachments release: {r.name}")
                    return self._release

            # Create new release
            logger.info(f"Creating new '{release_name}' release for attachments")
            self._release = self._github_repo.create_git_release(
                tag=release_tag,
                name=release_name,
                message="Storage for migrated GitLab attachments. Do not delete.",
                draft=True,
            )

        return self._release

    def process_content(self, content: str, context: str = "") -> str:
        """Download GitLab attachments and upload to GitHub, returning updated content.

        Args:
            content: Text content that may contain GitLab attachment URLs
            context: Context for log messages (e.g., "issue #5")

        Returns:
            Content with GitLab URLs replaced by GitHub URLs
        """
        files, updated_content = self._download_files(content)
        return self._upload_files(files, updated_content, context)

    def _download_files(self, content: str) -> tuple[list[DownloadedFile], str]:
        """Find attachment URLs, download files, replace cached URLs."""
        attachment_pattern = r"/uploads/([a-f0-9]{32})/([^)\s]+)"
        attachments = re.findall(attachment_pattern, content)

        downloaded_files: list[DownloadedFile] = []
        updated_content = content

        for secret, filename in attachments:
            short_url = f"/uploads/{secret}/{filename}"

            # If already uploaded, just replace URL
            if short_url in self._uploaded_cache:
                github_url = self._uploaded_cache[short_url]
                updated_content = updated_content.replace(short_url, github_url)
                logger.debug(f"Reusing cached attachment {filename}: {github_url}")
                continue

            full_url = f"{self._gitlab_project.web_url}{short_url}"
            try:
                attachment_content, content_type = glu.download_attachment(
                    self._gitlab_client, self._gitlab_project, secret, filename
                )

                if attachment_content:
                    downloaded_files.append(
                        DownloadedFile(
                            filename=filename,
                            content=attachment_content,
                            short_gitlab_url=short_url,
                            full_gitlab_url=full_url,
                        )
                    )
                else:
                    logger.warning(
                        f"GitLab returned empty content for attachment {short_url} "
                        f"(Content-Type: {content_type})"
                    )

            except Exception as e:
                logger.warning(f"Failed to download attachment {short_url}: {e}")

        return downloaded_files, updated_content

    def _upload_files(
        self, files: list[DownloadedFile], content: str, context: str
    ) -> str:
        """Upload files to GitHub release, update content with new URLs."""
        if not files:
            return content

        updated_content = content
        release = self.attachments_release

        for file_info in files:
            # Skip if already cached
            if file_info.short_gitlab_url in self._uploaded_cache:
                url = self._uploaded_cache[file_info.short_gitlab_url]
                updated_content = updated_content.replace(file_info.short_gitlab_url, url)
                continue

            # Skip empty files
            if not file_info.content:
                ctx = f" in {context}" if context else ""
                logger.warning(f"Skipping empty attachment {file_info.filename}{ctx}")
                continue

            temp_path = None
            try:
                file_ext = Path(file_info.filename).suffix if file_info.filename else ""
                with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as f:
                    temp_path = f.name
                    f.write(file_info.content)

                # Make filename unique with secret prefix
                url_parts = file_info.short_gitlab_url.split("/")
                secret = url_parts[2] if len(url_parts) >= 3 else ""
                unique_name = f"{secret[:8]}_{file_info.filename}" if secret else file_info.filename

                asset = release.upload_asset(path=temp_path, name=unique_name)
                download_url = asset.browser_download_url

                self._uploaded_cache[file_info.short_gitlab_url] = download_url
                updated_content = updated_content.replace(
                    file_info.short_gitlab_url, download_url
                )
                logger.debug(f"Uploaded {file_info.filename}: {download_url}")

            except (GithubException, OSError):
                logger.exception(f"Failed to upload attachment {file_info.filename}")
                raise
            finally:
                if temp_path:
                    p = Path(temp_path)
                    if p.exists():
                        p.unlink()

        return updated_content
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_attachments.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/attachments.py tests/test_attachments.py
git commit -m "feat: extract attachment handling to attachments.py

AttachmentHandler class for downloading from GitLab and uploading
to GitHub releases. Includes caching to avoid duplicate uploads.

Refs #57"
```

---

## Task 7: Add get_issue_cross_links to relationships.py

**Files:**
- Modify: `src/gitlab_to_github_migrator/relationships.py`
- Modify: `tests/test_relationships.py`

**Step 1: Write the test**

Add to `tests/test_relationships.py`:

```python
from unittest.mock import Mock


@pytest.mark.unit
class TestGetIssueCrossLinks:
    def test_returns_empty_when_no_links(self) -> None:
        from gitlab_to_github_migrator.relationships import get_issue_cross_links

        mock_issue = Mock()
        mock_issue.iid = 42
        mock_issue.links.list.return_value = []

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {"workItem": {"widgets": []}}
        }

        result = get_issue_cross_links(mock_issue, "org/project", mock_graphql)

        assert result.cross_links_text == ""
        assert result.parent_child_relations == []
        assert result.blocking_relations == []

    def test_categorizes_blocking_links(self) -> None:
        from gitlab_to_github_migrator.relationships import get_issue_cross_links

        mock_issue = Mock()
        mock_issue.iid = 42

        mock_link = Mock()
        mock_link.link_type = "blocks"
        mock_link.iid = 100
        mock_link.title = "Blocked issue"
        mock_link.references = {"full": "org/project#100"}
        mock_link.web_url = "https://gitlab.com/org/project/-/issues/100"
        mock_issue.links.list.return_value = [mock_link]

        mock_graphql = Mock()
        mock_graphql.execute.return_value = {
            "namespace": {"workItem": {"widgets": []}}
        }

        result = get_issue_cross_links(mock_issue, "org/project", mock_graphql)

        assert len(result.blocking_relations) == 1
        assert result.blocking_relations[0].type == "blocks"
        assert result.blocking_relations[0].target_iid == 100
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_relationships.py::TestGetIssueCrossLinks -v`
Expected: FAIL with "cannot import name 'get_issue_cross_links'"

**Step 3: Add the function to relationships.py**

Add imports and function to `src/gitlab_to_github_migrator/relationships.py`:

```python
# Add imports at top
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import gitlab_utils as glu

if TYPE_CHECKING:
    from gitlab import GraphQL

logger: logging.Logger = logging.getLogger(__name__)

# ... dataclasses stay the same ...

def get_issue_cross_links(
    gitlab_issue: Any,  # noqa: ANN401 - gitlab has no type stubs
    gitlab_project_path: str,
    graphql_client: GraphQL,
) -> IssueCrossLinks:
    """Get cross-linked issues separated by relationship type.

    Uses both GraphQL (parent-child) and REST API (blocking, relates_to).

    Args:
        gitlab_issue: GitLab issue object
        gitlab_project_path: Full project path
        graphql_client: GitLab GraphQL client

    Returns:
        IssueCrossLinks with categorized relationships
    """
    # Step 1: Get child tasks via GraphQL
    child_work_items = glu.get_work_item_children(
        graphql_client, gitlab_project_path, gitlab_issue.iid
    )
    logger.debug(f"Found {len(child_work_items)} tasks via GraphQL for issue #{gitlab_issue.iid}")

    # Step 2: Get regular issue links from REST API
    regular_links: list[IssueLinkInfo] = []
    links = gitlab_issue.links.list(get_all=True)

    for link in links:
        link_type = getattr(link, "link_type", "relates_to")
        target_iid = link.iid
        target_title = getattr(link, "title", "Unknown Title")
        references = getattr(link, "references", {})
        target_project_path = references.get("full", "").rsplit("#", 1)[0] if references else None
        target_web_url = getattr(link, "web_url", "")

        target_project_path = target_project_path or gitlab_project_path

        link_info = IssueLinkInfo(
            type=link_type,
            target_iid=target_iid,
            target_title=target_title,
            target_project_path=target_project_path,
            target_web_url=target_web_url,
            is_same_project=target_project_path == gitlab_project_path,
        )
        regular_links.append(link_info)

    # Step 3: Categorize relationships
    parent_child_relations = [
        IssueLinkInfo(
            type="child_of",
            target_iid=child.iid,
            target_title=child.title,
            target_project_path=gitlab_project_path,
            target_web_url=child.web_url,
            is_same_project=True,
            source="graphql_work_items",
        )
        for child in child_work_items
    ]

    blocking_relations: list[IssueLinkInfo] = []
    relates_to_links: list[tuple[str, IssueLinkInfo]] = []

    for link_info in regular_links:
        if link_info.type in ("blocks", "is_blocked_by"):
            if link_info.is_same_project:
                blocking_relations.append(link_info)
            else:
                label = "Blocked by" if link_info.type == "is_blocked_by" else "Blocks"
                relates_to_links.append((label, link_info))
        elif link_info.type == "relates_to":
            relates_to_links.append(("Related to", link_info))
        else:
            relates_to_links.append((f"Linked ({link_info.type})", link_info))

    # Step 4: Format cross-links text
    cross_links_text = ""
    if relates_to_links:
        cross_links_text = "\n\n---\n\n**Cross-linked Issues:**\n\n"
        for relationship, info in relates_to_links:
            if info.is_same_project:
                cross_links_text += f"- **{relationship}**: #{info.target_iid} - {info.target_title}\n"
            else:
                cross_links_text += f"- **{relationship}**: [{info.target_project_path}#{info.target_iid}]({info.target_web_url}) - {info.target_title}\n"

    logger.debug(
        f"Issue #{gitlab_issue.iid}: {len(parent_child_relations)} parent-child, "
        f"{len(blocking_relations)} blocking, {len(relates_to_links)} relates_to"
    )

    return IssueCrossLinks(
        cross_links_text=cross_links_text,
        parent_child_relations=parent_child_relations,
        blocking_relations=blocking_relations,
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_relationships.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/gitlab_to_github_migrator/relationships.py tests/test_relationships.py
git commit -m "feat: add get_issue_cross_links to relationships.py

Extracts relationship detection logic from migrator.
Categorizes links into parent-child, blocking, and relates_to.

Refs #57"
```

---

## Task 8: Refactor migrator.py to use new modules

**Files:**
- Modify: `src/gitlab_to_github_migrator/migrator.py`

**Step 1: Update imports in migrator.py**

Replace dataclass definitions and add imports:

```python
# At top of file, update imports:
from . import git_migration
from . import github_utils as ghu
from . import gitlab_utils as glu
from .attachments import AttachmentHandler
from .exceptions import MigrationError, NumberVerificationError
from .issue_builder import build_issue_body
from .label_translator import LabelTranslator
from .relationships import (
    IssueCrossLinks,
    IssueLinkInfo,
    WorkItemChild,
    get_issue_cross_links,
)
```

**Step 2: Remove dataclass definitions from migrator.py**

Delete the `DownloadedFile`, `WorkItemChild`, `IssueLinkInfo`, `IssueCrossLinks` dataclass definitions (they're now in relationships.py and attachments.py).

**Step 3: Remove migrated methods**

Delete:
- `_format_timestamp()` - now in issue_builder.py
- `get_work_item_children()` - now in gitlab_utils.py
- `download_gitlab_attachments()` - now in attachments.py
- `upload_github_attachments()` - now in attachments.py
- `attachments_release` property - now in AttachmentHandler
- `create_github_issue_dependency()` - now in github_utils.py
- `get_issue_cross_links()` - now in relationships.py
- `_make_graphql_request()` - no longer needed

**Step 4: Update __init__ to create AttachmentHandler**

```python
def __init__(self, ...):
    # ... existing code ...

    # Initialize attachment handler (lazy - needs github_repo)
    self._attachment_handler: AttachmentHandler | None = None
```

Add property:

```python
@property
def attachment_handler(self) -> AttachmentHandler:
    if self._attachment_handler is None:
        self._attachment_handler = AttachmentHandler(
            self.gitlab_client,
            self.gitlab_project,
            self.github_repo,
        )
    return self._attachment_handler
```

**Step 5: Update migrate_git_content() to delegate**

```python
def migrate_git_content(self) -> None:
    """Migrate git repository content from GitLab to GitHub."""
    git_migration.migrate_git_content(
        source_http_url=str(self.gitlab_project.http_url_to_repo),
        target_clone_url=self.github_repo.clone_url,
        source_token=self.gitlab_token,
        target_token=self.github_token,
        local_clone_path=self.local_clone_path,
    )
```

**Step 6: Split migrate_issues_with_number_preservation() into methods**

```python
def migrate_issues_with_number_preservation(self) -> None:
    """Migrate issues while preserving GitLab issue numbers."""
    try:
        gitlab_issues = self.gitlab_project.issues.list(get_all=True, state="all")
        gitlab_issues.sort(key=lambda i: i.iid)

        if not gitlab_issues:
            logger.info("No issues to migrate")
            return

        # First pass: create issues and placeholders
        github_issues, pending_relations = self._create_issues_first_pass(gitlab_issues)

        # Second pass: create parent-child relationships
        self._create_parent_child_relations(
            github_issues, pending_relations["parent_child"]
        )

        # Third pass: create blocking relationships
        self._create_blocking_relations(
            github_issues, pending_relations["blocking"]
        )

        logger.info(f"Migrated {len(gitlab_issues)} issues")

    except (GitlabError, GithubException) as e:
        msg = f"Failed to migrate issues: {e}"
        raise MigrationError(msg) from e
```

**Step 7: Create _create_issues_first_pass() method**

```python
def _create_issues_first_pass(
    self, gitlab_issues: list[Any]
) -> tuple[dict[int, github.Issue.Issue], dict[str, Any]]:
    """Create issues and placeholders, collecting relationships for later passes."""
    max_issue_number = max(i.iid for i in gitlab_issues)
    gitlab_issue_dict = {i.iid: i for i in gitlab_issues}
    github_issue_dict: dict[int, github.Issue.Issue] = {}
    pending_parent_child: dict[int, list[IssueLinkInfo]] = {}
    pending_blocking: list[dict[str, Any]] = []

    for issue_number in range(1, max_issue_number + 1):
        if issue_number in gitlab_issue_dict:
            gitlab_issue = gitlab_issue_dict[issue_number]
            github_issue = self._create_migrated_issue(
                gitlab_issue, pending_parent_child, pending_blocking
            )
            github_issue_dict[gitlab_issue.iid] = github_issue
        else:
            self._create_placeholder_issue(issue_number)

    return github_issue_dict, {
        "parent_child": pending_parent_child,
        "blocking": pending_blocking,
    }
```

**Step 8: Create _create_migrated_issue() helper**

```python
def _create_migrated_issue(
    self,
    gitlab_issue: Any,
    pending_parent_child: dict[int, list[IssueLinkInfo]],
    pending_blocking: list[dict[str, Any]],
) -> github.Issue.Issue:
    """Create a single migrated issue from GitLab."""
    # Process description with attachments
    processed_description = ""
    if gitlab_issue.description:
        processed_description = self.attachment_handler.process_content(
            gitlab_issue.description, context=f"issue #{gitlab_issue.iid}"
        )

    # Get cross-links
    cross_links = get_issue_cross_links(
        gitlab_issue, self.gitlab_project_path, self.gitlab_graphql_client
    )

    # Collect relationships for later passes
    if cross_links.parent_child_relations:
        pending_parent_child[gitlab_issue.iid] = cross_links.parent_child_relations
    if cross_links.blocking_relations:
        pending_blocking.extend(
            {"source_gitlab_iid": gitlab_issue.iid, "relation": r}
            for r in cross_links.blocking_relations
        )

    # Build issue body
    issue_body = build_issue_body(
        iid=gitlab_issue.iid,
        author_name=gitlab_issue.author["name"],
        author_username=gitlab_issue.author["username"],
        created_at=gitlab_issue.created_at,
        web_url=gitlab_issue.web_url,
        processed_description=processed_description,
        cross_links_text=cross_links.cross_links_text,
    )

    # Prepare labels and milestone
    issue_labels = [
        self.label_mapping[name]
        for name in gitlab_issue.labels
        if name in self.label_mapping
    ]

    milestone = None
    if gitlab_issue.milestone and gitlab_issue.milestone["id"] in self.milestone_mapping:
        milestone_number = self.milestone_mapping[gitlab_issue.milestone["id"]]
        milestone = self.github_repo.get_milestone(milestone_number)

    # Create GitHub issue
    create_kwargs: dict[str, Any] = {
        "title": gitlab_issue.title,
        "body": issue_body,
        "labels": issue_labels,
    }
    if milestone:
        create_kwargs["milestone"] = milestone

    github_issue = self.github_repo.create_issue(**create_kwargs)

    # Verify number
    if github_issue.number != gitlab_issue.iid:
        msg = f"Issue number mismatch: expected {gitlab_issue.iid}, got {github_issue.number}"
        raise NumberVerificationError(msg)

    # Migrate comments
    self.migrate_issue_comments(gitlab_issue, github_issue)

    # Close if needed
    if gitlab_issue.state == "closed":
        github_issue.edit(state="closed")

    logger.debug(f"Created issue #{gitlab_issue.iid}: {gitlab_issue.title}")
    return github_issue
```

**Step 9: Create _create_placeholder_issue() helper**

```python
def _create_placeholder_issue(self, issue_number: int) -> None:
    """Create a placeholder issue to preserve numbering."""
    placeholder = self.github_repo.create_issue(
        title="Placeholder",
        body="Placeholder to preserve issue numbering - will be deleted",
    )

    if placeholder.number != issue_number:
        msg = f"Placeholder number mismatch: expected {issue_number}, got {placeholder.number}"
        raise NumberVerificationError(msg)

    placeholder.edit(state="closed")
    logger.debug(f"Created placeholder issue #{issue_number}")
```

**Step 10: Create _create_parent_child_relations() method**

```python
def _create_parent_child_relations(
    self,
    github_issues: dict[int, github.Issue.Issue],
    pending_relations: dict[int, list[IssueLinkInfo]],
) -> None:
    """Second pass: create sub-issue relationships."""
    if not pending_relations:
        return

    logger.info(f"Processing {len(pending_relations)} parent-child relationships...")

    for parent_iid, child_relations in pending_relations.items():
        if parent_iid not in github_issues:
            logger.warning(f"Parent issue #{parent_iid} not found")
            continue

        parent_github = github_issues[parent_iid]

        for relation in child_relations:
            child_iid = int(relation.target_iid)
            if child_iid not in github_issues:
                logger.warning(f"Child issue #{child_iid} not found")
                continue

            child_github = github_issues[child_iid]

            try:
                parent_github.add_sub_issue(child_github.id)
                logger.debug(f"Linked #{child_iid} as sub-issue of #{parent_iid}")
            except GithubException as e:
                logger.warning(f"Failed to create sub-issue: #{child_iid} -> #{parent_iid}: {e}")
```

**Step 11: Create _create_blocking_relations() method**

```python
def _create_blocking_relations(
    self,
    github_issues: dict[int, github.Issue.Issue],
    pending_relations: list[dict[str, Any]],
) -> None:
    """Third pass: create blocking dependencies."""
    if not pending_relations:
        return

    logger.info(f"Processing {len(pending_relations)} blocking relationships...")
    owner, repo = self.github_repo_path.split("/")

    for pending in pending_relations:
        source_iid = pending["source_gitlab_iid"]
        relation = pending["relation"]
        target_iid = int(relation.target_iid)

        if source_iid not in github_issues or target_iid not in github_issues:
            logger.warning(f"Issue not found for blocking relation: #{source_iid} or #{target_iid}")
            continue

        source_github = github_issues[source_iid]
        target_github = github_issues[target_iid]

        # Determine blocked/blocking based on link type
        if relation.type == "blocks":
            blocked_number = target_github.number
            blocking_id = source_github.id
        else:  # is_blocked_by
            blocked_number = source_github.number
            blocking_id = target_github.id

        success = ghu.create_issue_dependency(
            self.github_client, owner, repo, blocked_number, blocking_id
        )

        if success:
            logger.debug(f"Created blocking: #{source_iid} {relation.type} #{target_iid}")
```

**Step 12: Update migrate_issue_comments() to use attachment handler**

```python
def migrate_issue_comments(
    self, gitlab_issue: GitlabProjectIssue, github_issue: github.Issue.Issue
) -> None:
    """Migrate comments for an issue."""
    notes = gitlab_issue.notes.list(get_all=True)
    notes.sort(key=lambda n: n.created_at)

    for note in notes:
        if note.system:
            comment_body = f"**System note:** {note.body}"
        else:
            from .issue_builder import format_timestamp

            comment_body = (
                f"**Comment by** {note.author['name']} (@{note.author['username']}) "
                f"**on** {format_timestamp(note.created_at)}\n\n---\n\n"
            )
            if note.body:
                comment_body += self.attachment_handler.process_content(
                    note.body, context=f"issue #{gitlab_issue.iid} note {note.id}"
                )

        github_issue.create_comment(comment_body)
        logger.debug(f"Migrated comment by {note.author['username']}")
```

**Step 13: Run all tests to verify behavior unchanged**

Run: `uv run pytest -v -s`
Expected: All tests PASS

**Step 14: Remove noqa comments and run linter**

Remove `# noqa: PLR0912, PLR0915` from any remaining methods.

Run: `uv run ruff check src/gitlab_to_github_migrator/migrator.py`
Expected: PASS (no PLR0912/PLR0915 errors)

**Step 15: Run type checker**

Run: `uv run basedpyright src/gitlab_to_github_migrator/`
Expected: PASS (no errors on migrator.py)

**Step 16: Commit**

```bash
git add src/gitlab_to_github_migrator/migrator.py
git commit -m "refactor: migrator.py uses extracted modules

- Delegates git operations to git_migration.py
- Uses AttachmentHandler for attachment migration
- Uses get_issue_cross_links from relationships.py
- Uses build_issue_body from issue_builder.py
- Splits migrate_issues_with_number_preservation into smaller methods
- Removes PLR0912/PLR0915 suppressions

Refs #57"
```

---

## Task 9: Update __init__.py exports

**Files:**
- Modify: `src/gitlab_to_github_migrator/__init__.py`

**Step 1: Check current exports**

Read the file to see what's currently exported.

**Step 2: Add new exports if needed**

If `DownloadedFile` was previously exported, update to import from `attachments`:

```python
from .attachments import AttachmentHandler, DownloadedFile
from .relationships import IssueCrossLinks, IssueLinkInfo, WorkItemChild
```

**Step 3: Run tests**

Run: `uv run pytest -v -s`
Expected: PASS

**Step 4: Commit**

```bash
git add src/gitlab_to_github_migrator/__init__.py
git commit -m "refactor: update __init__.py exports for new modules

Refs #57"
```

---

## Task 10: Update existing tests

**Files:**
- Modify: `tests/test_gitlab_to_github_migrator.py`

**Step 1: Update TestTimestampFormatting to use new location**

```python
@pytest.mark.unit
class TestTimestampFormatting:
    def test_format_timestamp_with_z_suffix(self) -> None:
        from gitlab_to_github_migrator.issue_builder import format_timestamp
        result = format_timestamp("2024-01-15T10:30:45.123Z")
        assert result == "2024-01-15 10:30:45Z"
    # ... update other tests similarly
```

**Step 2: Update attachment tests to use AttachmentHandler**

Update tests that reference `migrator.download_gitlab_attachments()` to use `AttachmentHandler.process_content()` or test the handler directly.

**Step 3: Run all tests**

Run: `uv run pytest -v -s`
Expected: PASS

**Step 4: Run full quality checks**

```bash
uv run ruff check .
uv run ruff format .
uv run basedpyright .
uv run codespell .
```

Expected: All PASS

**Step 5: Commit**

```bash
git add tests/
git commit -m "test: update tests for refactored module structure

- Update timestamp tests to use issue_builder.format_timestamp
- Update attachment tests to use AttachmentHandler

Refs #57"
```

---

## Task 11: Final verification and cleanup

**Step 1: Run integration tests**

Run: `uv run pytest -m integration -v -s`
Expected: PASS (if tokens configured)

**Step 2: Verify line counts**

Check that migrator.py is now ~600 lines:
```bash
wc -l src/gitlab_to_github_migrator/migrator.py
```

**Step 3: Verify no noqa suppressions remain for PLR0912/PLR0915**

```bash
grep -n "PLR0912\|PLR0915" src/gitlab_to_github_migrator/migrator.py
```
Expected: No output (no suppressions)

**Step 4: Final commit summarizing the refactoring**

```bash
git add -A
git commit -m "refactor: complete migrator.py restructure

Summary of changes:
- Created git_migration.py (~80 lines)
- Created attachments.py (~150 lines)
- Created relationships.py (~120 lines)
- Created issue_builder.py (~40 lines)
- Added get_work_item_children to gitlab_utils.py
- Added create_issue_dependency to github_utils.py
- Reduced migrator.py from ~1184 to ~600 lines
- Removed all PLR0912/PLR0915 linter suppressions

Closes #57"
```
