from __future__ import annotations

import logging
import os
from typing import Final, Literal, overload

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


@overload
def get_readonly_token(*, env_var: str, pass_path: None | Literal[""] = None) -> str | None: ...


@overload
def get_readonly_token(*, env_var: None | Literal[""] = None, pass_path: str | None = None) -> str | None: ...


def get_readonly_token(
    *,
    env_var: str | None = None,
    pass_path: str | None = None,
) -> str | None:
    """Get GitLab read-only token from pass path, environment variable, or default location.

    Only one of env_var or pass_path is allowed to be set to a non-empty string.

    Resolution order:
    1. If pass_path is provided, use it; if env_var is provided, use that
    2. Try the default env var (SOURCE_GITLAB_TOKEN)
    3. Try the default pass path (gitlab/api/ro_token)

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitLab token if found, None otherwise (allows anonymous access)

    Raises:
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
    token = os.environ.get(GITLAB_TOKEN_ENV_VAR)
    if token:
        return token

    # 3. Try the default pass path
    try:
        return get_pass_value(DEFAULT_GITLAB_RO_TOKEN_PASS_PATH)
    except PassError:
        pass

    logger.warning(
        f"No GitLab token found. If non-anonymous access is required, "
        f"set {GITLAB_TOKEN_ENV_VAR} environment variable or configure pass at {DEFAULT_GITLAB_RO_TOKEN_PASS_PATH}."
    )
    return None


@overload
def get_readwrite_token(*, env_var: str, pass_path: None | Literal[""] = None) -> str | None: ...


@overload
def get_readwrite_token(*, env_var: None | Literal[""] = None, pass_path: str | None = None) -> str | None: ...


def get_readwrite_token(
    *,
    env_var: str | None = None,
    pass_path: str | None = None,
) -> str | None:
    """Get GitLab read-write token from pass path, environment variable, or default location.

    Only one of env_var or pass_path is allowed to be set to a non-empty string.

    Resolution order:
    1. If pass_path is provided, use it; if env_var is provided, use that
    2. Try the default env var (SOURCE_GITLAB_TOKEN)
    3. Try the default pass path (gitlab/api/rw_token)

    Args:
        env_var: Environment variable name to check
        pass_path: Optional explicit pass path to use

    Returns:
        GitLab token if found, None otherwise

    Raises:
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
    token = os.environ.get(GITLAB_TOKEN_ENV_VAR)
    if token:
        return token

    # 3. Try the default pass path
    try:
        return get_pass_value(DEFAULT_GITLAB_RW_TOKEN_PASS_PATH)
    except PassError:
        pass

    logger.warning(
        f"No GitLab token found. "
        f"Set {GITLAB_TOKEN_ENV_VAR} environment variable or configure pass at {DEFAULT_GITLAB_RW_TOKEN_PASS_PATH}."
    )
    return None
