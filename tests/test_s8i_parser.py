"""
tests/test_s8i_parser.py
Unit tests for s8i_parser module.
"""

import pytest
from pathlib import Path
from app.models.s8i_parser import (
    parse_s8i,
    S8iModel,
    DamperDefinition,
    DamperElement,
    DycCase,
)


class TestS8iParserRealFile:
    """Test parsing real test file if available."""

    def test_parse_s8i_real_file(self):
        """Parse real test_impulse.s8i file if available."""
        test_file = Path(__file__).parent.parent / "test" / "test_impulse.s8i"
        if not test_file.exists():
            pytest.skip("test_impulse.s8i not available")

        model = parse_s8i(str(test_file))

        assert isinstance(model, S8iModel)
        assert model.file_path == str(test_file)
        assert len(model.nodes) > 0 or len(model.floors) > 0


class TestS8iParserMinimalFile:
    """Test parsing minimal .s8i files."""

    def test_parse_minimal_s8i(self, tmp_s8i_file):
        """Parse a minimal fixture .s8i file."""
        model = parse_s8i(tmp_s8i_file)

        assert isinstance(model, S8iModel)
        assert model.file_path == tmp_s8i_file

    def test_parse_title(self, tmp_path):
        """Parser extracts title from TTL line."""
        s8i_file = tmp_path / "test.s8i"
        content = "TTL / 3,3,3,0,0,テストモデル\nVER / 8"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.title == "テストモデル"

    def test_parse_version(self, tmp_path):
        """Parser extracts version from VER line."""
        s8i_file = tmp_path / "test.s8i"
        content = "VER / 8"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.version == "8"


class TestS8iModelProperties:
    """Test S8iModel properties."""

    def test_s8i_model_properties(self, tmp_path):
        """S8iModel provides correct property values."""
        s8i_file = tmp_path / "test.s8i"
        content = """TTL / 3,3,3,0,0,テストモデル
VER / 8
ND / 1,0.0,0.0,0.0
ND / 2,0.0,0.0,5.0
ND / 3,0.0,0.0,10.0
FL / F1
FL / F2
FL / F3
RD / damper1,1,2,2,D1
"""
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.num_nodes == 3
        assert model.num_floors == 3
        assert model.num_dampers == 1

    def test_num_floors(self, tmp_path):
        """num_floors property counts FL lines."""
        s8i_file = tmp_path / "test.s8i"
        content = "FL / F1\nFL / F2"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.num_floors == 2

    def test_num_nodes(self, tmp_path):
        """num_nodes property counts ND lines."""
        s8i_file = tmp_path / "test.s8i"
        content = "ND / 1,0,0,0\nND / 2,0,0,1\nND / 3,0,0,2"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.num_nodes == 3


class TestS8iGetNode:
    """Test get_node() method."""

    def test_get_node(self, tmp_path):
        """get_node() returns Node by ID."""
        s8i_file = tmp_path / "test.s8i"
        content = "ND / 1,0.0,0.0,0.0\nND / 2,0.0,0.0,5.0"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        node = model.get_node(1)

        assert node is not None
        assert node.id == 1
        assert node.z == 0.0

    def test_get_node_returns_none_if_not_found(self, tmp_path):
        """get_node() returns None for non-existent node."""
        s8i_file = tmp_path / "test.s8i"
        content = "ND / 1,0,0,0"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        node = model.get_node(999)

        assert node is None


class TestS8iGetDamperDef:
    """Test get_damper_def() method."""

    def test_get_damper_def_by_name(self, tmp_path):
        """get_damper_def() finds damper by name."""
        s8i_file = tmp_path / "test.s8i"
        content = "DVOD / C1,500,0.4,100"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        damper = model.get_damper_def("C1")

        assert damper is not None
        assert damper.name == "C1"
        assert damper.keyword == "DVOD"

    def test_get_damper_def_returns_none_if_not_found(self, tmp_path):
        """get_damper_def() returns None if not found."""
        s8i_file = tmp_path / "test.s8i"
        content = "DVOD / C1,500,0.4,100"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        damper = model.get_damper_def("NONEXISTENT")

        assert damper is None


