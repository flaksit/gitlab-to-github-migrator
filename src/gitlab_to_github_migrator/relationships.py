"""Issue relationship data structures and detection logic."""

from __future__ import annotations

from dataclasses import dataclass


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
