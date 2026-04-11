"""
tests/test_transfer_function_widget.py
=======================================

app.ui.transfer_function_widget のテスト。

FFT 計算ロジック・UI インスタンス化・空データ/異常データの
安全な処理を確認する。
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
# 純粋 FFT ロジックテスト（Qt 不要）
# ===========================================================================

class TestFFTLogic:
    """TransferFunctionWidget が使用する FFT 計算ロジックのテスト。"""

    def _compute_fft(self, y: np.ndarray, dt: float):
        """ウィジェット内部と同じ FFT 処理を再現する。"""
        try:
            from scipy.fft import rfft, rfftfreq
        except ImportError:
            from numpy.fft import rfft, rfftfreq

        n = len(y)
        Y = rfft(y)
        freqs = rfftfreq(n, d=dt)
        amplitude = np.abs(Y) * (2.0 / n)
        # DC 成分を除外
        return freqs[1:], amplitude[1:]

    def test_sine_wave_peak_at_correct_frequency(self):
        """正弦波の FFT ピークが正しい周波数に現れる。"""
        dt = 0.01
        f0 = 5.0  # 5 Hz
        n = 1024
        t = np.arange(n) * dt
        y = np.sin(2 * np.pi * f0 * t)
        freqs, amp = self._compute_fft(y, dt)
        peak_idx = int(np.argmax(amp))
        peak_freq = freqs[peak_idx]
        assert abs(peak_freq - f0) < 0.2

    def test_dc_signal_has_no_peak(self):
        """DC 成分のみの信号はピークが小さい。"""
        dt = 0.01
        n = 256
        y = np.ones(n) * 5.0
        freqs, amp = self._compute_fft(y, dt)
        # DC 除外後は全てほぼ 0
        assert np.max(amp) < 1e-10

    def test_zero_signal(self):
        """ゼロ信号は振幅がゼロ。"""
        dt = 0.01
        n = 256
        y = np.zeros(n)
        freqs, amp = self._compute_fft(y, dt)
        assert np.allclose(amp, 0.0)

    def test_frequency_resolution(self):
        """周波数分解能が 1/(N*dt) であることを確認。"""
        dt = 0.005
        n = 512
        y = np.random.randn(n)
        freqs, amp = self._compute_fft(y, dt)
        df_expected = 1.0 / (n * dt)
        # freqs[1] - freqs[0] は 1/(N*dt) に等しいはず
        df_actual = freqs[1] - freqs[0]
        assert abs(df_actual - df_expected) < 1e-8

    def test_nyquist_frequency(self):
        """最大周波数が Nyquist 周波数以下であることを確認。"""
        dt = 0.01
        n = 256
        y = np.random.randn(n)
        freqs, _ = self._compute_fft(y, dt)
        f_nyquist = 1.0 / (2.0 * dt)
        assert freqs[-1] <= f_nyquist + 1e-10

    def test_multi_tone_detection(self):
        """複合正弦波の各周波数成分が検出できることを確認。"""
        dt = 0.005
        n = 2048
        t = np.arange(n) * dt
        f1, f2 = 3.0, 12.0
        y = 2.0 * np.sin(2 * np.pi * f1 * t) + 1.0 * np.sin(2 * np.pi * f2 * t)
        freqs, amp = self._compute_fft(y, dt)
        # 振幅上位 2 つのピーク周波数を取得
        top2_idx = np.argsort(amp)[-2:]
        top2_freqs = sorted(freqs[top2_idx])
        assert abs(top2_freqs[0] - f1) < 0.5
        assert abs(top2_freqs[1] - f2) < 0.5

    def test_amplitude_scaling(self):
        """振幅スケーリングが正しいことを確認。"""
        dt = 0.01
        n = 1024
        f0 = 10.0
        A = 3.0
        t = np.arange(n) * dt
        y = A * np.sin(2 * np.pi * f0 * t)
        freqs, amp = self._compute_fft(y, dt)
        peak_amp = np.max(amp)
        # 振幅は A に近い値になるはず（窓関数なしの場合）
        assert abs(peak_amp - A) < 1.0  # spectral leakage reduces peak

    def test_short_signal(self):
        """非常に短い信号（2 サンプル）でもクラッシュしない。"""
        dt = 0.01
        y = np.array([1.0, -1.0])
        freqs, amp = self._compute_fft(y, dt)
        assert len(freqs) >= 0
        assert len(amp) >= 0


# ===========================================================================
# モジュールインポートテスト
# ===========================================================================

class TestTransferFunctionWidgetImport:
    """transfer_function_widget が import できることを確認。"""

    @pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
    def test_widget_class_importable(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget  # noqa: F401

    @pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
    def test_canvas_class_importable(self):
        from app.ui.transfer_function_widget import _MplCanvas  # noqa: F401


# ===========================================================================
# UI インスタンス化テスト（Qt 必須）
# ===========================================================================

@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestTransferFunctionWidgetInstantiation:
    """TransferFunctionWidget が Qt 環境で正常に動作することを確認する。"""

    @pytest.fixture(autouse=True)
    def _app(self):
        from PySide6.QtWidgets import QApplication
        import sys
        app = QApplication.instance() or QApplication(sys.argv)
        yield app

    def test_instantiate_no_crash(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        assert w is not None

    def test_set_entries_empty_no_crash(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        w.set_entries([])

    def test_set_entries_none_no_crash(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        w.set_entries(None)

    def test_ui_widgets_created(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        assert hasattr(w, "_canvas")
        assert hasattr(w, "_cat_combo")
        assert hasattr(w, "_rec_combo")
        assert hasattr(w, "_field_combo")
        assert hasattr(w, "_log_check")
        assert hasattr(w, "_peak_label")
        assert hasattr(w, "_status_label")

    def test_refresh_without_data_no_crash(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        w._refresh()

    def test_set_entries_with_no_hst_no_crash(self):
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget
        mock_loader = MagicMock()
        mock_loader.get.return_value = None
        w = TransferFunctionWidget()
        w.set_entries([("ケースA", mock_loader)])

    def test_set_entries_with_hst_but_no_header_no_crash(self):
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget
        mock_bc = MagicMock()
        mock_bc.hst = MagicMock()
        mock_bc.hst.header = None
        mock_loader = MagicMock()
        mock_loader.get.return_value = mock_bc
        w = TransferFunctionWidget()
        w.set_entries([("ケースA", mock_loader)])

    def test_log_scale_checkbox_toggle(self):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        assert not w._log_check.isChecked()
        w._log_check.setChecked(True)
        assert w._log_check.isChecked()

    def test_canvas_show_message_no_crash(self):
        from app.ui.transfer_function_widget import _MplCanvas
        canvas = _MplCanvas()
        canvas.show_message("テストメッセージ")

    def test_category_combo_populated_with_data(self):
        """HST データがあるカテゴリがコンボに表示される。"""
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget

        n = 256
        mock_header = MagicMock()
        mock_header.num_records = 3
        mock_header.fields_per_record = 4

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.dt = 0.01
        mock_hst.field_labels.return_value = ["X", "Y", "Z", "Rot"]
        mock_hst.ensure_loaded.return_value = None
        mock_hst.time_series.return_value = np.zeros(n, dtype=np.float32)

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        mock_bc.record_name.side_effect = lambda i: f"Rec-{i}"

        mock_loader = MagicMock()

        def get_side_effect(cat):
            if cat == "Floor":
                return mock_bc
            return None

        mock_loader.get.side_effect = get_side_effect

        w = TransferFunctionWidget()
        w.set_entries([("テスト", mock_loader)])

        cat_items = [w._cat_combo.itemText(i) for i in range(w._cat_combo.count())]
        assert "Floor" in cat_items

    def test_record_combo_populated_with_data(self):
        """レコードコンボがレコード名で正しく設定される。"""
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget

        n = 128
        mock_header = MagicMock()
        mock_header.num_records = 3
        mock_header.fields_per_record = 2

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.dt = 0.01
        mock_hst.field_labels.return_value = ["X変位", "Y変位"]
        mock_hst.ensure_loaded.return_value = None
        mock_hst.time_series.return_value = np.zeros(n, dtype=np.float32)

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        mock_bc.record_name.side_effect = lambda i: f"FL-{i + 1}"

        mock_loader = MagicMock()
        mock_loader.get.side_effect = lambda cat: mock_bc if cat == "Floor" else None

        w = TransferFunctionWidget()
        w.set_entries([("テスト", mock_loader)])

        rec_items = [w._rec_combo.itemText(i) for i in range(w._rec_combo.count())]
        assert len(rec_items) == 3
        assert "FL-1" in rec_items

    def test_refresh_with_valid_data_no_crash(self):
        """有効なデータを設定して _refresh がクラッシュしないことを確認。"""
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget

        n = 256
        dt = 0.01
        t = np.arange(n, dtype=np.float32) * dt
        y = np.sin(2 * np.pi * 5.0 * t).astype(np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 2
        mock_header.fields_per_record = 3

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.dt = dt
        mock_hst.field_labels.return_value = ["X", "Y", "Z"]
        mock_hst.ensure_loaded.return_value = None
        mock_hst.time_series.return_value = y

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        mock_bc.record_name.side_effect = lambda i: f"Rec-{i}"

        mock_loader = MagicMock()
        mock_loader.get.side_effect = lambda cat: mock_bc if cat == "Floor" else None

        w = TransferFunctionWidget()
        w.set_entries([("テスト", mock_loader)])
        # _refresh is called by set_entries, just verify no crash
        assert w._peak_label.text() != ""

    def test_multiple_cases_overlay_no_crash(self):
        """複数ケースの重ね描きでクラッシュしないことを確認。"""
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget

        n = 128
        dt = 0.01

        def make_loader(freq):
            t = np.arange(n, dtype=np.float32) * dt
            y = np.sin(2 * np.pi * freq * t).astype(np.float32)
            mock_header = MagicMock()
            mock_header.num_records = 1
            mock_header.fields_per_record = 1
            mock_hst = MagicMock()
            mock_hst.header = mock_header
            mock_hst.dt = dt
            mock_hst.field_labels.return_value = ["X"]
            mock_hst.ensure_loaded.return_value = None
            mock_hst.time_series.return_value = y
            mock_bc = MagicMock()
            mock_bc.hst = mock_hst
            mock_bc.record_name.side_effect = lambda i: f"Rec-{i}"
            loader = MagicMock()
            loader.get.side_effect = lambda cat: mock_bc if cat == "Floor" else None
            return loader

        w = TransferFunctionWidget()
        w.set_entries([
            ("ケースA (3Hz)", make_loader(3.0)),
            ("ケースB (7Hz)", make_loader(7.0)),
            ("ケースC (15Hz)", make_loader(15.0)),
        ])
        # 3 ケースがプロットされている
        assert "3 ケース" in w._status_label.text()


# =====================================================================
# Q-2: 基準データオーバーレイテスト
# =====================================================================

@pytest.mark.skipif(not _qt_available(), reason="PySide6 required")
class TestReferenceOverlay:
    """TransferFunctionWidget の基準データ保存・オーバーレイテスト。"""

    def test_initial_state(self):
        """初期状態では基準データは None。"""
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        assert w._reference_data is None
        assert w._btn_clear_ref.isEnabled() is False

    def test_set_reference_with_data(self):
        """ケースデータがある場合、基準データを保存できる。"""
        from unittest.mock import MagicMock
        from app.ui.transfer_function_widget import TransferFunctionWidget

        dt = 0.01
        n = 1024
        t = np.arange(n) * dt
        y = np.sin(2 * np.pi * 5.0 * t)

        mock_header = MagicMock()
        mock_header.num_records = 2
        mock_header.fields_per_record = 1
        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.dt = dt
        mock_hst.field_labels.return_value = ["X"]
        mock_hst.ensure_loaded.return_value = None
        mock_hst.time_series.return_value = y
        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        mock_bc.record_name.side_effect = lambda i: f"Rec-{i}"
        loader = MagicMock()
        loader.get.side_effect = lambda cat: mock_bc if cat == "Floor" else None

        w = TransferFunctionWidget()
        w.set_entries([("TestCase", loader)])

        # 基準に設定
        w._set_reference()
        assert w._reference_data is not None
        assert "基準: TestCase" in w._reference_data["name"]
        assert len(w._reference_data["freqs"]) > 0
        assert w._btn_clear_ref.isEnabled() is True

    def test_clear_reference(self):
        """基準データをクリアできる。"""
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        w._reference_data = {"name": "test", "freqs": np.array([1.0]), "amplitude": np.array([1.0])}
        w._btn_clear_ref.setEnabled(True)

        w._clear_reference()
        assert w._reference_data is None
        assert w._btn_clear_ref.isEnabled() is False

    def test_set_reference_no_entries(self):
        """ケースがない場合、基準設定はスキップされる。"""
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        w._set_reference()
        assert w._reference_data is None
