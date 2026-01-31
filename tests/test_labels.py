import pytest

from gitlab_to_github_migrator.labels import LabelTranslator


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
