"""
app/services/analysis_service.py
解析実行サービス。

QThread を使ってバックグラウンドで SNAP を実行します。
実行中もUIはレスポンスを保ちます。
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from PySide6.QtCore import QObject, QThread, Signal

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.s8i_parser import parse_s8i
from controller import Result
from controller.snap_exec import snap_exec

# デモ用のモック階数デフォルト値
_MOCK_FLOORS = 5


def _sanitize_for_filename(name: str) -> str:
    """ファイル名・フォルダ名として安全な文字列に変換します。

    Windows の禁則文字 (``\\ / : * ? " < > |``) と空白・タブを
    アンダースコアに置換します。長さも制限 (48 文字) します。
    """
    if not name:
        return "case"
    forbidden = '\\/:*?"<>|\t\r\n '
    out_chars: list = []
    for ch in name:
        if ch in forbidden:
            out_chars.append("_")
        else:
            out_chars.append(ch)
    safe = "".join(out_chars).strip("._")
    if not safe:
        safe = "case"
    return safe[:48]


class _AnalysisWorker(QThread):
    """1ケースを実行する QThread ワーカー。"""

    log_emitted = Signal(str)           # ログ行
    case_finished = Signal(str, bool)   # (case_id, success)
    status_changed = Signal(str)        # ステータスバー用メッセージ

    def __init__(self, case: AnalysisCase, snap_exe_path: str, snap_work_dir: str = "",
                 dyd_overrides: Optional[Dict[int, int]] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._case = case
        self._snap_exe_path = snap_exe_path
        self._snap_work_dir = snap_work_dir
        self._dyd_overrides = dyd_overrides

    def run(self) -> None:
        case = self._case
        case.status = AnalysisCaseStatus.RUNNING
        self.status_changed.emit(f"実行中: {case.name}")
        self.log_emitted.emit(f"=== 解析開始: {case.name} ===")

        try:
            src = Path(case.model_path)
            if not src.exists():
                raise FileNotFoundError(f"入力ファイルが見つかりません: {src}")

            out_dir, run_input = self._prepare_input(case, src)
            self._execute_snap(run_input)
            main_result_set = self._parse_results(case, run_input, out_dir)
            self._log_summary(case, main_result_set)

            case.status = AnalysisCaseStatus.COMPLETED
            self.log_emitted.emit(f"=== 解析完了: {case.name} (終了コード {case.return_code}) ===")
            self.status_changed.emit(f"完了: {case.name}")
            self.case_finished.emit(case.id, True)

        except Exception as e:
            case.status = AnalysisCaseStatus.ERROR
            self.log_emitted.emit(f"[ERROR] {e}")
            import traceback
            self.log_emitted.emit(traceback.format_exc())
            self.status_changed.emit(f"エラー: {case.name}")
            self.case_finished.emit(case.id, False)

    def _prepare_input(self, case: AnalysisCase, src: Path) -> tuple:
        """出力ディレクトリ作成・サポートファイルコピー・パラメータ適用を行います。

        Returns
        -------
        tuple of (out_dir, run_input)
        """
        # ---- 出力ディレクトリの決定・作成 ----
        out_dir = Path(case.output_dir) if case.output_dir else src.parent / case.name
        out_dir.mkdir(parents=True, exist_ok=True)
        case.result_path = str(out_dir)

        # ---- サポートファイルを out_dir にコピー ----
        _SUPPORT_EXTS = {".nap", ".gem", ".wav", ".txt"}
        for f in src.parent.iterdir():
            if f.is_file() and f.suffix.lower() in _SUPPORT_EXTS and f != src:
                dest = out_dir / f.name
                if not dest.exists():
                    shutil.copy2(f, dest)

        # ---- 入力ファイルを out_dir に配置 ----
        safe_case_name = _sanitize_for_filename(case.name or case.id[:8])
        run_input_name = f"{src.stem}__{safe_case_name}{src.suffix}"
        run_input = out_dir / run_input_name

        has_overrides = bool(case.damper_params) or bool(
            case.parameters.get("_rd_overrides")
        ) or bool(getattr(case, "extra_defs", None)) or bool(self._dyd_overrides)

        if has_overrides:
            model = parse_s8i(str(src))
            self._apply_damper_overrides(case, model)
            self._apply_extra_defs(case, model)
            self._apply_rd_overrides(case, model)
            self._apply_dyd_overrides(model)
            model.write(str(run_input))
            self.log_emitted.emit("  パラメータ変更を適用しました")
        else:
            shutil.copy2(src, run_input)

        self.log_emitted.emit(f"入力ファイル: {run_input}")
        self.log_emitted.emit(f"出力ディレクトリ (CWD): {out_dir}")
        self.log_emitted.emit(f"SNAP.exe: {self._snap_exe_path}")
        return out_dir, run_input

    def _apply_damper_overrides(self, case: AnalysisCase, model) -> None:
        """ダンパー定義パラメータの上書きを適用します。"""
        if not case.damper_params:
            return
        for def_name, overrides in case.damper_params.items():
            ddef = model.get_damper_def(def_name)
            if ddef is None:
                self.log_emitted.emit(
                    f"  [WARN] ダンパー定義 '{def_name}' が見つかりません"
                )
                continue
            for idx_str, new_val in overrides.items():
                idx = int(idx_str) - 1  # damper_params は 1-indexed
                if 0 <= idx < len(ddef.values):
                    old_val = ddef.values[idx]
                    ddef.values[idx] = str(new_val)
                    self.log_emitted.emit(
                        f"  {def_name}[{idx + 1}]: {old_val} → {new_val}"
                    )

    def _apply_extra_defs(self, case: AnalysisCase, model) -> None:
        """追加ダンパー定義（コピー元から派生 or 新規作成）を適用します。"""
        for edef in getattr(case, "extra_defs", []):
            base_name = edef.get("base_name", "")
            new_name = edef.get("name", "")
            keyword = edef.get("keyword", "")
            overrides = edef.get("overrides", {})

            if base_name == "(新規)":
                new_def = model.add_damper_def_new(
                    keyword=keyword,
                    new_name=new_name,
                    overrides=overrides,
                )
                self.log_emitted.emit(
                    f"  新規定義: {new_def.name} ({keyword})"
                )
            else:
                new_def = model.add_damper_def_copy(
                    base_name=base_name,
                    new_name=new_name,
                    overrides=overrides,
                )
                if new_def:
                    self.log_emitted.emit(
                        f"  追加定義: {new_def.name} (ベース: {base_name})"
                    )
                else:
                    self.log_emitted.emit(
                        f"  [WARN] 追加定義のベース '{base_name}' が見つかりません"
                    )

    def _apply_rd_overrides(self, case: AnalysisCase, model) -> None:
        """RD 配置・基数変更を適用します。"""
        rd_overrides = case.parameters.get("_rd_overrides", {})
        if not rd_overrides:
            return
        for row_str, changes in rd_overrides.items():
            row_idx = int(row_str)
            model.update_damper_element(
                row_idx,
                node_i=changes.get("node_i"),
                node_j=changes.get("node_j"),
                quantity=changes.get("quantity"),
                damper_def_name=changes.get("damper_def_name"),
            )
            elem = model.damper_elements[row_idx] if row_idx < len(model.damper_elements) else None
            if elem:
                self.log_emitted.emit(
                    f"  RD[{row_idx}] {elem.name}: "
                    + ", ".join(
                        f"{k}={v}" for k, v in changes.items()
                        if v is not None
                    )
                )
            else:
                self.log_emitted.emit(f"  RD[{row_idx}] 変更: {changes}")

    def _apply_dyd_overrides(self, model) -> None:
        """DYD 応答解析条件の履歴結果出力指定を上書きします。"""
        if not self._dyd_overrides:
            return
        dyd = model.dyd_record
        if dyd is None:
            self.log_emitted.emit("  [WARN] DYD レコードが見つかりません")
            return
        for idx, val in self._dyd_overrides.items():
            if 0 <= idx < len(dyd.values):
                old_val = dyd.values[idx]
                dyd.values[idx] = str(val)
                self.log_emitted.emit(f"  DYD[{idx}]: {old_val} → {val}")

    def _execute_snap(self, run_input: Path) -> None:
        """SNAP を実行し、return_code をケースに記録します。"""
        result = snap_exec(
            snap_exe=self._snap_exe_path,
            input_file=str(run_input),
            stdout_callback=lambda line: self.log_emitted.emit(line),
        )
        self._case.return_code = result.returncode

    def _parse_results(self, case: AnalysisCase, run_input: Path, out_dir: Path) -> bool:
        """DYC ケースごとの結果フォルダを特定してパースします。

        Returns
        -------
        bool
            結果が取得できたかどうか。
        """
        s8i_stem = run_input.stem

        dyc_model = parse_s8i(str(run_input))
        dyc_cases = dyc_model.dyc_cases

        snap_model_dir: Optional[Path] = None
        if self._snap_work_dir:
            candidate = Path(self._snap_work_dir) / s8i_stem
            if candidate.exists():
                snap_model_dir = candidate
                self.log_emitted.emit(f"  snap_work_dir モデルフォルダ: {snap_model_dir}")
            else:
                self.log_emitted.emit(
                    f"  [INFO] snap_work_dir/{s8i_stem} が見つかりません: {candidate}"
                )

        main_result_set = False

        if dyc_cases:
            dyc_results, main_result_set = self._parse_dyc_results(
                case, dyc_cases, snap_model_dir, out_dir
            )
        else:
            dyc_results = []
            main_result_set = self._parse_legacy_results(
                case, snap_model_dir, out_dir
            )

        case.dyc_results = dyc_results
        return main_result_set

    def _parse_dyc_results(
        self, case: AnalysisCase, dyc_cases, snap_model_dir: Optional[Path], out_dir: Path
    ) -> tuple:
        """DYC ケースごとに結果をパースします。

        Returns
        -------
        tuple of (dyc_results, main_result_set)
        """
        dyc_results = []
        main_result_set = False

        for dyc in dyc_cases:
            dr: dict = {
                "case_no":       dyc.case_no,
                "case_name":     dyc.name,
                "run_flag":      dyc.run_flag,
                "has_result":    False,
                "result_data":   {},
                "result_summary": {},
            }

            if dyc.is_run:
                search_dirs: list = []
                if snap_model_dir:
                    d_folder = snap_model_dir / dyc.folder_name
                    search_dirs.append(d_folder)
                search_dirs.append(out_dir)

                for rdir in search_dirs:
                    floor_files = list(rdir.glob("Floor*.txt")) if rdir.exists() else []
                    self.log_emitted.emit(
                        f"  [D{dyc.case_no}:{dyc.name}] 検索: {rdir} → Floor*.txt: {[f.name for f in floor_files]}"
                    )
                    if floor_files:
                        res = Result(str(rdir))
                        for log_line in getattr(res, "parse_log", []):
                            self.log_emitted.emit(log_line)
                        if res.max_disp or res.max_acc:
                            dr["has_result"] = True
                            dr["result_data"] = res.get_all()
                            dr["result_summary"] = self._build_summary_dict(res)
                            dr["result_dir"] = str(rdir)
                            self.log_emitted.emit(
                                f"  [D{dyc.case_no}:{dyc.name}] ✓ 結果取得 "
                                f"({len(res.max_disp)}層, フォルダ: {rdir})"
                            )
                            if not main_result_set:
                                self._store_summary(case, res)
                                case.binary_result_dir = str(rdir)
                                main_result_set = True
                            break
                if not dr["has_result"]:
                    self.log_emitted.emit(
                        f"  [D{dyc.case_no}:{dyc.name}] ✗ 結果なし (run_flag=1 だが Floor*.txt 未検出)"
                    )
            else:
                self.log_emitted.emit(
                    f"  [D{dyc.case_no}:{dyc.name}] スキップ (run_flag=0)"
                )

            dyc_results.append(dr)

        return dyc_results, main_result_set

    def _parse_legacy_results(
        self, case: AnalysisCase, snap_model_dir: Optional[Path], out_dir: Path
    ) -> bool:
        """DYC 行が存在しない s8i の結果をパースします。"""
        self.log_emitted.emit("  [INFO] DYC ケース未定義 → out_dir から直接パース")
        search_dirs: list = [out_dir]
        if snap_model_dir:
            d1 = snap_model_dir / "D1"
            if d1.exists():
                search_dirs.insert(0, d1)
        for rdir in search_dirs:
            floor_files = list(rdir.glob("Floor*.txt")) if rdir.exists() else []
            self.log_emitted.emit(
                f"  検索: {rdir} → Floor*.txt: {[f.name for f in floor_files]}"
            )
            if floor_files:
                res = Result(str(rdir))
                for log_line in getattr(res, "parse_log", []):
                    self.log_emitted.emit(log_line)
                if res.max_disp or res.max_acc:
                    self._store_summary(case, res)
                    case.binary_result_dir = str(rdir)
                    self.log_emitted.emit(
                        f"  ✓ 結果取得 ({len(res.max_disp)}層, フォルダ: {rdir})"
                    )
                    return True
        return False

    def _log_summary(self, case: AnalysisCase, main_result_set: bool) -> None:
        """パース結果サマリーをログ出力します。"""
        rs = case.result_summary
        if rs.get("max_disp"):
            self.log_emitted.emit(f"  → 最大相対変位: {rs['max_disp']:.5g} m")
        if rs.get("max_acc"):
            self.log_emitted.emit(f"  → 最大絶対加速度: {rs['max_acc']:.5g} m/s²")
        if rs.get("max_drift"):
            self.log_emitted.emit(f"  → 最大層間変形角: {rs['max_drift']:.5g}")
        if not main_result_set:
            self.log_emitted.emit(
                "  [WARN] 結果が読み取れませんでした。"
                "snap_work_dir の設定と D{N} フォルダの有無を確認してください。"
            )

    @staticmethod
    def _build_summary_dict(res: Result) -> dict:
        """Result オブジェクトから result_summary 辞書を作成します。"""
        summary: dict = {}
        if res.max_story_drift:
            summary["max_drift"] = max(res.max_story_drift.values())
        if res.max_acc:
            summary["max_acc"] = max(res.max_acc.values())
        if res.max_disp:
            summary["max_disp"] = max(res.max_disp.values())
        if res.max_vel:
            summary["max_vel"] = max(res.max_vel.values())
        if res.shear_coeff:
            summary["max_shear"] = max(res.shear_coeff.values())
        if res.max_otm:
            summary["max_otm"] = max(res.max_otm.values())
        summary["result_data"] = res.get_all()

        # 結果フォルダにバイナリファイル（.hst 時刻歴 / Period.xbn）が
        # あれば、時刻歴と固有値を result_summary に取り込みます。
        # これにより既存 TimeHistoryWidget / ModalPropertiesWidget が
        # モックではなく実データを表示できます。
        try:
            _AnalysisWorker._attach_binary_results(summary, res)
        except Exception as e:  # noqa: BLE001
            # 失敗しても他の結果は有効なので黙ってスキップ
            summary.setdefault("binary_load_error", str(e))
        return summary

    @staticmethod
    def _attach_binary_results(summary: dict, res: Result) -> None:
        """
        結果フォルダから SNAP バイナリ結果を読み取り、
        summary["time_history"] と summary["period_modes"] を設定します。
        """
        try:
            from controller.binary import SnapResultLoader  # noqa: WPS433
        except Exception:
            logger.debug("SnapResultLoader のインポートに失敗", exc_info=True)
            return

        result_dir = getattr(res, "result_dir", None)
        if not result_dir:
            return

        loader = SnapResultLoader(result_dir, dt=0.005)

        # ----- 時刻歴の取込 -----
        # TimeHistoryWidget が期待する形式:
        #   summary["time_history"][type_key] = {
        #       "time": np.ndarray,
        #       "<floor_no>": np.ndarray,
        #       "max_floor": np.ndarray,
        #   }
        import numpy as np

        def _extract_timehistory(cat, field_index: int) -> Optional[dict]:
            """カテゴリから指定 field index の全レコード時刻歴を取得。"""
            if not cat or not cat.hst or not cat.hst.header:
                return None
            if cat.hst.header.fields_per_record <= field_index:
                return None
            hst = cat.hst
            hst.ensure_loaded()
            t = hst.times()
            entry: dict = {"time": t.tolist()}
            peak_curve = None
            for r in range(hst.header.num_records):
                name = cat.record_name(r)
                floor_no = r + 1
                if name and name.endswith("F"):
                    try:
                        floor_no = int(name[:-1])
                    except ValueError:
                        logger.debug("階番号パース失敗: %s", name)
                arr = hst.time_series(r, field_index)
                entry[str(floor_no)] = arr.tolist()
                if peak_curve is None or float(np.abs(arr).max()) > float(np.abs(peak_curve).max()):
                    peak_curve = arr
            if peak_curve is not None:
                entry["max_floor"] = peak_curve.tolist()
            return entry

        floor_cat = loader.get("Floor")
        story_cat = loader.get("Story")
        th: dict = {}

        # Floor.hst の field index は SNAP バージョン依存の可能性があるため
        # 非ゼロ値を持つインデックスから物理量を推定します:
        #   field 0 : 相対変位 相当
        #   field 4 : 相対速度 相当
        #   field 6 : 絶対加速度 相当
        # 実サンプル example_3D/D4 で観測された分布と一致します。
        floor_field_map = {"disp": 0, "vel": 4, "acc": 6}
        for key, fidx in floor_field_map.items():
            entry = _extract_timehistory(floor_cat, fidx)
            if entry is not None:
                th[key] = entry

        # Story.hst: 28 field 中、先頭 3 が変形・せん断・モーメント相当と推定
        story_field_map = {"story_disp": 0, "shear": 6, "moment": 9}
        for key, fidx in story_field_map.items():
            entry = _extract_timehistory(story_cat, fidx)
            if entry is not None:
                th[key] = entry

        if th:
            summary["time_history"] = th

        # ----- 固有値（Period.xbn）の取込 -----
        if loader.period and loader.period.modes:
            summary["period_modes"] = [
                {
                    "mode_no": m.mode_no,
                    "period": m.period,
                    "frequency": m.frequency,
                    "omega": m.omega,
                    "dominant": m.dominant_direction,
                    "beta": dict(m.beta),
                    "pm": dict(m.pm),
                }
                for m in loader.period.modes
            ]

    @staticmethod
    def _store_summary(case: AnalysisCase, res: Result) -> None:
        """主要応答値を result_summary に格納します。"""
        case.result_summary = _AnalysisWorker._build_summary_dict(res)



class AnalysisService(QObject):
    """
    解析実行の管理クラス。

    MainWindow から呼び出されます。

    Signals
    -------
    log_emitted(line: str)
        ログ行を通知します。
    case_finished(case_id: str, success: bool)
        1ケースの解析完了を通知します。
    status_changed(message: str)
        ステータスバー用メッセージを通知します。
    progress_updated(current: int, total: int)
        複数ケース実行時の進捗を通知します。
        current = 完了数, total = 総ケース数。
        total == 0 のとき進捗バーを非表示にします。
    batch_state_changed(running: bool)
        バッチ実行の開始・終了を通知します。
        UIのキャンセル/一時停止ボタンの有効・無効切替に使います。
    """

    log_emitted = Signal(str)
    case_finished = Signal(str, bool)
    status_changed = Signal(str)
    progress_updated = Signal(int, int)  # (current, total)
    batch_state_changed = Signal(bool)   # True=実行中, False=停止

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._workers: List[_AnalysisWorker] = []
        self._queue: List[AnalysisCase] = []
        self._current_worker: Optional[_AnalysisWorker] = None
        self._snap_exe_path: str = ""
        self._snap_work_dir: str = ""
        self._dyd_overrides: Optional[Dict[int, int]] = None
        # 進捗追跡
        self._total_in_batch: int = 0
        self._completed_in_batch: int = 0
        # 一時停止・キャンセル制御
        self._paused: bool = False
        self._cancelled: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_snap_exe_path(self, path: str) -> None:
        """SNAP.exe のパスを設定します（プロジェクトレベル）。"""
        self._snap_exe_path = path

    def set_snap_work_dir(self, path: str) -> None:
        """SNAP work ディレクトリのパスを設定します。"""
        self._snap_work_dir = path

    def set_dyd_overrides(self, overrides: Optional[Dict[int, int]]) -> None:
        """DYD 履歴結果出力指定のオーバーライドを設定します（プロジェクトレベル）。"""
        self._dyd_overrides = overrides

    @property
    def is_running(self) -> bool:
        """現在バッチ実行中かどうかを返します。"""
        return (self._current_worker is not None
                and self._current_worker.isRunning()) or bool(self._queue)

    @property
    def is_paused(self) -> bool:
        """一時停止中かどうかを返します。"""
        return self._paused

    def run_case(self, case: AnalysisCase) -> None:
        """1ケースを即座に実行します。"""
        if not case.is_runnable(self._snap_exe_path):
            self.log_emitted.emit(
                f"[WARN] {case.name}: モデルファイルまたは SNAP.exe が設定されていません。"
            )
            return
        # 単体実行は 1/1 のバッチ
        self._paused = False
        self._cancelled = False
        self._total_in_batch = 1
        self._completed_in_batch = 0
        self.progress_updated.emit(0, 1)
        self.batch_state_changed.emit(True)
        self._start_worker(case)

    def run_all(self, cases: List[AnalysisCase]) -> None:
        """全ケースをキューに追加して順次実行します。"""
        runnable = [c for c in cases if c.is_runnable(self._snap_exe_path)]
        if not runnable:
            self.log_emitted.emit("[WARN] 実行可能なケースがありません。")
            return
        self._paused = False
        self._cancelled = False
        self._queue = list(runnable)
        self._total_in_batch = len(runnable)
        self._completed_in_batch = 0
        self.progress_updated.emit(0, self._total_in_batch)
        self.batch_state_changed.emit(True)
        self._run_next_in_queue()

    def cancel_batch(self) -> None:
        """実行中のバッチをキャンセルします。

        現在実行中のケースの完了を待ってからキューを破棄します。
        """
        if not self.is_running and not self._paused:
            return
        self._cancelled = True
        self._paused = False
        remaining = len(self._queue)
        self._queue.clear()
        self.log_emitted.emit(
            f"=== バッチキャンセル: 残り {remaining} ケースをスキップしました ==="
        )
        self.status_changed.emit("バッチをキャンセルしました")

    def pause_batch(self) -> None:
        """バッチ実行を一時停止します（現在のケース完了後に一時停止）。"""
        if not self.is_running or self._paused:
            return
        self._paused = True
        self.log_emitted.emit("=== バッチ一時停止: 現在のケース完了後に停止します ===")
        self.status_changed.emit("一時停止中（現在のケース完了後に停止）")

    def resume_batch(self) -> None:
        """一時停止中のバッチ実行を再開します。"""
        if not self._paused:
            return
        self._paused = False
        self.log_emitted.emit("=== バッチ再開 ===")
        self.status_changed.emit("バッチ実行を再開しました")
        self.batch_state_changed.emit(True)
        self._run_next_in_queue()

    def run_mock_all(self, cases: List[AnalysisCase], floors: int = _MOCK_FLOORS) -> None:
        """全ケースをモックデータで実行します（進捗バー付き）。"""
        if not cases:
            self.log_emitted.emit("[WARN] ケースがありません。")
            return
        self._paused = False
        self._cancelled = False
        self._total_in_batch = len(cases)
        self._completed_in_batch = 0
        self.progress_updated.emit(0, self._total_in_batch)
        self.batch_state_changed.emit(True)
        for case in cases:
            if self._cancelled:
                break
            self.run_mock(case, floors=floors)
        self._finish_batch("デモ実行が完了しました")

    def run_mock(self, case: AnalysisCase, floors: int = _MOCK_FLOORS) -> None:
        """
        モックデータでケースを完了させます（SNAP 不要のデモ用）。

        Result.from_mock() でランダムなダミー応答値を生成し、
        AnalysisCase.result_summary に格納してから case_finished を発火します。

        Parameters
        ----------
        case : AnalysisCase
            結果を書き込むケース。
        floors : int
            モックデータの階数。
        """
        import random

        case.status = AnalysisCaseStatus.RUNNING
        self.status_changed.emit(f"デモ実行中: {case.name}")
        self.log_emitted.emit(f"=== デモ実行開始 (モック): {case.name} ===")

        try:
            # ケースごとに少しスケールを変えて差をつける
            scale = round(0.7 + random.uniform(0.0, 0.6), 2)
            res = Result.from_mock(floors=floors)
            # スケールを適用して差別化
            for attr in ("max_disp", "max_vel", "max_acc",
                         "max_story_disp", "max_story_drift",
                         "shear_coeff", "max_otm"):
                original = getattr(res, attr)
                setattr(res, attr, {k: round(v * scale, 6) for k, v in original.items()})

            _AnalysisWorker._store_summary(case, res)
            case.status = AnalysisCaseStatus.COMPLETED
            case.return_code = 0
            self.log_emitted.emit(
                f"  [デモ] スケール係数 = {scale:.2f}, 階数 = {floors}"
            )
            self.log_emitted.emit(f"=== デモ実行完了: {case.name} ===")
            self.status_changed.emit(f"デモ完了: {case.name}")
            self.case_finished.emit(case.id, True)
        except Exception as e:
            case.status = AnalysisCaseStatus.ERROR
            self.log_emitted.emit(f"[ERROR] デモ実行中にエラー: {e}")
            self.status_changed.emit(f"デモエラー: {case.name}")
            self.case_finished.emit(case.id, False)

    def shutdown(self) -> None:
        """実行中のワーカーを停止します。"""
        self._queue.clear()
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.terminate()
            self._current_worker.wait(3000)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_worker(self, case: AnalysisCase) -> None:
        worker = _AnalysisWorker(case, self._snap_exe_path, self._snap_work_dir,
                                 dyd_overrides=self._dyd_overrides)
        worker.log_emitted.connect(self.log_emitted)
        worker.case_finished.connect(self._on_worker_finished)
        worker.status_changed.connect(self.status_changed)
        self._current_worker = worker
        self._workers.append(worker)
        worker.start()

    def _on_worker_finished(self, case_id: str, success: bool) -> None:
        self.case_finished.emit(case_id, success)
        # 進捗更新
        self._completed_in_batch += 1
        self.progress_updated.emit(self._completed_in_batch, self._total_in_batch)

        # キャンセル済みの場合
        if self._cancelled:
            self._finish_batch("バッチがキャンセルされました")
            return

        # 一時停止中の場合
        if self._paused:
            self.status_changed.emit(
                f"一時停止中 ({self._completed_in_batch}/{self._total_in_batch} 完了, "
                f"残り {len(self._queue)} ケース)"
            )
            return

        # キューが残っていれば次を実行
        if self._queue:
            next_case = self._queue.pop(0)
            self._start_worker(next_case)
        else:
            self._finish_batch("すべての解析が完了しました")

    def _finish_batch(self, message: str) -> None:
        """バッチ実行終了の共通処理。"""
        self._paused = False
        self._cancelled = False
        self.status_changed.emit(message)
        self.progress_updated.emit(0, 0)
        self.batch_state_changed.emit(False)

    def _run_next_in_queue(self) -> None:
        if not self._queue:
            return
        if self._current_worker and self._current_worker.isRunning():
            return  # 現在実行中のものが終わったら _on_worker_finished から呼ばれる
        case = self._queue.pop(0)
        self._start_worker(case)
