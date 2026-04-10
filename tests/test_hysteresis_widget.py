"""
tests/test_hysteresis_widget.py
================================

app.ui.hysteresis_widget / controller.binary.hysteresis_analysis のテスト。

純粋ロジック（統計計算・データ取得）は controller.binary から直接 import するため Qt 不要。
UI インスタンス化テストは Qt 環境でのみ実行する。
"""

from __future__ import annotations

import numpy as np
import pytest


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


# ===========================================================================
# 純粋ロジックテスト（Qt 不要 — controller.binary から直接 import）
# ===========================================================================

class TestComputePeakStats:
    """compute_peak_stats() のユニットテスト。"""

    def _fn(self, data):
        from controller.binary.hysteresis_analysis import compute_peak_stats
        return compute_peak_stats(data)

    def test_basic_peaks(self):
        data = {
            "F": np.array([0.0, 1.0, -2.0, 1.5]),
            "D": np.array([0.0, 0.01, -0.02, 0.015]),
            "V": np.array([0.0, 0.1, -0.2, 0.15]),
            "E": np.array([0.0, 0.5, 1.0, 1.5]),
        }
        stats = self._fn(data)
        assert stats["max_F"] == pytest.approx(2.0)
        assert stats["max_D"] == pytest.approx(0.02)
        assert stats["max_V"] == pytest.approx(0.2)
        assert stats["max_E"] == pytest.approx(1.5)

    def test_work_trapezoid_closed_loop(self):
        """閉ループの仕事量が正しい値になる（台形積分）。"""
        # 単純な矩形ループ: F=1 で D: 0→1→0→-1→0
        F = np.array([1.0, 1.0, 1.0, 1.0, 1.0])
        D = np.array([0.0, 1.0, 0.0, -1.0, 0.0])
        data = {"F": F, "D": D, "V": np.zeros_like(F), "E": np.zeros_like(F)}
        stats = self._fn(data)
        # trapz([1,1,1,1,1], [0,1,0,-1,0]) = 0 （正負相殺）
        assert stats["work"] == pytest.approx(0.0, abs=1e-10)

    def test_work_positive_area(self):
        """一方向の変位で仕事量が正になる。"""
        F = np.array([0.0, 1.0, 2.0, 3.0])
        D = np.array([0.0, 0.5, 1.0, 1.5])
        data = {"F": F, "D": D, "V": np.zeros_like(F), "E": np.zeros_like(F)}
        stats = self._fn(data)
        # trapz([0,1,2,3], [0,0.5,1,1.5]) = 0*0.5 + (0.5+1)/2*0.5 + (1+1.5)/2*0.5
        #   = 0 + 0.375 + 0.625 = 1.0? Wait: trapz(y, x)
        # np.trapz([0,1,2,3], [0,0.5,1,1.5])
        #   = (0+1)/2 * 0.5 + (1+2)/2 * 0.5 + (2+3)/2 * 0.5
        #   = 0.25 + 0.75 + 1.25 = 2.25
        assert stats["work"] == pytest.approx(2.25, rel=1e-6)

    def test_all_zero(self):
        data = {k: np.zeros(10) for k in ("F", "D", "V", "E")}
        stats = self._fn(data)
        assert stats["max_F"] == pytest.approx(0.0)
        assert stats["max_D"] == pytest.approx(0.0)
        assert stats["work"] == pytest.approx(0.0)

    def test_single_point(self):
        data = {
            "F": np.array([3.0]),
            "D": np.array([0.01]),
            "V": np.array([0.5]),
            "E": np.array([0.1]),
        }
        stats = self._fn(data)
        assert stats["max_F"] == pytest.approx(3.0)
        assert stats["max_D"] == pytest.approx(0.01)

    def test_negative_values_absolute(self):
        data = {
            "F": np.array([-5.0, 3.0, -2.0]),
            "D": np.array([0.0, 0.0, 0.0]),
            "V": np.array([0.0, 0.0, 0.0]),
            "E": np.array([0.0, 0.0, 0.0]),
        }
        stats = self._fn(data)
        assert stats["max_F"] == pytest.approx(5.0)

    def test_returns_all_keys(self):
        data = {k: np.zeros(5) for k in ("F", "D", "V", "E")}
        stats = self._fn(data)
        for key in ("max_F", "max_D", "max_V", "max_E", "work"):
            assert key in stats


