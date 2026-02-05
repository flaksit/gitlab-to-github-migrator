from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, Final, Literal, overload

import requests
from github import Auth, Github, GithubException, UnknownObjectException
from github.AuthenticatedUser import AuthenticatedUser

from .exceptions import MigrationError
from .utils import PassError, get_pass_value

if TYPE_CHECKING:
    from github.Organization import Organization
    from github.Repository import Repository

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

# Default token configuration
GITHUB_TOKEN_ENV_VAR: Final[str] = "TARGET_GITHUB_TOKEN"  # noqa: S105
DEFAULT_GITHUB_TOKEN_PASS_PATH: Final[str] = "github/api/token"  # noqa: S105


def _sanitize_description(description: str | None) -> str:
    """Remove control characters from description that GitHub doesn't allow."""
    if not description:
        return ""
    # GitHub repo descriptions don't support newlines or control characters
    # Replace line endings with spaces and remove control characters
    result = description.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # Remove remaining control characters (ASCII 0-31 except tab)
    # and other problematic Unicode control characters
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", "", result)


@overload
def get_token(*, env_var: str, pass_path: None | Literal[""] = None) -> str: ...


@overload
def get_token(*, env_var: None | Literal[""] = None, pass_path: str | None = None) -> str: ...


def get_token(
    *,
    env_var: str | None = None,
    pass_path: str | None = None,
) -> str:
    """Get GitHub token from pass or environment variable.

    Only one of env_var or pass_path is allowed to be set to a non-empty string.

    Resolution order:
    1. If pass_path is provided, use it; if env_var is provided, use that
    2. Try the default env var (TARGET_GITHUB_TOKEN)
    3. Try the default pass path (github/api/token)

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitHub token

    Raises:
        MigrationError: If no token is found (GitHub requires authentication)
        ValueError: If both env_var and pass_path are set to non-empty strings
    """
    # Validate constraint: only one of env_var or pass_path can be non-empty
    if pass_path and env_var:
        msg = "Only one of env_var or pass_path can be set to a non-empty string"
        raise ValueError(msg)

    # 1. If pass_path is provided, use it
    if pass_path:
        return get_pass_value(pass_path)

    # 1. If env_var is set (non-empty), check that environment variable
    if env_var:
        token: str | None = os.environ.get(env_var)
        if token:
            return token

    # 2. Try the default env var
    token = os.environ.get(GITHUB_TOKEN_ENV_VAR)
    if token:
        return token

    # 3. Try the default pass path
    try:
        return get_pass_value(DEFAULT_GITHUB_TOKEN_PASS_PATH)
    except PassError:
        pass

    msg = (
        f"No GitHub token found. "
        f"Set {GITHUB_TOKEN_ENV_VAR} environment variable or configure pass at {DEFAULT_GITHUB_TOKEN_PASS_PATH}."
    )
    raise MigrationError(msg)


def get_client(token: str | None = None) -> Github:
    """Get a GitHub client using the token."""
    if token:
        return Github(auth=Auth.Token(token))
    return Github()


def get_repo(client: Github, repo_path: str) -> Repository | None:
    try:
        return client.get_repo(repo_path)
    except UnknownObjectException as e:
        if e.status == 404:
            return None
        msg = f"Error checking repository existence: {e}"
        raise MigrationError(msg) from e


def delete_issue(github_token: str, issue_node_id: str) -> None:
    """Delete a GitHub issue using GraphQL API.

    Uses direct GraphQL API call since PyGithub doesn't support issue deletion.

    Args:
        github_token: GitHub authentication token
        issue_node_id: The global node ID of the issue (issue.node_id)

    Raises:
        GithubException: If the GraphQL mutation fails
        MigrationError: If the response is unexpected
    """
    # Make GraphQL request directly
    graphql_url = "https://api.github.com/graphql"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Content-Type": "application/json",
    }

    mutation = """
    mutation DeleteIssue($input: DeleteIssueInput!) {
      deleteIssue(input: $input) {
        clientMutationId
      }
    }
    """

    payload = {
        "query": mutation,
        "variables": {
            "input": {
                "issueId": issue_node_id,
            }
        },
    }

    response = requests.post(graphql_url, headers=headers, data=json.dumps(payload), timeout=30)

    if response.status_code != 200:
        msg = f"GraphQL request failed with status {response.status_code}: {response.text}"
        raise MigrationError(msg)

    result = response.json()

    # Check for GraphQL errors
    if "errors" in result:
        error_msg = json.dumps(result["errors"])
        msg = f"GraphQL errors: {error_msg}"
        raise MigrationError(msg)

    # Verify successful deletion
    if not (result.get("data") and "deleteIssue" in result["data"]):
        msg = f"Unexpected GraphQL response when deleting issue {issue_node_id}: {result}"
        raise MigrationError(msg)

    logger.debug(f"Deleted issue with node ID {issue_node_id}")


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


def create_repo(client: Github, repo_path: str, description: str | None) -> Repository:
    """Create GitHub repository with GitLab project metadata."""
    # Validate GitHub repo path format
    repo_path_stripped = repo_path.strip()
    if not repo_path_stripped or repo_path_stripped.count("/") != 1:
        msg = (
            f"Invalid GitHub repository path: '{repo_path}'. "
            "Expected format: 'owner/repository'. "
            "Example: 'myorg/myrepo' or 'myusername/myrepo'"
        )
        raise MigrationError(msg)

    # Parse GitHub repo path
    owner, repo_name = repo_path_stripped.split("/")

    # Validate both parts are non-empty
    if not owner.strip() or not repo_name.strip():
        msg = (
            f"Invalid GitHub repository path: '{repo_path}'. "
            "Both owner and repository name must be non-empty. "
            "Expected format: 'owner/repository'. "
            "Example: 'myorg/myrepo' or 'myusername/myrepo'"
        )
        raise MigrationError(msg)

    # Sanitize description to remove control characters GitHub doesn't allow
    safe_description = _sanitize_description(description)

    # Check if repository already exists
    if get_repo(client, repo_path):
        msg = f"Repository {repo_path} already exists"
        raise MigrationError(msg)

    # Try to get as organization first, fall back to user
    try:
        org: Organization = client.get_organization(owner)
        # Create repository in organization
        return org.create_repo(
            name=repo_name,
            description=safe_description,
            private=True,
            has_issues=True,
        )
    except UnknownObjectException as e:
        if e.status == 404:
            # Not an organization, validate it's the authenticated user
            authenticated_user = client.get_user()
            assert isinstance(authenticated_user, AuthenticatedUser)  # always true
            if owner != authenticated_user.login:
                msg = (
                    f"Cannot create repository for '{owner}'. "
                    "The specified owner is not an organization and does not match "
                    f"the authenticated user '{authenticated_user.login}'. "
                    "You can only create repositories for organizations you have access to "
                    "or for your own user account."
                )
                raise MigrationError(msg) from None

            # Create repository for authenticated user
            return authenticated_user.create_repo(
                name=repo_name,
                description=safe_description,
                private=True,
                has_issues=True,
            )
        raise


def count_unique_commits(repo: Repository) -> int:
    """Count unique commits across all branches in a GitHub repository.

    Args:
        repo: GitHub repository object

    Returns:
        Number of unique commits across all branches
    """
    branches = list(repo.get_branches())
    commit_shas = set()
    for branch in branches:
        branch_commits = list(repo.get_commits(sha=branch.name))
        for commit in branch_commits:
            commit_shas.add(commit.sha)
    return len(commit_shas)
