from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast, overload

import requests
from gitlab import Gitlab, GraphQL

from .utils import PassError, get_pass_value

if TYPE_CHECKING:
    from gitlab.v4.objects import Project, ProjectIssue

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


def get_work_item_children(
    graphql_client: GraphQL,
    project_path: str,
    issue_iid: int,
) -> list[int]:
    """Get child work items for an issue using GraphQL Work Items API.

    Args:
        graphql_client: GitLab GraphQL client
        project_path: Full project path (e.g., "namespace/project")
        issue_iid: The internal ID of the issue

    Returns:
        List of IIDs of child work items
    """
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
                                title
                                state
                                workItemType {
                                    name
                                }
                                webUrl
                            }
                        }
                    }
                }
            }
        }
    }
    """

    variables = {"fullPath": project_path, "iid": str(issue_iid)}

    response = graphql_client.execute(query, variable_values=variables)

    namespace = response.get("namespace")
    # if not namespace:
    #     logger.debug(f"Namespace {project_path} not found in GraphQL response")
    #     return []

    work_item = namespace.get("workItem")
    # if not work_item:
    #     logger.debug(f"Work item {issue_iid} not found in project {project_path}")
    #     return []

    children: list[int] = []
    widgets = work_item.get("widgets", [])

    for widget in widgets:
        if widget.get("type") == "HIERARCHY":
            child_nodes = widget.get("children", {}).get("nodes", [])
            children.extend(int(child.get("iid")) for child in child_nodes)

    logger.debug(f"Found {len(children)} child work items for issue #{issue_iid}")
    return children


@dataclass(frozen=True)
class IssueCrossLinks:
    """Cross-linked issues separated by relationship type."""

    cross_links_text: str
    blocked_issue_iids: list[int]
    """List of issue IIDs that are blocked by this issue."""


def get_normal_issue_cross_links(
    gitlab_issue: ProjectIssue,
    gitlab_project_path: str,
) -> IssueCrossLinks:
    """Get cross-linked issues separated by relationship type.

    Uses REST API.

    Args:
        gitlab_issue: GitLab issue object
        gitlab_project_path: Full project path

    Returns:
        IssueCrossLinks with categorized relationships
    """
    # Get regular issue links from REST API
    links = gitlab_issue.links.list(get_all=True)
    blocked_issue_iids: list[int] = []

    link_type_to_label = {
        "blocks": "Blocks",
        "is_blocked_by": "Blocked by",
        "relates_to": "Related to",
    }
    cross_links_text = ""

    for link in links:
        link_type = getattr(link, "link_type", "relates_to")

        references = getattr(link, "references", {})
        target_project_path = references.get("full", "").rsplit("#", 1)[0] if references else None
        target_project_path = target_project_path or gitlab_project_path
        is_same_project = target_project_path == gitlab_project_path

        if link_type in ("blocks", "is_blocked_by") and is_same_project:
            # GitLab "blocks" means: source blocks target -> target is blocked by source
            # GitLab "is_blocked_by" means: source is blocked by target
            # We receive each relation twice (once per direction), so skip the reverse direction
            if link_type == "blocks":
                blocked_issue_iids.append(link.iid)
        else:
            # Format cross-links text
            label = link_type_to_label.get(link_type, f"Linked ({link_type})")
            target_title = getattr(link, "title", "Unknown Title")
            target_web_url = getattr(link, "web_url", "")

            if is_same_project:
                cross_links_text += f"- **{label}**: #{link.iid} - {target_title}\n"
            else:
                cross_links_text += (
                    f"- **{label}**: [{target_project_path}#{link.iid}]({target_web_url}) - {target_title}\n"
                )

    if cross_links_text:
        cross_links_text = "\n\n---\n\n**Cross-linked Issues:**\n\n" + cross_links_text

    return IssueCrossLinks(cross_links_text, blocked_issue_iids)
