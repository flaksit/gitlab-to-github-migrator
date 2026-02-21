"""Git repository operations using git CLI."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .exceptions import MigrationError

logger: logging.Logger = logging.getLogger(__name__)


def _inject_token(url: str, token: str | None, prefix: str = "") -> str:
    """Inject authentication token into HTTPS URL.

    Args:
        url: The URL to modify
        token: Token to inject (if None, returns original URL)
        prefix: Prefix before token (e.g., "oauth2:" for GitLab)

    Returns:
        URL with token injected, or original if not HTTPS or no token
    """
    if not token or not url.startswith("https://"):
        return url
    return url.replace("https://", f"https://{prefix}{token}@")


def _sanitize_error(error: str, tokens: list[str | None]) -> str:
    """Remove tokens from error message to prevent leakage.

    Args:
        error: Error message that may contain tokens
        tokens: List of tokens to redact (None values are ignored)

    Returns:
        Error message with tokens replaced by ***TOKEN***
    """
    result = error
    for token in tokens:
        if token:
            result = result.replace(token, "***TOKEN***")
    return result


def migrate_git_content(
    source_http_url: str,
    target_clone_url: str,
    source_token: str | None,
    target_token: str,
) -> str:
    """Mirror git repository from source to target.

    Always creates a temporary mirror clone to ensure all branches and tags are included.
    Returns the path to the cloned repository for further operations.

    Args:
        source_http_url: Source repository HTTPS URL (e.g., GitLab)
        target_clone_url: Target repository HTTPS URL (e.g., GitHub)
        source_token: Authentication token for source (may be None for public repos)
        target_token: Authentication token for target

    Returns:
        Path to the temporary clone directory

    Raises:
        MigrationError: If cloning or pushing fails
    """
    tokens = [source_token, target_token]
    temp_clone_path: str | None = None

    try:
        temp_clone_path = tempfile.mkdtemp(prefix="gitlab_migration_")
        clone_path = temp_clone_path

        source_url = _inject_token(source_http_url, source_token, prefix="oauth2:")

        result = subprocess.run(  # noqa: S603
            ["git", "clone", "--mirror", source_url, temp_clone_path],
            check=False,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            msg = f"Failed to clone repository: {_sanitize_error(result.stderr, tokens)}"
            raise MigrationError(msg)

        # Add target remote with token
        target_url = _inject_token(target_clone_url, target_token, prefix="")

        try:
            subprocess.run(  # noqa: S603
                ["git", "remote", "add", "github", target_url],
                cwd=clone_path,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            msg = f"Failed to add remote: {_sanitize_error(str(e), tokens)}"
            raise MigrationError(msg) from e

        # Push all branches and tags
        subprocess.run(
            ["git", "push", "--mirror", "github"],
            cwd=clone_path,
            check=True,
        )

        # Clean up remote to remove token from git config
        try:
            subprocess.run(
                ["git", "remote", "remove", "github"],
                cwd=clone_path,
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            pass  # Ignore cleanup errors

        print("Repository content migrated successfully")  # noqa: T201

    except (subprocess.CalledProcessError, OSError) as e:
        # Clean up on error
        if temp_clone_path and Path(temp_clone_path).exists():
            shutil.rmtree(temp_clone_path)
        msg = f"Failed to migrate repository content: {_sanitize_error(str(e), tokens)}"
        raise MigrationError(msg) from e

    return temp_clone_path


def cleanup_git_clone(clone_path: str) -> None:
    """Clean up temporary git clone directory.

    Args:
        clone_path: Path to the git clone directory to remove
    """
    if clone_path and Path(clone_path).exists():
        try:
            shutil.rmtree(clone_path)
            logger.debug(f"Cleaned up git clone at {clone_path}")
        except OSError as e:
            logger.warning(f"Failed to clean up git clone at {clone_path}: {e}")


def count_branches(clone_path: str) -> int:
    """Count branches in a git repository using git CLI.

    Args:
        clone_path: Path to the git repository

    Returns:
        Number of branches in the repository
    """
    try:
        result = subprocess.run(
            ["git", "branch", "-r"],
            cwd=clone_path,
            check=True,
            capture_output=True,
            text=True,
        )
        # Filter out HEAD reference and count unique branches
        branches = [line.strip() for line in result.stdout.strip().split("\n") if line.strip() and "HEAD" not in line]
        return len(branches)
    except subprocess.CalledProcessError:
        logger.exception("Failed to count branches")
        return 0


def count_tags(clone_path: str) -> int:
    """Count tags in a git repository using git CLI.

    Args:
        clone_path: Path to the git repository

    Returns:
        Number of tags in the repository
    """
    try:
        result = subprocess.run(
            ["git", "tag"],
            cwd=clone_path,
            check=True,
            capture_output=True,
            text=True,
        )
        tags = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        return len(tags)
    except subprocess.CalledProcessError:
        logger.exception("Failed to count tags")
        return 0


def count_unique_commits(clone_path: str) -> int:
    """Count unique commits across all branches in a git repository using git CLI.

    Args:
        clone_path: Path to the git repository

    Returns:
        Number of unique commits across all branches
    """
    try:
        # Use git rev-list with --all to get all commits across all branches
        result = subprocess.run(
            ["git", "rev-list", "--all", "--count"],
            cwd=clone_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())
    except subprocess.CalledProcessError, ValueError:
        logger.exception("Failed to count commits")
        return 0


_GITLAB_HOSTS = ("gitlab.com", "www.gitlab.com")


def _matches_gitlab_project(url: str, gitlab_project_path: str) -> bool:
    """Check whether a git remote URL refers to the given GitLab project on gitlab.com.

    Handles both SSH (``git@gitlab.com:ns/repo.git``) and HTTPS
    (``https://gitlab.com/ns/repo.git``) URLs, with or without the ``.git`` suffix.

    Args:
        url: Remote URL to test.
        gitlab_project_path: GitLab project path, e.g. ``namespace/project``.

    Returns:
        True if the URL points to the given project on gitlab.com.
    """
    path = gitlab_project_path.rstrip("/")
    normalized = url.rstrip("/").removesuffix(".git")
    if "://" in normalized:
        # HTTPS: https://gitlab.com/namespace/project
        parts = normalized.split("/", 3)
        return len(parts) == 4 and parts[2] in _GITLAB_HOSTS and parts[3] == path
    if ":" in normalized:
        # SSH: git@gitlab.com:namespace/project
        prefix, _, remote_path = normalized.partition(":")
        host = prefix.split("@")[-1]
        return host in _GITLAB_HOSTS and remote_path == path
    return False


def _build_github_url(original_url: str, github_repo_path: str) -> str:
    """Build a GitHub remote URL that mirrors the protocol of *original_url*.

    If *original_url* is SSH-style (starts with ``git@`` or contains
    ``ssh://``), returns an SSH GitHub URL; otherwise returns an HTTPS URL.

    Args:
        original_url: The existing remote URL (used to detect protocol).
        github_repo_path: GitHub repository path, e.g. ``owner/repo``.

    Returns:
        GitHub remote URL in the same protocol as the original.
    """
    if original_url.startswith(("git@", "ssh://")):
        return f"git@github.com:{github_repo_path}.git"
    return f"https://github.com/{github_repo_path}.git"


def _get_backup_remote_name(remote_name: str) -> str:
    """Return the name to use for the backup GitLab remote.

    * ``"origin"`` → ``"gitlab"``
    * ``"<name>"`` → ``"<name>-gitlab"``

    Args:
        remote_name: Current name of the remote that points to GitLab.

    Returns:
        Name for the backup remote that will keep the GitLab URL.
    """
    if remote_name == "origin":
        return "gitlab"
    return f"{remote_name}-gitlab"


@dataclass
class UpdatedRemote:
    """A git remote that was updated from GitLab to GitHub."""

    remote_name: str
    old_url: str
    backup_name: str
    new_url: str


def update_remotes_after_migration(
    gitlab_project_path: str,
    github_repo_path: str,
    cwd: str | None = None,
) -> list[UpdatedRemote]:
    """Update git remotes in the current working directory after a successful migration.

    If the working directory (or *cwd*) is a git repository whose remotes
    include the migrated GitLab project, each such remote is updated to point
    to the new GitHub repository.  The old GitLab URL is kept as a backup
    remote so no history is lost.

    Because git worktrees share the same ``.git/config`` as their main
    worktree, updating remotes once from any worktree directory covers all
    linked worktrees automatically.

    Naming convention for the backup remote:

    * If the original remote was named ``origin``, the backup is named
      ``gitlab``.
    * Otherwise the backup is named ``<original-name>-gitlab``.

    Args:
        gitlab_project_path: GitLab project path (``namespace/project``).
        github_repo_path: GitHub repository path (``owner/repo``).
        cwd: Directory to operate in; defaults to the current working directory.

    Returns:
        List of updated remotes.  Empty list if nothing was changed.
    """
    work_dir = cwd or "."

    # Verify we are inside a git repository.
    check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=work_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        logger.debug("Not inside a git repository - skipping remote update")
        return []

    # List all remotes with their fetch URLs.
    result = subprocess.run(
        ["git", "remote", "-v"],
        cwd=work_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.debug("Failed to list git remotes - skipping remote update")
        return []

    # Parse only the fetch lines: "<name>\t<url> (fetch)"
    remotes: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "(fetch)" in line:
            parts = line.split("\t", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                url = parts[1].replace(" (fetch)", "").strip()
                remotes[name] = url

    updated: list[UpdatedRemote] = []
    for remote_name, remote_url in remotes.items():
        if not _matches_gitlab_project(remote_url, gitlab_project_path):
            continue

        github_url = _build_github_url(remote_url, github_repo_path)
        backup_name = _get_backup_remote_name(remote_name)

        # Add backup remote pointing to the old GitLab URL.
        try:
            subprocess.run(  # noqa: S603
                ["git", "remote", "add", backup_name, remote_url],
                cwd=work_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"Added backup remote '{backup_name}' → {remote_url}")
        except subprocess.CalledProcessError:
            logger.warning(f"Could not add backup remote '{backup_name}' (may already exist)")

        # Update the existing remote to point to GitHub.
        try:
            subprocess.run(  # noqa: S603
                ["git", "remote", "set-url", remote_name, github_url],
                cwd=work_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"Updated remote '{remote_name}' → {github_url}")
            updated.append(
                UpdatedRemote(remote_name=remote_name, old_url=remote_url, backup_name=backup_name, new_url=github_url)
            )
        except subprocess.CalledProcessError as e:
            logger.warning(f"Failed to update remote '{remote_name}': {e}")

    return updated
