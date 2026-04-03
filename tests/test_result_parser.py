"""
tests/test_result_parser.py
controller/result.py (Result パーサー) のユニットテスト。
"""

import pytest
from pathlib import Path

from controller.result import Result


class TestResultParser:
    """Result パーサーのテスト。"""

    def test_parse_result_dir(self, tmp_result_dir):
        """結果ディレクトリからデータを正しく読み込む。"""
        res = Result(tmp_result_dir)
        assert res.max_disp != {}
        assert res.max_vel != {}
        assert res.max_acc != {}

    def test_parsed_values(self, tmp_result_dir):
        """パースされた値が正しいか確認。"""
        res = Result(tmp_result_dir)
        # 最大応答相対変位の値を確認
        if res.max_disp:
            assert 1 in res.max_disp
            assert isinstance(res.max_disp[1], float)

    def test_get_all(self, tmp_result_dir):
        """get_all() が全応答項目を含む辞書を返す。"""
        res = Result(tmp_result_dir)
        all_data = res.get_all()
        # 必須 7 項目（input_pga / base_otm はスカラーのため含まれない場合もある）
        required_keys = {
            "max_disp", "max_vel", "max_acc",
            "max_story_disp", "max_story_drift",
            "shear_coeff", "max_otm",
        }
        assert required_keys.issubset(set(all_data.keys()))

    def test_get_floor_count(self, tmp_result_dir):
        """get_floor_count() が正しい層数を返す。"""
        res = Result(tmp_result_dir)
        if res.max_disp:
            assert res.get_floor_count() >= 1

    def test_empty_directory(self, tmp_path):
        """空のディレクトリではデータが空。"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        res = Result(str(empty_dir))
        assert res.max_disp == {}
        assert res.get_floor_count() == 0

    def test_nonexistent_directory(self, tmp_path):
        """存在しないディレクトリでもエラーにならない。"""
        res = Result(str(tmp_path / "nonexistent"))
        assert res.max_disp == {}


class TestResultMock:
    """Result.from_mock() のテスト。"""

    def test_mock_creation(self):
        """from_mock() がデータを生成する。"""
        res = Result.from_mock(floors=5)
        assert len(res.max_disp) == 5
        assert len(res.max_vel) == 5
        assert len(res.max_acc) == 5
        assert len(res.max_story_disp) == 5
        assert len(res.max_story_drift) == 5
        assert len(res.shear_coeff) == 5
        assert len(res.max_otm) == 5

    def test_mock_floor_keys(self):
        """from_mock() の層番号が 1 から始まる。"""
        res = Result.from_mock(floors=3)
        assert set(res.max_disp.keys()) == {1, 2, 3}

    def test_mock_different_floors(self):
        """異なる階数で生成できる。"""
        for n in [1, 3, 10, 20]:
            res = Result.from_mock(floors=n)
            assert len(res.max_disp) == n

    def test_mock_to_dataframe(self):
        """from_mock() の結果を DataFrame に変換できる。"""
        res = Result.from_mock(floors=5)
        df = res.to_dataframe()
        assert len(df) == 5
        assert "Floor" in df.columns
        assert "MaxDisp[m]" in df.columns
