from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Final

from github import Auth, Github, UnknownObjectException
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


def get_token(
    *,
    env_var: str = GITHUB_TOKEN_ENV_VAR,
    pass_path: str | None = None,
) -> str:
    """Get GitHub token from pass path, environment variable, or default pass location.

    Resolution order:
    1. If pass_path is provided, use it
    2. Try the environment variable
    3. Try the default pass path

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitHub token

    Raises:
        MigrationError: If no token is found (GitHub requires authentication)
    """
    # Try explicit pass path first
    if pass_path:
        return get_pass_value(pass_path)

    # Try environment variable
    token: str | None = os.environ.get(env_var)
    if token:
        return token

    # Try default pass path
    try:
        return get_pass_value(DEFAULT_GITHUB_TOKEN_PASS_PATH)
    except PassError:
        msg = (
            f"No GitHub token specified nor found. "
            f"Specify correct pass path or set {env_var} environment variable."
        )
        raise MigrationError(msg) from None


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

def create_repo(client: Github, repo_path: str, description: str | None) -> Repository:
    """Create GitHub repository with GitLab project metadata."""
    # Parse GitHub repo path
    owner, repo_name = repo_path.split("/")

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
