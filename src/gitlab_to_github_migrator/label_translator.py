"""
Label translation functionality for the GitLab to GitHub migration tool.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


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
