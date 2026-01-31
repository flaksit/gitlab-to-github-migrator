from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Final, Literal, cast, overload

import requests
from gitlab import Gitlab, GraphQL

from .utils import PassError, get_pass_value

if TYPE_CHECKING:
    from gitlab.v4.objects import Project

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


def download_attachment(
    gitlab_client: Gitlab,
    project: Project | int,
    secret: str,
    filename: str,
    *,
    timeout: int = 30,
) -> tuple[bytes, str]:
    """Download an attachment from GitLab using the REST API.

    Uses the GitLab REST API endpoint (GitLab 17.4+) to download uploads
    by secret and filename, avoiding Cloudflare blocks on web URLs.

    Args:
        gitlab_client: Authenticated GitLab client
        project: GitLab project object or project ID
        secret: The 32-character hex secret from the upload URL
        filename: The filename from the upload URL
        timeout: Request timeout in seconds

    Returns:
        Tuple of (content bytes, content-type header)

    Raises:
        requests.RequestException: If the download fails
    """
    project_id: int = project if isinstance(project, int) else cast(int, project.id)
    api_path = f"/projects/{project_id}/uploads/{secret}/{filename}"

    # http_get with raw=True returns requests.Response (type stubs are incorrect)
    response = cast(
        requests.Response,
        gitlab_client.http_get(api_path, raw=True, timeout=timeout),
    )
    response.raise_for_status()

    content = response.content
    content_type = response.headers.get("Content-Type", "unknown")
    logger.debug(f"Downloaded {filename}: {len(content)} bytes, Content-Type: {content_type}")

    return content, content_type


@overload
def get_readonly_token(*, env_var: str, pass_path: None | Literal[""] = None) -> str | None: ...


@overload
def get_readonly_token(*, env_var: None | Literal[""] = None, pass_path: str | None = None) -> str | None: ...


def get_readonly_token(
    *,
    env_var: str | None = None,
    pass_path: str | None = None,
) -> str | None:
    """Get GitLab read-only token from pass path or environment variable.

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
    """Get GitLab read-write token from pass path or environment variable.

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

    return None


def get_parent_child_relationships(
    gitlab_client: Gitlab,
    graphql_client: GraphQL,
    project_path: str,
) -> dict[int, list[int]]:
    """Get all parent-child relationships from a GitLab project.

    Args:
        gitlab_client: GitLab REST API client
        graphql_client: GitLab GraphQL API client
        project_path: Full project path (e.g., "namespace/project")

    Returns:
        Dictionary mapping parent issue IID to list of child issue IIDs

    Note:
        This function queries all issues in the project and checks for work item children
        using the GraphQL API. It may make many API calls for projects with many issues.
    """
    parent_to_children: dict[int, list[int]] = {}

    try:
        project = gitlab_client.projects.get(project_path)
        issues = project.issues.list(iterator=True, state="all")

        for issue in issues:
            # Query GraphQL API for work item children
            query = """
            query GetWorkItemWithChildren($fullPath: ID!, $iid: String!) {
                namespace(fullPath: $fullPath) {
                    workItem(iid: $iid) {
                        iid
                        widgets {
                            type
                            ... on WorkItemWidgetHierarchy {
                                children {
                                    nodes {
                                        iid
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """

            variables = {"fullPath": project_path, "iid": str(issue.iid)}

            try:
                response = graphql_client.execute(query, variable_values=variables)

                # Parse response - note that GitLab GraphQL client returns data directly,
                # not wrapped in a "data" key
                namespace = response.get("namespace")
                if not namespace:
                    continue

                work_item = namespace.get("workItem")
                if not work_item:
                    continue

                # Find hierarchy widget
                widgets = work_item.get("widgets", [])
                for widget in widgets:
                    if widget.get("type") == "HIERARCHY":
                        child_nodes = widget.get("children", {}).get("nodes", [])
                        if child_nodes:
                            child_iids = [int(child.get("iid")) for child in child_nodes if child.get("iid")]
                            if child_iids:
                                parent_to_children[issue.iid] = child_iids
                        break

            except Exception as e:
                # Log but continue - some issues might not be work items
                logger.debug(f"Could not get children for issue #{issue.iid}: {e}")
                continue

    except Exception:
        logger.exception("Failed to get parent-child relationships")

    return parent_to_children


