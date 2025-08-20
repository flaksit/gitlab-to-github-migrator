from __future__ import annotations

import logging
import os
from typing import Final

from gitlab import Gitlab

from . import utils

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

_TOKEN_ENV_VAR: Final[str] = "GITLAB_TOKEN"  # noqa: S105
_DEFAULT_TOKEN_PASS_PATH: Final[str] = "gitlab/cli/ro_token"  # noqa: S105


def get_token(pass_path: str | None = None) -> str | None:
    """Get GitLab token from pass path, env var GITLAB_TOKEN, or default pass location."""
    # Try pass path first
    if pass_path:
        return utils.get_pass_value(pass_path)

    # Try environment variable
    token: str | None = os.environ.get(_TOKEN_ENV_VAR)
    if token:
        return token

    # Try default pass path or default
    try:
        return utils.get_pass_value(_DEFAULT_TOKEN_PASS_PATH)
    except ValueError:
        logger.warning("No GitLab token specified nor found")
        return None


def get_client(token: str | None = None) -> Gitlab:
    """Get a GitLab client using the token."""
    return Gitlab(private_token=token)
