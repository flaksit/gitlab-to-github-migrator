# GitLab to GitHub Migration Tool

A Python tool for migrating GitLab projects to GitHub with full metadata preservation, including exact issue/milestone numbers, comments, attachments, and relationships.

## Features

- **Complete Metadata Preservation**: Migrates issues, milestones, labels, comments, and attachments
- **Attachment Handling**: Downloads and preserves GitLab attachment references in issues and comments
- **Exact Number Preservation**: Ensures GitLab issue #X becomes GitHub issue #X
- **Configurable Label Translation**: Transform GitLab labels using flexible patterns
- **Issue Relationship Migration**:
  - **Blocking relationships** (`blocks`/`is_blocked_by`): Migrated to GitHub's native issue dependencies API
  - **Parent-child relationships** (GitLab work item hierarchy): Migrated to GitHub sub-issues
  - **Related issues** (`relates_to`): Preserved as formatted text in issue description
- **Robust Error Handling**: Comprehensive validation and rollback capabilities
- **Authentication Support**: Works with environment variables or `pass` utility

## Installation

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- Git
- Access to both GitLab and GitHub APIs

### Quick Install

```bash
# Clone the repository
git clone git@github.com:abuflow/gitlab-to-github-migrator.git
cd gitlab-to-github-migrator

# Install dependencies
uv sync --no-dev

# The tool is now ready to use
uv run gitlab-to-github-migrator --help
```

## Authentication Setup

### Token Requirements

- **GitLab Token**: Read access to source project, issues, milestones, and labels
- **GitHub Token**: Full repository access for target user/organization. Optional delete_repo permission for cleanup script.

### Option 1: Using `pass` Utility

```bash
# Store GitLab token in the default location (read-only recommended)
pass insert gitlab/api/ro_token

# Store GitHub token in the default location (requires repo creation permissions)
pass insert github/api/token
```

### Option 2: Using Environment Variables
Not recommended to use environment variables directly because this shows tokens in process lists and logs.

```bash
# Set environment variables
export GITLAB_TOKEN="your_gitlab_token"
export GITHUB_TOKEN="your_github_token"
```

## Usage

### Basic Migration

```bash
uv run gitlab-to-github-migrator flaks/jk/jkx abuflow/migrated-project
```

### Advanced Migration with Label Translation

```bash
# See help for available options
uv run gitlab-to-github-migrator -h

# Using short options
uv run gitlab-to-github-migrator flaks/jk/jkx abuflow/migrated-project \
  -l "p_*:priority: *" \
  -l "comp_*:component: *" \
  -l "t_*:type: *" \
  --local-clone "/path/to/existing/clone" \
  -v

# Using full option names
uv run gitlab-to-github-migrator \
  --gitlab-project "flaks/jk/jkx" \
  --github-repo "abuflow/migrated-project" \
  --label-translation "p_*:priority: *" \
  --label-translation "comp_*:component: *" \
  --label-translation "t_*:type: *" \
  --local-clone-path "/path/to/existing/clone" \
  --gitlab-token-pass-path "gitlab/api/other_ro_token" \
  --github-token-pass-path "github/api/other_token" \
  --verbose
```

#### Label Translation Patterns

Label translation uses glob-style patterns:

- `"p_high:priority: high"` - Literal replacement
- `"p_*:priority: *"` - Wildcard transformation (p_high → priority: high)


## Migration Process

1. **Validation**: Verifies API access and project existence
2. **Repository Creation**: Creates GitHub repository with metadata
3. **Git Content Migration**: Pushes all branches and tags
4. **Label Migration**: Creates and translates labels
5. **Milestone Migration**: Preserves milestone numbers using placeholders
6. **Issue Migration**: Preserves issue numbers with full content
7. **Relationship Migration**: Creates GitHub sub-issues and issue dependencies
8. **Cleanup**: Removes placeholder items
9. **Validation**: Generates migration report

## Example Migration Report

```
==================================================
MIGRATION REPORT
==================================================
GitLab Project: flaks/jk/jkx
GitHub Repository: abuflow/migrated-project
Success: True

Statistics:
  gitlab_issues_total: 378
  gitlab_issues_open: 123
  gitlab_issues_closed: 255
  github_issues_total: 378
  github_issues_open: 123
  github_issues_closed: 255
  gitlab_milestones_total: 17
  gitlab_milestones_open: 5
  gitlab_milestones_closed: 12
  github_milestones_total: 17
  github_milestones_open: 5
  github_milestones_closed: 12
  gitlab_labels_total: 31
  github_labels_existing: 9
  github_labels_created: 22
  labels_translated: 31

Migration completed successfully!
```

