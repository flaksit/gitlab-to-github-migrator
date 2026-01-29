from __future__ import annotations

import logging
import os
from typing import Final

from gitlab import Gitlab, GraphQL

from .utils import PassError, get_pass_value

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

# Default token configuration
GITLAB_TOKEN_ENV_VAR: Final[str] = "SOURCE_GITLAB_TOKEN"  # noqa: S105
DEFAULT_GITLAB_RO_TOKEN_PASS_PATH: Final[str] = "gitlab/api/ro_token"  # noqa: S105
DEFAULT_GITLAB_RW_TOKEN_PASS_PATH: Final[str] = "gitlab/api/rw_token"  # noqa: S105

def get_client(url: str = "https://gitlab.com", token: str | None = None) -> Gitlab:
    """Get a GitLab client using the token.
    
    Args:
        url: GitLab instance URL (defaults to gitlab.com)
        token: Private access token for authentication
        
    Returns:
        Gitlab client instance
    """
    return Gitlab(url=url, private_token=token)

def get_graphql_client(url: str = "https://gitlab.com", token: str | None = None) -> GraphQL:
    """Get a GitLab GraphQL client using the token.

    Args:
        url: GitLab instance URL (defaults to gitlab.com)
        token: Private access token for authentication

    Returns:
        GraphQL client instance for executing GraphQL queries
    """
    return GraphQL(url=url, token=token)


def get_readonly_token(
    *,
    env_var: str = GITLAB_TOKEN_ENV_VAR,
    pass_path: str | None = None,
) -> str | None:
    """Get GitLab read-only token from pass path, environment variable, or default pass location.

    Resolution order:
    1. If pass_path is provided, use it
    2. Try the environment variable
    3. Try the default pass path (gitlab/api/ro_token)

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitLab token if found, None otherwise (allows anonymous access)
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
        return get_pass_value(DEFAULT_GITLAB_RO_TOKEN_PASS_PATH)
    except PassError:
        logger.warning(
            f"No GitLab token specified nor found. If non-anonymous access is required, "
            f"specify correct pass path or set {env_var} environment variable."
        )
        return None


def get_readwrite_token(
    *,
    env_var: str = GITLAB_TOKEN_ENV_VAR,
    pass_path: str | None = None,
) -> str | None:
    """Get GitLab read-write token from pass path, environment variable, or default pass location.

    Resolution order:
    1. If pass_path is provided, use it
    2. Try the environment variable
    3. Try the default pass path (gitlab/api/rw_token)

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitLab token if found, None otherwise
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
        return get_pass_value(DEFAULT_GITLAB_RW_TOKEN_PASS_PATH)
    except PassError:
        logger.warning(
            f"No GitLab token specified nor found. "
            f"Specify correct pass path or set {env_var} environment variable."
        )
        return None
