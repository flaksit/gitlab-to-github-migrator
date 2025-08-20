"""
Utility functions for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import logging
import re
import subprocess
from subprocess import CompletedProcess

from .exceptions import MigrationError


def setup_logging(*, verbose: bool = False) -> None:
    """Configure logging for the migration process."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("migration.log", mode="a")],
    )


def _validate_pass_path(pass_path: str) -> None:
    """Validate the pass path format."""
    # Validate pass_path format
    if not re.fullmatch(r"(?:[A-Za-z0-9_-]+)(?:/[A-Za-z0-9_-]+)*", pass_path):
        msg = f"Invalid pass path: {pass_path}"
        raise ValueError(msg)


def get_pass_value(pass_path: str) -> str:
    """Get value from pass utility at specified path."""
    _validate_pass_path(pass_path)

    try:
        result: CompletedProcess[str] = subprocess.run(  # noqa: S603
            ["pass", pass_path], capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            msg = f"Pass path '{pass_path}' not found or invalid."
        elif e.returncode == 2:
            msg = "Pass needs you to enter the passphrase for the GPG key. Do that first and retry."
        else:
            msg = f"Error retrieving pass value for '{pass_path}'"
        raise MigrationError(msg) from e
    else:
        return result.stdout.strip()
