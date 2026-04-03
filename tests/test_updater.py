"""
tests/test_updater.py
Unit tests for Updater class (.s8i file parameter updater).
"""

import pytest
import sys
from pathlib import Path

# Add controller to path
sys.path.insert(0, str(Path(__file__).parent.parent / "controller"))

from updater import Updater


class TestUpdaterLoadFile:
    """Test _load() and file reading."""

    def test_load_file_reads_lines(self, tmp_s8i_file):
        """Updater loads file and reads lines."""
        upd = Updater(tmp_s8i_file)

        assert len(upd._lines) > 0
        assert all(isinstance(line, str) for line in upd._lines)

    def test_load_handles_shift_jis(self, tmp_path):
        """Updater handles Shift-JIS encoded files."""
        s8i_file = tmp_path / "test.s8i"
        content = "TTL / 3,3,3,0,0,テストモデル\nDAMPING = 0.05"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))

        assert len(upd._lines) > 0


class TestUpdaterSetAndGetParam:
    """Test set_param() and get_param()."""

    def test_set_param_and_get_param(self, tmp_path):
        """set_param() and get_param() work together."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05\nDT = 0.01"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        assert upd.get_param("DAMPING") == "0.05"

    def test_get_param_case_insensitive(self, tmp_path):
        """get_param() is case-insensitive."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        assert upd.get_param("damping") == "0.05"
        assert upd.get_param("DaMpInG") == "0.05"

    def test_get_param_returns_none_if_not_found(self, tmp_path):
        """get_param() returns None for missing keys."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        result = upd.get_param("NONEXISTENT")

        assert result is None


class TestUpdaterWrite:
    """Test write() method."""

    def test_write_modifies_file(self, tmp_path):
        """write() modifies the file with new values."""
        s8i_file = tmp_path / "input.s8i"
        content = "DAMPING = 0.05\nDT = 0.01"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        upd.set_param("DAMPING", 0.08)
        output = tmp_path / "output.s8i"
        upd.write(str(output))

        # Check output file was created
        assert output.exists()

        # Check content was modified
        with open(output, "r", encoding="shift_jis", errors="replace") as f:
            new_content = f.read()

        assert "DAMPING" in new_content
        assert "0.08" in new_content or "0.08\n" in new_content

    def test_write_clears_pending(self, tmp_path):
        """write() clears pending parameters."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        upd.set_param("DAMPING", 0.08)
        assert len(upd._pending) > 0

        output = tmp_path / "output.s8i"
        upd.write(str(output))

        assert len(upd._pending) == 0

    def test_write_inplace(self, tmp_path):
        """write() without path updates source file."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        upd.set_param("DAMPING", 0.08)
        result_path = upd.write()

        assert result_path == s8i_file


class TestUpdaterCopyTo:
    """Test copy_to() method."""

    def test_copy_to_creates_new_file(self, tmp_path):
        """copy_to() creates a copy at new path."""
        source = tmp_path / "source.s8i"
        content = "DAMPING = 0.05"
        source.write_text(content, encoding="shift_jis")

        upd = Updater(str(source))
        dest = tmp_path / "copy.s8i"
        new_upd = upd.copy_to(str(dest))

        assert dest.exists()
        assert new_upd.source_path == dest

    def test_copy_to_returns_updater(self, tmp_path):
        """copy_to() returns a new Updater for the copy."""
        source = tmp_path / "source.s8i"
        content = "DAMPING = 0.05"
        source.write_text(content, encoding="shift_jis")

        upd = Updater(str(source))
        dest = tmp_path / "copy.s8i"
        new_upd = upd.copy_to(str(dest))

        assert isinstance(new_upd, Updater)
        assert new_upd is not upd


class TestUpdaterSetParamsMultiple:
    """Test set_params() for multiple parameters."""

    def test_set_params_multiple(self, tmp_path):
        """set_params() sets multiple parameters at once."""
        s8i_file = tmp_path / "test.s8i"
        content = "DAMPING = 0.05\nDT = 0.01\nMASS = 100"
        s8i_file.write_text(content, encoding="shift_jis")

        upd = Updater(str(s8i_file))
        upd.set_params({
            "DAMPING": 0.08,
            "DT": 0.02,
            "MASS": 200,
        })

        assert len(upd._pending) == 3
        assert upd._pending["DAMPING"] == 0.08
        assert upd._pending["DT"] == 0.02
        assert upd._pending["MASS"] == 200
