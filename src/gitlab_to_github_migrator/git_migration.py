"""Git repository mirroring from source to target."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
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
) -> None:
    """Mirror git repository from source to target.

    Always creates a temporary mirror clone to ensure all branches and tags are included.

    Args:
        source_http_url: Source repository HTTPS URL (e.g., GitLab)
        target_clone_url: Target repository HTTPS URL (e.g., GitHub)
        source_token: Authentication token for source (may be None for public repos)
        target_token: Authentication token for target

    Raises:
        MigrationError: If cloning or pushing fails
    """
    temp_clone_path: str | None = None
    tokens = [source_token, target_token]

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

        logger.info("Repository content migrated successfully")

    except (subprocess.CalledProcessError, OSError) as e:
        msg = f"Failed to migrate repository content: {_sanitize_error(str(e), tokens)}"
        raise MigrationError(msg) from e
    finally:
        if temp_clone_path and Path(temp_clone_path).exists():
            shutil.rmtree(temp_clone_path)
