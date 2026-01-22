from __future__ import annotations

import logging

from gitlab import Gitlab, GraphQL

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

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
