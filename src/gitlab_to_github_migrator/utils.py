"""
Utility functions for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import logging
import re
import subprocess
from subprocess import CompletedProcess


class PassError(Exception):
    """Base class for pass-related errors."""


class InvalidPassPathError(PassError):
    """Raised when the pass path format is invalid."""


class PassphraseRequiredError(PassError):
    """Raised when a GPG passphrase is required for the pass utility."""


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
            raise InvalidPassPathError(msg) from e
        if e.returncode == 2:
            # TODO If we are in an interactive session, ask user for passphrase and pass it to the command. See https://unix.stackexchange.com/questions/688163/unix-pass-passing-passphrase-with-the-usage-of-password-store-gpg-opts#702718 and https://www.gnupg.org/documentation/manuals/gnupg24/gpg.1.html
            msg = "Pass needs you to enter the passphrase for the GPG key. Do that first and retry."
            raise PassphraseRequiredError(msg) from e
        msg = (
            f"Failed to get value from pass at '{pass_path}'.\n"
            f"Output: {e.stdout.strip()}\n"
            f"Error: {e.stderr.strip()}\n"
            f"Return code: {e.returncode}"
        )
        raise PassError(msg) from e
    else:
        return result.stdout.strip()
