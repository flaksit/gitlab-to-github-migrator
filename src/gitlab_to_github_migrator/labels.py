"""
Label translation and migration for GitLab to GitHub.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, NamedTuple

from github import GithubException
from gitlab.exceptions import GitlabError

from .exceptions import MigrationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from github.Repository import Repository as GithubRepository
    from gitlab.v4.objects import Project as GitlabProject

logger: logging.Logger = logging.getLogger(__name__)


def _is_already_exists_error(exc: GithubException) -> bool:
    """Check if a GithubException is a 422 'already_exists' validation error."""
    if not isinstance(exc.data, dict):
        return False
    errors: object = exc.data.get("errors")  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(errors, list):
        return False
    return any(isinstance(e, dict) and e.get("code") == "already_exists" for e in errors)  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]


class LabelTranslator:
    """Handles label translation patterns."""

    def __init__(self, patterns: Sequence[str] | None) -> None:
        self.patterns: list[tuple[str, str]] = []

        for pattern in patterns or []:
            if ":" not in pattern:
                msg = f"Invalid pattern format: {pattern}"
                raise ValueError(msg)
            source, target = pattern.split(":", 1)
            self.patterns.append((source, target))

    def translate(self, label_name: str) -> str:
        """Translate a label name using configured patterns."""
        for source_pattern, target_pattern in self.patterns:
            if "*" in source_pattern:
                # Convert glob pattern to regex
                regex_pattern = source_pattern.replace("*", "(.*)")
                match = re.match(f"^{regex_pattern}$", label_name)
                if match:
                    return target_pattern.replace("*", match.group(1))
            elif source_pattern == label_name:
                return target_pattern
        return label_name


class LabelMigrationResult(NamedTuple):
    """Result of label migration."""

    label_mapping: dict[str, str]
    """Mapping from original GitLab label names to GitHub label names."""
    initial_github_labels: dict[str, str]
    """Existing GitHub labels before migration (lowercase name -> actual name)."""


def migrate_labels(
    gitlab_project: GitlabProject,
    github_repo: GithubRepository,
    label_translations: Sequence[str] | None = None,
) -> LabelMigrationResult:
    """Migrate and translate labels from GitLab to GitHub.

    Matching with existing GitHub labels is case-insensitive (GitHub treats
    "Bug" and "bug" as the same label). When a translated label matches an
    existing label, the existing label's name is used in the mapping.

    Args:
        gitlab_project: The GitLab project to migrate labels from
        github_repo: The GitHub repository to migrate labels to
        label_translations: Optional list of translation patterns ("source:target")

    Returns:
        LabelMigrationResult with label_mapping and initial_github_labels

    Raises:
        MigrationError: If label migration fails
    """
    translator = LabelTranslator(label_translations)
    label_mapping: dict[str, str] = {}

    print("Migrating labels...")

    try:
        # Get existing GitHub labels (case-insensitive lookup: lowercase -> actual name)
        initial_github_labels: dict[str, str] = {label.name.lower(): label.name for label in github_repo.get_labels()}

        # Get GitLab labels
        gitlab_labels: list[Any] = gitlab_project.labels.list(get_all=True)

        for gitlab_label in gitlab_labels:
            # Translate label name
            translated_name = translator.translate(gitlab_label.name)

            # Skip if label already exists (case-insensitive, as GitHub labels are)
            existing_label = initial_github_labels.get(translated_name.lower())
            if existing_label is not None:
                label_mapping[gitlab_label.name] = existing_label
                logger.info(f"Using existing label: {gitlab_label.name} -> {existing_label}")
                continue

            # Create new label
            try:
                github_label = github_repo.create_label(
                    name=translated_name,
                    color=gitlab_label.color.lstrip("#"),
                    description=gitlab_label.description or "",
                )
                label_mapping[gitlab_label.name] = github_label.name
                logger.info(f"Created label: {gitlab_label.name} -> {translated_name}")
            except GithubException as e:
                if e.status == 422 and _is_already_exists_error(e):
                    # Label appeared between get_labels() and create_label() (race condition
                    # with GitHub's default label provisioning)
                    existing = github_repo.get_label(translated_name)
                    label_mapping[gitlab_label.name] = existing.name
                    logger.debug(f"Label already existed: {gitlab_label.name} -> {existing.name}")
                else:
                    msg = f"Failed to create label {translated_name}"
                    raise MigrationError(msg) from e

        print(f"Migrated {len(label_mapping)} labels")

    except (GitlabError, GithubException) as e:
        msg = f"Failed to migrate labels: {e}"
        raise MigrationError(msg) from e

    return LabelMigrationResult(
        label_mapping=label_mapping,
        initial_github_labels=initial_github_labels,
    )
