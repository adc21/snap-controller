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
        """fields_per_record < 2 の場合 None を返す（F/D が取れない）。"""
        from unittest.mock import MagicMock
        mock_header = MagicMock()
        mock_header.num_records = 5
        mock_header.fields_per_record = 1
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

    def test_damper_fpr4_reads_F_D_E_V_correctly(self):
        """Damper fpr=4 は [F, D, E, V] 順。従来の [F, D, V, E] 解釈は誤り。"""
        from unittest.mock import MagicMock

        n = 50
        F_arr = np.sin(np.linspace(0, 2 * np.pi, n)).astype(np.float32)
        D_arr = (F_arr * 0.01).astype(np.float32)
        E_arr = np.cumsum(np.abs(F_arr)).astype(np.float32)  # 単調増加
        V_arr = np.cos(np.linspace(0, 2 * np.pi, n)).astype(np.float32)  # 振動

        mock_header = MagicMock()
        mock_header.num_records = 1
        mock_header.fields_per_record = 4

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = np.arange(n, dtype=np.float32) * 0.005
        mock_hst.time_series.side_effect = lambda r, f: {
            0: F_arr, 1: D_arr, 2: E_arr, 3: V_arr
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Damper", 0, 0.005)
        assert result is not None
        np.testing.assert_allclose(result["F"], F_arr, rtol=1e-5)
        np.testing.assert_allclose(result["D"], D_arr, rtol=1e-5)
        np.testing.assert_allclose(result["V"], V_arr, rtol=1e-5)
        np.testing.assert_allclose(result["E"], E_arr, rtol=1e-5)
        # E は単調増加、V は振動
        assert np.all(np.diff(result["E"]) >= -1e-6)
        assert np.any(np.diff(result["V"]) < 0)

    def test_damper_fpr8_no_V_computes_from_D(self):
        """Damper fpr=8 は V フィールドなし。D の数値微分で V を補完する。"""
        from unittest.mock import MagicMock

        n = 200
        dt = 0.01
        t = np.arange(n, dtype=np.float32) * dt
        F_arr = np.sin(2 * np.pi * t).astype(np.float32)
        D_arr = np.sin(2 * np.pi * t).astype(np.float32) * 0.01
        E_arr = np.cumsum(np.abs(F_arr)).astype(np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 1
        mock_header.fields_per_record = 8

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = t
        # fpr=8 の layout: F@0, D@1, ..., E@7
        mock_hst.time_series.side_effect = lambda r, f: {
            0: F_arr, 1: D_arr, 7: E_arr
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Damper", 0, dt)
        assert result is not None
        np.testing.assert_allclose(result["F"], F_arr, rtol=1e-5)
        np.testing.assert_allclose(result["D"], D_arr, rtol=1e-5)
        np.testing.assert_allclose(result["E"], E_arr, rtol=1e-5)
        # V = dD/dt = 2π * 0.01 * cos(2πt) ≈ 0.0628 * cos(...)
        expected_V_peak = 2 * np.pi * 0.01
        assert result["V"].max() == pytest.approx(expected_V_peak, rel=0.02)

    def test_damper_fpr11_irdt_layout(self):
        """Damper fpr=11 (iRDT) は F@1, D@2, V@4, E@9。"""
        from unittest.mock import MagicMock

        n = 40
        F_arr = np.sin(np.linspace(0, 2 * np.pi, n)).astype(np.float32) * 100
        D_arr = (F_arr * 0.0001).astype(np.float32)
        V_arr = np.cos(np.linspace(0, 2 * np.pi, n)).astype(np.float32) * 5
        E_arr = np.cumsum(np.abs(F_arr)).astype(np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 1
        mock_header.fields_per_record = 11

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = np.arange(n, dtype=np.float32) * 0.005
        mock_hst.time_series.side_effect = lambda r, f: {
            1: F_arr, 2: D_arr, 4: V_arr, 9: E_arr,
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Damper", 0, 0.005)
        assert result is not None
        np.testing.assert_allclose(result["F"], F_arr, rtol=1e-5)
        np.testing.assert_allclose(result["D"], D_arr, rtol=1e-5)
        np.testing.assert_allclose(result["V"], V_arr, rtol=1e-5)
        np.testing.assert_allclose(result["E"], E_arr, rtol=1e-5)

    def test_spring_fpr5_uses_legacy_layout(self):
        """Spring.hst fpr=5 は従来通り [F, D, V, E, ...] 順。"""
        from unittest.mock import MagicMock

        n = 30
        F_arr = np.ones(n, dtype=np.float32)
        D_arr = np.full(n, 2.0, dtype=np.float32)
        V_arr = np.full(n, 3.0, dtype=np.float32)
        E_arr = np.full(n, 4.0, dtype=np.float32)

        mock_header = MagicMock()
        mock_header.num_records = 1
        mock_header.fields_per_record = 5

        mock_hst = MagicMock()
        mock_hst.header = mock_header
        mock_hst.times.return_value = np.arange(n, dtype=np.float32) * 0.005
        mock_hst.time_series.side_effect = lambda r, f: {
            0: F_arr, 1: D_arr, 2: V_arr, 3: E_arr,
        }.get(f, np.zeros(n, dtype=np.float32))

        mock_bc = MagicMock()
        mock_bc.hst = mock_hst
        loader = MagicMock()
        loader.get.return_value = mock_bc

        result = self._fn(loader, "Spring", 0, 0.005)
        assert result is not None
        assert result["F"][0] == pytest.approx(1.0)
        assert result["D"][0] == pytest.approx(2.0)
        assert result["V"][0] == pytest.approx(3.0)
        assert result["E"][0] == pytest.approx(4.0)


class TestDamperFieldMap:
    """damper_field_map() のテスト。"""

    def test_fpr4_layout(self):
        from controller.binary.hysteresis_analysis import damper_field_map
        m = damper_field_map(4)
        assert m == {"F": 0, "D": 1, "E": 2, "V": 3}

    def test_fpr8_layout_no_V(self):
        from controller.binary.hysteresis_analysis import damper_field_map
        m = damper_field_map(8)
        assert m["F"] == 0
        assert m["D"] == 1
        assert m["E"] == 7
        assert "V" not in m

    def test_fpr11_irdt_layout(self):
        from controller.binary.hysteresis_analysis import damper_field_map
        m = damper_field_map(11)
        assert m == {"F": 1, "D": 2, "V": 4, "E": 9}

    def test_unknown_fpr_fallback(self):
        from controller.binary.hysteresis_analysis import damper_field_map
        m = damper_field_map(6)
        assert m["F"] == 0
        assert m["D"] == 1
        assert m["E"] == 5


class TestMixedFprLayout:
    """混在 fpr レイアウト（iOD Damper.hst）対応テスト。

    iOD（Inerter Output Damper）の Damper.hst は、同一ファイル内で
    前半レコード群が fpr=4（取付部サブ要素）、後半レコード群が fpr=11
    （iOD 本体）という不均一な構造を持つ。meta 値の切替を境界として
    per-record fpr を検出する機能を検証する。
    """

    def _build_mixed_hst(self, tmp_path, num_a=5, fpr_a=4, num_b=3, fpr_b=11):
        """2 セグメント混在 fpr の合成 Damper.hst を書き出す。

        デフォルト値 (5×4 + 3×11 = 53, +sh=1 = 54) は一律 fpr で
        割り切れない組合せなので、segmented 検出パスが確実に走る。
        """
        import struct
        from pathlib import Path

        num_records = num_a + num_b
        step_header = 1
        step_size = step_header + num_a * fpr_a + num_b * fpr_b
        num_steps = 5
        meta_per = 1  # Damper.hst の仕様

        p = tmp_path / "Damper.hst"
        # 4-int header
        header = struct.pack(
            "<4i", 0x12345678, num_steps, step_size, num_records
        )
        # meta: first num_a records have meta key 7680, rest have 8186
        # (これらは実 iOD データから観測された値)
        meta_bytes = b""
        for i in range(num_a):
            meta_bytes += struct.pack("<i", 7680)
        for i in range(num_b):
            meta_bytes += struct.pack("<i", 8186)

        # step data: construct distinct values per (step, record, field) so
        # we can verify per-record offset calculations
        step_bytes = b""
        for step in range(num_steps):
            row = [float(step)]  # step_header
            for rec in range(num_a):
                for f in range(fpr_a):
                    # encode value = step*1000 + rec*100 + f
                    row.append(float(step * 1000 + rec * 100 + f))
            for rec in range(num_b):
                abs_rec = num_a + rec
                for f in range(fpr_b):
                    row.append(float(step * 1000 + abs_rec * 100 + f))
            step_bytes += struct.pack(f"<{step_size}f", *row)

        with open(p, "wb") as f:
            f.write(header)
            f.write(meta_bytes)
            f.write(step_bytes)
        return p

    def test_detects_segmented_fpr(self, tmp_path):
        """混在 fpr ファイルを読むと per_record_fpr が設定される。"""
        from controller.binary.hst_reader import HstReader
        p = self._build_mixed_hst(tmp_path)
        r = HstReader(p, dt=0.005, lazy=False)
        assert r.header is not None
        # 5×fpr4 + 3×fpr11
        assert r.header.per_record_fpr == [4, 4, 4, 4, 4, 11, 11, 11]
        assert r.fpr_for_record(0) == 4
        assert r.fpr_for_record(4) == 4
        assert r.fpr_for_record(5) == 11
        assert r.fpr_for_record(7) == 11

    def test_per_record_offset_correct(self, tmp_path):
        """各レコードのカラムオフセットが正しく算出される。"""
        from controller.binary.hst_reader import HstReader
        p = self._build_mixed_hst(tmp_path)
        r = HstReader(p, dt=0.005, lazy=False)
        # sh=1, rec0..4 (fpr=4): cols 1, 5, 9, 13, 17
        # rec5..7 (fpr=11): cols 21, 32, 43
        assert r.record_offset(0) == 1
        assert r.record_offset(1) == 5
        assert r.record_offset(4) == 17
        assert r.record_offset(5) == 21
        assert r.record_offset(7) == 43

    def test_time_series_reads_correct_values(self, tmp_path):
        """混在 fpr で time_series が各レコードの正しい値を読む。"""
        from controller.binary.hst_reader import HstReader
        p = self._build_mixed_hst(tmp_path)
        r = HstReader(p, dt=0.005, lazy=False)
        # step s, rec 0, field 0 → s*1000 + 0*100 + 0 = s*1000
        ts = r.time_series(0, 0)
        np.testing.assert_array_equal(ts, [0, 1000, 2000, 3000, 4000])
        # step s, rec 4, field 3 → s*1000 + 400 + 3
        ts = r.time_series(4, 3)
        np.testing.assert_array_equal(ts, [403, 1403, 2403, 3403, 4403])
        # step s, rec 7 (abs_rec=7), field 9 → s*1000 + 700 + 9
        ts = r.time_series(7, 9)
        np.testing.assert_array_equal(ts, [709, 1709, 2709, 3709, 4709])

    def test_time_series_rejects_out_of_record_field(self, tmp_path):
        """fpr=4 レコードに対して field_index=5 を要求すると IndexError。"""
        import pytest as _pt
        from controller.binary.hst_reader import HstReader
        p = self._build_mixed_hst(tmp_path)
        r = HstReader(p, dt=0.005, lazy=False)
        with _pt.raises(IndexError):
            r.time_series(0, 5)  # rec 0 は fpr=4 なので field 5 は不正

    def test_field_labels_vary_by_record(self, tmp_path):
        """fpr が異なるレコードでは field_labels_for_record が異なるレイアウトを返す。"""
        from controller.binary.hst_reader import HstReader
        p = self._build_mixed_hst(tmp_path)
        r = HstReader(p, dt=0.005, lazy=False)
        labels_rec0 = r.field_labels_for_record(0)
        labels_rec5 = r.field_labels_for_record(5)
        # fpr=4: [Force, Disp, Energy, Vel]
        assert labels_rec0 == ["Force", "Disp", "Energy", "Vel"]
        # fpr=11: iRDT レイアウト
        assert len(labels_rec5) == 11
        assert labels_rec5[1] == "Force"
        assert labels_rec5[9] == "Energy"

    def test_fetch_hysteresis_data_uses_per_record_fpr(self, tmp_path):
        """fetch_hysteresis_data が混在 fpr ファイルでレコード毎に正しい fpr を使う。"""
        from controller.binary.hst_reader import HstReader
        from controller.binary.hysteresis_analysis import fetch_hysteresis_data
        from unittest.mock import MagicMock

        p = self._build_mixed_hst(tmp_path)
        reader = HstReader(p, dt=0.005, lazy=False)

        mock_bc = MagicMock()
        mock_bc.hst = reader
        loader = MagicMock()
        loader.get.return_value = mock_bc

        # rec 0 は fpr=4: F@0, D@1, E@2, V@3
        d0 = fetch_hysteresis_data(loader, "Damper", 0, 0.005)
        assert d0 is not None
        # rec 0 field 0 for each step = step*1000
        np.testing.assert_array_equal(d0["F"], [0, 1000, 2000, 3000, 4000])
        # D = field 1 = step*1000 + 1
        np.testing.assert_array_equal(d0["D"], [1, 1001, 2001, 3001, 4001])
        # E = field 2 = step*1000 + 2
        np.testing.assert_array_equal(d0["E"], [2, 1002, 2002, 3002, 4002])
        assert d0["v_derived"] is False  # V field exists at fpr=4

        # rec 5 は fpr=11 (iOD whole サブ要素): F@1, D@2, E@9, V=d(D)/dt
        # mixed fpr=4+fpr=11 なので is_iod_layout=True → 自動で全体を返す
        d5 = fetch_hysteresis_data(loader, "Damper", 5, 0.005)
        assert d5 is not None
        # rec 5 field 1 = step*1000 + 5*100 + 1
        np.testing.assert_array_equal(d5["F"], [501, 1501, 2501, 3501, 4501])
        # D = field 2 = step*1000 + 5*100 + 2
        np.testing.assert_array_equal(d5["D"], [502, 1502, 2502, 3502, 4502])
        # E = field 9 = step*1000 + 5*100 + 9
        np.testing.assert_array_equal(d5["E"], [509, 1509, 2509, 3509, 4509])
        # V は D を dt=0.005 で数値微分 → ΔD=1000 で一定 → V=200000
        assert d5["v_derived"] is True
        assert np.allclose(d5["V"], 200000.0)

    def test_uniform_fpr_still_works(self, tmp_path):
        """単一 fpr ファイル（非混在）は従来どおり per_record_fpr=None。"""
        import struct
        from controller.binary.hst_reader import HstReader

        # 単純な fpr=4 × 5 レコード のファイル
        num_records, fpr = 5, 4
        step_size = 1 + num_records * fpr
        num_steps = 3
        p = tmp_path / "Damper.hst"
        with open(p, "wb") as f:
            f.write(struct.pack("<4i", 0x1234, num_steps, step_size, num_records))
            f.write(struct.pack(f"<{num_records}i", *[100] * num_records))  # meta
            for s in range(num_steps):
                row = [float(s)] + [float(s * 10 + i) for i in range(num_records * fpr)]
                f.write(struct.pack(f"<{step_size}f", *row))

        r = HstReader(p, dt=0.005, lazy=False)
        assert r.header is not None
        assert r.header.fields_per_record == 4
        assert r.header.per_record_fpr is None  # 一律 fpr なので None


class TestIodSubElement:
    """iOD (Inerter Output Damper) サブ要素マッピングのテスト。

    iOD fpr=11 は単一レコードに複数サブ要素 (全体/質量/ダッシュポット) を
    packing する。SNAP 参照図と f1=f4+f7 恒等式から確定したレイアウト::

      whole   : F=f1, D=f2, E=f9
      mass    : F=f4, A=f5, E=f9   (F = m·A)
      dashpot : F=f7, V=f8, E=f9   (ヒステリシス)
    """

    def test_is_iod_layout_mixed_fpr(self):
        from controller.binary.hysteresis_analysis import is_iod_layout
        assert is_iod_layout([4, 4, 4, 11, 11, 11]) is True
        assert is_iod_layout([11, 4, 11, 4]) is True

    def test_is_iod_layout_uniform_fpr11_false(self):
        """iRDT 一様 fpr=11 は iOD ではない。"""
        from controller.binary.hysteresis_analysis import is_iod_layout
        assert is_iod_layout([11, 11, 11]) is False
        assert is_iod_layout([4, 4, 4]) is False

    def test_is_iod_layout_none_or_empty(self):
        from controller.binary.hysteresis_analysis import is_iod_layout
        assert is_iod_layout(None) is False
        assert is_iod_layout([]) is False

    def test_sub_element_map_whole(self):
        from controller.binary.hysteresis_analysis import (
            iod_fpr11_sub_element_map, SUB_ELEMENT_WHOLE,
        )
        m = iod_fpr11_sub_element_map(SUB_ELEMENT_WHOLE)
        assert m["F"] == 1
        assert m["D"] == 2
        assert m["E"] == 9

    def test_sub_element_map_mass(self):
        from controller.binary.hysteresis_analysis import (
            iod_fpr11_sub_element_map, SUB_ELEMENT_MASS,
        )
        m = iod_fpr11_sub_element_map(SUB_ELEMENT_MASS)
        assert m["F"] == 4
        assert m["A"] == 5
        assert m["E"] == 9
        assert "D" not in m  # 質量要素は変位なし
        assert "V" not in m

    def test_sub_element_map_dashpot(self):
        from controller.binary.hysteresis_analysis import (
            iod_fpr11_sub_element_map, SUB_ELEMENT_DASHPOT,
        )
        m = iod_fpr11_sub_element_map(SUB_ELEMENT_DASHPOT)
        assert m["F"] == 7
        assert m["V"] == 8
        assert m["E"] == 9
        assert "D" not in m
        assert "A" not in m

    def test_sub_element_primary_kind(self):
        """主グラフの横軸 kind (whole→D, mass→A, dashpot→V)。"""
        from controller.binary.hysteresis_analysis import (
            SUB_ELEMENT_PRIMARY_KIND,
            SUB_ELEMENT_WHOLE, SUB_ELEMENT_MASS, SUB_ELEMENT_DASHPOT,
        )
        assert SUB_ELEMENT_PRIMARY_KIND[SUB_ELEMENT_WHOLE] == "D"
        assert SUB_ELEMENT_PRIMARY_KIND[SUB_ELEMENT_MASS] == "A"
        assert SUB_ELEMENT_PRIMARY_KIND[SUB_ELEMENT_DASHPOT] == "V"

    def test_sub_element_labels_has_three_options(self):
        """SNAP 参照図と一致する 3 サブ要素のみラベルを持つ。"""
        from controller.binary.hysteresis_analysis import (
            SUB_ELEMENT_LABELS, SUB_ELEMENT_AUTO,
            SUB_ELEMENT_WHOLE, SUB_ELEMENT_MASS, SUB_ELEMENT_DASHPOT,
        )
        assert SUB_ELEMENT_AUTO in SUB_ELEMENT_LABELS
        assert SUB_ELEMENT_WHOLE in SUB_ELEMENT_LABELS
        assert SUB_ELEMENT_MASS in SUB_ELEMENT_LABELS
        assert SUB_ELEMENT_DASHPOT in SUB_ELEMENT_LABELS
        # スプリングは SNAP で独立サブ要素として扱われない
        assert len(SUB_ELEMENT_LABELS) == 4  # auto + 3

    def test_fetch_iod_fpr11_whole(self, tmp_path):
        """iOD whole: F=f1, D=f2, E=f9 を読み取り、V は D から数値微分。"""
        from controller.binary.hysteresis_analysis import (
            fetch_hysteresis_data, SUB_ELEMENT_WHOLE,
        )
        from unittest.mock import MagicMock
        p = TestMixedFprLayout()._build_mixed_hst(tmp_path)
        from controller.binary.hst_reader import HstReader
        reader = HstReader(p, dt=0.005, lazy=False)

        mock_bc = MagicMock()
        mock_bc.hst = reader
        loader = MagicMock()
        loader.get.return_value = mock_bc

        d = fetch_hysteresis_data(
            loader, "Damper", 5, 0.005, sub_element=SUB_ELEMENT_WHOLE
        )
        assert d is not None
        assert d["sub_element"] == SUB_ELEMENT_WHOLE
        assert d["x_kind"] == "D"
        # F = field 1, D = field 2, E = field 9
        np.testing.assert_array_equal(d["F"], [501, 1501, 2501, 3501, 4501])
        np.testing.assert_array_equal(d["D"], [502, 1502, 2502, 3502, 4502])
        np.testing.assert_array_equal(d["E"], [509, 1509, 2509, 3509, 4509])
        assert d["v_derived"] is True

    def test_fetch_iod_fpr11_mass(self, tmp_path):
        """iOD mass: F=f4, A=f5 を読み取り x_kind=A。"""
        from controller.binary.hysteresis_analysis import (
            fetch_hysteresis_data, SUB_ELEMENT_MASS,
        )
        from unittest.mock import MagicMock
        p = TestMixedFprLayout()._build_mixed_hst(tmp_path)
        from controller.binary.hst_reader import HstReader
        reader = HstReader(p, dt=0.005, lazy=False)

        mock_bc = MagicMock()
        mock_bc.hst = reader
        loader = MagicMock()
        loader.get.return_value = mock_bc

        d = fetch_hysteresis_data(
            loader, "Damper", 5, 0.005, sub_element=SUB_ELEMENT_MASS
        )
        assert d is not None
        assert d["sub_element"] == SUB_ELEMENT_MASS
        assert d["x_kind"] == "A"
        np.testing.assert_array_equal(d["F"], [504, 1504, 2504, 3504, 4504])
        np.testing.assert_array_equal(d["A"], [505, 1505, 2505, 3505, 4505])

    def test_fetch_iod_fpr11_dashpot(self, tmp_path):
        """iOD dashpot: F=f7, V=f8 を読み取り x_kind=V。"""
        from controller.binary.hysteresis_analysis import (
            fetch_hysteresis_data, SUB_ELEMENT_DASHPOT,
        )
        from unittest.mock import MagicMock
        p = TestMixedFprLayout()._build_mixed_hst(tmp_path)
        from controller.binary.hst_reader import HstReader
        reader = HstReader(p, dt=0.005, lazy=False)

        mock_bc = MagicMock()
        mock_bc.hst = reader
        loader = MagicMock()
        loader.get.return_value = mock_bc

        d = fetch_hysteresis_data(
            loader, "Damper", 5, 0.005, sub_element=SUB_ELEMENT_DASHPOT
        )
        assert d is not None
        assert d["sub_element"] == SUB_ELEMENT_DASHPOT
        assert d["x_kind"] == "V"
        np.testing.assert_array_equal(d["F"], [507, 1507, 2507, 3507, 4507])
        np.testing.assert_array_equal(d["V"], [508, 1508, 2508, 3508, 4508])

    def test_sub_element_not_applicable_on_fpr4(self, tmp_path):
        """iOD fpr=4 レコードに mass サブ要素を指定すると applies=False を返す。"""
        from controller.binary.hysteresis_analysis import (
            fetch_hysteresis_data, SUB_ELEMENT_MASS,
        )
        from unittest.mock import MagicMock
        p = TestMixedFprLayout()._build_mixed_hst(tmp_path)
        from controller.binary.hst_reader import HstReader
        reader = HstReader(p, dt=0.005, lazy=False)

        mock_bc = MagicMock()
        mock_bc.hst = reader
        loader = MagicMock()
        loader.get.return_value = mock_bc

        # rec 0 は fpr=4 (iOD 全体レコード) → mass/dashpot は適用不可
        d = fetch_hysteresis_data(
            loader, "Damper", 0, 0.005, sub_element=SUB_ELEMENT_MASS
        )
        assert d is not None
        assert d["applies"] is False


class TestFieldConstants:
    """フィールドインデックス定数のテスト（Spring 用デフォルト）。"""

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

    def test_energy_field_index_damper_fpr4_points_to_field2(self):
        """旧コードは fpr=4 で field[3] を Energy としていたバグを検出する。"""
        from controller.binary.hysteresis_analysis import energy_field_index
        assert energy_field_index("Damper", 4) == 2

    def test_energy_field_index_damper_fpr8_points_to_field7(self):
        from controller.binary.hysteresis_analysis import energy_field_index
        assert energy_field_index("Damper", 8) == 7

    def test_energy_field_index_damper_fpr11_points_to_field9(self):
        """iRDT は E が末尾ではなく f9 にある。"""
        from controller.binary.hysteresis_analysis import energy_field_index
        assert energy_field_index("Damper", 11) == 9

    def test_energy_field_index_spring_fpr5_points_to_field3(self):
        from controller.binary.hysteresis_analysis import energy_field_index
        assert energy_field_index("Spring", 5) == 3


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
        assert hasattr(w, "_main_canvas")
        assert hasattr(w, "_fv_canvas")
        assert hasattr(w, "_peak_table")
        assert hasattr(w, "_record_list")
        assert hasattr(w, "_cat_combo")
        assert hasattr(w, "_sub_combo")

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
        w._draw_main_loop()

    def test_draw_fv_no_selection_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._record_list.clearSelection()
        w._draw_fv_loop()

    def test_draw_peak_table_empty_no_crash(self):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        w._draw_peak_table()
