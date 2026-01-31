# Migrator.py Restructure Design

**Issue:** #57
**Date:** 2026-01-31
**Goal:** Make migrator.py simpler and more understandable by extracting distinct responsibilities into separate modules. Remove PLR0912/PLR0915 linter suppressions.

## Current State

`migrator.py` is 1184 lines with several concerns tangled together:
- Git clone/push operations (~90 lines)
- Attachment download/upload (~120 lines)
- Relationship detection and creation (~190 lines)
- Issue body construction (~40 lines)
- Three-pass issue migration (~200 lines)

Two methods have `# noqa: PLR0912, PLR0915` suppressions:
- `migrate_git_content()`
- `migrate_issues_with_number_preservation()`

## Design Principles

1. **Extract by domain** - Each new file has one clear responsibility
2. **Pure operations to utils** - GitLab-only or GitHub-only operations go to `g*_utils.py`
3. **No single-line wrappers** - Don't wrap simple API calls unnecessarily
4. **Explicit dependencies** - Extracted functions take explicit parameters, not the whole migrator

## New File Structure

```text
src/gitlab_to_github_migrator/
├── migrator.py           # Orchestration + metadata migration (~600 lines)
├── git_migration.py      # Git mirroring function (~80 lines)
├── attachments.py        # AttachmentHandler class (~150 lines)
├── relationships.py      # Dataclasses + cross-link detection (~120 lines)
├── issue_builder.py      # Build issue body from GitLab data (~40 lines)
├── gitlab_utils.py       # + get_work_item_children() (~350 lines)
├── github_utils.py       # + create_issue_dependency() (~200 lines)
├── label_translator.py   # (unchanged)
├── exceptions.py         # (unchanged)
├── utils.py              # (unchanged)
└── cli.py                # (unchanged)
```

## Module Details

### git_migration.py

Stateless function for mirroring git repositories.

```python
def migrate_git_content(
    source_http_url: str,
    target_clone_url: str,
    source_token: str | None,
    target_token: str,
    local_clone_path: Path | None = None,
) -> None:
    """Mirror git repository from source to target."""
```

**Private helpers:**
- `_inject_token(url, token, prefix)` - Insert token into HTTPS URL
- `_sanitize_error(error, tokens)` - Remove tokens from error messages

**Logic:**
1. Use local clone or create temp mirror clone
2. Inject source token, clone with `--mirror`
3. Add target remote with injected token
4. Push `--mirror` to target
5. Remove remote (cleanup token from git config)
6. Remove temp directory if created

### attachments.py

Class for handling attachment migration with caching.

```python
@dataclass
class DownloadedFile:
    filename: str
    content: bytes
    short_gitlab_url: str
    full_gitlab_url: str

class AttachmentHandler:
    def __init__(
        self,
        gitlab_client: Gitlab,
        gitlab_project: GitlabProject,
        github_repo: Repository,
    ) -> None: ...

    @property
    def attachments_release(self) -> GitRelease:
        """Get or create draft release for storing attachments (cached)."""

    def process_content(self, content: str, context: str = "") -> str:
        """Download GitLab attachments, upload to GitHub, return updated content."""

    def _download_files(self, content: str) -> tuple[list[DownloadedFile], str]:
        """Find attachment URLs, download files, replace cached URLs."""

    def _upload_files(self, files: list[DownloadedFile], content: str, context: str) -> str:
        """Upload files to GitHub release, update content with new URLs."""
```

### relationships.py

Dataclasses and detection logic for issue relationships.

```python
@dataclass
class WorkItemChild:
    iid: int
    title: str
    state: str
    type: str
    web_url: str

@dataclass
class IssueLinkInfo:
    type: str
    target_iid: int
    target_title: str
    target_project_path: str
    target_web_url: str
    is_same_project: bool
    source: str = "rest_api"

@dataclass
class IssueCrossLinks:
    cross_links_text: str
    parent_child_relations: list[IssueLinkInfo]
    blocking_relations: list[IssueLinkInfo]

def get_issue_cross_links(
    gitlab_issue: GitlabProjectIssue,
    gitlab_project_path: str,
    graphql_client: GraphQL,
) -> IssueCrossLinks:
    """Get cross-linked issues separated by relationship type."""
```

