from __future__ import annotations

import logging
import os
from typing import Final

from github import Github, GithubException, UnknownObjectException
from github.AuthenticatedUser import AuthenticatedUser
from github.Organization import Organization
from github.Repository import Repository

from . import utils
from .exceptions import MigrationError

# Module-wide logger
logger: logging.Logger = logging.getLogger(__name__)

def get_client(token: str | None = None) -> Github:
    """Get a GitHub client using the token."""
    return Github(token)

def get_repo(client: Github, repo_path: str) -> Repository | None:
    try:
        return client.get_repo(repo_path)
    except UnknownObjectException as e:
        if e.status == 404 and e.message == "Not Found":
            return None
        msg = f"Error checking repository existence: {e}"
        raise MigrationError(msg) from e

def create_repo(client: Github, repo_path: str, description: str | None) -> Repository:
    """Create GitHub repository with GitLab project metadata."""
    # Parse GitHub repo path
    owner, repo_name = repo_path.split("/")

    # Check if repository already exists
    if client.get_repo(repo_path):
        # Repository already exists, raise an error
        msg = f"Repository {repo_path} already exists"
        raise MigrationError(msg)

    # Try to get as organization first, fall back to user
    try:
        org: Organization = client.get_organization(owner)
        # Create repository in organization
        return org.create_repo(
            name=repo_name,
            description=description or "",
            private=True,
            has_issues=True,
        )
    except UnknownObjectException as e:
        if e.status == 404 and e.message == "Not Found":
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
                description=description or "",
                private=True,
                has_issues=True,
            )
        raise
