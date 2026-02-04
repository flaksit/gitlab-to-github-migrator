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

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) package manager
- Git
- Access to both GitLab and GitHub APIs

### Quick Install

```bash
# Bleeding edge
uv tool install git+https://github.com/flaksit/gitlab-to-github-migrator

# Specific version, e.g., v0.1.0
uv tool install git+https://github.com/flaksit/gitlab-to-github-migrator@v0.1.0

# The tool is now ready to use
uv run gitlab-to-github-migrator --help
```

Note: Only the main `gitlab-to-github-migrator` CLI is installed. Developer-only tools are run from a checkout (see Development).

## Authentication Setup

### Token Requirements

- **GitLab Token**: Read access to source project, issues, milestones, and labels. The token should have at least `read_api` and `read_repository` scope.
- **GitHub Token**: Repository access for target user/organization: Finegrained token with:
  - Owner: the target user/org
  - Repository permissions: Read and Write for `Administration`, `Contents`, `Issues`
  Alas, this gives immediately delete rights for repositories.

### Token Resolution Order

When no explicit token path is provided via CLI options, tokens are resolved in this order:
1. Environment variable (`SOURCE_GITLAB_TOKEN` / `TARGET_GITHUB_TOKEN`)
2. Default `pass` path (`gitlab/api/ro_token` / `github/api/token`)

### Option 1: Using Environment Variables

```bash
# Set environment variables
export SOURCE_GITLAB_TOKEN="your_gitlab_token"
export TARGET_GITHUB_TOKEN="your_github_token"
```

Note: Environment variables may be visible in process lists and logs.

### Option 2: Using `pass` Utility

```bash
# Store GitLab token in the default location (read-only recommended)
pass insert gitlab/api/ro_token

# Store GitHub token in the default location (requires repo creation permissions)
pass insert github/api/token
```

## Usage

### Basic Migration

```bash
uv run gitlab-to-github-migrator source/project target/repo
```

### Advanced Migration with Label Translation

```bash
# See help for available options
uv run gitlab-to-github-migrator -h

# Using short options
uv run gitlab-to-github-migrator source/project target/repo \
  -l "p_*:priority: *" \
  -l "comp_*:component: *" \
  -l "t_*:type: *" \
  --local-clone "/path/to/existing/clone" \
  -v

# Using full option names
uv run gitlab-to-github-migrator \
  --gitlab-project "source/project" \
  --github-repo "target/repo" \
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

#### Case-Insensitive Label Matching

GitHub treats labels as case-insensitive ("Bug" and "bug" are the same label). When a translated GitLab label matches an existing GitHub label (including organization defaults), the migrator uses the existing label's name rather than creating a duplicate. For example, if GitLab has a "documentation" label and GitHub has "Documentation", the existing "Documentation" label will be used.

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

```text
==================================================
MIGRATION REPORT
==================================================
GitLab Project: source/project
GitHub Repository: target/repo
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
git clone git@github.com:flaksit/gitlab-to-github-migrator.git
cd gitlab-to-github-migrator

# Install development dependencies
uv sync
```

### Running Tests

#### TL;DR
```bash
# Before running, ensure the passphrase cache won't expire during the tests, so just run `pass` once to enter the passphrase.
pass github/api/token > /dev/null

# Set required environment variables for integration tests
export SOURCE_GITLAB_TEST_PROJECT="your-namespace/your-project"
export TARGET_GITHUB_TEST_OWNER="your-org-or-username"

# Run all tests (unit and integration), with default tokens from `pass` (see below)
uv run pytest -v

# Cleanup all test repos that were created under the GitHub owner
uv run python -m gitlab_to_github_migrator.delete_test_repos github/admin_token

# Run just unit tests
uv run pytest -m "not integration" -v
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

**Test Configuration:**

Integration tests require configuration via environment variables to specify the source GitLab project and target GitHub organization/user:

**Required Environment Variables:**

```bash
# Required: Set GitLab test project
export SOURCE_GITLAB_TEST_PROJECT="your-namespace/your-project"

# Required: Set GitHub organization/user for test repositories
export TARGET_GITHUB_TEST_OWNER="your-org-or-username"
```

**Running Integration Tests:**

```bash
# Run all integration tests
uv run pytest -m integration -v

# Run specific integration test
uv run pytest -m integration tests/test_integration_real.py::TestRealAPIIntegration::test_gitlab_source_project_access -v
```

#### Creating a GitLab Test Project

The `create_gitlab_test_project` module creates a GitLab project with test data covering all migration edge cases: labels, milestones (with gaps in numbering), issues (with gaps), issue relationships (parent-child, blocking, related), comments, attachments, branches, and tags.

**Prerequisites:**
- GitLab token with write access: set `SOURCE_GITLAB_TOKEN` env var or store in `pass` at `gitlab/api/rw_token`
- Git configured for SSH access to GitLab

**Usage:**
```bash
# Run the script with the project path
uv run python -m gitlab_to_github_migrator.create_gitlab_test_project namespace/project-name

# For nested groups
uv run python -m gitlab_to_github_migrator.create_gitlab_test_project group/subgroup/project-name

# Then follow the manual instructions printed at the end for adding attachments
# (attachments cannot be uploaded via API)

# Verify with integration tests
export SOURCE_GITLAB_TEST_PROJECT=namespace/project-name
export TARGET_GITHUB_TEST_OWNER=your-org-or-username
uv run pytest tests/test_integration_real.py -v -m integration
```

