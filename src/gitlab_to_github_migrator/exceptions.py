"""
Custom exception classes for the GitLab to GitHub migration tool.
"""

from __future__ import annotations


class MigrationError(Exception):
    """Base exception for migration errors."""


class NumberVerificationError(MigrationError):
    """Raised when milestone/issue number verification fails."""
