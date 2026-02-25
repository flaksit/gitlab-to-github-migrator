"""
Utility functions for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from subprocess import CompletedProcess


class PassError(Exception):
    """Base class for pass-related errors."""


class InvalidPassPathError(PassError):
    """Raised when the pass path format is invalid."""


class PassphraseRequiredError(PassError):
    """Raised when a GPG passphrase is required for the pass utility."""


def setup_logging(*, verbosity: int = 0) -> None:
    """Configure logging for the migration process.

    Args:
        verbosity: Console log level. 0 = WARNING+ only (default), 1 = INFO+, 2+ = DEBUG+.
                   The log file always receives DEBUG+ regardless of this setting.
    """
    if verbosity >= 2:
        console_level = logging.DEBUG
    elif verbosity == 1:
        console_level = logging.INFO
    else:
        console_level = logging.WARNING

    # Configure root logger to write to file with full formatting
    file_handler = logging.FileHandler("gitlab-to-github-migration.log", mode="a")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))

    # Configure console handler (stderr) based on verbosity
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=[console_handler, file_handler],
    )

    # Suppress verbose HTTP logging from httpx (used by GitLab library)
    # Even in warning mode, HTTP request logs are too noisy
    # Set to ERROR to completely suppress informational HTTP logs
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)


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
    except FileNotFoundError as e:
        msg = "The 'pass' command is not available. Install pass or use environment variables."
        raise PassError(msg) from e
    except subprocess.CalledProcessError as e:
        if e.returncode == 1 and "not in the password store" in e.stderr.lower():
            msg = f"Pass path '{pass_path}' not found or invalid."
            raise InvalidPassPathError(msg) from e
        if e.returncode == 2 and "gpg" in e.stderr.lower() and "public key decryption failed" in e.stderr.lower():
            # Failed, likely because user needs to enter the passphrase for the GPG key.
            # We ask user for passphrase here and pass it to pass. This will fail in non-interactive sessions (e.g. pytest).
            try:
                passphrase = input("Enter passphrase for GPG key used by pass: ")
            except EOFError as e:
                msg = "Passphrase input was interrupted. Please run the command in an interactive session."
                raise PassphraseRequiredError(msg) from e

            env = os.environ.copy() | {"PASSWORD_STORE_GPG_OPTS": "--pinentry-mode=loopback --passphrase-fd 0"}
            try:
                result = subprocess.run(  # noqa: S603
                    ["pass", pass_path], input=passphrase, capture_output=True, text=True, check=True, env=env
                )
            except subprocess.CalledProcessError as e:
                msg = (
                    f"Failed to get value from pass at '{pass_path}' with passphrase.\n"
                    f"Output: {e.stdout.strip()}\n"
                    f"Error: {e.stderr.strip()}\n"
                    f"Return code: {e.returncode}"
                )
                raise PassphraseRequiredError(msg) from e
        else:
            msg = (
                f"Failed to get value from pass at '{pass_path}'.\n"
                f"Output: {e.stdout.strip()}\n"
                f"Error: {e.stderr.strip()}\n"
                f"Return code: {e.returncode}"
            )
            raise PassError(msg) from e

    return result.stdout.strip()