class TestS8iUpdateDamperElement:
    """Test update_damper_element() method."""

    def test_update_damper_element(self, tmp_path):
        """update_damper_element() modifies damper placement."""
        s8i_file = tmp_path / "test.s8i"
        content = "RD / damper1,1,2,2,D1"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        success = model.update_damper_element(0, node_i=5)

        assert success is True
        assert model.damper_elements[0].node_i == 5

    def test_update_damper_element_quantity(self, tmp_path):
        """update_damper_element() updates quantity."""
        s8i_file = tmp_path / "test.s8i"
        content = "RD / damper1,1,2,2,D1"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        success = model.update_damper_element(0, quantity=4)

        assert success is True
        assert model.damper_elements[0].quantity == 4

    def test_update_damper_element_invalid_index(self, tmp_path):
        """update_damper_element() returns False for invalid index."""
        s8i_file = tmp_path / "test.s8i"
        content = "RD / damper1,1,2,2,D1"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        success = model.update_damper_element(999, node_i=5)

        assert success is False


class TestS8iDamperDefinition:
    """Test DamperDefinition properties."""

    def test_damper_def_display_label(self):
        """DamperDefinition.display_label provides formatted string."""
        damper = DamperDefinition(
            keyword="DVOD",
            name="C1",
            values=["C1", "500", "0.4", "100"],
        )

        label = damper.display_label

        assert "粘性" in label or "オイル" in label
        assert "C1" in label


class TestS8iDamperElement:
    """Test DamperElement properties."""

    def test_damper_element_display_label(self):
        """DamperElement.display_label provides formatted string."""
        elem = DamperElement(
            name="damper1",
            node_i=1,
            node_j=2,
            quantity=2,
            damper_def_name="D1",
            values=["damper1", "1", "2", "2", "D1"],
        )

        label = elem.display_label

        assert "damper1" in label
        assert "1" in label
        assert "2" in label


class TestS8iDycCase:
    """Test DycCase parsing and properties."""

    def test_parse_dyc_cases(self, tmp_path):
        """Parser extracts DYC cases with correct fields."""
        s8i_file = tmp_path / "test.s8i"
        content = "DYC / BCJL2-MIX,1,3,100\nDYC / ELC,0,1,50"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert len(model.dyc_cases) == 2
        assert model.dyc_cases[0].name == "BCJL2-MIX"
        assert model.dyc_cases[0].run_flag == 1
        assert model.dyc_cases[0].case_no == 1
        assert model.dyc_cases[0].is_run is True
        assert model.dyc_cases[1].name == "ELC"
        assert model.dyc_cases[1].run_flag == 0
        assert model.dyc_cases[1].is_run is False

    def test_dyc_run_flag_2_is_run(self, tmp_path):
        """run_flag=2 (解析済み) も is_run=True として扱われる。"""
        s8i_file = tmp_path / "test.s8i"
        content = "DYC / TEST,2,1,100"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))

        assert model.dyc_cases[0].run_flag == 2
        assert model.dyc_cases[0].is_run is True

    def test_dyc_folder_name(self):
        """DycCase.folder_name returns D{N} format."""
        dyc = DycCase(case_no=4, name="TEST", run_flag=1, num_waves=1, values=["TEST", "1", "1"])
        assert dyc.folder_name == "D4"


class TestS8iWriteDyc:
    """Test DYC write-back in S8iModel.write()."""

    def test_write_preserves_dyc_changes(self, tmp_path):
        """write() reflects DYC run_flag changes in output file."""
        s8i_file = tmp_path / "input.s8i"
        content = "TTL / 1,1,1,0,0,Test\nDYC / CASE1,2,3,100\nDYC / CASE2,0,1,50"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        # run_flag=2 を 1 にリセット
        model.dyc_cases[0].run_flag = 1
        model.dyc_cases[0].values[1] = "1"

        output_file = tmp_path / "output.s8i"
        model.write(str(output_file))

        # 書き出し後のファイルを再パースして確認
        model2 = parse_s8i(str(output_file))
        assert model2.dyc_cases[0].run_flag == 1
        assert model2.dyc_cases[1].run_flag == 0

    def test_write_dyc_no_change_preserves_original(self, tmp_path):
        """write() without DYC changes preserves original values."""
        s8i_file = tmp_path / "input.s8i"
        content = "DYC / CASE1,1,3,100"
        s8i_file.write_text(content, encoding="shift_jis")

        model = parse_s8i(str(s8i_file))
        output_file = tmp_path / "output.s8i"
        model.write(str(output_file))

        model2 = parse_s8i(str(output_file))
        assert model2.dyc_cases[0].run_flag == 1
        assert model2.dyc_cases[0].name == "CASE1"