## Development

### Development Setup

```bash
# Clone and setup development environment
git clone git@github.com:abuflow/gitlab-to-github-migrator.git
cd gitlab-to-github-migrator

# Install development dependencies
uv sync
```

### Running Tests

#### TL;DR
```bash
# Before running, ensure the passphrase cache won't expire during the tests, so just run `pass` once to enter the passphrase.
pass github/api/token > /dev/null
# Run all tests (unit and integration) in parallel, with default tokens from `pass` (see below)
uv run pytest -v -n auto
# If the GitHub token doesn't have repository deletion rights, run test repo cleanup script
uv run delete_test_repos abuflow github/admin_token

# Run just unit tests (fast, in parallel)
uv run pytest -m "not integration" -v -n auto
```

#### Test Structure

Tests are organized to clearly distinguish between **unit tests** (fast, no real API calls) and **integration tests** (use real APIs):

- **Unit tests**: Warmly encouraged to be marked with the `@pytest.mark.unit` marker (but marking not strictly required).
- **Integration tests**: Should be marked with the `@pytest.mark.integration` marker. These require real API tokens and may create/delete real resources.

```python
# At the top of a test file or class
import pytest

class TestLabelTranslator:
    ...

@pytest.mark.integration
class TestRealAPIIntegration:
    ...
```

#### Unit Tests (Fast)
```bash
# Run all unit tests
uv run pytest -m "not integration" -v

# Run specific test class
uv run pytest -m "not integration" tests/test_gitlab_to_github_migrator.py::TestLabelTranslator -v

# Run with coverage
uv run pytest -m "not integration" --cov=src/gitlab_to_github_migrator
```

