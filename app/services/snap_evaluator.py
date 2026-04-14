"""
app/services/snap_evaluator.py
SNAP 実行ベースの評価関数。

最適化ループ内で呼び出され、指定されたダンパーパラメータを
.s8i ファイルに反映し、SNAP を実行して応答値を取得します。

これにより、optimizer.py のモック評価を置き換えて
実際の SNAP 解析結果に基づく最適化が可能になります。

使い方:
    evaluator = SnapEvaluator(
        snap_exe_path="/path/to/SNAP.exe",
        base_s8i_path="/path/to/model.s8i",
        damper_def_name="DVOD1",
        log_callback=print,
    )
    response = evaluator({"Cd": 500.0, "alpha": 0.4})
    # response = {"max_drift": 0.003, "max_acc": 2.5, ...}
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.models.s8i_parser import parse_s8i
from controller.result import Result
from controller.snap_exec import snap_exec

logger = logging.getLogger(__name__)


class SnapEvaluator:
    """
    SNAP 実行による評価関数クラス。

    optimizer の evaluate_fn として使用可能。
    __call__(params) → Dict[str, float] のインターフェースを提供。

    Parameters
    ----------
    snap_exe_path : str
        SNAP.exe のパス。
    base_s8i_path : str
        ベースとなる .s8i 入力ファイルのパス。
    damper_def_name : str
        パラメータを変更するダンパー定義名（例: "DVOD1"）。
    param_field_map : dict, optional
        最適化パラメータキーから .s8i ダンパー定義のフィールドインデックスへの対応。
        例: {"Cd": 0, "alpha": 1} → 最適化の "Cd" 値は damper_def の 0番目フィールドに設定。
        指定がない場合は、ダンパー定義のフィールドを順にマッピングします。
    rd_overrides : dict, optional
        RD（免制振装置配置）の固定変更。最適化では配置を固定してパラメータのみ変更する場合に使用。
    timeout : int
        SNAP 実行のタイムアウト（秒）。デフォルト 300秒。
    log_callback : callable, optional
        ログ出力コールバック。指定しない場合は logging を使用。
    keep_temp_files : bool
        デバッグ用に一時ファイルを保持するかどうか。デフォルト False。
    snap_work_dir : str, optional
        SNAP の work ディレクトリ。SNAP はここに結果を書き出す。
        指定しない場合は tmp ディレクトリ内から結果を検索する。
    """

    def __init__(
        self,
        snap_exe_path: str,
        base_s8i_path: str,
        damper_def_name: str = "",
        param_field_map: Optional[Dict[str, int]] = None,
        rd_overrides: Optional[Dict[str, Any]] = None,
        timeout: int = 300,
        log_callback: Optional[Callable[[str], None]] = None,
        keep_temp_files: bool = False,
        snap_work_dir: str = "",
        floor_rd_map: Optional[Dict[str, List[int]]] = None,
    ) -> None:
        self.snap_exe_path = snap_exe_path
        self.base_s8i_path = base_s8i_path
        self.damper_def_name = damper_def_name
        self.param_field_map = param_field_map or {}
        self.rd_overrides = rd_overrides or {}
        self.timeout = timeout
        self.log_callback = log_callback or (lambda msg: logger.info(msg))
        self.keep_temp_files = keep_temp_files
        self.snap_work_dir = snap_work_dir
        self.floor_rd_map = floor_rd_map or {}

        # 統計情報
        self._eval_count: int = 0
        self._success_count: int = 0
        self._error_count: int = 0

        # キャッシュ（同一パラメータの再計算を回避）
        self._cache: Dict[str, Dict[str, float]] = {}

        # ベースファイルの検証
        if not Path(self.base_s8i_path).exists():
            raise FileNotFoundError(f"ベース .s8i ファイルが見つかりません: {self.base_s8i_path}")
        if not Path(self.snap_exe_path).exists():
            raise FileNotFoundError(f"SNAP.exe が見つかりません: {self.snap_exe_path}")

    def __call__(self, params: Dict[str, float]) -> Dict[str, float]:
        """
        パラメータを .s8i に反映し SNAP を実行して応答値を返す。

        Parameters
        ----------
        params : dict
            最適化パラメータ辞書。例: {"Cd": 500.0, "alpha": 0.4}

        Returns
        -------
        dict
            応答値辞書。例: {"max_drift": 0.003, "max_acc": 2.5, ...}
            エラーの場合は全て inf を返す。
        """
        self._eval_count += 1

        # キャッシュ確認
        cache_key = self._make_cache_key(params)
        if cache_key in self._cache:
            self.log_callback(f"  [キャッシュヒット] #{self._eval_count} params={params}")
            return self._cache[cache_key]

        self.log_callback(
            f"  [SNAP評価] #{self._eval_count} params={params}"
        )

        try:
            response = self._run_snap_evaluation(params)
            # 基数パラメータがある場合は total_damper_count を応答に追加
            total = self._compute_total_damper_count(params)
            if total > 0:
                response["total_damper_count"] = float(total)
            self._success_count += 1
            self._cache[cache_key] = response
            return response
        except Exception as e:
            self._error_count += 1
            self.log_callback(f"  [ERROR] SNAP評価エラー: {e}")
            # エラー時は無限大を返す（この候補は不採用となる）
            return self._error_response()

    def _run_snap_evaluation(self, params: Dict[str, float]) -> Dict[str, float]:
        """
        実際に SNAP を実行して応答値を取得する内部メソッド。
        """
        tmp_dir = tempfile.mkdtemp(prefix="snap_opt_")
        try:
            tmp_path = Path(tmp_dir)
            src = Path(self.base_s8i_path)
            tmp_input = tmp_path / src.name

            # サポートファイル (.NAP, .GEM, .wav 等) を tmp にコピー
            _SUPPORT_EXTS = {".nap", ".gem", ".wav"}
            for f in src.parent.iterdir():
                if f.is_file() and f.suffix.lower() in _SUPPORT_EXTS:
                    shutil.copy2(f, tmp_path / f.name)

            # .s8i をパースしてパラメータを変更
            model = parse_s8i(str(src))

            # DYC ケースの run_flag=2（解析済み）を 1（解析する）にリセット
            for dyc in model.dyc_cases:
                if dyc.run_flag == 2:
                    dyc.run_flag = 1
                    dyc.values[1] = "1"

            # ダンパー定義パラメータの変更
            if self.damper_def_name:
                ddef = model.get_damper_def(self.damper_def_name)
                if ddef is None:
                    raise ValueError(
                        f"ダンパー定義 '{self.damper_def_name}' が見つかりません"
                    )

                if self.param_field_map:
                    # 明示的なマッピングを使用
                    for param_key, field_idx in self.param_field_map.items():
                        if param_key in params:
                            old_val = ddef.values[field_idx] if field_idx < len(ddef.values) else "N/A"
                            ddef.values[field_idx] = str(params[param_key])
                            self.log_callback(
                                f"    {self.damper_def_name}[{field_idx}]: "
                                f"{old_val} -> {params[param_key]}"
                            )
                else:
                    # マッピングなし → パラメータキーをフィールド名として検索
                    for param_key, value in params.items():
                        # フィールドインデックスが数字キーの場合
                        try:
                            idx = int(param_key)
                            if 0 <= idx < len(ddef.values):
                                ddef.values[idx] = str(value)
                        except ValueError:
                            logger.debug("パラメータキーを整数変換できず: %s", param_key)

            # 基数パラメータの適用（floor_count_* → RD要素のquantity）
            self._apply_floor_count_params(model, params)

            # RD 配置の変更（固定オーバーライド）
            if self.rd_overrides:
                for row_str, changes in self.rd_overrides.items():
                    try:
                        row_idx = int(row_str)
                    except (ValueError, TypeError):
                        self.log_callback(f"[WARN] rd_overrides の行番号が不正: {row_str}")
                        continue
                    model.update_damper_element(
                        row_idx,
                        node_i=changes.get("node_i"),
                        node_j=changes.get("node_j"),
                        quantity=changes.get("quantity"),
                    )

            # 変更を書き出し
            model.write(str(tmp_input))

            # SNAP 実行
            result = snap_exec(
                snap_exe=self.snap_exe_path,
                input_file=str(tmp_input),
                timeout=self.timeout,
                stdout_callback=lambda line: None,  # 最適化中は標準出力を抑制
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"SNAP が異常終了しました (code={result.returncode})"
                )

            # 結果フォルダを探す
            # SNAP は snap_work_dir/{s8i_stem}/D{N}/ に結果を書き出す
            result_dir = self._find_result_dir(tmp_input, tmp_path)

            # 結果パース
            res = Result(str(result_dir))
            return self._extract_response(res)
        finally:
            if not self.keep_temp_files:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _apply_floor_count_params(self, model, params: Dict[str, float]) -> None:
        """floor_count_* パラメータを RD 要素の quantity に反映する。

        パラメータ名が 'floor_count_' プレフィックスを持つ場合、
        floor_rd_map を使って対応する RD 要素の quantity を更新する。
        例: floor_count_F3=4 → F3 階の全 RD 要素に合計 4 本を分配。
        """
        if not self.floor_rd_map:
            return

        for param_key, value in params.items():
            if not param_key.startswith("floor_count_"):
                continue
            floor_key = param_key[len("floor_count_"):]
            rd_indices = self.floor_rd_map.get(floor_key, [])
            if not rd_indices:
                self.log_callback(
                    f"    [WARN] floor_rd_map にフロアキー '{floor_key}' がありません"
                )
                continue

            qty = int(round(value))
            n_rd = len(rd_indices)
            base_qty = qty // n_rd
            remainder = qty % n_rd
            for i, rd_idx in enumerate(rd_indices):
                elem_qty = base_qty + (1 if i < remainder else 0)
                model.update_damper_element(rd_idx, quantity=elem_qty)
            self.log_callback(
                f"    {floor_key}: ダンパー本数 {qty} 本 (RD {n_rd} 要素に分配)"
            )

    def _compute_total_damper_count(self, params: Dict[str, float]) -> int:
        """基数パラメータから全階合計ダンパー本数を算出する。"""
        total = 0
        for param_key, value in params.items():
            if param_key.startswith("floor_count_"):
                total += int(round(value))
        return total

    def _find_result_dir(self, input_file: Path, tmp_path: Path) -> Path:
        """SNAP 結果ファイルのディレクトリを探索する。

        検索順序:
        1. snap_work_dir/{s8i_stem}/D{N}/ (run_flag=1 の最初のDYCケース)
        2. snap_work_dir/{s8i_stem}/ 直下の D* フォルダ (最大番号)
        3. tmp ディレクトリ自体 (フォールバック)
        """
        s8i_stem = input_file.stem

        if self.snap_work_dir:
            model_dir = Path(self.snap_work_dir) / s8i_stem
            if model_dir.exists():
                # DYC ケース情報からアクティブな D{N} フォルダを特定
                try:
                    dyc_model = parse_s8i(str(input_file))
                    for dyc in dyc_model.dyc_cases:
                        if dyc.is_run:
                            d_folder = model_dir / dyc.folder_name
                            if d_folder.exists() and list(d_folder.glob("Floor*.txt")):
                                return d_folder
                except Exception as e:
                    logger.debug("DYCケース解析に失敗（フォールバック使用）: %s", e)

                # フォールバック: D* フォルダの最大番号を使用
                d_folders = sorted(
                    [d for d in model_dir.iterdir()
                     if d.is_dir() and d.name.startswith("D") and d.name[1:].isdigit()],
                    key=lambda p: int(p.name[1:]),
                    reverse=True,
                )
                for d_folder in d_folders:
                    if list(d_folder.glob("Floor*.txt")):
                        return d_folder

        # フォールバック: tmp ディレクトリ自体
        return tmp_path

    def _extract_response(self, res: Result) -> Dict[str, float]:
        """
        Result オブジェクトから応答値辞書を生成。
        AnalysisService._store_summary と同じ形式。
        """
        return _extract_minimizer_response(res)

    def _error_response(self) -> Dict[str, float]:
        """エラー時に返すデフォルト応答値（全て inf）。"""
        return {
            "max_drift": float("inf"),
            "max_acc": float("inf"),
            "max_disp": float("inf"),
            "max_vel": float("inf"),
            "shear_coeff": float("inf"),
            "max_otm": float("inf"),
            "max_story_disp": float("inf"),
        }

    @staticmethod
    def _make_cache_key(params: Dict[str, float]) -> str:
        """パラメータからキャッシュキーを生成。

        浮動小数点の表現差異によるキャッシュミスを防ぐため、
        値を有効数字6桁に丸めてからキーを生成します。
        """
        items = sorted(params.items())
        return "|".join(f"{k}={v:.6g}" for k, v in items)

    @property
    def stats(self) -> Dict[str, int]:
        """評価統計情報。"""
        return {
            "total": self._eval_count,
            "success": self._success_count,
            "error": self._error_count,
            "cache_hits": self._eval_count - self._success_count - self._error_count,
        }

    def get_stats_text(self) -> str:
        """統計情報のテキスト表示。"""
        s = self.stats
        return (
            f"SNAP評価 統計: 合計 {s['total']} 回, "
            f"成功 {s['success']}, エラー {s['error']}, "
            f"キャッシュヒット {s['cache_hits']}"
        )


class MultiWaveEvaluator:
    """
    複数地震波のエンベロープ評価関数。

    複数の SnapEvaluator（各波形に対応）を保持し、
    全波形の応答値の最大値（エンベロープ）を返す。
    構造設計では全波形でクリティカルな応答を制約充足する必要があるため、
    このラッパーにより複数波同時最適化が実現できる。

    Parameters
    ----------
    evaluators : list of (wave_name, SnapEvaluator)
        波形名とSnapEvaluatorのペアリスト。
    aggregation : str
        応答集約方法。"max"=最大値（保守側）, "mean"=平均値。デフォルト "max"。
    log_callback : callable, optional
        ログコールバック。
    """

    def __init__(
        self,
        evaluators: List[tuple],
        aggregation: str = "max",
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        if not evaluators:
            raise ValueError("evaluators は1つ以上必要です")
        self.evaluators = evaluators  # [(wave_name, SnapEvaluator), ...]
        self.aggregation = aggregation
        self.log_callback = log_callback or (lambda msg: logger.info(msg))
        self._eval_count = 0
        self._last_per_wave: Dict[str, Dict[str, float]] = {}

    def __call__(self, params: Dict[str, float]) -> Dict[str, float]:
        """全波形で評価し、エンベロープ応答を返す。"""
        self._eval_count += 1
        self.log_callback(
            f"  [多波評価] #{self._eval_count} ({len(self.evaluators)}波) params={params}"
        )

        per_wave: Dict[str, Dict[str, float]] = {}
        all_responses: List[Dict[str, float]] = []

        for wave_name, evaluator in self.evaluators:
            resp = evaluator(params)
            per_wave[wave_name] = resp
            all_responses.append(resp)

        self._last_per_wave = per_wave

        if not all_responses:
            return self._error_response()

        # エンベロープ（各応答キーごとに集約）
        envelope: Dict[str, float] = {}
        all_keys = set()
        for resp in all_responses:
            all_keys.update(resp.keys())

        for key in all_keys:
            values = [resp.get(key, float("inf")) for resp in all_responses]
            if self.aggregation == "mean":
                finite_vals = [v for v in values if v != float("inf")]
                envelope[key] = sum(finite_vals) / len(finite_vals) if finite_vals else float("inf")
            else:  # "max" (default, conservative)
                envelope[key] = max(values)

        # クリティカル波形をログ出力
        for key in ["max_drift", "max_acc", "max_disp"]:
            if key in envelope and envelope[key] != float("inf"):
                critical_wave = max(
                    per_wave.items(),
                    key=lambda wv: wv[1].get(key, 0.0),
                )[0]
                self.log_callback(
                    f"    {key}: {envelope[key]:.4g} (クリティカル: {critical_wave})"
                )

        return envelope

    @property
    def last_per_wave_results(self) -> Dict[str, Dict[str, float]]:
        """直近の評価における各波形ごとの応答値。"""
        return dict(self._last_per_wave)

    @property
    def stats(self) -> Dict[str, Any]:
        """全evaluatorの統計を集約。"""
        total_stats: Dict[str, int] = {"total": 0, "success": 0, "error": 0, "cache_hits": 0}
        per_wave_stats: Dict[str, Dict[str, int]] = {}
        for wave_name, evaluator in self.evaluators:
            s = evaluator.stats
            for k in total_stats:
                total_stats[k] += s.get(k, 0)
            per_wave_stats[wave_name] = s
        return {
            **total_stats,
            "n_waves": len(self.evaluators),
            "per_wave": per_wave_stats,
            "aggregation": self.aggregation,
        }

    def get_stats_text(self) -> str:
        """統計情報のテキスト表示。"""
        s = self.stats
        lines = [
            f"多波SNAP評価 ({s['n_waves']}波, {s['aggregation']}): "
            f"合計 {s['total']} 回, 成功 {s['success']}, エラー {s['error']}, "
            f"キャッシュ {s['cache_hits']}",
        ]
        for wave_name, ws in s.get("per_wave", {}).items():
            lines.append(
                f"  {wave_name}: {ws['total']}回 (成功{ws['success']}, "
                f"エラー{ws['error']}, キャッシュ{ws['cache_hits']})"
            )
        return "\n".join(lines)

    @staticmethod
    def _error_response() -> Dict[str, float]:
        return {
            "max_drift": float("inf"),
            "max_acc": float("inf"),
            "max_disp": float("inf"),
            "max_vel": float("inf"),
            "shear_coeff": float("inf"),
            "max_otm": float("inf"),
            "max_story_disp": float("inf"),
        }


def create_snap_evaluator(
    snap_exe_path: str,
    base_case: "AnalysisCase",
    param_ranges: List["ParameterRange"],
    log_callback: Optional[Callable[[str], None]] = None,
    snap_work_dir: str = "",
    timeout: int = 300,
) -> Optional[SnapEvaluator]:
    """
    AnalysisCase と ParameterRange リストから SnapEvaluator を構築するヘルパー。

    Parameters
    ----------
    snap_exe_path : str
        SNAP.exe のパス。
    base_case : AnalysisCase
        ベースとなる解析ケース。
    param_ranges : list of ParameterRange
        最適化パラメータ範囲リスト。
    log_callback : callable, optional
        ログコールバック。

    Returns
    -------
    SnapEvaluator or None
        生成に成功した場合は SnapEvaluator、失敗時は None。
    """
    from app.models import AnalysisCase
    from app.services.optimizer import ParameterRange

    exe_path = snap_exe_path or base_case.snap_exe_path
    model_path = base_case.model_path

    if not exe_path or not model_path:
        if log_callback:
            log_callback("[WARN] SNAP.exe またはモデルパスが未設定です。モック評価を使用します。")
        return None

    if not Path(exe_path).exists():
        if log_callback:
            log_callback(f"[WARN] SNAP.exe が見つかりません: {exe_path}。モック評価を使用します。")
        return None

    if not Path(model_path).exists():
        if log_callback:
            log_callback(f"[WARN] モデルファイルが見つかりません: {model_path}。モック評価を使用します。")
        return None

    # ダンパー定義名を推定
    damper_def_name = ""
    if base_case.damper_params:
        # damper_params の最初のキーをダンパー定義名として使用
        for key in base_case.damper_params:
            damper_def_name = key
            break

    # param_field_map: param_ranges のキーから damper_params のフィールドインデックスへ
    param_field_map: Dict[str, int] = {}
    if damper_def_name and damper_def_name in (base_case.damper_params or {}):
        overrides = base_case.damper_params[damper_def_name]
        if isinstance(overrides, dict):
            # overrides: {field_idx_str: value}
            # param_ranges のキーが overrides のキーに一致するものを探す
            override_keys = list(overrides.keys())
            for pr in param_ranges:
                # パラメータキーがフィールドインデックスの場合
                if pr.key in override_keys:
                    try:
                        param_field_map[pr.key] = int(pr.key)
                    except ValueError:
                        logger.debug("パラメータキーを整数変換できず: %s", pr.key)
                # または、フィールドインデックス順にマッピング
                for idx_str in override_keys:
                    try:
                        idx = int(idx_str)
                        # ラベルの一部がマッチする場合
                        if pr.key.lower() in str(overrides.get(idx_str, "")).lower():
                            param_field_map[pr.key] = idx
                    except (ValueError, TypeError):
                        logger.debug("overrideキー変換失敗: %s", idx_str)

    # RD オーバーライドを取得
    rd_overrides = base_case.parameters.get("_rd_overrides", {})

    try:
        evaluator = SnapEvaluator(
            snap_exe_path=exe_path,
            base_s8i_path=model_path,
            damper_def_name=damper_def_name,
            param_field_map=param_field_map,
            rd_overrides=rd_overrides,
            timeout=timeout,
            log_callback=log_callback,
            snap_work_dir=snap_work_dir,
        )
        if log_callback:
            log_callback(
                f"[INFO] SNAP評価モードで最適化を開始します。\n"
                f"  SNAP: {exe_path}\n"
                f"  モデル: {model_path}\n"
                f"  ダンパー定義: {damper_def_name or '(なし)'}\n"
                f"  パラメータマッピング: {param_field_map or '(自動)'}"
            )
        return evaluator
    except FileNotFoundError as e:
        if log_callback:
            log_callback(f"[WARN] {e}。モック評価を使用します。")
        return None


def create_unified_evaluator(
    snap_exe_path: str,
    base_case: "AnalysisCase",
    param_ranges: List["ParameterRange"],
    log_callback: Optional[Callable[[str], None]] = None,
    snap_work_dir: str = "",
    timeout: int = 300,
    damper_def_name: str = "",
) -> Optional[SnapEvaluator]:
    """
    統合最適化用の SnapEvaluator を構築するファクトリ関数。

    物理パラメータ（Cd, alpha 等）と基数パラメータ（floor_count_F3 等）を
    同時に扱える SnapEvaluator を返す。

    基数パラメータ（is_floor_count=True の ParameterRange）がある場合、
    build_floor_rd_map() でフロア→RD要素マッピングを自動構築し、
    SnapEvaluator に渡す。

    Parameters
    ----------
    snap_exe_path : str
        SNAP.exe のパス。
    base_case : AnalysisCase
        ベースとなる解析ケース。
    param_ranges : list of ParameterRange
        最適化パラメータ範囲リスト（物理パラメータ + 基数パラメータ混在可）。
    log_callback : callable, optional
        ログコールバック。
    snap_work_dir : str
        SNAP 作業ディレクトリ。
    timeout : int
        SNAP タイムアウト（秒）。

    Returns
    -------
    SnapEvaluator or None
        生成に成功した場合は SnapEvaluator、失敗時は None。
    """
    from app.models import AnalysisCase
    from app.services.optimizer import ParameterRange

    exe_path = snap_exe_path or base_case.snap_exe_path
    model_path = base_case.model_path

    if not _validate_unified_paths(exe_path, model_path, log_callback):
        return None

    phys_ranges = [pr for pr in param_ranges if not pr.is_floor_count]
    count_ranges = [pr for pr in param_ranges if pr.is_floor_count]

    damper_def_name = _resolve_damper_def_name(damper_def_name, base_case)
    param_field_map = _build_param_field_map(phys_ranges, damper_def_name, base_case)
    floor_rd_map = _build_floor_rd_map_for_counts(count_ranges, model_path, log_callback)
    rd_overrides = base_case.parameters.get("_rd_overrides", {})

    try:
        evaluator = SnapEvaluator(
            snap_exe_path=exe_path,
            base_s8i_path=model_path,
            damper_def_name=damper_def_name,
            param_field_map=param_field_map,
            rd_overrides=rd_overrides,
            timeout=timeout,
            log_callback=log_callback,
            snap_work_dir=snap_work_dir,
            floor_rd_map=floor_rd_map,
        )
        if log_callback:
            log_callback(
                f"[INFO] 統合評価モードで最適化を開始します。\n"
                f"  SNAP: {exe_path}\n"
                f"  モデル: {model_path}\n"
                f"  ダンパー定義: {damper_def_name or '(なし)'}\n"
                f"  物理パラメータ: {[pr.key for pr in phys_ranges]}\n"
                f"  基数パラメータ: {[pr.key for pr in count_ranges]}\n"
                f"  パラメータマッピング: {param_field_map or '(自動)'}"
            )
        return evaluator
    except FileNotFoundError as e:
        if log_callback:
            log_callback(f"[WARN] {e}。モック評価を使用します。")
        return None


def _validate_unified_paths(
    exe_path: str, model_path: str, log_callback: Optional[Callable[[str], None]]
) -> bool:
    if not exe_path or not model_path:
        if log_callback:
            log_callback("[WARN] SNAP.exe またはモデルパスが未設定です。モック評価を使用します。")
        return False
    if not Path(exe_path).exists():
        if log_callback:
            log_callback(f"[WARN] SNAP.exe が見つかりません: {exe_path}。モック評価を使用します。")
        return False
    if not Path(model_path).exists():
        if log_callback:
            log_callback(f"[WARN] モデルファイルが見つかりません: {model_path}。モック評価を使用します。")
        return False
    return True


def _resolve_damper_def_name(damper_def_name: str, base_case: "AnalysisCase") -> str:
    if damper_def_name:
        return damper_def_name
    if base_case.damper_params:
        for key in base_case.damper_params:
            return key
    return damper_def_name


def _build_param_field_map(
    phys_ranges: List["ParameterRange"], damper_def_name: str, base_case: "AnalysisCase"
) -> Dict[str, int]:
    param_field_map: Dict[str, int] = {}

    for pr in phys_ranges:
        if pr.key.startswith("field_"):
            try:
                idx = int(pr.key.replace("field_", ""))
                param_field_map[pr.key] = idx
            except ValueError:
                logger.debug("パラメータキーを整数変換できず: %s", pr.key)

    if damper_def_name and damper_def_name in (base_case.damper_params or {}):
        overrides = base_case.damper_params[damper_def_name]
        if isinstance(overrides, dict):
            override_keys = list(overrides.keys())
            for pr in phys_ranges:
                if pr.key in override_keys and pr.key not in param_field_map:
                    try:
                        param_field_map[pr.key] = int(pr.key)
                    except ValueError:
                        logger.debug("パラメータキーを整数変換できず: %s", pr.key)
                for idx_str in override_keys:
                    try:
                        idx = int(idx_str)
                        if pr.key.lower() in str(overrides.get(idx_str, "")).lower():
                            if pr.key not in param_field_map:
                                param_field_map[pr.key] = idx
                    except (ValueError, TypeError):
                        logger.debug("overrideキー変換失敗: %s", idx_str)
    return param_field_map


def _build_floor_rd_map_for_counts(
    count_ranges: List["ParameterRange"],
    model_path: str,
    log_callback: Optional[Callable[[str], None]],
) -> Dict[str, List[int]]:
    if not count_ranges:
        return {}
    try:
        floor_rd_map_full, _, _ = build_floor_rd_map(model_path)
        if log_callback:
            log_callback(
                f"[INFO] 基数パラメータ {len(count_ranges)} 個検出。"
                f"floor_rd_map: {list(floor_rd_map_full.keys())}"
            )
        return floor_rd_map_full
    except Exception as e:
        if log_callback:
            log_callback(f"[WARN] floor_rd_map 構築に失敗: {e}")
        return {}


def _normalize_floor_key(z_grid: str) -> str:
    """z_grid をフロアキーに正規化 (例: "2" → "F2", "Z3" → "F3")。"""
    z = str(z_grid).strip()
    try:
        return f"F{int(z)}"
    except (ValueError, TypeError):
        digits = "".join(c for c in z if c.isdigit())
        return f"F{digits}" if digits else f"F{z}"


def build_floor_rd_map(
    base_s8i_path: str,
) -> "Tuple[Dict[str, List[int]], Dict[str, int], List[str]]":
    """
    .s8i ファイルからフロアキー→RD要素インデックスのマッピングを構築する。

    ダンパーノードの z_grid が設定されている場合はそれを使い、
    空の場合は z 座標からどの層間（ストーリー）に属するかを推定する。

    Returns
    -------
    floor_rd_map : Dict[str, List[int]]
        フロアキー → RD要素インデックスリスト
    current_quantities : Dict[str, int]
        各フロアの現在のダンパー合計本数
    floor_keys : List[str]
        フロアキーの昇順リスト
    """
    from collections import defaultdict
    import bisect

    model = parse_s8i(base_s8i_path)

    # z_grid → z 座標のマッピングを構築（フロアレベル推定用）
    z_levels: Dict[float, str] = {}  # z座標 → z_grid名
    for node in model.nodes.values():
        if node.z_grid and node.z is not None:
            z_levels[node.z] = node.z_grid

    # z座標を昇順ソート（層境界として使用）
    sorted_z = sorted(z_levels.keys())

    floor_rd_map: Dict[str, List[int]] = defaultdict(list)
    floor_qty: Dict[str, int] = defaultdict(int)

    for idx, elem in enumerate(model.damper_elements):
        node_j = model.nodes.get(elem.node_j)
        floor_key = None

        if node_j and node_j.z_grid:
            # z_grid が直接設定されている場合
            floor_key = _normalize_floor_key(node_j.z_grid)
        elif node_j and node_j.z is not None and sorted_z:
            # z座標からどの層間に属するかを推定
            # ダンパーの z がフロアレベル z_i と z_{i+1} の間にある場合、
            # そのストーリー（i番目の層間 = F{i}）に属するとする
            z = node_j.z
            pos = bisect.bisect_right(sorted_z, z)
            if pos == 0:
                # 最下層レベルより下 → F1
                story_idx = 1
            elif pos >= len(sorted_z):
                # 最上層レベルより上 → 最上層
                story_idx = len(sorted_z) - 1
            else:
                # sorted_z[pos-1] <= z < sorted_z[pos] → ストーリー pos
                story_idx = pos
            floor_key = f"F{story_idx}"

        if floor_key is None:
            floor_key = f"RD{idx}"

        floor_rd_map[floor_key].append(idx)
        floor_qty[floor_key] += max(1, elem.quantity)

    # フロアキーをソート（F1, F2, ... の順）
    def sort_key(k):
        digits = "".join(c for c in k if c.isdigit())
        return int(digits) if digits else 0

    floor_keys = sorted(floor_rd_map.keys(), key=sort_key)
    return dict(floor_rd_map), dict(floor_qty), floor_keys


def create_minimizer_evaluate_fn(
    snap_exe_path: str,
    base_s8i_path: str,
    criteria: "PerformanceCriteria",
    floor_rd_map: Dict[str, List[int]],
    timeout: int = 300,
    log_callback: Optional[Callable[[str], None]] = None,
    snap_work_dir: str = "",
) -> "Optional[Callable]":
    """
    ダンパー本数最小化用の evaluate_fn を生成するファクトリ関数。

    新インターフェース:
        evaluate_fn(quantities: Dict[str, int]) -> EvaluationResult

    Parameters
    ----------
    snap_exe_path : str
        SNAP.exe のパス。
    base_s8i_path : str
        ベースとなる .s8i ファイルパス。
    criteria : PerformanceCriteria
        判定に使う性能基準。
    floor_rd_map : Dict[str, List[int]]
        フロアキー → RD要素インデックスリスト (build_floor_rd_map() で取得)。
    timeout : int
        SNAP タイムアウト（秒）。
    log_callback : callable, optional
        ログコールバック。
    snap_work_dir : str
        SNAP作業ディレクトリ。

    Returns
    -------
    callable or None
        evaluate_fn。SNAP が利用不可の場合は None。
    """
    from app.services.damper_count_minimizer import EvaluationResult

    if not snap_exe_path or not Path(snap_exe_path).exists():
        if log_callback:
            log_callback(f"[WARN] SNAP.exe が見つかりません: {snap_exe_path}")
        return None
    if not base_s8i_path or not Path(base_s8i_path).exists():
        if log_callback:
            log_callback(f"[WARN] ベース .s8i が見つかりません: {base_s8i_path}")
        return None

    _eval_count = [0]

    def evaluate_fn(quantities: Dict[str, int]) -> EvaluationResult:
        _eval_count[0] += 1
        total = sum(quantities.values())
        if log_callback:
            log_callback(
                f"  [最小化評価] #{_eval_count[0]} 合計本数={total}"
            )

        try:
            model = _prepare_minimizer_model(
                base_s8i_path, quantities, floor_rd_map
            )

            res, summary = _run_snap_in_tmpdir(
                model, base_s8i_path, snap_exe_path, snap_work_dir,
                timeout, _eval_count[0],
            )

            is_feasible = criteria.is_all_pass(summary) is True
            margin = _compute_margin(summary, criteria)
            floor_responses = _build_floor_responses(
                quantities, res, criteria
            )

            return EvaluationResult(
                floor_responses=floor_responses,
                total_count=total,
                is_feasible=is_feasible,
                worst_margin=margin,
                summary=summary,
            )

        except Exception as e:
            if log_callback:
                log_callback(f"  [ERROR] 最小化評価エラー: {e}")
            return EvaluationResult(
                total_count=total,
                is_feasible=False,
                worst_margin=-1.0,
            )

    return evaluate_fn


def _prepare_minimizer_model(
    base_s8i_path: str,
    quantities: Dict[str, int],
    floor_rd_map: Dict[str, List[int]],
):
    """ベース .s8i をパースし、フロア別本数を RD 要素に適用したモデルを返します。"""
    model = parse_s8i(base_s8i_path)

    # DYC run_flag=2 → 1 リセット
    for dyc in model.dyc_cases:
        if dyc.run_flag == 2:
            dyc.run_flag = 1
            dyc.values[1] = "1"

    # フロアキーに基づいてRD要素のquantityを設定
    for floor_key, rd_indices in floor_rd_map.items():
        qty = quantities.get(floor_key, 0)
        n_rd = len(rd_indices)
        if n_rd == 0:
            continue
        base_qty = qty // n_rd
        remainder = qty % n_rd
        for i, rd_idx in enumerate(rd_indices):
            elem_qty = base_qty + (1 if i < remainder else 0)
            model.update_damper_element(rd_idx, quantity=elem_qty)

    return model


def _run_snap_in_tmpdir(
    model, base_s8i_path: str, snap_exe_path: str, snap_work_dir: str,
    timeout: int, eval_count: int,
) -> tuple:
    """一時ディレクトリで SNAP を実行し、(Result, summary_dict) を返します。"""
    tmp_dir = tempfile.mkdtemp(prefix="snap_min_")
    src = Path(base_s8i_path)
    unique_stem = f"{src.stem}_min{eval_count}"
    try:
        tmp_path = Path(tmp_dir)
        tmp_input = tmp_path / f"{unique_stem}{src.suffix}"
        model.write(str(tmp_input))

        # サポートファイル (.NAP, .GEM 等) をコピー
        for sf in src.parent.iterdir():
            if sf.is_file() and sf.suffix.lower() in {".nap", ".gem", ".wav"}:
                if sf.stem == src.stem:
                    dest = tmp_path / f"{unique_stem}{sf.suffix}"
                else:
                    dest = tmp_path / sf.name
                shutil.copy2(sf, dest)

        result = snap_exec(
            snap_exe=snap_exe_path,
            input_file=str(tmp_input),
            timeout=timeout,
            stdout_callback=lambda line: None,
        )

        if result.returncode != 0:
            raise RuntimeError(f"SNAP 異常終了 (code={result.returncode})")

        result_dir = _find_minimizer_result_dir(
            tmp_input, tmp_path, snap_work_dir
        )
        res = Result(str(result_dir))
        summary = _extract_minimizer_response(res)
        return res, summary
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if snap_work_dir:
            work_result = Path(snap_work_dir) / unique_stem
            if work_result.exists():
                shutil.rmtree(str(work_result), ignore_errors=True)


def _build_floor_responses(
    quantities: Dict[str, int], res: Result, criteria,
) -> list:
    """層別応答リストを構築します。"""
    from app.services.damper_count_minimizer import FloorResponse

    floor_responses = []
    sorted_floor_keys = sorted(
        quantities.keys(),
        key=lambda k: int("".join(c for c in k if c.isdigit()) or "0"),
    )
    for i, fk in enumerate(sorted_floor_keys):
        floor_no = i + 1
        values: Dict[str, float] = {}
        _floor_fields = [
            ("max_drift", res.max_story_drift),
            ("max_acc", res.max_acc),
            ("max_disp", res.max_disp),
            ("max_story_disp", res.max_story_disp),
        ]
        for fkey, fdict in _floor_fields:
            if fdict and floor_no in fdict:
                v = fdict[floor_no]
                if v is not None:
                    values[fkey] = v
        for item in criteria.items:
            if not item.enabled or item.limit_value is None:
                continue
            val = values.get(item.key)
            if val is not None and item.limit_value != 0:
                m = (item.limit_value - val) / abs(item.limit_value)
                values[f"margin_{item.key}"] = m

        floor_responses.append(FloorResponse(
            floor_key=fk,
            values=values,
            damper_count=quantities.get(fk, 0),
        ))
    return floor_responses


def _find_minimizer_result_dir(
    input_file: Path, tmp_path: Path, snap_work_dir: str
) -> Path:
    """最小化評価用の結果ディレクトリ探索。SnapEvaluator._find_result_dir と同等。"""
    s8i_stem = input_file.stem
    if snap_work_dir:
        model_dir = Path(snap_work_dir) / s8i_stem
        if model_dir.exists():
            try:
                dyc_model = parse_s8i(str(input_file))
                for dyc in dyc_model.dyc_cases:
                    if dyc.is_run:
                        d_folder = model_dir / dyc.folder_name
                        if d_folder.exists() and list(d_folder.glob("Floor*.txt")):
                            return d_folder
            except Exception as e:
                logger.debug("DYCケース解析に失敗（フォールバック使用）: %s", e)
            d_folders = sorted(
                [d for d in model_dir.iterdir()
                 if d.is_dir() and d.name.startswith("D") and d.name[1:].isdigit()],
                key=lambda p: int(p.name[1:]),
                reverse=True,
            )
            for d_folder in d_folders:
                if list(d_folder.glob("Floor*.txt")):
                    return d_folder
    return tmp_path


def _safe_dict_max(d: Optional[Dict]) -> Optional[float]:
    """辞書の値の最大値を安全に取得。空辞書・Noneはスキップ。"""
    if d and d.values():
        vals = [v for v in d.values() if v is not None]
        return max(vals) if vals else None
    return None


def _extract_minimizer_response(res: Result) -> Dict[str, float]:
    """Result から応答値辞書を生成（SnapEvaluator._extract_response と同等）。"""
    response: Dict[str, float] = {}
    _fields = [
        ("max_drift", res.max_story_drift),
        ("max_acc", res.max_acc),
        ("max_disp", res.max_disp),
        ("max_vel", res.max_vel),
        ("shear_coeff", res.shear_coeff),
        ("max_otm", res.max_otm),
        ("max_story_disp", res.max_story_disp),
    ]
    for key, d in _fields:
        val = _safe_dict_max(d)
        if val is not None:
            response[key] = val
    return response


def _compute_margin(
    summary: Dict[str, float],
    criteria: "PerformanceCriteria",
) -> float:
    """
    基準に対する最小マージンを計算する。

    margin = min((limit - value) / limit) for each enabled criterion.
    正の値: 基準内（余裕あり）、負の値: 基準超過。
    """
    margins = []
    for item in criteria.items:
        if not item.enabled or item.limit_value is None:
            continue
        val = summary.get(item.key)
        if val is None:
            continue
        if item.limit_value == 0:
            margins.append(-abs(val))
        else:
            margins.append((item.limit_value - val) / abs(item.limit_value))
    if not margins:
        return 0.0
    return min(margins)
