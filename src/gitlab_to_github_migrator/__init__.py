"""
GitLab to GitHub Migration Tool

Migrates GitLab projects to GitHub with full metadata preservation including
exact issue/milestone numbers, comments, attachments, and relationships.
"""

from __future__ import annotations

from .cli import main

# Import main classes for backward compatibility
from .exceptions import MigrationError, NumberVerificationError
from .migrator import GitLabToGitHubMigrator
from .label_translator import LabelTranslator
from .utils import setup_logging

# Package version
__version__ = "0.1.0"

# Public API - maintain backward compatibility
__all__ = [
    "GitLabToGitHubMigrator",
    "LabelTranslator",
    "MigrationError",
    "NumberVerificationError",
    "main",
    "setup_logging",
]
