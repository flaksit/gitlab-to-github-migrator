# CLAUDE.md / AGENTS.md

This file provides guidance to AI agents when working with code in this repository.

## Project Overview

GitLab to GitHub Migrator - Python tool for migrating GitLab projects to GitHub with full metadata preservation. Preserves exact issue/milestone numbers, comments, attachments, and relationships (blocking, parent-child, related).

## Commands

```bash
# Install dependencies
uv sync              # With dev dependencies
uv sync --no-dev     # Production only

# Run the tool
uv run gitlab-to-github-migrator --help

# Code quality (run all before committing)
uv run ruff check .         # Lint
uv run ruff format .        # Auto-format
uv run basedpyright .       # Type check
uv run codespell .          # Spell check

# Tests
uv run pytest -v -s                           # All tests (requires API tokens)
uv run pytest -m "not integration" -v -s      # Unit tests only (fast, doesn't require API tokens)
uv run pytest -m integration -v -s            # Integration tests only
uv run pytest -v -s --cov=src/gitlab_to_github_migrator  # With coverage

# Run single test
uv run pytest tests/test_gitlab_to_github_migrator.py::TestLabelTranslator::test_basic -v -s

# Test utilities
uv run create-gitlab-test-project namespace/project-name  # Create test project
uv run delete-test-repos github/admin_token               # Cleanup test repos
```

## Architecture

**Core module:** `src/gitlab_to_github_migrator/migrator.py` (~1200 lines)
- `GitlabToGithubMigrator` class orchestrates the entire migration
- Number preservation via placeholder items (creates gaps, verifies each number)
- GraphQL API for GitLab work items (parent-child relationships)
- REST API for most other operations

**Supporting modules:**
- `cli.py` - argparse CLI with label translation patterns
- `label_translator.py` - Glob-style pattern translation (`p_*:priority: *`)
- `github_utils.py` / `gitlab_utils.py` - API client setup, token handling via `pass` utility
- `exceptions.py` - `MigrationError`, `NumberVerificationError`

**Data flow:**
1. Validate API access → 2. Create GitHub repo → 3. Push git content → 4. Migrate labels → 5. Migrate milestones (with number preservation) → 6. Migrate issues (with number preservation) → 7. Create relationships (sub-issues, dependencies) → 8. Cleanup placeholders → 9. Generate report

## Test Environment

Integration tests require:
```bash
export GITLAB_TEST_PROJECT="namespace/project"
export GITHUB_TEST_ORG="org-or-username"
```

Tokens stored in env vars SOURCE_GITLAB_TOKEN and TARGET_GITHUB_TOKEN, or retrieved via `pass`:
- `gitlab/api/ro_token` (read-only for tests)
- `github/api/token` (needs repo creation)
- `gitlab/api/rw_token` (for creating test project)

## Code Style

- Python 3.14+, strict typing with BasedPyright
- Ruff with 119-char line length
- GitLab library lacks type stubs - `allowedUntypedLibraries = ["gitlab"]`
- Test files have relaxed type checking rules

## Development Notes

- TDD approach: write tests first (red), then implement (green)
- Integration tests use real APIs - they create/delete real resources
- Test repos prefixed with `gl2ghmigr-` for easy cleanup (format: `gl2ghmigr-<test-type>-test-<hash>`)