#### Integration Tests (Requires Authentication)
For authentication setup, see the [Authentication Setup](#authentication-setup) section.

```bash
# Run all integration tests (in parallel)
uv run pytest -m integration -v -n auto

# Run integration tests sequentially (for debugging)
uv run pytest -m integration -v -s

# Run specific integration test
uv run pytest -m integration tests/test_integration_real_api.py::TestRealAPIIntegration::test_gitlab_source_project_access -v -s
```

#### Cleanup of Test Repositories

Integration tests create temporary repositories in the `abuflow` GitHub organization for testing. If the GitHub token doesn't have delete permissions for repositories, these repositories require manual cleanup. In that case, the tests will display instructions like:
```
⚠️  Cannot delete test repository abuflow/migration-test-abc123: insufficient permissions
   To clean up test repositories, run:
   uv run delete_test_repos <github_owner> <pass_path>
   where <github_owner> is the GitHub organization or user (e.g., 'abuflow')
   and <pass_path> is a 'pass' path containing a GitHub token with repository deletion rights.
```

**Manual Cleanup:**
```bash
# Using the cleanup script with admin token for organization
uv run delete_test_repos abuflow github/admin/token

# Using the cleanup script for a user account
uv run delete_test_repos myusername github/admin/token

# List what would be cleaned up without actually deleting
# TODO add a dry-run option to the cleanup script
uv run python -c "
import subprocess
result = subprocess.run(['pass', 'github/admin/token'], capture_output=True, text=True)
token = result.stdout.strip()
from github import Github
g = Github(token)
org = g.get_organization('abuflow')
repos = [r for r in org.get_repos() if r.name.startswith('migration-test-') or r.name.startswith('deletion-test-')]
print(f'Found {len(repos)} test repositories to clean up')
for repo in repos:
    print(f'  - {repo.name} (created: {repo.created_at})')
"
```

#### Test Configuration

Integration tests require configuration via environment variables:
- **Source**: GitLab project (REQUIRED via `GITLAB_TEST_PROJECT` environment variable)
- **Target**: Temporary GitHub repositories (REQUIRED via `GITHUB_TEST_ORG` environment variable)

**Required Environment Variables for Testing:**

```bash
# Required: Set GitLab test project
export GITLAB_TEST_PROJECT="your-namespace/your-project"

# Required: Set GitHub organization/user for test repositories
export GITHUB_TEST_ORG="your-org-or-username"

# Run integration tests
uv run pytest -m integration -v
```

**Note:** Test repositories require manual cleanup if the GitHub token doesn't have deletion permissions.

### Project Structure

```
gitlab-to-github-migrator/
├── src/
│   └── gitlab_to_github_migrator/
│       ├── __init__.py           # Package marker
│       ├── cli.py                # Command-line interface
│       ├── exceptions.py         # Custom exception classes
│       ├── migrator.py           # Main module: migration logic and orchestration
│       ├── translator.py         # Label and metadata translation logic
│       └── utils.py              # Utility/helper functions
├── tests/
│   ├── test_gitlab_to_github_migrator.py # Unit tests (mocked)
│   └── test_integration_real.py          # Integration tests (real APIs)
├── uv.lock                # Dependency lock file
├── pyproject.toml         # Project configuration and dependencies
└── README.md              # This file
```

### Code Architecture

#### Core Components

- **`GitLabToGitHubMigrator`**: Main migration class
- **`LabelTranslator`**: Handles label pattern translation
- **Error Classes**: `MigrationError`, `NumberVerificationError`

#### Key Methods

- `validate_api_access()`: Verify API connectivity
- `create_github_repository()`: Create target repository
- `migrate_repository_content()`: Git content migration
- `handle_labels()`: Label creation and translation
- `migrate_milestones_with_number_preservation()`: Milestone migration
- `migrate_issues_with_number_preservation()`: Issue migration
- `validate_migration()`: Generate migration report

### Contributing

#### Code Quality Tools

The project includes several code quality tools as development dependencies:

```bash
# Run linting with ruff
uv run ruff check .
uv run ruff format .  # Auto-format code

# Run type checking with basedpyright
uv run basedpyright .

# Run spell checking with codespell
uv run codespell .

# Run all quality checks together
uv run ruff check . && uv run basedpyright . && uv run codespell .
```

**Configuration:**
All are configured in `pyproject.toml`:
- **Ruff**: Comprehensive rule set and 119-character line length
- **BasedPyright**: Strict settings for better type safety

#### Adding New Features

1. **Write Tests First**: Add unit tests in `tests/unit/` (or mark with `@pytest.mark.unit`).
2. **Implement Feature**: Update the relevant code in `src/gitlab_to_github_migrator/`.
3. **Integration Test**: Add integration tests in `tests/integration/` (or mark with `@pytest.mark.integration`) if needed.
4. **Documentation**: Update this README.

#### Testing Strategy

- **Unit Tests**: Minimal, for not spending too much resources on mocking external APIs
- **Integration Tests**: Use real APIs with actual GitLab project data
- **Test Coverage**: Aim for >90% coverage of core migration logic

### Troubleshooting

#### Common Issues

**Authentication Errors**
```bash
# Verify token access
uv run python -c "
import gitlab, os
token = subprocess.run(['pass', 'gitlab/api/ro_token'], capture_output=True, text=True).stdout.strip()
gl = gitlab.Gitlab('https://gitlab.com', private_token=token)
print('GitLab access:', gl.projects.get('flaks/jk/jkx').name)
"
```

**Rate Limiting**
- Rate limit handling is built into the PyGithub and python-gitlab libraries and enabled by default
- PyGithub: Uses `GithubRetry` with 10 retries, automatically waits on 403 with Retry-After header
- python-gitlab: Uses `obey_rate_limit=True` by default with `max_retries=10`, sleeps on 429 responses
- Note: GraphQL calls for Work Items and attachment downloads use raw `requests` without retry logic, but these are low-volume operations (one call per issue/attachment) and unlikely to hit rate limits

**Target Repository Already Exists**
- Tool will abort if target repository exists
- Manually delete or choose different name

#### Debug Mode

```bash
# Enable maximum verbosity
uv run gitlab-to-github-migrator "source/project" "target/repo" --verbose

# Check migration logs
tail -f migration.log
```

### API Documentation

- [python-gitlab Documentation](https://python-gitlab.readthedocs.io/)
- [PyGithub Documentation](https://pygithub.readthedocs.io/)
- [GitLab API Reference](https://docs.gitlab.com/ee/api/)
- [GitHub API Reference](https://docs.github.com/en/rest)

## License

This project is part of the Abu trading platform ecosystem.
