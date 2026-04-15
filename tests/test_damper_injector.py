"""
tests/test_damper_injector.py

DamperInjector サービスのユニットテスト。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.damper_injector import (
    DamperInjector,
    DamperInsertSpec,
    InjectionResult,
    create_injector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_s8i_content() -> str:
    """パース可能な最小限の .s8i テキストを返す。"""
    return (
        "SNAP Ver.8 Test Model\n"
        "VER 8\n"
        "END\n"
    )


def _write_temp_s8i(content: str) -> str:
    """一時ファイルに .s8i 内容を書き出してパスを返す。"""
    fd, path = tempfile.mkstemp(suffix=".s8i")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# DamperInsertSpec
# ---------------------------------------------------------------------------

class TestDamperInsertSpec:
    def test_default_values(self):
        spec = DamperInsertSpec()
        assert spec.damper_type == "iRDT"
        assert spec.def_name == "IRDT1"
        assert spec.mass_kN_s2_m == 100.0
        assert spec.spring_kN_m == 5000.0
        assert spec.damping_kN_s_m == 200.0
        assert spec.stroke_m == 0.3
        assert spec.quantity == 1

    def test_iod_params(self):
        spec = DamperInsertSpec(
            damper_type="iOD",
            def_name="IOD1",
            spring_kN_m=0.0,
        )
        assert spec.damper_type == "iOD"
        assert spec.spring_kN_m == 0.0


# ---------------------------------------------------------------------------
# DamperInjector — _build_dvod_overrides
# ---------------------------------------------------------------------------

class TestDamperInjectorOverrides:
    def test_overrides_dvod_values(self):
        """DVOD overrides: 減衰モデル=3, 質量=md, C0=cd, 取付け剛性=kb。"""
        injector = DamperInjector()
        spec = DamperInsertSpec(
            mass_kN_s2_m=150.0,
            spring_kN_m=8000.0,
            damping_kN_s_m=300.0,
            stroke_m=0.25,
        )
        ov = injector._build_dvod_overrides(spec)
        # 減衰モデル = 3 (ダッシュポットと質量)
        assert ov["5"] == "3"
        # 質量 md
        assert float(ov["6"]) == pytest.approx(150.0)
        # ダッシュポット特性-種別 = 0 (線形弾性型 EL1)
        assert ov["7"] == "0"
        # C0 = 減衰係数 cd
        assert float(ov["8"]) == pytest.approx(300.0)
        # 装置剛性 = 0
        assert ov["14"] == "0"
        # 取付け剛性 = 支持部材剛性 kb
        assert float(ov["15"]) == pytest.approx(8000.0)
        # 温度変動係数 τ (下限/上限) = 1.0
        assert float(ov["20"]) == pytest.approx(1.0)
        assert float(ov["22"]) == pytest.approx(1.0)

    def test_overrides_keyword_is_dvod(self):
        """inject() が DVOD キーワードで add_damper_def_new を呼ぶ。"""
        injector = DamperInjector()
        mock_model = MagicMock()
        mock_model.nodes = {1: MagicMock(), 2: MagicMock()}
        mock_model.damper_defs = []
        mock_model.damper_elements = []
        mock_model.get_damper_def.return_value = None
        mock_model.add_damper_def_new.return_value = MagicMock()

        with patch("app.services.damper_injector.parse_s8i", return_value=mock_model):
            injector.inject(
                base_s8i_path="model.s8i",
                specs=[DamperInsertSpec(def_name="IRDT1", node_i=1, node_j=2)],
                output_s8i_path="/tmp/out.s8i",
            )
        call = mock_model.add_damper_def_new.call_args
        assert call.kwargs.get("keyword") == "DVOD"
        assert call.kwargs.get("num_fields") == 22


# ---------------------------------------------------------------------------
# DamperInjector.inject — エラーケース
# ---------------------------------------------------------------------------

class TestDamperInjectorErrors:
    def test_empty_specs(self):
        injector = DamperInjector()
        result = injector.inject(
            base_s8i_path="dummy.s8i",
            specs=[],
            output_s8i_path="out.s8i",
        )
        assert not result.success
        assert "指定されていません" in result.message

    def test_nonexistent_file(self):
        injector = DamperInjector()
        spec = DamperInsertSpec(node_i=1, node_j=2)
        result = injector.inject(
            base_s8i_path="/nonexistent/model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )
        assert not result.success
        assert "読み込みに失敗" in result.message


# ---------------------------------------------------------------------------
# DamperInjector.inject — 正常ケース (parse_s8i をモック)
# ---------------------------------------------------------------------------

class TestDamperInjectorSuccess:
    def _make_mock_model(self):
        """テスト用のモック S8iModel を構築。"""
        model = MagicMock()
        model.nodes = {101: MagicMock(), 201: MagicMock()}
        model.damper_defs = []
        model.damper_elements = []
        model.get_damper_def.return_value = None
        model.add_damper_def_new.return_value = MagicMock()
        model.write.return_value = None
        return model

    @patch("app.services.damper_injector.parse_s8i")
    def test_single_irdt_injection(self, mock_parse):
        mock_model = self._make_mock_model()
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        spec = DamperInsertSpec(
            damper_type="iRDT",
            def_name="IRDT1",
            floor_name="F5",
            node_i=101,
            node_j=201,
            quantity=2,
            mass_kN_s2_m=150.0,
            spring_kN_m=8000.0,
            damping_kN_s_m=300.0,
        )

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )

        assert result.success
        assert "IRDT1" in result.added_def_names
        assert result.added_element_count == 1
        mock_model.add_damper_def_new.assert_called_once()
        mock_model.write.assert_called_once_with("/tmp/out.s8i")
        assert len(mock_model.damper_elements) == 1

    @patch("app.services.damper_injector.parse_s8i")
    def test_multiple_specs(self, mock_parse):
        mock_model = self._make_mock_model()
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        specs = [
            DamperInsertSpec(def_name="IRDT1", node_i=101, node_j=201),
            DamperInsertSpec(def_name="IOD1", damper_type="iOD", node_i=102, node_j=202, spring_kN_m=0.0),
        ]

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=specs,
            output_s8i_path="/tmp/out.s8i",
        )

        assert result.success
        assert result.added_element_count == 2
        assert "IRDT1" in result.added_def_names
        assert "IOD1" in result.added_def_names

    @patch("app.services.damper_injector.parse_s8i")
    def test_duplicate_def_warning(self, mock_parse):
        mock_model = self._make_mock_model()
        mock_model.get_damper_def.return_value = MagicMock()  # 既存定義あり
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        spec = DamperInsertSpec(def_name="IRDT1", node_i=101, node_j=201)

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )

        assert result.success
        assert any("上書き" in w for w in result.warnings)

    @patch("app.services.damper_injector.parse_s8i")
    def test_node_not_found_warning(self, mock_parse):
        mock_model = self._make_mock_model()
        mock_model.nodes = {101: MagicMock()}  # node_j=999 は存在しない
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        spec = DamperInsertSpec(def_name="IRDT1", node_i=101, node_j=999)

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )

        assert result.success
        assert any("999" in w for w in result.warnings)

    @patch("app.services.damper_injector.parse_s8i")
    def test_write_failure(self, mock_parse):
        mock_model = self._make_mock_model()
        mock_model.write.side_effect = IOError("disk full")
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        spec = DamperInsertSpec(def_name="IRDT1", node_i=101, node_j=201)

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )

        assert not result.success
        assert "書き出しに失敗" in result.message

    @patch("app.services.damper_injector.parse_s8i")
    def test_new_case_creation(self, mock_parse):
        """base_case 指定時に new_case が生成される。"""
        from app.models.analysis_case import AnalysisCase

        mock_model = self._make_mock_model()
        mock_parse.return_value = mock_model

        base_case = AnalysisCase(
            name="BASE",
            model_path="model.s8i",
            snap_exe_path="snap.exe",
            output_dir="/tmp/output",
        )

        injector = DamperInjector()
        spec = DamperInsertSpec(
            damper_type="iRDT",
            def_name="IRDT1",
            node_i=101,
            node_j=201,
        )

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
            base_case=base_case,
            new_case_name="BASE_IRDT",
        )

        assert result.success
        assert result.new_case is not None
        assert result.new_case.name == "BASE_IRDT"
        assert result.new_case.model_path == "/tmp/out.s8i"

    @patch("app.services.damper_injector.parse_s8i")
    def test_add_def_failure(self, mock_parse):
        """add_damper_def_new が None を返した場合の警告。"""
        mock_model = self._make_mock_model()
        mock_model.add_damper_def_new.return_value = None
        mock_parse.return_value = mock_model

        injector = DamperInjector()
        spec = DamperInsertSpec(def_name="IRDT1", node_i=101, node_j=201)

        result = injector.inject(
            base_s8i_path="model.s8i",
            specs=[spec],
            output_s8i_path="/tmp/out.s8i",
        )

        assert result.success  # write は成功
        assert result.added_element_count == 0
        assert any("失敗" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# create_injector factory
# ---------------------------------------------------------------------------

class TestCreateInjector:
    def test_returns_injector_instance(self):
        injector = create_injector()
        assert isinstance(injector, DamperInjector)


# ---------------------------------------------------------------------------
# InjectionResult
# ---------------------------------------------------------------------------

class TestInjectionResult:
    def test_default_values(self):
        r = InjectionResult()
        assert not r.success
        assert r.output_s8i_path == ""
        assert r.new_case is None
        assert r.added_def_names == []
        assert r.added_element_count == 0
        assert r.message == ""
        assert r.warnings == []
