"""
tests/test_result.py
Unit tests for Result class (SNAP result parser).
"""

import pytest
import sys
from pathlib import Path

# Add controller to path
sys.path.insert(0, str(Path(__file__).parent.parent / "controller"))

from result import Result


class TestResultFromMock:
    """Test Result with mock data directory."""

    def test_from_mock_creates_valid_data(self, tmp_result_dir):
        """Result reads mock directory and populates all 7 metrics."""
        res = Result(tmp_result_dir)

        # Check that Result created all expected attributes
        assert hasattr(res, "max_disp")
        assert hasattr(res, "max_vel")
        assert hasattr(res, "max_acc")
        assert hasattr(res, "max_story_disp")
        assert hasattr(res, "max_story_drift")
        assert hasattr(res, "shear_coeff")
        assert hasattr(res, "max_otm")


class TestResultGetAll:
    """Test get_all() method."""

    def test_get_all_returns_all_keys(self, tmp_result_dir):
        """get_all() returns dict with all 7 metric keys."""
        res = Result(tmp_result_dir)
        all_data = res.get_all()

        assert isinstance(all_data, dict)
        expected_keys = [
            "max_disp",
            "max_vel",
            "max_acc",
            "max_story_disp",
            "max_story_drift",
            "shear_coeff",
            "max_otm",
        ]
        for key in expected_keys:
            assert key in all_data


class TestResultGetFloorCount:
    """Test get_floor_count() method."""

    def test_get_floor_count(self, tmp_result_dir):
        """get_floor_count() returns number of floors."""
        res = Result(tmp_result_dir)
        count = res.get_floor_count()

        assert isinstance(count, int)
        assert count > 0

    def test_get_floor_count_empty_dir(self, tmp_path):
        """get_floor_count() returns 0 for empty directory."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        res = Result(str(empty_dir))
        count = res.get_floor_count()

        assert count == 0


class TestResultToDataframe:
    """Test to_dataframe() method if pandas is available."""

    def test_to_dataframe_if_pandas_available(self, tmp_result_dir):
        """to_dataframe() returns DataFrame if pandas is available."""
        res = Result(tmp_result_dir)

        try:
            import pandas  # noqa: F401
            df = res.to_dataframe()
            assert df is not None
            # Check basic structure
            assert "Floor" in df.columns
        except ImportError:
            # Skip if pandas not available
            pytest.skip("pandas not installed")

    def test_to_dataframe_raises_if_pandas_missing(self, tmp_result_dir):
        """to_dataframe() raises ImportError if pandas missing."""
        res = Result(tmp_result_dir)

        # Temporarily hide pandas
        import sys
        pandas_module = sys.modules.get("pandas")
        if pandas_module:
            # pandas is installed, so this test can't verify the error
            pytest.skip("pandas is installed, cannot test ImportError")

        with pytest.raises(ImportError):
            res.to_dataframe()


class TestResultFromMockGenerator:
    """Test Result.from_mock() class method."""

    def test_from_mock_returns_result(self):
        """from_mock() returns a Result with data."""
        res = Result.from_mock(floors=5)
        assert len(res.max_disp) == 5
        assert len(res.max_vel) == 5
        assert len(res.max_acc) == 5
        assert len(res.max_story_disp) == 5
        assert len(res.max_story_drift) == 5
        assert len(res.shear_coeff) == 5
        assert len(res.max_otm) == 5

    def test_from_mock_floor_keys(self):
        """from_mock() creates floor keys 1..N."""
        res = Result.from_mock(floors=3)
        assert set(res.max_disp.keys()) == {1, 2, 3}

    def test_from_mock_get_all(self):
        """from_mock() get_all() works."""
        res = Result.from_mock(floors=4)
        all_data = res.get_all()
        # 必須 7 項目が含まれること（input_pga / base_otm はスカラーのため別扱い）
        required_keys = {
            "max_disp", "max_vel", "max_acc",
            "max_story_disp", "max_story_drift",
            "shear_coeff", "max_otm",
        }
        assert required_keys.issubset(set(all_data.keys()))
        # 各フロアデータの件数確認
        for key in required_keys:
            assert len(all_data[key]) == 4, f"{key} should have 4 entries"

    def test_from_mock_get_floor_count(self):
        """from_mock() floor count is correct."""
        res = Result.from_mock(floors=7)
        assert res.get_floor_count() == 7

    def test_from_mock_values_are_positive(self):
        """from_mock() produces positive values."""
        res = Result.from_mock(floors=5)
        for attr in ("max_disp", "max_vel", "max_acc", "max_story_disp",
                     "shear_coeff", "max_otm"):
            data = getattr(res, attr)
            for v in data.values():
                assert v > 0, f"{attr} should have positive values"


class TestResultParseEmptyDir:
    """Test behavior with empty result directory."""

    def test_parse_empty_dir_no_crash(self, tmp_path):
        """Result handles empty directory without crashing."""
        empty_dir = tmp_path / "empty_results"
        empty_dir.mkdir()

        # Should not raise
        res = Result(str(empty_dir))

        assert res.result_dir == empty_dir
        assert len(res.max_disp) == 0
        assert len(res.max_acc) == 0

    def test_parse_nonexistent_dir(self, tmp_path):
        """Result handles non-existent directory gracefully."""
        nonexistent = tmp_path / "does_not_exist"

        # Should not raise
        res = Result(str(nonexistent))

        assert res.result_dir == nonexistent
        assert len(res.max_disp) == 0
