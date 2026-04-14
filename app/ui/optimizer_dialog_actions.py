"""
app/ui/optimizer_dialog_actions.py
OptimizerDialogのエクスポート・分析・設定プリセット関連メソッドを提供するMixinクラス。

optimizer_dialog.py からのモジュール分割: 結果適用・CSV/HTML出力・
感度解析・Sobol解析・相関分析・収束診断・ヒートマップ・JSON保存読込・
設定プリセット保存読込のメソッドを分離。
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
)

from app.services.optimizer import (
    OptimizationResult,
    compute_convergence_diagnostics,
    compute_correlation_analysis,
    compute_sensitivity,
    compute_sobol_sensitivity,
    export_optimization_log,
)
from app.services.snap_evaluator import create_snap_evaluator
from .optimizer_analysis_dialogs import (
    ComparisonDialog,
    CorrelationDialog,
    DiagnosticsDialog,
    HeatmapDialog as _HeatmapDialog,
    ParetoDialog,
    SensitivityDialog,
    SobolDialog,
)

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .optimizer_dialog import OptimizerDialog


class _OptimizerResultActionsMixin:
    """OptimizerDialog の結果エクスポート・分析・設定プリセット操作メソッド群。

    OptimizerDialog が多重継承で取り込むことを想定しています。
    self は OptimizerDialog のインスタンスとして動作します。
    """

    def _apply_best(self) -> None:
        """最良解を .s8i ファイルに書き戻してからケースに適用して閉じます。"""
        if not self._result or not self._result.best:
            QMessageBox.information(self, "情報", "適用可能な最良解がありません。")
            return

        best = self._result.best
        model_path = getattr(self._base_case, "model_path", "") if self._base_case else ""

        if not model_path or not Path(model_path).exists():
            self.accept()
            return

        param_lines = [f"  {k} = {v:.6g}" for k, v in best.params.items()]
        detail = "\n".join(param_lines)
        obj_label = self._result.config.objective_label if self._result.config else "目的関数"
        ret = QMessageBox.question(
            self,
            "適用確認",
            f"以下の最良パラメータを .s8i ファイルに書き戻します:\n\n"
            f"{detail}\n\n"
            f"{obj_label} = {best.objective_value:.6g}\n\n"
            f"対象: {model_path}\n\n"
            "元のファイルが上書きされます。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            from app.models.s8i_parser import parse_s8i

            model = parse_s8i(model_path)

            damper_def_name = ""
            if self._base_case and self._base_case.damper_params:
                for key in self._base_case.damper_params:
                    damper_def_name = key
                    break

            if damper_def_name:
                ddef = model.get_damper_def(damper_def_name)
                if ddef is not None:
                    overrides = (self._base_case.damper_params or {}).get(damper_def_name, {})
                    if isinstance(overrides, dict):
                        override_keys = list(overrides.keys())
                        param_field_map: dict[str, int] = {}
                        config_params = self._result.config.parameters if self._result.config else []
                        for pr in config_params:
                            if pr.key in override_keys:
                                try:
                                    param_field_map[pr.key] = int(pr.key)
                                except ValueError:
                                    logger.debug("パラメータキー '%s' は整数変換不可、フィールドマッチングで解決", pr.key)
                            for idx_str in override_keys:
                                try:
                                    idx = int(idx_str)
                                    if pr.key.lower() in str(overrides.get(idx_str, "")).lower():
                                        param_field_map[pr.key] = idx
                                except (ValueError, TypeError):
                                    logger.debug("override key '%s' のマッチング失敗（param=%s）", idx_str, pr.key)

                        applied_count = 0
                        for param_key, field_idx in param_field_map.items():
                            if param_key in best.params and field_idx < len(ddef.values):
                                ddef.values[field_idx] = str(best.params[param_key])
                                applied_count += 1
                        if not param_field_map:
                            logger.warning("ダンパー '%s' のパラメータマッピングが空 — 書き戻しなし", damper_def_name)

            model.write(model_path)
            QMessageBox.information(
                self,
                "適用完了",
                f"最良パラメータを .s8i ファイルに書き戻しました。\n"
                f"対象: {Path(model_path).name}",
            )
            self._progress_label.setText("最良パラメータを .s8i に適用しました")
        except Exception as exc:
            logger.error("最良パラメータの .s8i 書き戻しに失敗", exc_info=True)
            QMessageBox.critical(
                self, "書き戻しエラー",
                f".s8i ファイルへの書き戻しに失敗しました:\n{exc}",
            )
            return

        self.accept()

    def _copy_best_params(self) -> None:
        """最良解のパラメータをクリップボードにコピーします。"""
        if not self._result or not self._result.best:
            QMessageBox.information(self, "情報", "コピーするパラメータがありません。")
            return
        best = self._result.best
        lines = ["[最適化結果 - 最良パラメータ]"]
        for key, val in best.params.items():
            lines.append(f"  {key} = {val}")
        lines.append(f"  目的関数値 = {best.objective_value:.6g}")
        if best.response_values:
            lines.append("[応答値]")
            for key, val in best.response_values.items():
                lines.append(f"  {key} = {val:.6g}")
        text = "\n".join(lines)
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        self._progress_label.setText("最良パラメータをクリップボードにコピーしました")

    def _export_csv(self) -> None:
        """探索結果をCSVファイルにエクスポートします。"""
        if not self._result or not self._result.all_candidates:
            QMessageBox.information(self, "情報", "エクスポートする結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "CSV出力先を選択", "optimization_results.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return

        ranked = self._result.all_ranked_candidates
        obj_key = self._result.config.objective_key if self._result.config else ""

        if ranked:
            param_keys = list(ranked[0].params.keys())
            response_keys = sorted({
                k for c in ranked for k in c.response_values.keys()
            })
            margin_keys = sorted({
                k for c in ranked for k in c.constraint_margins.keys()
            })
        else:
            param_keys = []
            response_keys = []
            margin_keys = []

        header = ["順位"] + param_keys + ["目的関数値", "判定"] + response_keys
        if margin_keys:
            header += [f"マージン:{k}" for k in margin_keys]

        try:
            eval_label = "SNAP実解析" if self._result.evaluation_method == "snap" else "モック評価（デモ用）"
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([f"# 評価方式: {eval_label}"])
                if self._result.evaluator_stats:
                    s = self._result.evaluator_stats
                    writer.writerow([
                        f"# SNAP統計: 成功 {s.get('success', 0)}, "
                        f"エラー {s.get('error', 0)}, "
                        f"キャッシュヒット {s.get('cache_hits', 0)}"
                    ])
                writer.writerow(header)
                for rank, cand in enumerate(ranked):
                    row = [rank + 1]
                    row += [cand.params.get(k, "") for k in param_keys]
                    row.append(cand.objective_value)
                    row.append("OK" if cand.is_feasible else "NG")
                    row += [cand.response_values.get(k, "") for k in response_keys]
                    if margin_keys:
                        row += [cand.constraint_margins.get(k, "") for k in margin_keys]
                    writer.writerow(row)

            QMessageBox.information(
                self, "CSV出力完了",
                f"{len(ranked)} 件の探索結果を出力しました。\n{path}",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    def _export_html_report(self) -> None:
        """最適化結果をHTMLレポートとして出力します。"""
        if not self._result or not self._result.all_candidates:
            QMessageBox.information(self, "情報", "レポート出力する結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "HTMLレポート出力先を選択", "optimization_report.html",
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return

        try:
            from app.services.report_generator import generate_optimization_report
            generate_optimization_report(
                result=self._result,
                output_path=path,
                include_charts=True,
            )
            QMessageBox.information(
                self, "HTMLレポート出力完了",
                f"最適化レポートを出力しました。\n{path}",
            )
        except Exception as e:
            logger.exception("HTMLレポート出力エラー")
            QMessageBox.warning(
                self, "エラー",
                f"レポートの出力に失敗しました:\n{e}",
            )

    def _save_convergence_plot(self) -> None:
        """収束グラフをPNG/SVG画像として保存します。"""
        path, _ = QFileDialog.getSaveFileName(
            self, "収束グラフの保存先を選択", "convergence_plot.png",
            "PNG Image (*.png);;SVG Image (*.svg);;PDF (*.pdf);;All Files (*)",
        )
        if not path:
            return
        try:
            self._conv_canvas.fig.savefig(
                path, dpi=150, bbox_inches="tight",
                facecolor=self._conv_canvas.fig.get_facecolor(),
            )
            QMessageBox.information(
                self, "画像保存完了",
                f"収束グラフを保存しました。\n{path}",
            )
        except Exception as e:
            logger.exception("収束グラフ画像保存エラー")
            QMessageBox.warning(
                self, "エラー",
                f"画像の保存に失敗しました:\n{e}",
            )

    def _run_sensitivity(self) -> None:
        """最適解周りのパラメータ感度解析を実行し、結果ダイアログを表示します。"""
        if not self._result or not self._result.best or not self._result.config:
            return

        config = self._result.config
        best_params = self._result.best.params

        evaluate_fn = None
        if self._base_case and self._snap_exe_path:
            evaluate_fn = create_snap_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                snap_work_dir=self._snap_work_dir,
                timeout=config.snap_timeout,
            )
        if evaluate_fn is None:
            from app.services.optimizer import _mock_evaluate
            base = {}
            if config.base_case and config.base_case.result_summary:
                base = config.base_case.result_summary
            evaluate_fn = lambda params: _mock_evaluate(
                params, base, config.objective_key
            )

        try:
            sensitivity = compute_sensitivity(
                evaluate_fn=evaluate_fn,
                best_params=best_params,
                parameters=config.parameters,
                objective_key=config.objective_key,
            )
            sensitivity.objective_label = config.objective_label
        except Exception as exc:
            logger.warning("感度解析に失敗しました: %s", exc, exc_info=True)
            QMessageBox.warning(
                self, "感度解析エラー",
                f"感度解析の実行中にエラーが発生しました:\n{exc}",
            )
            return

        dlg = SensitivityDialog(sensitivity, parent=self)
        dlg.exec()

    def _run_sobol(self) -> None:
        """Sobol グローバル感度解析を実行し、結果ダイアログを表示します。"""
        if not self._result or not self._result.config:
            return

        config = self._result.config
        n_params = len(config.parameters)
        n_base = 64
        n_evals = n_base * (2 * n_params + 2)
        reply = QMessageBox.question(
            self, "Sobol感度解析",
            f"Sobol分散ベース感度解析を実行します。\n\n"
            f"パラメータ数: {n_params}\n"
            f"推定評価回数: {n_evals} 回\n\n"
            f"OAT法より評価回数が多いですが、パラメータ間の\n"
            f"交互作用を捉えることができます。\n\n実行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        evaluate_fn = None
        if self._base_case and self._snap_exe_path:
            evaluate_fn = create_snap_evaluator(
                snap_exe_path=self._snap_exe_path,
                base_case=self._base_case,
                param_ranges=config.parameters,
                snap_work_dir=self._snap_work_dir,
                timeout=config.snap_timeout,
            )
        if evaluate_fn is None:
            from app.services.optimizer import _mock_evaluate
            base = {}
            if config.base_case and config.base_case.result_summary:
                base = config.base_case.result_summary
            evaluate_fn = lambda params: _mock_evaluate(
                params, base, config.objective_key
            )

        try:
            sobol = compute_sobol_sensitivity(
                evaluate_fn=evaluate_fn,
                parameters=config.parameters,
                objective_key=config.objective_key,
                n_samples=n_base,
                objective_label=config.objective_label,
            )
        except Exception as exc:
            logger.warning("Sobol解析に失敗しました: %s", exc, exc_info=True)
            QMessageBox.warning(
                self, "Sobol解析エラー",
                f"Sobol感度解析の実行中にエラーが発生しました:\n{exc}",
            )
            return

        dlg = SobolDialog(sobol, parent=self)
        dlg.exec()

    def _show_pareto(self) -> None:
        """Pareto frontダイアログを表示します。"""
        if not self._result:
            return
        dlg = ParetoDialog(self._result, parent=self)
        dlg.exec()

    def _show_comparison(self) -> None:
        """結果比較ダイアログを表示します。"""
        dlg = ComparisonDialog(parent=self)
        dlg.exec()

    def _show_correlation(self) -> None:
        """パラメータ相関分析ダイアログを表示します。"""
        if not self._result:
            return
        corr = compute_correlation_analysis(self._result)
        if corr is None:
            QMessageBox.information(
                self, "相関分析",
                "候補数またはパラメータ数が不足しているため相関分析できません。\n"
                "（3候補以上・2パラメータ以上が必要です）",
            )
            return
        dlg = CorrelationDialog(corr, parent=self)
        dlg.exec()

    def _export_log(self) -> None:
        """最適化の全評価履歴をCSVログとして出力します。"""
        if not self._result:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "評価ログ出力先を選択", "optimization_log.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            export_optimization_log(self._result, path)
            QMessageBox.information(
                self, "評価ログ出力完了",
                f"全{len(self._result.all_candidates)}件の評価履歴を出力しました:\n{path}",
            )
        except Exception as exc:
            logger.exception("評価ログ出力エラー")
            QMessageBox.warning(self, "出力エラー", str(exc))

    def _show_diagnostics(self) -> None:
        """収束品質診断ダイアログを表示します。"""
        if not self._result:
            return
        diag = compute_convergence_diagnostics(self._result)
        if diag is None:
            QMessageBox.information(
                self, "診断不可", "候補数が不足しているため診断できません。"
            )
            return
        dlg = DiagnosticsDialog(diag, parent=self)
        dlg.exec()

    def _show_heatmap(self) -> None:
        """パラメータ空間ヒートマップダイアログを表示します。"""
        if not self._result or not self._result.config:
            return
        params = self._result.config.parameters
        if len(params) < 2:
            QMessageBox.information(
                self, "情報", "ヒートマップには2パラメータ以上必要です。"
            )
            return
        dlg = _HeatmapDialog(self._result, parent=self)
        dlg.exec()

    def _save_result_json(self) -> None:
        """最適化結果をJSONファイルに保存します。"""
        if not self._result:
            QMessageBox.information(self, "情報", "保存する結果がありません。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "結果の保存先を選択", "optimization_result.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            self._result.save_json(path)
            QMessageBox.information(
                self, "保存完了",
                f"最適化結果を保存しました。\n{path}\n"
                f"({len(self._result.all_candidates)} 件の候補データ)",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    def _load_result_json(self) -> None:
        """JSONファイルから最適化結果を読み込み、ダイアログに反映します。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "結果ファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            result = OptimizationResult.load_json(path)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            QMessageBox.warning(self, "読込エラー", f"ファイルの読み込みに失敗しました:\n{e}")
            return

        if not result.all_candidates:
            QMessageBox.information(self, "情報", "候補データが含まれていません。")
            return

        self._result = result
        self._populate_result_table(result)
        self._draw_convergence(result)
        self._update_best_summary_card(result)

        self._export_csv_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._report_btn.setEnabled(True)
        self._log_export_btn.setEnabled(True)
        self._diagnostics_btn.setEnabled(True)
        self._save_plot_btn.setEnabled(True)
        if result.best:
            self._apply_btn.setEnabled(True)
            self._sensitivity_btn.setEnabled(True)
            self._sobol_btn.setEnabled(True)
        if result.config and result.config.objective_weights:
            self._pareto_btn.setEnabled(True)
        if (result.config and len(result.config.parameters) >= 2
                and len(result.all_candidates) >= 3):
            self._heatmap_btn.setEnabled(True)
            self._correlation_btn.setEnabled(True)

        obj_label = result.config.objective_label if result.config else "目的関数"
        n_cands = len(result.all_candidates)
        n_feasible = len(result.feasible_candidates)
        self._result_summary.setText(
            f"読込完了: {n_cands}点, 制約満足: {n_feasible}点"
        )
        self._progress_label.setText(
            f"JSONから読込 ({result.elapsed_sec:.1f}秒の結果)"
        )

    def _browse_warm_start(self) -> None:
        """ウォームスタート用の前回結果JSONを選択します。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "ウォームスタート用の結果ファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            result = OptimizationResult.load_json(path)
        except (OSError, json.JSONDecodeError, KeyError) as e:
            QMessageBox.warning(self, "読込エラー", f"ファイルの読み込みに失敗しました:\n{e}")
            return

        if not result.feasible_candidates:
            QMessageBox.warning(
                self, "ウォームスタート",
                "このファイルには制約を満たす候補が含まれていません。\n"
                "全候補を初期値として使用します。",
            )
            self._warm_start_candidates = list(result.all_candidates)
        else:
            self._warm_start_candidates = list(result.feasible_candidates)

        n = len(self._warm_start_candidates)
        fname = os.path.basename(path)
        self._warm_start_path_label.setText(f"{fname} ({n}点)")
        self._warm_start_path_label.setToolTip(path)

    # ------------------------------------------------------------------
    # 設定プリセット保存・読込
    # ------------------------------------------------------------------

    def _save_config_preset(self) -> None:
        """現在のパラメータ範囲・目的関数・探索手法の設定をJSONに保存します。"""
        try:
            config = self._build_config()
        except Exception as e:
            QMessageBox.warning(self, "設定エラー", f"設定の取得に失敗しました:\n{e}")
            return

        preset = config.to_dict()
        preset["_preset_version"] = 1

        path, _ = QFileDialog.getSaveFileName(
            self, "設定プリセットの保存先を選択", "optimizer_preset.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(preset, f, ensure_ascii=False, indent=2)
            QMessageBox.information(
                self, "保存完了",
                f"設定プリセットを保存しました。\n{path}",
            )
        except OSError as e:
            QMessageBox.warning(self, "エラー", f"ファイルの書き込みに失敗しました:\n{e}")

    def _load_config_preset(self) -> None:
        """JSONファイルからパラメータ設定を読み込み、UIに反映します。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "設定プリセットファイルを選択", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                preset = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "読込エラー", f"ファイルの読み込みに失敗しました:\n{e}")
            return

        try:
            self._apply_config_preset(preset)
        except Exception as e:
            logger.warning("設定プリセット適用エラー: %s", e, exc_info=True)
            QMessageBox.warning(self, "適用エラー", f"設定の適用に失敗しました:\n{e}")

    def _apply_config_preset(self, preset: dict) -> None:
        """プリセット辞書の内容をUI要素に反映します。"""
        self._apply_preset_damper_and_objective(preset)
        self._apply_preset_method_and_params(preset)
        self._apply_preset_penalty_parallel_checkpoint(preset)
        self._apply_preset_robust_cost_envelope(preset)
        self._apply_preset_acquisition_ga_seed_timeout(preset)
        self._update_est_run_label()

    def _apply_preset_damper_and_objective(self, preset: dict) -> None:
        from .optimizer_dialog import _OBJECTIVE_ITEMS

        damper_type = preset.get("damper_type", "")
        if damper_type:
            idx = self._damper_combo.findText(damper_type)
            if idx >= 0:
                self._damper_combo.setCurrentIndex(idx)

        obj_key = preset.get("objective_key", "max_drift")
        for i, (key, _, _) in enumerate(_OBJECTIVE_ITEMS):
            if key == obj_key:
                self._obj_combo.setCurrentIndex(i)
                break

        obj_weights = preset.get("objective_weights", {})
        if obj_weights:
            self._composite_check.setChecked(True)
            for w in self._weight_spins:
                w["spin"].setValue(obj_weights.get(w["key"], 0.0))
        else:
            self._composite_check.setChecked(False)

    def _apply_preset_method_and_params(self, preset: dict) -> None:
        method = preset.get("method", "grid")
        for i in range(self._method_combo.count()):
            if self._method_combo.itemData(i) == method:
                self._method_combo.setCurrentIndex(i)
                break

        self._iter_spin.setValue(preset.get("max_iterations", 100))

        params = preset.get("parameters", [])
        if params and self._param_widgets:
            for pw, pd in zip(self._param_widgets, params):
                pw["min"].setValue(pd.get("min_val", pw["min"].value()))
                pw["max"].setValue(pd.get("max_val", pw["max"].value()))
                pw["step"].setValue(pd.get("step", pw["step"].value()))

    def _apply_preset_penalty_parallel_checkpoint(self, preset: dict) -> None:
        penalty = preset.get("constraint_penalty_weight", 0.0)
        if penalty > 0:
            self._penalty_cb.setChecked(True)
            self._penalty_spin.setValue(penalty)
        else:
            self._penalty_cb.setChecked(False)

        self._parallel_spin.setValue(preset.get("n_parallel", 1))

        cp_interval = preset.get("checkpoint_interval", 10)
        if cp_interval > 0:
            self._checkpoint_check.setChecked(True)
            self._checkpoint_interval_spin.setValue(cp_interval)
        else:
            self._checkpoint_check.setChecked(False)

    def _apply_preset_robust_cost_envelope(self, preset: dict) -> None:
        rob_samples = preset.get("robustness_samples", 0)
        if rob_samples > 0:
            self._robust_check.setChecked(True)
            self._robust_samples_spin.setValue(rob_samples)
            self._robust_delta_spin.setValue(preset.get("robustness_delta", 0.05))
        else:
            self._robust_check.setChecked(False)

        cost_weight = preset.get("cost_weight", 0.0)
        cost_coeffs = preset.get("cost_coefficients", {})
        if cost_weight > 0 and cost_coeffs:
            self._cost_check.setChecked(True)
            self._cost_weight_spin.setValue(cost_weight)
            self._cost_coefficients = dict(cost_coeffs)
            n = len(self._cost_coefficients)
            self._cost_label.setText(f"{n}パラメータに係数設定済み")
        else:
            self._cost_check.setChecked(False)
            self._cost_coefficients = {}
            self._cost_label.setText("")

        envelope_mode = preset.get("envelope_mode", "")
        if envelope_mode:
            self._envelope_check.setChecked(True)
            idx = self._envelope_mode_combo.findData(envelope_mode)
            if idx >= 0:
                self._envelope_mode_combo.setCurrentIndex(idx)
        else:
            self._envelope_check.setChecked(False)

    def _apply_preset_acquisition_ga_seed_timeout(self, preset: dict) -> None:
        acq_func = preset.get("acquisition_function", "ei")
        for i in range(self._acq_combo.count()):
            if self._acq_combo.itemData(i) == acq_func:
                self._acq_combo.setCurrentIndex(i)
                break
        self._acq_kappa_spin.setValue(preset.get("acquisition_kappa", 2.0))

        self._ga_adaptive_cb.setChecked(preset.get("ga_adaptive_mutation", False))

        seed = preset.get("random_seed")
        if seed is not None:
            self._seed_check.setChecked(True)
            self._seed_spin.setValue(seed)
        else:
            self._seed_check.setChecked(False)

        self._timeout_spin.setValue(preset.get("snap_timeout", 300))