The script is idempotent - it can be run multiple times and will skip resources that already exist.

#### Cleanup of Test Repositories

Integration tests create temporary repositories in the GitHub organization or user account specified by `TARGET_GITHUB_TEST_OWNER`. These repositories require manual cleanup, because pytest deliberately does not delete them so you can inspect them manually after the test. The tests will display instructions like:
```text
⚠️  Cannot delete test repository <owner>/gl2ghmigr-full-migration-test-abc123: insufficient permissions
   To clean up test repositories, run:
  uv run python -m gitlab_to_github_migrator.delete_test_repos <github_owner> <pass_path>
   where <github_owner> is the GitHub organization or user
   and <pass_path> is a 'pass' path containing a GitHub token with repository deletion rights.
```

**Manual Cleanup:**
```bash
# Using the cleanup script with TARGET_GITHUB_TEST_OWNER environment variable
export TARGET_GITHUB_TEST_OWNER="your-org-or-username"
uv run python -m gitlab_to_github_migrator.delete_test_repos github/admin/token

# Or specify the owner explicitly
uv run python -m gitlab_to_github_migrator.delete_test_repos your-org github/admin/token
uv run python -m gitlab_to_github_migrator.delete_test_repos your-username github/admin/token

# List what would be cleaned up without actually deleting
# TODO add a dry-run option to the cleanup script
uv run python -c "
import subprocess
result = subprocess.run(['pass', 'github/admin/token'], capture_output=True, text=True)
token = result.stdout.strip()
from github import Auth, Github
g = Github(auth=Auth.Token(token))
org = g.get_organization('your-org')  # or g.get_user('your-username') for user account
import re
pattern = re.compile(r'gl2ghmigr-(.+-)?test\\b')
repos = [r for r in org.get_repos() if pattern.match(r.name)]
print(f'Found {len(repos)} test repositories to clean up')
for repo in repos:
    print(f'  - {repo.name} (created: {repo.created_at})')
"
```

### Project Structure

```text
gitlab-to-github-migrator/
├── src/
│   └── gitlab_to_github_migrator/
│       ├── __init__.py                      # Package marker
│       ├── cli.py                           # Command-line interface
│       ├── create_gitlab_test_project.py    # Creates GitLab test project for integration tests
│       ├── delete_test_repos.py             # Cleanup script for orphaned test repositories
│       ├── exceptions.py                    # Custom exception classes
│       ├── migrator.py                      # Main module: migration logic and orchestration
│       ├── translator.py                    # Label and metadata translation logic
│       └── utils.py                         # Utility/helper functions
├── tests/
│   ├── test_gitlab_to_github_migrator.py # Unit tests (mocked)
│   └── test_integration_real.py          # Integration tests (real APIs)
├── uv.lock                # Dependency lock file
├── pyproject.toml         # Project configuration and dependencies
└── README.md              # This file
```

### Code Architecture

#### Core Components

- **`GitlabToGithubMigrator`**: Main migration class
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

```bash
# Clone the repository
git clone git@github.com:flaksit/gitlab-to-github-migrator.git
cd gitlab-to-github-migrator

# If you have direnv, allow it. Then uv sync is done automatically
direnv allow

# Install dependencies
uv sync
```

#### Git Hooks

Enable the pre-commit hook to enforce code formatting:
```bash
git config core.hooksPath .githooks
# Optional: auto-format on commit instead of just checking
git config hooks.autoformat true
```

**Note:** With `hooks.autoformat`, files that need formatting are fully staged after formatting. If you had partially staged a file, the entire file will be committed.

For AI agents (e.g., GitHub Copilot), enable strict checks to run linting and type checking on every commit:
```bash
git config hooks.strictChecks true
```

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

Follow full **Test-Driven Development (TDD)** red-green approach:

1. **Write ALL Tests First** (Red Phase):
   - Add **unit tests** in `tests/unit/` (or mark with `@pytest.mark.unit`)
   - Add **integration tests** in `tests/integration/` (or mark with `@pytest.mark.integration`)
   - Run tests to verify they fail (red)
2. **Implement Feature** (Green Phase): Update the relevant code in `src/gitlab_to_github_migrator/` to make tests pass.
3. **Verify Tests Pass**: Run all tests (unit and integration) to ensure they pass (green).
4. **Documentation**: Update this README.

#### Testing Strategy

- **Unit Tests**: Minimal, for not spending too much resources on mocking external APIs
- **Integration Tests**: Use real APIs with actual GitLab project data
- **Test Coverage**: Aim for >90% coverage of core migration logic

### Troubleshooting

#### Common Issues

##### Authentication Errors
```bash
# Verify token access
uv run python -c "
import gitlab, subprocess
token = subprocess.run(['pass', 'gitlab/api/ro_token'], capture_output=True, text=True).stdout.strip()
gl = gitlab.Gitlab('https://gitlab.com', private_token=token)
print('GitLab access:', gl.projects.get('your-namespace/your-project').name)
"
```

##### Rate Limiting
- Rate limit handling is built into the PyGithub and python-gitlab libraries and enabled by default
- PyGithub: Uses `GithubRetry` with 10 retries, automatically waits on 403 with Retry-After header
- python-gitlab: Uses `obey_rate_limit=True` by default with `max_retries=10`, sleeps on 429 responses
- Note: GraphQL calls for Work Items and attachment downloads use raw `requests` without retry logic, but these are low-volume operations (one call per issue/attachment) and unlikely to hit rate limits

##### Target Repository Already Exists
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

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
