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
    ) -> None:
        self.snap_exe_path = snap_exe_path
        self.base_s8i_path = base_s8i_path
        self.damper_def_name = damper_def_name
        self.param_field_map = param_field_map or {}
        self.rd_overrides = rd_overrides or {}
        self.timeout = timeout
        self.log_callback = log_callback or (lambda msg: logger.info(msg))
        self.keep_temp_files = keep_temp_files

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
        with tempfile.TemporaryDirectory(
            prefix="snap_opt_",
            delete=not self.keep_temp_files,
        ) as tmp_dir:
            tmp_path = Path(tmp_dir)
            src = Path(self.base_s8i_path)
            tmp_input = tmp_path / src.name

            # .s8i をパースしてパラメータを変更
            model = parse_s8i(str(src))

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
                                f"{old_val} → {params[param_key]}"
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
                            pass

            # RD 配置の変更（固定オーバーライド）
            if self.rd_overrides:
                for row_str, changes in self.rd_overrides.items():
                    row_idx = int(row_str)
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

            # 出力ファイルを収集
            out_dir = tmp_path / "results"
            out_dir.mkdir(exist_ok=True)
            for f in tmp_path.iterdir():
                if f.suffix.lower() in (".out", ".txt", ".res", ".log"):
                    shutil.copy2(f, out_dir / f.name)

            # 結果パース
            res = Result(str(out_dir))
            return self._extract_response(res)

    def _extract_response(self, res: Result) -> Dict[str, float]:
        """
        Result オブジェクトから応答値辞書を生成。
        AnalysisService._store_summary と同じ形式。
        """
        response: Dict[str, float] = {}

        if res.max_story_drift:
            response["max_drift"] = max(res.max_story_drift.values())
        if res.max_acc:
            response["max_acc"] = max(res.max_acc.values())
        if res.max_disp:
            response["max_disp"] = max(res.max_disp.values())
        if res.max_vel:
            response["max_vel"] = max(res.max_vel.values())
        if res.shear_coeff:
            response["shear_coeff"] = max(res.shear_coeff.values())
        if res.max_otm:
            response["max_otm"] = max(res.max_otm.values())
        if res.max_story_disp:
            response["max_story_disp"] = max(res.max_story_disp.values())

        return response

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
        """パラメータからキャッシュキーを生成。"""
        items = sorted(params.items())
        return "|".join(f"{k}={v}" for k, v in items)

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


def create_snap_evaluator(
    snap_exe_path: str,
    base_case: "AnalysisCase",
    param_ranges: List["ParameterRange"],
    log_callback: Optional[Callable[[str], None]] = None,
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
                        pass
                # または、フィールドインデックス順にマッピング
                for idx_str in override_keys:
                    try:
                        idx = int(idx_str)
                        # ラベルの一部がマッチする場合
                        if pr.key.lower() in str(overrides.get(idx_str, "")).lower():
                            param_field_map[pr.key] = idx
                    except (ValueError, TypeError):
                        pass

    # RD オーバーライドを取得
    rd_overrides = base_case.parameters.get("_rd_overrides", {})

    try:
        evaluator = SnapEvaluator(
            snap_exe_path=exe_path,
            base_s8i_path=model_path,
            damper_def_name=damper_def_name,
            param_field_map=param_field_map,
            rd_overrides=rd_overrides,
            log_callback=log_callback,
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