### issue_builder.py

Functions for constructing GitHub issue content from GitLab data.

```python
def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO 8601 timestamp to human-readable format.

    Returns "2024-01-15 10:30:45Z" for UTC, keeps original if parsing fails.
    """

def build_issue_body(
    gitlab_issue: GitlabProjectIssue,
    processed_description: str,
    cross_links_text: str,
) -> str:
    """Build complete GitHub issue body with migration header.

    Includes: migration notice, original author, created date, GitLab URL,
    separator, processed description, and cross-links section.
    """
```

### gitlab_utils.py additions

Move `get_work_item_children()` from migrator:

```python
def get_work_item_children(
    graphql_client: GraphQL,
    project_path: str,
    issue_iid: int,
) -> list[WorkItemChild]:
    """Get child work items for an issue using GraphQL Work Items API."""
```

Import `WorkItemChild` from `relationships.py`.

### github_utils.py additions

Move `create_github_issue_dependency()` from migrator:

```python
def create_issue_dependency(
    client: Github,
    owner: str,
    repo: str,
    blocked_issue_number: int,
    blocking_issue_id: int,
) -> bool:
    """Create GitHub issue dependency (blocked-by relationship).

    Returns True if created, False if already exists.
    """
```

### migrator.py changes

After extraction, the class structure becomes:

```python
class GitlabToGithubMigrator:
    def __init__(...) -> None:
        # ... existing init ...
        self._attachment_handler = AttachmentHandler(
            self.gitlab_client, self.gitlab_project, self.github_repo
        )

    # Properties (unchanged)
    @property
    def github_repo(self) -> Repository: ...

    # Validation (unchanged)
    def validate_api_access(self) -> None: ...

    # Git migration (now delegates)
    def migrate_git_content(self) -> None:
        git_migration.migrate_git_content(
            source_http_url=self.gitlab_project.http_url_to_repo,
            target_clone_url=self.github_repo.clone_url,
            source_token=self.gitlab_token,
            target_token=self.github_token,
            local_clone_path=self.local_clone_path,
        )

    # Metadata migration (simplified)
    def migrate_labels(self) -> None: ...  # unchanged
    def migrate_milestones_with_number_preservation(self) -> None: ...  # unchanged

    # Issue migration (split into passes)
    def migrate_issues_with_number_preservation(self) -> None:
        """Orchestrates the three-pass issue migration."""
        issues, placeholders = self._create_issues_first_pass()
        self._create_parent_child_relations(issues)
        self._create_blocking_relations(issues)

    def _create_issues_first_pass(self) -> tuple[dict, list]:
        """Create issues and placeholders, collect pending relationships."""

    def _create_parent_child_relations(self, github_issues: dict) -> None:
        """Second pass: create sub-issue relationships."""

    def _create_blocking_relations(self, github_issues: dict) -> None:
        """Third pass: create blocking dependencies."""

    def migrate_issue_comments(self, gitlab_issue, github_issue) -> None: ...

    # Cleanup and validation (unchanged)
    def cleanup_placeholders(self) -> None: ...
    def validate_migration(self) -> dict[str, Any]: ...

    # Main orchestrator (unchanged)
    def create_github_repo(self) -> None: ...
    def migrate(self) -> dict[str, Any]: ...
```

## Migration Path

1. Create new modules with extracted code
2. Update imports in migrator.py
3. Replace inline code with calls to new modules
4. Split `migrate_issues_with_number_preservation()` into private methods
5. Remove `# noqa` suppressions
6. Run linter and type checker to verify
7. Run tests to verify behavior unchanged

## Expected Outcomes

- `migrator.py` reduced from ~1184 to ~600 lines
- All `# noqa: PLR0912, PLR0915` suppressions removed
- Each module has single, clear responsibility
- Easier to test individual components
- No change in external behavior or API
