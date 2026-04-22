"""
app/services/transfer_function_evaluator.py
伝達関数ピーク最小化用の SNAP 評価関数。

``SnapEvaluator`` と同じ ``__call__(params) -> Dict[str, float]`` インターフェースを
提供します。最適化ループ内で呼び出され、次の処理を行います:

1. インパルス波（.wv）を生成し、SNAP の wave フォルダに配置
2. ベース .s8i を一時ディレクトリにコピーし、ダンパーパラメータを反映
3. ユーザ選択の DYC ケースをインパルス入力モードに切り替え
4. SNAP を実行
5. Floor.hst から応答時刻歴を取得
6. Period.xbn から 1 次固有周期 T1 を取得
7. インパルス入力と応答のフーリエ振幅比（伝達関数）を計算
8. 周波数帯 ``[1/(scale*T1), scale/T1]`` の最大ゲインを目的関数として返す
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.models.s8i_parser import parse_s8i
from app.services.impulse_wave_writer import (
    DEFAULT_DT,
    DEFAULT_IMPULSE_INDEX,
    DEFAULT_NUM_POINTS,
    ImpulseWaveSpec,
    make_impulse_filename,
    write_impulse_wave,
)
from app.services.transfer_function_service import (
    TransferFunctionResult,
    compute_impulse_transfer_function,
)
from controller.binary.result_loader import SnapResultLoader
from controller.snap_exec import snap_exec

logger = logging.getLogger(__name__)


# Floor.hst のフィールドインデックス
_FLOOR_FIELD_REL_DISP: int = 0   # 相対変位
_FLOOR_FIELD_ABS_ACC: int = 6    # 絶対加速度

RESPONSE_REL_DISP = "rel_disp"
RESPONSE_ABS_ACC = "abs_acc"

#: 目的関数応答キー（最適化側が読むキー名）
OBJECTIVE_KEY = "transfer_function_peak"


@dataclass
class TransferFunctionEvalConfig:
    """伝達関数評価の設定。"""

    # 必須
    snap_exe_path: str
    base_s8i_path: str
    snap_work_dir: str
    snap_wave_dir: str
    target_case_no: int  # 1-indexed, D{N} のN

    # 応答
    response_type: str = RESPONSE_REL_DISP  # "rel_disp" or "abs_acc"
    response_floor_index: int = -1          # Floor.hst レコードインデックス (-1=最上階)

    # インパルス波
    impulse_amax: float = 1000.0             # gal (cm/s^2)
    impulse_dt: float = DEFAULT_DT
    impulse_num_points: int = DEFAULT_NUM_POINTS
    impulse_index: int = DEFAULT_IMPULSE_INDEX
    impulse_filename_override: Optional[str] = None

    # 周波数範囲: [1/(scale*T1), scale/T1]
    freq_range_scale: float = 5.0
    fallback_T1: Optional[float] = None  # Period.xbn が読めないときのフォールバック

    # ダンパーパラメータ反映
    damper_def_name: str = ""
    param_field_map: Dict[str, int] = field(default_factory=dict)
    floor_rd_map: Dict[str, List[int]] = field(default_factory=dict)
    rd_overrides: Dict[str, Any] = field(default_factory=dict)

    # 実行
    timeout: int = 300
    keep_temp_files: bool = False

    def validate(self) -> None:
        if not self.snap_exe_path or not Path(self.snap_exe_path).exists():
            raise FileNotFoundError(f"SNAP.exe が見つかりません: {self.snap_exe_path}")
        if not self.base_s8i_path or not Path(self.base_s8i_path).exists():
            raise FileNotFoundError(f"ベース .s8i が見つかりません: {self.base_s8i_path}")
        if not self.snap_wave_dir:
            raise ValueError("SNAP wave フォルダが未設定です")
        if self.target_case_no <= 0:
            raise ValueError(f"target_case_no は 1 以上: {self.target_case_no}")
        if self.response_type not in (RESPONSE_REL_DISP, RESPONSE_ABS_ACC):
            raise ValueError(
                f"response_type は '{RESPONSE_REL_DISP}' か '{RESPONSE_ABS_ACC}': "
                f"{self.response_type}"
            )


class TransferFunctionEvaluator:
    """伝達関数ピーク最小化用の SNAP 評価関数。

    ``__call__(params)`` で ``{"transfer_function_peak": peak_gain_db}`` を返す。
    エラー時は ``inf`` を返す。
    """

    def __init__(
        self,
        config: TransferFunctionEvalConfig,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        config.validate()
        self.config = config
        self.log_callback = log_callback or (lambda msg: logger.info(msg))

        # 統計
        self._eval_count: int = 0
        self._success_count: int = 0
        self._error_count: int = 0
        self._cache: Dict[str, Dict[str, float]] = {}

        # 装置グループ整合性の事前チェック（警告のみ、ブロックしない）
        # UI 側でもチェックしているが、スクリプト実行やテストから直接
        # 呼ばれた場合でも問題が見えるようにログに残す。
        self._warn_if_damper_group_mismatch()

        # インパルス波を wave フォルダに生成（1回のみ）
        self._impulse_filename = (
            config.impulse_filename_override
            or make_impulse_filename(
                case_id=f"D{config.target_case_no}",
                amax=config.impulse_amax,
            )
        )
        self._impulse_wave_path: Path = self._prepare_impulse_wave()
        self._impulse_signal: np.ndarray = self._build_impulse_signal()

        # 最新結果の保持（UI プロット用）
        self.last_result: Optional[TransferFunctionResult] = None
        self.last_T1: Optional[float] = None

    def _warn_if_damper_group_mismatch(self) -> None:
        """対象ケースの装置グループと damper_def_name の整合性を検査して
        不整合ならログに警告を出す。

        SNAP では DYC.values[5] (ダンパーグループ名) と RD.values[0] が一致
        する装置のみが当該ケースで有効。グループが空、または ``damper_def_name``
        がグループ内の RD に出現しない場合は、どんなに DVOD/DSD 値を
        変更しても応答に反映されないため、最適化が無言で no-op になる。
        """
        if not self.config.damper_def_name:
            return
        try:
            model = parse_s8i(self.config.base_s8i_path)
        except Exception:
            logger.debug("装置グループ検査の為の parse_s8i に失敗", exc_info=True)
            return
        case = model.get_dyc_case(self.config.target_case_no)
        if case is None:
            self.log_callback(
                f"  [WARN] ケース D{self.config.target_case_no} が .s8i に存在しません。"
            )
            return
        group = case.damper_group
        active_defs = model.active_damper_defs_for_case(self.config.target_case_no)
        if not group:
            self.log_callback(
                f"  [WARN] ケース D{self.config.target_case_no} ({case.name}) の"
                f" 装置グループが空欄です。装置 '{self.config.damper_def_name}' の"
                f" パラメータを変更しても応答に反映されません (SNAP: 装置未選択)。"
            )
            return
        if self.config.damper_def_name not in active_defs:
            self.log_callback(
                f"  [WARN] ケース D{self.config.target_case_no} ({case.name}) の"
                f" 装置グループ '{group}' には装置 '{self.config.damper_def_name}'"
                f" が含まれません。パラメータ変更は応答に反映されません。"
                f" このケースで有効な装置: {active_defs}"
            )
            return
        self.log_callback(
            f"  [INFO] 装置グループ整合性 OK: ケース D{self.config.target_case_no}"
            f" ({case.name}), グループ '{group}', 有効装置 {active_defs},"
            f" 最適化対象 '{self.config.damper_def_name}'"
        )

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def __call__(self, params: Dict[str, float]) -> Dict[str, float]:
        self._eval_count += 1
        cache_key = self._make_cache_key(params)
        if cache_key in self._cache:
            self.log_callback(f"  [TFキャッシュヒット] #{self._eval_count}")
            return self._cache[cache_key]

        self.log_callback(f"  [TF評価] #{self._eval_count} params={params}")

        try:
            response = self._run_evaluation(params)
            # 基数パラメータの合計
            total = self._compute_total_damper_count(params)
            if total > 0:
                response["total_damper_count"] = float(total)
            self._success_count += 1
            self._cache[cache_key] = response
            return response
        except Exception as e:
            self._error_count += 1
            self.log_callback(f"  [ERROR] TF評価エラー: {e}")
            logger.exception("TransferFunctionEvaluator error")
            return {OBJECTIVE_KEY: float("inf")}

    # ------------------------------------------------------------------
    # Internal - impulse preparation
    # ------------------------------------------------------------------

    def _prepare_impulse_wave(self) -> Path:
        """インパルス波を wave フォルダに書き出す。"""
        wave_dir = Path(self.config.snap_wave_dir)
        wave_dir.mkdir(parents=True, exist_ok=True)
        out = wave_dir / f"{self._impulse_filename}.wv"
        spec = ImpulseWaveSpec(
            amax=self.config.impulse_amax,
            dt=self.config.impulse_dt,
            num_points=self.config.impulse_num_points,
            impulse_index=self.config.impulse_index,
            filename=self._impulse_filename,
        )
        write_impulse_wave(out, spec)
        self.log_callback(f"  インパルス波書き出し: {out} (amax={self.config.impulse_amax} gal)")
        return out

    def _build_impulse_signal(self) -> np.ndarray:
        """伝達関数計算用のインパルス時系列を生成。"""
        sig = np.zeros(self.config.impulse_num_points, dtype=np.float64)
        sig[self.config.impulse_index] = float(self.config.impulse_amax)
        return sig

    # ------------------------------------------------------------------
    # Internal - main evaluation
    # ------------------------------------------------------------------

    def _run_evaluation(self, params: Dict[str, float]) -> Dict[str, float]:
        tmp_dir = tempfile.mkdtemp(prefix="snap_tf_")
        try:
            tmp_path = Path(tmp_dir)
            src = Path(self.config.base_s8i_path)
            tmp_input = tmp_path / src.name

            self._copy_support_files(src.parent, tmp_path)

            model = parse_s8i(str(src))
            self._reset_dyc_run_flags(model)
            self._apply_damper_def_params(model, params)
            self._apply_floor_count_params(model, params)
            self._apply_rd_overrides(model)

            applied = model.apply_impulse_mode(
                target_case_no=self.config.target_case_no,
                impulse_wave_name=self._impulse_filename,
            )
            if applied is None:
                raise ValueError(
                    f"DYC ケース D{self.config.target_case_no} が見つかりません"
                )

            model.write(str(tmp_input))

            self._execute_snap(tmp_input)

            result_dir = self._find_result_dir(tmp_input)
            return self._compute_tf_response(result_dir)
        finally:
            if not self.config.keep_temp_files:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    @staticmethod
    def _copy_support_files(src_dir: Path, tmp_path: Path) -> None:
        exts = {".nap", ".gem", ".wav", ".wv"}
        for f in src_dir.iterdir():
            if f.is_file() and f.suffix.lower() in exts:
                shutil.copy2(f, tmp_path / f.name)

    @staticmethod
    def _reset_dyc_run_flags(model) -> None:
        for dyc in model.dyc_cases:
            if dyc.run_flag == 2:
                dyc.run_flag = 1
                dyc.values[1] = "1"

    def _apply_damper_def_params(self, model, params: Dict[str, float]) -> None:
        if not self.config.damper_def_name:
            return
        ddef = model.get_damper_def(self.config.damper_def_name)
        if ddef is None:
            raise ValueError(
                f"ダンパー定義 '{self.config.damper_def_name}' が見つかりません"
            )
        if self.config.param_field_map:
            for param_key, field_idx in self.config.param_field_map.items():
                if param_key in params and 0 <= field_idx < len(ddef.values):
                    ddef.values[field_idx] = str(params[param_key])

    def _apply_floor_count_params(self, model, params: Dict[str, float]) -> None:
        if not self.config.floor_rd_map:
            return
        for param_key, value in params.items():
            if not param_key.startswith("floor_count_"):
                continue
            floor_key = param_key[len("floor_count_"):]
            rd_indices = self.config.floor_rd_map.get(floor_key, [])
            if not rd_indices:
                continue
            qty = int(round(value))
            n_rd = len(rd_indices)
            base_qty = qty // n_rd
            remainder = qty % n_rd
            for i, rd_idx in enumerate(rd_indices):
                elem_qty = base_qty + (1 if i < remainder else 0)
                model.update_damper_element(rd_idx, quantity=elem_qty)

    def _apply_rd_overrides(self, model) -> None:
        if not self.config.rd_overrides:
            return
        for row_str, changes in self.config.rd_overrides.items():
            try:
                row_idx = int(row_str)
            except (ValueError, TypeError):
                continue
            model.update_damper_element(
                row_idx,
                node_i=changes.get("node_i"),
                node_j=changes.get("node_j"),
                quantity=changes.get("quantity"),
            )

    def _execute_snap(self, tmp_input: Path) -> None:
        result = snap_exec(
            snap_exe=self.config.snap_exe_path,
            input_file=str(tmp_input),
            timeout=self.config.timeout,
            stdout_callback=lambda line: None,
        )
        if result.returncode != 0:
            raise RuntimeError(f"SNAP 異常終了 (code={result.returncode})")

    def _find_result_dir(self, input_file: Path) -> Path:
        """実行された DYC ケース（target_case_no）の結果フォルダを返す。"""
        s8i_stem = input_file.stem
        if self.config.snap_work_dir:
            model_dir = Path(self.config.snap_work_dir) / s8i_stem
            if model_dir.exists():
                try:
                    dyc_model = parse_s8i(str(input_file))
                    for dyc in dyc_model.dyc_cases:
                        if dyc.is_run:
                            d_folder = model_dir / dyc.folder_name
                            if d_folder.exists() and list(d_folder.glob("Floor*")):
                                return d_folder
                except Exception:
                    logger.debug("DYC解析に失敗（フォールバック）", exc_info=True)

                d_folders = sorted(
                    [
                        d for d in model_dir.iterdir()
                        if d.is_dir() and d.name.startswith("D") and d.name[1:].isdigit()
                    ],
                    key=lambda p: int(p.name[1:]),
                    reverse=True,
                )
                for d_folder in d_folders:
                    if list(d_folder.glob("Floor*")):
                        return d_folder
        raise FileNotFoundError(
            f"結果フォルダが見つかりません: {self.config.snap_work_dir}/{s8i_stem}/D{self.config.target_case_no}"
        )

    # ------------------------------------------------------------------
    # Internal - TF computation
    # ------------------------------------------------------------------

    def _compute_tf_response(self, result_dir: Path) -> Dict[str, float]:
        loader = SnapResultLoader(result_dir, dt=self.config.impulse_dt)
        response_signal, response_dt = self._extract_response_signal(loader)
        T1 = self._extract_T1(loader)
        freq_range = self._compute_freq_range(T1)

        tf = compute_impulse_transfer_function(
            input_signal=self._impulse_signal,
            output_signal=response_signal,
            dt=response_dt,
            freq_range=freq_range,
            input_label="Impulse",
            output_label=self._response_label(),
        )
        self.last_result = tf
        self.last_T1 = T1

        peak_db = tf.peak_gain_db
        self.log_callback(
            f"    TF ピーク: {peak_db:.2f} dB @ {tf.peak_freq:.3f} Hz "
            f"(T1={T1:.3f}s, 範囲={freq_range[0]:.3f}~{freq_range[1]:.3f} Hz)"
        )
        return {
            OBJECTIVE_KEY: float(peak_db),
            "transfer_function_peak_freq": float(tf.peak_freq),
            "first_mode_period": float(T1),
        }

    def _extract_response_signal(
        self, loader: SnapResultLoader
    ) -> Tuple[np.ndarray, float]:
        bc = loader.get("Floor")
        if bc is None or bc.hst is None or bc.hst.header is None:
            raise RuntimeError("Floor.hst を読み込めません")
        hst = bc.hst
        hst.ensure_loaded()
        h = hst.header

        rec = self.config.response_floor_index
        if rec < 0:
            rec = h.num_records + rec
        if not (0 <= rec < h.num_records):
            raise IndexError(
                f"response_floor_index 範囲外: {self.config.response_floor_index} "
                f"(レコード数={h.num_records})"
            )

        field_idx = (
            _FLOOR_FIELD_ABS_ACC
            if self.config.response_type == RESPONSE_ABS_ACC
            else _FLOOR_FIELD_REL_DISP
        )
        signal = hst.time_series(rec, field_idx)
        return np.asarray(signal, dtype=np.float64), float(hst.dt)

    def _extract_T1(self, loader: SnapResultLoader) -> float:
        if loader.period is not None:
            periods = loader.period.periods
            if periods:
                mode_no = min(periods.keys())
                T1 = float(periods[mode_no])
                if T1 > 0:
                    return T1
        if self.config.fallback_T1 is not None and self.config.fallback_T1 > 0:
            return float(self.config.fallback_T1)
        raise RuntimeError(
            "Period.xbn から 1 次周期を取得できません。fallback_T1 を設定してください。"
        )

    def _compute_freq_range(self, T1: float) -> Tuple[float, float]:
        if T1 <= 0:
            return (0.1, 10.0)
        f1 = 1.0 / T1
        scale = max(self.config.freq_range_scale, 1.01)
        return (f1 / scale, f1 * scale)

    def _response_label(self) -> str:
        floor_part = (
            "最上階" if self.config.response_floor_index == -1
            else f"Floor[{self.config.response_floor_index}]"
        )
        resp_part = (
            "絶対加速度" if self.config.response_type == RESPONSE_ABS_ACC
            else "相対変位"
        )
        return f"{floor_part} {resp_part}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_total_damper_count(params: Dict[str, float]) -> int:
        total = 0
        for k, v in params.items():
            if k.startswith("floor_count_"):
                total += int(round(v))
        return total

    @staticmethod
    def _make_cache_key(params: Dict[str, float]) -> str:
        items = sorted(params.items())
        return "|".join(f"{k}={v:.6g}" for k, v in items)

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total": self._eval_count,
            "success": self._success_count,
            "error": self._error_count,
            "cache_hits": self._eval_count - self._success_count - self._error_count,
        }

    def get_stats_text(self) -> str:
        s = self.stats
        return (
            f"TF評価 統計: 合計 {s['total']} 回, "
            f"成功 {s['success']}, エラー {s['error']}, "
            f"キャッシュヒット {s['cache_hits']}"
        )
