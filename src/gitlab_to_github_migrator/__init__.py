# pyright: reportImportCycles=false
"""
GitLab to GitHub Migration Tool

Migrates GitLab projects to GitHub with full metadata preservation including
exact issue/milestone numbers, comments, attachments, and relationships.
"""

from __future__ import annotations

from .cli import main
from .exceptions import MigrationError, NumberVerificationError
from .label_translator import LabelTranslator
from .migrator import GitlabToGithubMigrator
from .utils import setup_logging

__version__ = "0.1.0"

__all__ = [
    "GitlabToGithubMigrator",
    "LabelTranslator",
    "MigrationError",
    "NumberVerificationError",
    "main",
    "setup_logging",
]
