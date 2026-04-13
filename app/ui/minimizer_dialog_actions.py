"""MinimizerDialog のアクション（エクスポート・結果適用）Mixin。

minimizer_dialog.py からエクスポート/適用メソッドを分離し、
MinimizerDialog の行数を削減する。
"""

from __future__ import annotations

import csv
import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from PySide6.QtWidgets import QFileDialog, QMessageBox

if TYPE_CHECKING:
    from app.services.damper_count_minimizer import (
        FloorResponse,
        MinimizationResult,
    )

logger = logging.getLogger(__name__)


class _MinimizerResultActionsMixin:
    """MinimizerDialog に結果エクスポート・適用機能を提供する Mixin。

    使用する属性 (MinimizerDialog 側で定義):
        _result: Optional[MinimizationResult]
        _is_snap: bool
        _model_path: str
        _floor_rd_map: Dict[str, List[int]]
        _lbl_status: QLabel
    """

    # -------------------------------------------------------------------
    # CSV出力
    # -------------------------------------------------------------------

    def _export_csv(self) -> None:
        from app.services.damper_count_minimizer import FloorResponse, STRATEGIES

        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV出力", "minimizer_result.csv",
            "CSV (*.csv);;すべて (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                eval_tag = "SNAP" if self._is_snap else "モック"
                writer.writerow([f"# ダンパー本数最小化結果 (評価: {eval_tag})"])
                writer.writerow([f"# 戦略: {STRATEGIES.get(self._result.strategy, self._result.strategy)}"])
                writer.writerow([f"# 最終合計: {self._result.final_count}本"])
                writer.writerow([f"# マージン: {self._result.final_margin:+.4f}"])
                writer.writerow([f"# 評価回数: {self._result.evaluations}"])
                writer.writerow([])

                # 最終配置
                floor_resp_map: Dict[str, FloorResponse] = {
                    fr.floor_key: fr for fr in self._result.final_floor_responses
                }
                has_margins = any(
                    any(k.startswith("margin_") for k in fr.values)
                    for fr in self._result.final_floor_responses
                )
                header = ["階", "最終本数", "初期本数", "変化"]
                if has_margins:
                    header.append("最小マージン")
                writer.writerow(header)
                for fk in sorted(self._result.final_quantities.keys(),
                                 key=lambda k: int("".join(c for c in k if c.isdigit()) or "0")):
                    final = self._result.final_quantities.get(fk, 0)
                    initial = self._result.initial_quantities.get(fk, 0)
                    row_data = [fk, final, initial, final - initial]
                    if has_margins:
                        fr = floor_resp_map.get(fk)
                        if fr:
                            m = [v for k, v in fr.values.items() if k.startswith("margin_")]
                            row_data.append(f"{min(m):+.4f}" if m else "—")
                        else:
                            row_data.append("—")
                    writer.writerow(row_data)

                # ステップ履歴
                if self._result.history:
                    writer.writerow([])
                    writer.writerow(["# ステップ履歴"])
                    writer.writerow(["ステップ", "操作", "合計本数", "判定", "マージン", "備考"])
                    for step in self._result.history:
                        writer.writerow([
                            step.iteration, step.action, step.total_count,
                            "OK" if step.is_feasible else "NG",
                            f"{step.worst_margin:+.4f}", step.note,
                        ])
            QMessageBox.information(self, "CSV出力", f"保存しました:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "CSV出力エラー", str(exc))

    # -------------------------------------------------------------------
    # HTMLレポート出力
    # -------------------------------------------------------------------

    def _export_html_report(self) -> None:
        """最小化結果をHTMLレポートとして出力します。"""
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "HTMLレポート出力", "minimizer_report.html",
            "HTML (*.html);;すべて (*)",
        )
        if not path:
            return
        try:
            from app.services.report_generator import generate_minimizer_report
            generate_minimizer_report(
                result=self._result,
                output_path=path,
                include_charts=True,
                is_snap=self._is_snap,
            )
            QMessageBox.information(
                self, "HTMLレポート出力",
                f"レポートを出力しました:\n{path}",
            )
        except Exception as exc:
            logger.exception("HTMLレポート出力エラー")
            QMessageBox.critical(self, "HTMLレポート出力エラー", str(exc))

    # -------------------------------------------------------------------
    # コピー
    # -------------------------------------------------------------------

    def _copy_result(self) -> None:
        if self._result is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._result.summary_text())
        self._lbl_status.setText("クリップボードにコピーしました")

    # -------------------------------------------------------------------
    # 結果を .s8i に適用
    # -------------------------------------------------------------------

    def _apply_result_to_s8i(self) -> None:
        """最適化結果のダンパー本数を元の .s8i ファイルに書き戻す。"""
        if self._result is None or not self._model_path or not self._floor_rd_map:
            return

        if not self._result.is_feasible:
            ret = QMessageBox.warning(
                self,
                "制約未充足",
                "最適化結果は性能基準を満たしていません。\n"
                "それでも .s8i ファイルに書き戻しますか？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        # 確認ダイアログ
        quantities = self._result.final_quantities
        detail_lines = [f"  {k}: {v}本" for k, v in sorted(quantities.items())]
        detail = "\n".join(detail_lines)
        ret = QMessageBox.question(
            self,
            "適用確認",
            f"以下のダンパー本数を .s8i ファイルに書き戻します:\n\n"
            f"{detail}\n\n"
            f"合計: {self._result.final_count}本\n\n"
            f"対象: {self._model_path}\n\n"
            "元のファイルが上書きされます。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return

        try:
            from app.models.s8i_parser import parse_s8i

            model = parse_s8i(self._model_path)
            for floor_key, rd_indices in self._floor_rd_map.items():
                qty = quantities.get(floor_key, 0)
                n_rd = len(rd_indices)
                if n_rd == 0:
                    continue
                base_qty = qty // n_rd
                remainder = qty % n_rd
                for i, rd_idx in enumerate(rd_indices):
                    elem_qty = base_qty + (1 if i < remainder else 0)
                    model.update_damper_element(rd_idx, quantity=elem_qty)
            model.write(self._model_path)
            QMessageBox.information(
                self,
                "適用完了",
                f"ダンパー本数を .s8i ファイルに書き戻しました。\n"
                f"合計: {self._result.final_count}本",
            )
            self._lbl_status.setText("結果を .s8i に適用しました")
        except Exception as exc:
            logger.error("結果の .s8i 書き戻しに失敗", exc_info=True)
            QMessageBox.critical(
                self, "書き戻しエラー",
                f".s8i ファイルへの書き戻しに失敗しました:\n{exc}",
            )
