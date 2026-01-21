from __future__ import annotations

import logging

from gitlab import Gitlab

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

def get_client(token: str | None = None) -> Gitlab:
    """Get a GitLab client using the token."""
    return Gitlab(private_token=token)
