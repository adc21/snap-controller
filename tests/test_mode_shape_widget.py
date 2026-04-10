"""
tests/test_mode_shape_widget.py
================================

app.ui.mode_shape_widget / controller.binary.mode_analysis のテスト。

純粋ロジック（数値計算）は controller.binary から直接 import するため Qt 不要。
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

class TestEstimateMdfloorStructure:
    """estimate_mdfloor_structure() のユニットテスト。"""

    def _fn(self, num_modes, values_per_record):
        from controller.binary.mode_analysis import estimate_mdfloor_structure
        return estimate_mdfloor_structure(num_modes, values_per_record)

    def test_6dof_exact(self):
        dof, labels = self._fn(5, 30)
        assert dof == 6
        assert labels == ["Dx", "Dy", "Dz", "Rx", "Ry", "Rz"]

    def test_4dof_exact(self):
        dof, labels = self._fn(3, 12)
        assert dof == 4
        assert labels == ["Dx", "Dy", "Rx", "Ry"]

    def test_3dof_exact(self):
        dof, labels = self._fn(4, 12)
        assert dof == 3
        assert labels == ["Dx", "Dy", "Dz"]

    def test_2dof_exact(self):
        dof, labels = self._fn(10, 20)
        assert dof == 2
        assert labels == ["Dx", "Dy"]

    def test_1dof_exact(self):
        dof, labels = self._fn(5, 5)
        assert dof == 1
        assert labels == ["Dx"]

    def test_no_fit_fallback(self):
        """割り切れない場合は全フィールドを raw として返す。"""
        dof, labels = self._fn(3, 7)
        assert dof == 7
        assert len(labels) == 7
        assert labels[0] == "f0"
        assert labels[6] == "f6"

    def test_zero_modes(self):
        dof, labels = self._fn(0, 12)
        assert dof == 0
        assert labels == []

    def test_zero_values(self):
        dof, labels = self._fn(5, 0)
        assert dof == 0
        assert labels == []

    def test_1mode_3dof(self):
        dof, labels = self._fn(1, 3)
        assert dof == 3
        assert labels == ["Dx", "Dy", "Dz"]

    def test_label_count_matches_dof(self):
        """返されるラベル数が dof_per_mode と一致する。"""
        for n_modes in (1, 3, 5, 10):
            for n_val in (n_modes * 6, n_modes * 4, n_modes * 3, n_modes * 2):
                dof, labels = self._fn(n_modes, n_val)
                assert len(labels) == dof, (
                    f"num_modes={n_modes}, values={n_val}: "
                    f"expected {dof} labels, got {len(labels)}"
                )


class TestGetMdfloorModeSeries:
    """get_mdfloor_mode_series() のユニットテスト。"""

    def _fn(self, records, mode_idx, dof_idx, dof_per_mode):
        from controller.binary.mode_analysis import get_mdfloor_mode_series
        return get_mdfloor_mode_series(records, mode_idx, dof_idx, dof_per_mode)

    def _make_records(self, n_floors, dof_per_mode, n_modes):
        """records[floor, mode*dof + d] = floor * 10 + mode + d * 0.1"""
        total = n_modes * dof_per_mode
        records = np.zeros((n_floors, total), dtype=np.float32)
        for f in range(n_floors):
            for m in range(n_modes):
                for d in range(dof_per_mode):
                    records[f, m * dof_per_mode + d] = float(f * 10 + m + d * 0.1)
        return records

    def test_mode0_dof0(self):
        records = self._make_records(n_floors=5, dof_per_mode=6, n_modes=3)
        result = self._fn(records, 0, 0, 6)
        assert result.shape == (5,)
        # floor=2, mode=0, dof=0 → 2*10 + 0 + 0*0.1 = 20.0
        assert float(result[2]) == pytest.approx(20.0)

    def test_mode1_dof0(self):
        records = self._make_records(n_floors=4, dof_per_mode=6, n_modes=2)
        result = self._fn(records, 1, 0, 6)
        # col=6; floor=3 → 3*10 + 1 + 0*0.1 = 31.0
        assert float(result[3]) == pytest.approx(31.0)

    def test_mode0_dof1(self):
        records = self._make_records(n_floors=3, dof_per_mode=6, n_modes=2)
        result = self._fn(records, 0, 1, 6)
        # col=1; floor=1 → 1*10 + 0 + 1*0.1 = 10.1
        assert float(result[1]) == pytest.approx(10.1, abs=1e-4)

    def test_out_of_range_col_returns_zeros(self):
        """存在しない列を指定した場合はゼロ配列を返す。"""
        records = np.ones((5, 6), dtype=np.float32)
        result = self._fn(records, 2, 0, 6)  # col = 12, 範囲外
        assert np.all(result == 0.0)
        assert result.shape == (5,)

    def test_zero_dof_per_mode_returns_zeros(self):
        records = np.ones((4, 6), dtype=np.float32)
        result = self._fn(records, 0, 0, 0)
        assert np.all(result == 0.0)

    def test_single_floor(self):
        records = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        result = self._fn(records, 0, 1, 3)
        assert float(result[0]) == pytest.approx(2.0)

    def test_output_dtype_is_float(self):
        records = np.zeros((3, 6), dtype=np.float32)
        result = self._fn(records, 0, 0, 6)
        assert result.dtype in (np.float32, np.float64)


# ===========================================================================
# モジュールインポートテスト
# ===========================================================================

class TestControllerModeAnalysisImport:
    """controller.binary.mode_analysis が PySide6 なしで import できることを確認。"""

    def test_functions_importable(self):
        from controller.binary.mode_analysis import (  # noqa: F401
            estimate_mdfloor_structure,
            get_mdfloor_mode_series,
        )

    def test_available_via_package(self):
        from controller.binary import (  # noqa: F401
            estimate_mdfloor_structure,
            get_mdfloor_mode_series,
        )


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestModeShapeWidgetImport:
    """UI クラスが Qt 環境で import できることを確認する。"""

    def test_widget_class_importable(self):
        from app.ui.mode_shape_widget import ModeShapeWidget  # noqa: F401


# ===========================================================================
# UI インスタンス化テスト（Qt 必須）
# ===========================================================================

@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestModeShapeWidgetInstantiation:
    """ModeShapeWidget が Qt 環境で正常に動作することを確認する。"""

    @pytest.fixture(autouse=True)
    def _app(self):
        from PySide6.QtWidgets import QApplication
        import sys
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def test_instantiate_no_crash(self):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        assert w is not None

    def test_set_entries_empty_no_crash(self):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        w.set_entries([])

    def test_set_entries_none_no_crash(self):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        w.set_entries(None)

    def test_refresh_without_data_no_crash(self):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        w._refresh()

    def test_ui_widgets_created(self):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        assert hasattr(w, "_table")
        assert hasattr(w, "_beta_canvas")
        assert hasattr(w, "_shape_canvas")
        assert hasattr(w, "_mode_combo")
        assert hasattr(w, "_dof_combo")

    def test_set_entries_with_mock_loader_no_crash(self):
        """モック SnapResultLoader で set_entries() がクラッシュしないことを確認。"""
        from unittest.mock import MagicMock
        from app.ui.mode_shape_widget import ModeShapeWidget

        mock_loader = MagicMock()
        mock_loader.period = None
        mock_loader.get.return_value = None

        w = ModeShapeWidget()
        w.set_entries([("テストケース", mock_loader)])

    def test_update_table_with_period_data(self):
        """Period.xbn データがあるときテーブルが正しく更新されることを確認。"""
        from unittest.mock import MagicMock
        from app.ui.mode_shape_widget import ModeShapeWidget
        from controller.binary.period_xbn_reader import ModeInfo

        mode1 = ModeInfo(
            mode_no=1, period=1.0, omega=6.28,
            beta={"X": 1.2, "Y": 0.1, "Z": 0.0, "RX": 0.0, "RY": 0.0},
            pm={"X": 85.0, "Y": 3.0, "Z": 0.0, "R": 0.0},
        )
        mode2 = ModeInfo(
            mode_no=2, period=0.5, omega=12.56,
            beta={"X": 0.1, "Y": 1.1, "Z": 0.0, "RX": 0.0, "RY": 0.0},
            pm={"X": 2.0, "Y": 80.0, "Z": 0.0, "R": 0.0},
        )
        mock_period = MagicMock()
        mock_period.modes = [mode1, mode2]

        mock_loader = MagicMock()
        mock_loader.period = mock_period
        mock_loader.get.return_value = None

        w = ModeShapeWidget()
        w.set_entries([("ケースA", mock_loader)])

        assert w._table.rowCount() == 2
        assert w._mode_combo.count() == 2

    def test_multicase_table_rows(self):
        """複数ケース時にすべてのモード行が追加される。"""
        from unittest.mock import MagicMock
        from app.ui.mode_shape_widget import ModeShapeWidget
        from controller.binary.period_xbn_reader import ModeInfo

        def _make_mock(n_modes):
            modes = [
                ModeInfo(
                    mode_no=i + 1,
                    period=1.0 / (i + 1),
                    omega=6.28 * (i + 1),
                    beta={"X": float(i), "Y": 0.0, "Z": 0.0, "RX": 0.0, "RY": 0.0},
                    pm={"X": 80.0, "Y": 5.0, "Z": 0.0, "R": 0.0},
                )
                for i in range(n_modes)
            ]
            mock_period = MagicMock()
            mock_period.modes = modes
            mock_loader = MagicMock()
            mock_loader.period = mock_period
            mock_loader.get.return_value = None
            return mock_loader

        w = ModeShapeWidget()
        w.set_entries([
            ("ケースA", _make_mock(3)),
            ("ケースB", _make_mock(5)),
        ])
        # ケースA 3行 + ケースB 5行 = 8行
        assert w._table.rowCount() == 8
