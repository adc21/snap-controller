"""
tests/test_transfer_function_evaluator.py
TransferFunctionEvaluator のユニットテスト (SNAP 実行に依存しない部分)。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.services.transfer_function_evaluator import (
    OBJECTIVE_KEY,
    RESPONSE_ABS_ACC,
    RESPONSE_REL_DISP,
    TransferFunctionEvalConfig,
    TransferFunctionEvaluator,
)


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_snap_exe(tmp_path: Path) -> Path:
    p = tmp_path / "SNAP.exe"
    p.write_bytes(b"dummy")
    return p


@pytest.fixture
def fake_s8i(tmp_path: Path) -> Path:
    p = tmp_path / "model.s8i"
    p.write_bytes(b"dummy")
    return p


@pytest.fixture
def base_config(tmp_path, fake_snap_exe, fake_s8i) -> TransferFunctionEvalConfig:
    return TransferFunctionEvalConfig(
        snap_exe_path=str(fake_snap_exe),
        base_s8i_path=str(fake_s8i),
        snap_work_dir=str(tmp_path / "work"),
        snap_wave_dir=str(tmp_path / "wave"),
        target_case_no=1,
    )


# ---------------------------------------------------------------------------
# TransferFunctionEvalConfig - validate
# ---------------------------------------------------------------------------

class TestConfigValidate:
    def test_valid_config(self, base_config):
        base_config.validate()

    def test_missing_snap_exe(self, base_config):
        base_config.snap_exe_path = "C:/nonexistent/SNAP.exe"
        with pytest.raises(FileNotFoundError, match="SNAP.exe"):
            base_config.validate()

    def test_missing_s8i(self, base_config):
        base_config.base_s8i_path = "C:/nonexistent/model.s8i"
        with pytest.raises(FileNotFoundError, match=r"\.s8i"):
            base_config.validate()

    def test_missing_wave_dir(self, base_config):
        base_config.snap_wave_dir = ""
        with pytest.raises(ValueError, match="wave"):
            base_config.validate()

    def test_invalid_target_case_no(self, base_config):
        base_config.target_case_no = 0
        with pytest.raises(ValueError, match="target_case_no"):
            base_config.validate()

    def test_invalid_response_type(self, base_config):
        base_config.response_type = "invalid"
        with pytest.raises(ValueError, match="response_type"):
            base_config.validate()

    def test_accepts_rel_disp(self, base_config):
        base_config.response_type = RESPONSE_REL_DISP
        base_config.validate()

    def test_accepts_abs_acc(self, base_config):
        base_config.response_type = RESPONSE_ABS_ACC
        base_config.validate()


# ---------------------------------------------------------------------------
# TransferFunctionEvaluator - impulse wave generation
# ---------------------------------------------------------------------------

class TestImpulsePreparation:
    def test_impulse_wave_file_created(self, base_config):
        evaluator = TransferFunctionEvaluator(base_config)
        assert evaluator._impulse_wave_path.exists()
        assert evaluator._impulse_wave_path.suffix == ".wv"

    def test_impulse_signal_has_single_peak(self, base_config):
        base_config.impulse_amax = 500.0
        base_config.impulse_num_points = 128
        base_config.impulse_index = 5
        evaluator = TransferFunctionEvaluator(base_config)
        sig = evaluator._impulse_signal
        assert sig.shape == (128,)
        assert sig[5] == 500.0
        # Everything else is zero
        mask = np.ones_like(sig, dtype=bool)
        mask[5] = False
        np.testing.assert_array_equal(sig[mask], np.zeros(127))

    def test_impulse_filename_override(self, base_config):
        base_config.impulse_filename_override = "MYTEST"
        evaluator = TransferFunctionEvaluator(base_config)
        assert evaluator._impulse_filename == "MYTEST"
        assert evaluator._impulse_wave_path.name == "MYTEST.wv"

    def test_impulse_filename_auto(self, base_config):
        base_config.impulse_filename_override = None
        evaluator = TransferFunctionEvaluator(base_config)
        assert "IMPULSE_" in evaluator._impulse_filename


# ---------------------------------------------------------------------------
# TransferFunctionEvaluator - helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_freq_range_normal(self, base_config):
        base_config.freq_range_scale = 5.0
        evaluator = TransferFunctionEvaluator(base_config)
        lo, hi = evaluator._compute_freq_range(T1=0.5)  # f1 = 2 Hz
        assert lo == pytest.approx(2.0 / 5.0)
        assert hi == pytest.approx(2.0 * 5.0)

    def test_freq_range_nonpositive_T1_returns_default(self, base_config):
        evaluator = TransferFunctionEvaluator(base_config)
        lo, hi = evaluator._compute_freq_range(T1=0.0)
        assert (lo, hi) == (0.1, 10.0)
        lo, hi = evaluator._compute_freq_range(T1=-1.0)
        assert (lo, hi) == (0.1, 10.0)

    def test_freq_range_scale_floor(self, base_config):
        base_config.freq_range_scale = 0.5  # < 1.01
        evaluator = TransferFunctionEvaluator(base_config)
        lo, hi = evaluator._compute_freq_range(T1=1.0)
        # 内部で scale=1.01 に丸められる
        assert lo == pytest.approx(1.0 / 1.01)
        assert hi == pytest.approx(1.01)

    def test_total_damper_count(self):
        params = {
            "floor_count_F1": 3.0,
            "floor_count_F2": 2.6,
            "other_param": 10.0,
        }
        assert TransferFunctionEvaluator._compute_total_damper_count(params) == 6

    def test_total_damper_count_no_floor_params(self):
        params = {"x": 1.0, "y": 2.0}
        assert TransferFunctionEvaluator._compute_total_damper_count(params) == 0

    def test_cache_key_is_deterministic(self):
        k1 = TransferFunctionEvaluator._make_cache_key({"a": 1.0, "b": 2.0})
        k2 = TransferFunctionEvaluator._make_cache_key({"b": 2.0, "a": 1.0})
        assert k1 == k2

    def test_response_label_rel_disp_top(self, base_config):
        base_config.response_type = RESPONSE_REL_DISP
        base_config.response_floor_index = -1
        evaluator = TransferFunctionEvaluator(base_config)
        label = evaluator._response_label()
        assert "最上階" in label
        assert "相対変位" in label

    def test_response_label_abs_acc_specific_floor(self, base_config):
        base_config.response_type = RESPONSE_ABS_ACC
        base_config.response_floor_index = 3
        evaluator = TransferFunctionEvaluator(base_config)
        label = evaluator._response_label()
        assert "Floor[3]" in label
        assert "絶対加速度" in label


# ---------------------------------------------------------------------------
# __call__ - error path (no SNAP)
# ---------------------------------------------------------------------------

class TestCallErrorHandling:
    def test_returns_inf_on_missing_snap_result(self, base_config):
        """SNAP 実行でエラーになった場合、目的関数値として inf を返す。"""
        base_config.timeout = 1  # 短時間でタイムアウト
        evaluator = TransferFunctionEvaluator(base_config)
        result = evaluator({"x": 1.0})
        assert OBJECTIVE_KEY in result
        assert result[OBJECTIVE_KEY] == float("inf")

    def test_stats_tracks_errors(self, base_config):
        base_config.timeout = 1
        evaluator = TransferFunctionEvaluator(base_config)
        evaluator({"x": 1.0})
        stats = evaluator.stats
        assert stats["total"] == 1
        assert stats["error"] == 1
        assert stats["success"] == 0

    def test_cache_hit(self, base_config):
        base_config.timeout = 1
        evaluator = TransferFunctionEvaluator(base_config)
        evaluator({"x": 1.0})
        evaluator({"x": 1.0})
        # 2回目はキャッシュヒット...ただし失敗はキャッシュしないので、エラーは2回出る
        stats = evaluator.stats
        assert stats["total"] == 2


# ---------------------------------------------------------------------------
# Support file copy
# ---------------------------------------------------------------------------

class TestCopySupportFiles:
    def test_copies_wv_and_wav(self, tmp_path):
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.wv").write_text("wv")
        (src / "b.wav").write_text("wav")
        (src / "c.nap").write_text("nap")
        (src / "d.gem").write_text("gem")
        (src / "e.txt").write_text("txt")

        TransferFunctionEvaluator._copy_support_files(src, dst)

        assert (dst / "a.wv").exists()
        assert (dst / "b.wav").exists()
        assert (dst / "c.nap").exists()
        assert (dst / "d.gem").exists()
        # .txt はコピーされない
        assert not (dst / "e.txt").exists()
