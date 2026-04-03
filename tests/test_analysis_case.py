"""
tests/test_analysis_case.py
Unit tests for AnalysisCase model.
"""

import pytest
import uuid
from app.models import AnalysisCase, AnalysisCaseStatus


class TestAnalysisCaseDefaults:
    """Test default values when creating new AnalysisCase."""

    def test_default_values(self):
        """Test that new AnalysisCase has correct defaults."""
        case = AnalysisCase()
        assert case.id is not None
        assert isinstance(case.id, str)
        # Verify it's a valid UUID
        uuid.UUID(case.id)
        assert case.status == AnalysisCaseStatus.PENDING
        assert case.name == "新規ケース"
        assert case.model_path == ""
        assert case.snap_exe_path == ""
        assert case.output_dir == ""
        assert case.parameters == {}
        assert case.damper_params == {}
        assert case.return_code is None
        assert case.notes == ""
        assert case.result_summary == {}

    def test_unique_ids(self):
        """Test that each AnalysisCase gets a unique ID."""
        case1 = AnalysisCase()
        case2 = AnalysisCase()
        assert case1.id != case2.id


class TestAnalysisCaseSerialization:
    """Test to_dict and from_dict roundtrip."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        case = AnalysisCase()
        case.name = "Test Case"
        case.status = AnalysisCaseStatus.COMPLETED
        case.return_code = 0
        d = case.to_dict()

        assert isinstance(d, dict)
        assert d["name"] == "Test Case"
        assert d["status"] == "completed"  # Enum value, not object
        assert d["return_code"] == 0

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "id": "test-id-123",
            "name": "Restored Case",
            "model_path": "/path/to/model.s8i",
            "status": "completed",
            "return_code": 0,
            "parameters": {"DAMPING": 0.05},
        }
        case = AnalysisCase.from_dict(data)

        assert case.id == "test-id-123"
        assert case.name == "Restored Case"
        assert case.model_path == "/path/to/model.s8i"
        assert case.status == AnalysisCaseStatus.COMPLETED
        assert case.return_code == 0
        assert case.parameters == {"DAMPING": 0.05}

    def test_roundtrip(self):
        """Test to_dict -> from_dict roundtrip preserves all data."""
        original = AnalysisCase()
        original.name = "Roundtrip Test"
        original.model_path = "/test/model.s8i"
        original.snap_exe_path = "/test/snap.exe"
        original.output_dir = "/test/output"
        original.parameters = {"DT": 0.01, "DAMPING": 0.03}
        original.damper_params = {"Cd": 500.0, "alpha": 0.4}
        original.status = AnalysisCaseStatus.RUNNING
        original.return_code = None
        original.notes = "Test notes"
        original.result_summary = {"max_drift": 0.005}

        # Roundtrip
        d = original.to_dict()
        restored = AnalysisCase.from_dict(d)

        # Check all fields match
        assert restored.name == original.name
        assert restored.model_path == original.model_path
        assert restored.snap_exe_path == original.snap_exe_path
        assert restored.output_dir == original.output_dir
        assert restored.parameters == original.parameters
        assert restored.damper_params == original.damper_params
        assert restored.status == original.status
        assert restored.return_code == original.return_code
        assert restored.notes == original.notes
        assert restored.result_summary == original.result_summary


class TestAnalysisCaseIsRunnable:
    """Test is_runnable() method with various combinations."""

    def test_runnable_with_both_paths(self):
        """Case is runnable when both model_path and snap_exe_path are set."""
        case = AnalysisCase()
        case.model_path = "/path/to/model.s8i"
        case.snap_exe_path = "/path/to/snap.exe"
        assert case.is_runnable() is True

    def test_not_runnable_without_model(self):
        """Case is not runnable without model_path."""
        case = AnalysisCase()
        case.snap_exe_path = "/path/to/snap.exe"
        assert case.is_runnable() is False

    def test_not_runnable_without_exe(self):
        """Case is not runnable without snap_exe_path."""
        case = AnalysisCase()
        case.model_path = "/path/to/model.s8i"
        assert case.is_runnable() is False

    def test_runnable_with_project_exe(self):
        """Case uses project-level exe when not specified locally."""
        case = AnalysisCase()
        case.model_path = "/path/to/model.s8i"
        assert case.is_runnable(snap_exe_path="/path/to/snap.exe") is True

    def test_case_exe_overrides_project_exe(self):
        """Case-level exe_path takes precedence."""
        case = AnalysisCase()
        case.model_path = "/path/to/model.s8i"
        case.snap_exe_path = "/case/snap.exe"
        assert case.is_runnable(snap_exe_path="/project/snap.exe") is True


class TestAnalysisCaseReset:
    """Test reset() method."""

    def test_reset_clears_status(self):
        """reset() sets status back to PENDING."""
        case = AnalysisCase()
        case.status = AnalysisCaseStatus.COMPLETED
        case.return_code = 0
        case.result_summary = {"max_drift": 0.005}

        case.reset()

        assert case.status == AnalysisCaseStatus.PENDING
        assert case.return_code is None
        assert case.result_summary == {}

    def test_reset_preserves_other_fields(self):
        """reset() only clears status/results, not other fields."""
        case = AnalysisCase()
        case.name = "Test"
        case.model_path = "/path/model.s8i"
        case.parameters = {"DAMPING": 0.05}

        case.reset()

        assert case.name == "Test"
        assert case.model_path == "/path/model.s8i"
        assert case.parameters == {"DAMPING": 0.05}


class TestAnalysisCaseClone:
    """Test clone() method."""

    def test_clone_creates_new_id(self):
        """clone() generates a new unique ID."""
        original = AnalysisCase()
        original.id = "original-id"
        clone = original.clone()

        assert clone.id != original.id
        assert clone.id != "original-id"

    def test_clone_copies_parameters(self):
        """clone() copies all parameters and settings."""
        original = AnalysisCase()
        original.name = "Original"
        original.model_path = "/path/model.s8i"
        original.parameters = {"DAMPING": 0.05}
        original.damper_params = {"Cd": 500.0}

        clone = original.clone()

        assert clone.name == "Original (コピー)"
        assert clone.model_path == "/path/model.s8i"
        assert clone.parameters == {"DAMPING": 0.05}
        assert clone.damper_params == {"Cd": 500.0}

    def test_clone_resets_state(self):
        """clone() resets status and results."""
        original = AnalysisCase()
        original.status = AnalysisCaseStatus.COMPLETED
        original.return_code = 0
        original.result_summary = {"max_drift": 0.005}

        clone = original.clone()

        assert clone.status == AnalysisCaseStatus.PENDING
        assert clone.return_code is None
        assert clone.result_summary == {}


class TestAnalysisCaseGetStatusLabel:
    """Test get_status_label() method."""

    def test_status_labels(self):
        """Test correct Japanese labels are returned."""
        test_cases = [
            (AnalysisCaseStatus.PENDING, "未実行"),
            (AnalysisCaseStatus.RUNNING, "実行中"),
            (AnalysisCaseStatus.COMPLETED, "完了"),
            (AnalysisCaseStatus.ERROR, "エラー"),
        ]

        for status, expected_label in test_cases:
            case = AnalysisCase()
            case.status = status
            assert case.get_status_label() == expected_label
