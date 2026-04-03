"""
tests/test_project.py
Unit tests for Project model.
"""

import pytest
import json
from pathlib import Path
from app.models import Project, AnalysisCase, AnalysisCaseStatus


class TestProjectAddCase:
    """Test add_case() method."""

    def test_add_case_increments_list(self):
        """Adding a case increases the case list."""
        project = Project()
        assert len(project.cases) == 0

        case = project.add_case()

        assert len(project.cases) == 1
        assert isinstance(case, AnalysisCase)

    def test_add_case_auto_names(self):
        """add_case() auto-names cases."""
        project = Project()
        case1 = project.add_case()
        case2 = project.add_case()

        assert case1.name == "Case 1"
        assert case2.name == "Case 2"

    def test_add_case_with_existing_case(self):
        """Can add an existing AnalysisCase instance."""
        project = Project()
        case = AnalysisCase()
        case.name = "Custom Case"

        added = project.add_case(case)

        assert added is case
        assert added in project.cases

    def test_add_case_sets_modified_flag(self):
        """Adding a case sets the modified flag."""
        project = Project()
        project.modified = False

        project.add_case()

        assert project.modified is True


class TestProjectRemoveCase:
    """Test remove_case() method."""

    def test_remove_case_by_id(self):
        """Removing a case by ID removes it from the list."""
        project = Project()
        case = project.add_case()
        case_id = case.id

        success = project.remove_case(case_id)

        assert success is True
        assert len(project.cases) == 0

    def test_remove_case_returns_false_if_not_found(self):
        """Removing non-existent case returns False."""
        project = Project()
        success = project.remove_case("non-existent-id")

        assert success is False

    def test_remove_case_sets_modified_flag(self):
        """Removing a case sets the modified flag."""
        project = Project()
        case = project.add_case()
        project.modified = False

        project.remove_case(case.id)

        assert project.modified is True


class TestProjectGetCase:
    """Test get_case() method."""

    def test_get_case_by_id(self):
        """get_case() returns the correct case."""
        project = Project()
        case = project.add_case()

        retrieved = project.get_case(case.id)

        assert retrieved is case

    def test_get_case_returns_none_if_not_found(self):
        """get_case() returns None for non-existent ID."""
        project = Project()

        result = project.get_case("non-existent-id")

        assert result is None


class TestProjectDuplicateCase:
    """Test duplicate_case() method."""

    def test_duplicate_case(self):
        """duplicate_case() creates a copy with new ID."""
        project = Project()
        original = project.add_case()
        original.name = "Original"
        original.parameters = {"DAMPING": 0.05}

        clone = project.duplicate_case(original.id)

        assert clone is not None
        assert clone.id != original.id
        assert clone.name == "Original (コピー)"
        assert clone.parameters == {"DAMPING": 0.05}
        assert clone in project.cases

    def test_duplicate_case_returns_none_if_not_found(self):
        """duplicate_case() returns None if original not found."""
        project = Project()

        result = project.duplicate_case("non-existent-id")

        assert result is None

    def test_duplicate_case_sets_modified_flag(self):
        """duplicate_case() sets the modified flag."""
        project = Project()
        case = project.add_case()
        project.modified = False

        project.duplicate_case(case.id)

        assert project.modified is True


class TestProjectGetCompletedCases:
    """Test get_completed_cases() method."""

    def test_get_completed_cases_filter(self):
        """get_completed_cases() returns only completed cases."""
        project = Project()
        case1 = project.add_case()
        case2 = project.add_case()
        case3 = project.add_case()

        case1.status = AnalysisCaseStatus.COMPLETED
        case2.status = AnalysisCaseStatus.PENDING
        case3.status = AnalysisCaseStatus.COMPLETED

        completed = project.get_completed_cases()

        assert len(completed) == 2
        assert case1 in completed
        assert case3 in completed
        assert case2 not in completed

    def test_get_completed_cases_empty(self):
        """get_completed_cases() returns empty list if no completed."""
        project = Project()
        project.add_case()
        project.add_case()

        completed = project.get_completed_cases()

        assert completed == []


class TestProjectSaveAndLoad:
    """Test save() and load() methods."""

    def test_save_creates_file(self, tmp_path):
        """save() creates a .snapproj file."""
        project = Project(name="Test Project")
        project.snap_exe_path = "/path/to/snap.exe"
        project.add_case()

        output_path = tmp_path / "test.snapproj"
        project.save(str(output_path))

        assert output_path.exists()

    def test_save_contains_valid_json(self, tmp_path):
        """Saved file contains valid JSON."""
        project = Project(name="Test Project")
        output_path = tmp_path / "test.snapproj"
        project.save(str(output_path))

        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert data["name"] == "Test Project"
        assert "version" in data
        assert "cases" in data

    def test_save_and_load_roundtrip(self, tmp_path):
        """Save and load preserves all data."""
        # Create and save
        original = Project(name="Original Project")
        original.snap_exe_path = "/path/to/snap.exe"
        original.s8i_path = "/path/to/model.s8i"

        case1 = original.add_case()
        case1.name = "Case 1"
        case1.parameters = {"DAMPING": 0.05}

        case2 = original.add_case()
        case2.name = "Case 2"
        case2.status = AnalysisCaseStatus.COMPLETED
        case2.result_summary = {"max_drift": 0.005}

        output_path = tmp_path / "test.snapproj"
        original.save(str(output_path))

        # Load
        loaded = Project.load(str(output_path))

        # Verify
        assert loaded.name == "Original Project"
        assert loaded.snap_exe_path == "/path/to/snap.exe"
        assert loaded.s8i_path == "/path/to/model.s8i"
        assert len(loaded.cases) == 2
        assert loaded.cases[0].name == "Case 1"
        assert loaded.cases[0].parameters == {"DAMPING": 0.05}
        assert loaded.cases[1].status == AnalysisCaseStatus.COMPLETED
        assert loaded.cases[1].result_summary == {"max_drift": 0.005}

    def test_load_sets_modified_false(self, tmp_path):
        """load() sets modified flag to False."""
        project = Project(name="Test")
        output_path = tmp_path / "test.snapproj"
        project.save(str(output_path))

        loaded = Project.load(str(output_path))

        assert loaded.modified is False

    def test_load_sets_file_path(self, tmp_path):
        """load() sets file_path property."""
        project = Project(name="Test")
        output_path = tmp_path / "test.snapproj"
        project.save(str(output_path))

        loaded = Project.load(str(output_path))

        assert loaded.file_path == output_path


class TestProjectModifiedFlag:
    """Test modified flag behavior."""

    def test_modified_set_on_add_case(self):
        """modified flag set when adding case."""
        project = Project()
        project.modified = False

        project.add_case()

        assert project.modified is True

    def test_modified_cleared_on_save(self):
        """modified flag cleared after save."""
        project = Project()
        project.add_case()
        project.modified = True

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project.save(Path(tmp) / "test.snapproj")

        assert project.modified is False

    def test_title_includes_modified_indicator(self):
        """title property shows * for modified."""
        project = Project(name="Test")
        project.file_path = Path("test.snapproj")
        project.modified = False

        title1 = project.title
        assert "*" not in title1

        project.modified = True
        title2 = project.title
        assert "*" in title2
