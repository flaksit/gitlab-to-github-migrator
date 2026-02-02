# CI Workflows Design

Issue: #77

## Overview

Three separate GitHub Actions workflows for CI:

| Workflow | File | Trigger |
|----------|------|---------|
| Lint | `.github/workflows/lint.yml` | All PRs, push to main, manual |
| Unit Tests | `.github/workflows/test-unit.yml` | All PRs, push to main, manual |
| Integration Tests | `.github/workflows/test-integration.yml` | Non-draft PRs, push to main, manual |

## Workflow Details

### Lint

Single job running all checks, collecting all errors before failing:

```bash
exit_code=0
uv run ruff check . || exit_code=1
uv run ruff format --check . || exit_code=1
uv run basedpyright . || exit_code=1
uv run codespell . || exit_code=1
exit $exit_code
```

### Unit Tests

```bash
uv run pytest -m "not integration" -v -s -ra
```

### Integration Tests

```bash
uv run pytest -m integration -v -s -ra
```

Only runs on non-draft PRs using:
- `types: [opened, synchronize, ready_for_review]`
- `if: github.event.pull_request.draft == false`

Requires secrets:
- `SOURCE_GITLAB_TOKEN`
- `TARGET_GITHUB_TOKEN`
- `SOURCE_GITLAB_TEST_PROJECT`
- `TARGET_GITHUB_TEST_OWNER`

## Common Configuration

All workflows use:
- `ubuntu-latest` runner
- `astral-sh/setup-uv` for Python/uv
- `workflow_dispatch` for manual triggering
- Concurrency groups with `cancel-in-progress: true`

## Branch Protection

Configure on `main`:
- Require status checks to pass before merging
- Require branches to be up to date before merging
- Required checks: `lint`, `test-unit`, `test-integration`

## Manual Setup Steps

1. Add secrets (Settings → Secrets and variables → Actions):
   - `SOURCE_GITLAB_TOKEN` — GitLab API token (read access)
   - `TARGET_GITHUB_TOKEN` — GitHub API token (repo creation)
   - `SOURCE_GITLAB_TEST_PROJECT` — e.g., `namespace/project`
   - `TARGET_GITHUB_TEST_OWNER` — e.g., `org-or-username`

2. Configure branch protection (Settings → Branches → Add rule for `main`)