class TestFetchHysteresisData:
    """fetch_hysteresis_data() のユニットテスト。"""

    def _fn(self, loader, category, rec_idx, dt):
        from controller.binary.hysteresis_analysis import fetch_hysteresis_data
        return fetch_hysteresis_data(loader, category, rec_idx, dt)

    def test_returns_none_for_missing_category(self):
        from unittest.mock import MagicMock
        loader = MagicMock()
        loader.get.return_value = None
        assert self._fn(loader, "Damper", 0, 0.005) is None

    def test_returns_none_for_missing_hst(self):
        from unittest.mock import MagicMock
        mock_bc = MagicMock()
        mock_bc.hst = None
        loader = MagicMock()
        loader.get.return_value = mock_bc
        assert self._fn(loader, "Damper", 0, 0.005) is None

    def test_returns_none_for_no_header(self):
        from unittest.mock import MagicMock
        mock_hst = MagicMock()
        mock_hst.header = None
        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc
        assert self._fn(loader, "Damper", 0, 0.005) is None

    def test_returns_none_for_insufficient_fields(self):
        """fields_per_record < 3 の場合 None を返す（V が取れない）。"""
        from unittest.mock import MagicMock
        mock_header = MagicMock()
        mock_header.num_records = 5
        mock_header.fields_per_record = 2
        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc
        assert self._fn(loader, "Damper", 0, 0.005) is None

    def test_returns_none_for_out_of_range_rec(self):
        from unittest.mock import MagicMock
        mock_header = MagicMock()
        mock_header.num_records = 3
        mock_header.fields_per_record = 8
        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc
        assert self._fn(loader, "Damper", rec_idx=5, dt=0.005) is None

    def test_returns_data_dict_on_success(self):
        """正常系でデータ辞書が返ることを確認する。"""
        from unittest.mock import MagicMock

        n = 100
        t_arr = np.arange(n, dtype=np.float32) * 0.005
        F_arr = np.sin(np.linspace(0, 2 * np.pi, n)).astype(np.float32)
        D_arr = (F_arr * 0.01).astype(np.float32)
        V_arr = np.cos(np.linspace(0, 2 * np.pi, n)).astype(np.float32)
        E_arr = np.cumsum(np.abs(F_arr)).astype(np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 5
        mock_header.fields_per_record = 8

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = t_arr
        mock_hst.time_series.side_effect = lambda r, f: {
            0: F_arr, 1: D_arr, 2: V_arr, 3: E_arr
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Damper", rec_idx=2, dt=0.005)
        assert result is not None
        assert set(result.keys()) >= {"t", "F", "D", "V", "E"}
        assert len(result["F"]) == n
        np.testing.assert_allclose(result["F"], F_arr, rtol=1e-5)

    def test_energy_defaults_to_zero_when_no_field(self):
        """fields_per_record == 3 (F/D/V のみ) の場合 E はゼロ配列になる。"""
        from unittest.mock import MagicMock

        n = 50
        F_arr = np.ones(n, dtype=np.float32)
        D_arr = np.zeros(n, dtype=np.float32)
        V_arr = np.zeros(n, dtype=np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 2
        mock_header.fields_per_record = 3  # E フィールドなし

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = np.arange(n, dtype=np.float32) * 0.005
        mock_hst.time_series.side_effect = lambda r, f: {
            0: F_arr, 1: D_arr, 2: V_arr
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Damper", rec_idx=0, dt=0.005)
        assert result is not None
        assert np.all(result["E"] == 0.0)


class TestFieldConstants:
    """フィールドインデックス定数のテスト。"""

    def test_constants_correct(self):
        from controller.binary.hysteresis_analysis import (
            FIELD_FORCE, FIELD_DISP, FIELD_VEL, FIELD_ENERGY
        )
        assert FIELD_FORCE == 0
        assert FIELD_DISP == 1
        assert FIELD_VEL == 2
        assert FIELD_ENERGY == 3

    def test_available_via_package(self):
        from controller.binary import FIELD_FORCE, FIELD_DISP, FIELD_VEL, FIELD_ENERGY  # noqa
        assert FIELD_FORCE == 0


# ===========================================================================
# モジュールインポートテスト
# ===========================================================================

class TestControllerHysteresisAnalysisImport:
    """controller.binary.hysteresis_analysis が PySide6 なしで import できることを確認。"""

    def test_functions_importable(self):
        from controller.binary.hysteresis_analysis import (  # noqa: F401
            compute_peak_stats,
            fetch_hysteresis_data,
            FIELD_FORCE,
            FIELD_DISP,
            FIELD_VEL,
            FIELD_ENERGY,
        )

    def test_available_via_package(self):
        from controller.binary import (  # noqa: F401
            compute_peak_stats,
            fetch_hysteresis_data,
        )


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestHysteresisWidgetImport:
    """UI クラスが Qt 環境で import できることを確認する。"""

    def test_widget_class_importable(self):
        from app.ui.hysteresis_widget import HysteresisWidget  # noqa: F401


# ===========================================================================
# UI インスタンス化テスト（Qt 必須）
# ===========================================================================

@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestHysteresisWidgetInstantiation:
    """HysteresisWidget が Qt 環境で正常に動作することを確認する。"""

    @pytest.fixture(autouse=True)
    def _app(self):
        from PySide6.QtWidgets import QApplication
        import sys
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def test_instantiate_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        assert w is not None

    def test_set_entries_empty_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w.set_entries([])

    def test_set_entries_none_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w.set_entries(None)

    def test_ui_widgets_created(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        assert hasattr(w, "_fd_canvas")
        assert hasattr(w, "_fv_canvas")
        assert hasattr(w, "_peak_table")
        assert hasattr(w, "_record_list")
        assert hasattr(w, "_cat_combo")

    def test_category_combo_has_damper_spring(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        items = [w._cat_combo.itemData(i) for i in range(w._cat_combo.count())]
        assert "Damper" in items
        assert "Spring" in items

    def test_refresh_without_data_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._refresh()

    def test_set_entries_with_no_hst_no_crash(self):
        from unittest.mock import MagicMock
        from app.ui.hysteresis_widget import HysteresisWidget
        mock_loader = MagicMock()
        mock_loader.get.return_value = None
        w = HysteresisWidget()
        w.set_entries([("ケースA", mock_loader)])

    def test_draw_fd_no_selection_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._record_list.clearSelection()
        w._draw_fd_loop()

    def test_draw_fv_no_selection_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._record_list.clearSelection()
        w._draw_fv_loop()

    def test_draw_peak_table_empty_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._draw_peak_table()
