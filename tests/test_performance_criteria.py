"""
tests/test_performance_criteria.py
Unit tests for PerformanceCriteria model.
"""

import pytest
from app.models.performance_criteria import PerformanceCriteria, CriterionItem


class TestPerformanceCriteriaDefaults:
    """Test default items creation."""

    def test_default_items_created(self):
        """PerformanceCriteria creates default items on init."""
        criteria = PerformanceCriteria()

        assert len(criteria.items) > 0
        # Should have at least the standard items
        keys = [item.key for item in criteria.items]
        assert "max_drift" in keys
        assert "max_acc" in keys
        assert "max_disp" in keys
        assert "max_vel" in keys
        assert "max_story_disp" in keys
        assert "shear_coeff" in keys
        assert "max_otm" in keys

    def test_default_name(self):
        """Default name is set."""
        criteria = PerformanceCriteria()
        assert criteria.name == "デフォルト基準"


class TestCriterionEvaluate:
    """Test evaluate() method."""

    def test_evaluate_pass(self):
        """evaluate() returns True when below limit."""
        criteria = PerformanceCriteria()
        # Enable drift criterion with 1/200 rad limit
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        result = criteria.evaluate({"max_drift": 0.005})

        assert result["max_drift"] is True

    def test_evaluate_fail(self):
        """evaluate() returns False when above limit."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        result = criteria.evaluate({"max_drift": 0.015})

        assert result["max_drift"] is False

    def test_evaluate_none_when_disabled(self):
        """evaluate() returns None for disabled items."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = False

        result = criteria.evaluate({"max_drift": 0.005})

        assert result["max_drift"] is None

    def test_evaluate_none_when_no_data(self):
        """evaluate() returns None when result has no data."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        result = criteria.evaluate({})

        assert result["max_drift"] is None


class TestPerformanceCriteriaIsAllPass:
    """Test is_all_pass() method."""

    def test_is_all_pass_true(self):
        """is_all_pass() returns True when all enabled items pass."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        acc_item = next(i for i in criteria.items if i.key == "max_acc")

        drift_item.enabled = True
        drift_item.limit_value = 0.01
        acc_item.enabled = True
        acc_item.limit_value = 5.0

        result = criteria.is_all_pass({
            "max_drift": 0.005,
            "max_acc": 4.0,
        })

        assert result is True

    def test_is_all_pass_false(self):
        """is_all_pass() returns False when any item fails."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        acc_item = next(i for i in criteria.items if i.key == "max_acc")

        drift_item.enabled = True
        drift_item.limit_value = 0.01
        acc_item.enabled = True
        acc_item.limit_value = 5.0

        result = criteria.is_all_pass({
            "max_drift": 0.015,  # Above limit
            "max_acc": 4.0,
        })

        assert result is False

    def test_is_all_pass_none_when_no_enabled(self):
        """is_all_pass() returns None when nothing enabled."""
        criteria = PerformanceCriteria()
        for item in criteria.items:
            item.enabled = False

        result = criteria.is_all_pass({"max_drift": 0.005})

        assert result is None


class TestPerformanceCriteriaGetSummaryText:
    """Test get_summary_text() method."""

    def test_get_summary_text(self):
        """get_summary_text() returns formatted text."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        text = criteria.get_summary_text({"max_drift": 0.005})

        assert "最大層間変形角" in text
        assert "0.005" in text or "0.00500" in text
        assert "0.01" in text or "0.01000" in text

    def test_get_summary_text_shows_pass_mark(self):
        """get_summary_text() shows check mark for passing."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        text = criteria.get_summary_text({"max_drift": 0.005})

        assert "✓" in text

    def test_get_summary_text_shows_fail_mark(self):
        """get_summary_text() shows X mark for failing."""
        criteria = PerformanceCriteria()
        drift_item = next(i for i in criteria.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.01

        text = criteria.get_summary_text({"max_drift": 0.015})

        assert "✗" in text

    def test_get_summary_text_no_criteria_set(self):
        """get_summary_text() shows message when no criteria enabled."""
        criteria = PerformanceCriteria()
        for item in criteria.items:
            item.enabled = False

        text = criteria.get_summary_text({})

        assert "設定されていません" in text


class TestPerformanceCriteriaSerialization:
    """Test to_dict and from_dict methods."""

    def test_to_dict(self):
        """to_dict() returns proper structure."""
        criteria = PerformanceCriteria(name="Test Criteria")
        d = criteria.to_dict()

        assert isinstance(d, dict)
        assert d["name"] == "Test Criteria"
        assert "items" in d
        assert isinstance(d["items"], list)
        assert len(d["items"]) > 0

    def test_from_dict(self):
        """from_dict() recreates object from dict."""
        data = {
            "name": "Restored Criteria",
            "items": [
                {
                    "key": "max_drift",
                    "label": "最大層間変形角",
                    "unit": "rad",
                    "enabled": True,
                    "limit_value": 0.01,
                    "decimals": 4,
                }
            ]
        }

        criteria = PerformanceCriteria.from_dict(data)

        assert criteria.name == "Restored Criteria"
        assert len(criteria.items) >= 1

    def test_roundtrip(self):
        """to_dict -> from_dict roundtrip preserves data."""
        original = PerformanceCriteria(name="Custom")
        drift_item = next(i for i in original.items if i.key == "max_drift")
        drift_item.enabled = True
        drift_item.limit_value = 0.0075

        d = original.to_dict()
        restored = PerformanceCriteria.from_dict(d)

        assert restored.name == "Custom"
        restored_drift = next(i for i in restored.items if i.key == "max_drift")
        assert restored_drift.enabled is True
        assert restored_drift.limit_value == 0.0075
