from unittest.mock import Mock

import pytest
from github import GithubException

from gitlab_to_github_migrator.labels import LabelTranslator, migrate_labels


@pytest.mark.unit
class TestMigrateLabels:
    """Test migrate_labels function."""

    def _make_gitlab_label(self, name: str, color: str = "#ff0000", description: str = "") -> Mock:
        label = Mock()
        label.name = name
        label.color = color
        label.description = description
        return label

    def test_already_exists_error_is_handled(self) -> None:
        """When create_label raises 422 already_exists, use the existing label instead of crashing."""
        gitlab_project = Mock()
        github_repo = Mock()

        # GitLab has a "bug" label
        gitlab_project.labels.list.return_value = [self._make_gitlab_label("bug")]

        # GitHub repo returns no labels initially (race condition: default labels not yet provisioned)
        github_repo.get_labels.return_value = []

        # create_label raises 422 "already_exists" (default label appeared between get and create)
        github_repo.create_label.side_effect = GithubException(
            422,
            {"message": "Validation Failed", "errors": [{"resource": "Label", "code": "already_exists"}]},
            headers={},
        )

        # After the error, get_label fetches the existing label
        existing_label = Mock()
        existing_label.name = "bug"
        github_repo.get_label.return_value = existing_label

        result = migrate_labels(gitlab_project, github_repo)

        assert result.label_mapping["bug"] == "bug"


@pytest.mark.unit
class TestLabelTranslator:
    """Test label translation functionality."""

    def test_simple_translation(self) -> None:
        translator = LabelTranslator(["p_high:priority: high", "bug:defect"])
        assert translator.translate("p_high") == "priority: high"
        assert translator.translate("bug") == "defect"
        assert translator.translate("unknown") == "unknown"

    def test_wildcard_translation(self) -> None:
        translator = LabelTranslator(["p_*:priority: *", "status_*:status: *"])
        assert translator.translate("p_high") == "priority: high"
        assert translator.translate("p_low") == "priority: low"
        assert translator.translate("status_open") == "status: open"
        assert translator.translate("unmatched") == "unmatched"

    def test_invalid_pattern(self) -> None:
        with pytest.raises(ValueError, match="Invalid pattern format"):
            LabelTranslator(["invalid_pattern"])

    def test_multiple_patterns(self) -> None:
        translator = LabelTranslator(["p_*:priority: *", "comp_*:component: *", "bug:defect"])
        assert translator.translate("p_critical") == "priority: critical"
        assert translator.translate("comp_ui") == "component: ui"
        assert translator.translate("bug") == "defect"
