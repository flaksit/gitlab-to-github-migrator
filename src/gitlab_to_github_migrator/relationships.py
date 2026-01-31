"""Issue relationship data structures and detection logic."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gitlab import GraphQL

logger: logging.Logger = logging.getLogger(__name__)


@dataclass
class WorkItemChild:
    """Child work item from GraphQL Work Items API."""

    iid: int
    title: str
    state: str
    type: str
    web_url: str


@dataclass
class IssueLinkInfo:
    """Information about a linked issue."""

    type: str
    target_iid: int
    target_title: str
    target_project_path: str
    target_web_url: str
    is_same_project: bool
    source: str = "rest_api"


@dataclass
class IssueCrossLinks:
    """Cross-linked issues separated by relationship type."""

    cross_links_text: str
    parent_child_relations: list[IssueLinkInfo]
    blocking_relations: list[IssueLinkInfo]


def get_work_item_children(
    graphql_client: GraphQL,
    project_path: str,
    issue_iid: int,
) -> list[WorkItemChild]:
    """Get child work items for an issue using GraphQL Work Items API.

    Args:
        graphql_client: GitLab GraphQL client
        project_path: Full project path (e.g., "namespace/project")
        issue_iid: The internal ID of the issue

    Returns:
        List of child work items
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

    try:
        response = graphql_client.execute(query, variable_values=variables)

        namespace = response.get("namespace")
        if not namespace:
            logger.debug(f"Namespace {project_path} not found in GraphQL response")
            return []

        work_item = namespace.get("workItem")
        if not work_item:
            logger.debug(f"Work item {issue_iid} not found in project {project_path}")
            return []

        children: list[WorkItemChild] = []
        widgets = work_item.get("widgets", [])

        for widget in widgets:
            if widget.get("type") == "HIERARCHY":
                child_nodes = widget.get("children", {}).get("nodes", [])
                for child in child_nodes:
                    child_info = WorkItemChild(
                        iid=int(child.get("iid")),
                        title=child.get("title"),
                        state=child.get("state"),
                        type=child.get("workItemType", {}).get("name"),
                        web_url=child.get("webUrl"),
                    )
                    children.append(child_info)

        logger.debug(f"Found {len(children)} child work items for issue #{issue_iid}")
    except Exception as e:
        logger.debug(f"Could not get children for issue #{issue_iid}: {e}")
        return []
    else:
        return children


def get_issue_cross_links(
    gitlab_issue: Any,  # noqa: ANN401 - gitlab has no type stubs
    gitlab_project_path: str,
    graphql_client: GraphQL,
) -> IssueCrossLinks:
    """Get cross-linked issues separated by relationship type.

    Uses both GraphQL (parent-child) and REST API (blocking, relates_to).

    Args:
        gitlab_issue: GitLab issue object
        gitlab_project_path: Full project path
        graphql_client: GitLab GraphQL client

    Returns:
        IssueCrossLinks with categorized relationships
    """
    # Step 1: Get child tasks via GraphQL
    child_work_items = get_work_item_children(graphql_client, gitlab_project_path, gitlab_issue.iid)
    logger.debug(f"Found {len(child_work_items)} tasks via GraphQL for issue #{gitlab_issue.iid}")

    # Step 2: Get regular issue links from REST API
    regular_links: list[IssueLinkInfo] = []
    links = gitlab_issue.links.list(get_all=True)

    for link in links:
        link_type = getattr(link, "link_type", "relates_to")
        target_iid = link.iid
        target_title = getattr(link, "title", "Unknown Title")
        references = getattr(link, "references", {})
        target_project_path = references.get("full", "").rsplit("#", 1)[0] if references else None
        target_web_url = getattr(link, "web_url", "")

        target_project_path = target_project_path or gitlab_project_path

        link_info = IssueLinkInfo(
            type=link_type,
            target_iid=target_iid,
            target_title=target_title,
            target_project_path=target_project_path,
            target_web_url=target_web_url,
            is_same_project=target_project_path == gitlab_project_path,
        )
        regular_links.append(link_info)

    # Step 3: Categorize relationships
    parent_child_relations = [
        IssueLinkInfo(
            type="child_of",
            target_iid=child.iid,
            target_title=child.title,
            target_project_path=gitlab_project_path,
            target_web_url=child.web_url,
            is_same_project=True,
            source="graphql_work_items",
        )
        for child in child_work_items
    ]

    blocking_relations: list[IssueLinkInfo] = []
    relates_to_links: list[tuple[str, IssueLinkInfo]] = []

    for link_info in regular_links:
        if link_info.type in ("blocks", "is_blocked_by"):
            if link_info.is_same_project:
                blocking_relations.append(link_info)
            else:
                label = "Blocked by" if link_info.type == "is_blocked_by" else "Blocks"
                relates_to_links.append((label, link_info))
        elif link_info.type == "relates_to":
            relates_to_links.append(("Related to", link_info))
        else:
            relates_to_links.append((f"Linked ({link_info.type})", link_info))

    # Step 4: Format cross-links text
    cross_links_text = ""
    if relates_to_links:
        cross_links_text = "\n\n---\n\n**Cross-linked Issues:**\n\n"
        for relationship, info in relates_to_links:
            if info.is_same_project:
                cross_links_text += f"- **{relationship}**: #{info.target_iid} - {info.target_title}\n"
            else:
                cross_links_text += (
                    f"- **{relationship}**: [{info.target_project_path}#{info.target_iid}]"
                    f"({info.target_web_url}) - {info.target_title}\n"
                )

    logger.debug(
        f"Issue #{gitlab_issue.iid}: {len(parent_child_relations)} parent-child, "
        f"{len(blocking_relations)} blocking, {len(relates_to_links)} relates_to"
    )

    return IssueCrossLinks(
        cross_links_text=cross_links_text,
        parent_child_relations=parent_child_relations,
        blocking_relations=blocking_relations,
    )
