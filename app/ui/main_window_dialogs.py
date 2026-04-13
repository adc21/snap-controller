"""
app/ui/main_window_dialogs.py
MainWindowのダイアログ起動・設定・レポート関連メソッドを提供するMixinクラス。

main_window.py からのモジュール分割: レポート生成・エクスポート・設定・
スイープ・基準設定・グループ管理・最適化・iRDT設計・ダンパー最小化・
ダンパー挿入・ケース比較・カタログ・テンプレート・入力チェックのメソッドを分離。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

import logging

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass


class _MainWindowDialogsMixin:
    """MainWindow のダイアログ起動・設定・レポート関連メソッド。"""

    # ------------------------------------------------------------------
    # レポート・エクスポート
    # ------------------------------------------------------------------

    def _generate_report(self) -> None:
        """HTML レポートを生成して保存します。"""
        if self._project is None:
            return
        completed = self._project.get_completed_cases()
        if not completed:
            QMessageBox.information(
                self, "レポート生成",
                "完了済みの解析ケースがありません。\n先に解析を実行してください。",
            )
            return

        default_dir = ""
        if self._project.file_path:
            default_dir = str(self._project.file_path.parent)
        default_name = f"{self._project.name}_report.html"
        path, _ = QFileDialog.getSaveFileName(
            self, "HTMLレポート保存先",
            str(Path(default_dir) / default_name) if default_dir else default_name,
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return

        try:
            from app.services.report_generator import generate_report
            generate_report(
                project=self._project,
                cases=completed,
                output_path=path,
                include_charts=True,
            )
            self._log.append_line(f"[レポート] HTML レポートを生成しました: {path}")
            self.statusBar().showMessage(f"レポート生成完了: {path}", 5000)
            QMessageBox.information(
                self, "レポート生成完了",
                f"HTML レポートを生成しました。\n{path}",
            )
        except Exception as e:
            self._log.append_line(f"[レポートエラー] {e}")
            QMessageBox.warning(self, "レポート生成エラー", str(e))

    def _export_results(self) -> None:
        """結果エクスポートダイアログを開きます。"""
        if self._project is None:
            return
        default_dir = ""
        if self._project.file_path:
            default_dir = str(self._project.file_path.parent)
        from .export_dialog import ExportDialog
        dlg = ExportDialog(
            cases=self._project.cases,
            default_dir=default_dir,
            parent=self,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    # 設定
    # ------------------------------------------------------------------

    def _open_settings(self) -> None:
        """アプリケーション設定ダイアログを開きます。"""
        from .settings_dialog import SettingsDialog
        from .theme import ThemeManager

        old_theme = ThemeManager.saved_mode()
        dlg = SettingsDialog(parent=self)
        if dlg.exec():
            settings = dlg.get_settings()
            # グローバル SNAP.exe パスを現在のプロジェクトとサービスに反映
            if self._project and settings.get("snap_exe_path"):
                self._project.snap_exe_path = settings["snap_exe_path"]
                self._service.set_snap_exe_path(settings["snap_exe_path"])
                self._run_selection.set_snap_exe_path(settings["snap_exe_path"])
            # 設定変更後にSNAP未設定警告バナーを更新
            _snap_now_configured = bool(settings.get("snap_exe_path", ""))
            self._welcome.show_snap_warning(not _snap_now_configured)
            if self._project and settings.get("snap_work_dir"):
                self._project.snap_work_dir = settings["snap_work_dir"]
                self._service.set_snap_work_dir(settings["snap_work_dir"])
            # テーマが変更された場合、即時適用を試みる
            new_theme = settings.get("theme", "auto")
            if new_theme != old_theme:
                app = QApplication.instance()
                if app:
                    ThemeManager.apply(app, new_theme)
                    # 各ウィジェットのテーマ依存スタイルを更新
                    self._log.update_theme()
                    self._case_table.refresh()
                    self._chart.update_theme()
                    self._compare_chart.update_theme()
                    self._radar_chart.update_theme()
                    self._result_table.update_theme()
                    self._dashboard.update_theme()
                    self._envelope_chart.update_theme()
                    self._file_preview.update_theme()
                QMessageBox.information(
                    self, "テーマ変更",
                    "テーマを変更しました。\n"
                    "一部の表示は次回起動時に完全に反映されます。"
                )
            self.statusBar().showMessage("設定を保存しました")
            self._update_setup_guide()

    # ------------------------------------------------------------------
    # 解析ダイアログ
    # ------------------------------------------------------------------

    def _open_sweep_dialog(self) -> None:
        """パラメータスイープダイアログを開いて一括ケース生成を行います。"""
        if self._project is None:
            return
        # 選択中のケースがあればベースケースとして使う
        base_case = None
        case_id = self._case_table.selected_case_id()
        if case_id:
            base_case = self._project.get_case(case_id)
        from .sweep_dialog import SweepDialog
        dlg = SweepDialog(
            base_case=base_case,
            parent=self,
        )
        if dlg.exec():
            for case in dlg.generated_cases:
                self._project.add_case(case)
            self._case_table.refresh()
            n = len(dlg.generated_cases)
            self.statusBar().showMessage(f"パラメータスイープ: {n} ケースを追加しました")
            self._log.append_line(f"=== パラメータスイープ: {n} ケースを追加 ===")

    def _open_criteria_dialog(self) -> None:
        """目標性能基準設定ダイアログを開きます。"""
        if self._project is None:
            return
        from .criteria_dialog import CriteriaDialog
        dlg = CriteriaDialog(self._project.criteria, parent=self)
        if dlg.exec():
            self._project.criteria = dlg.get_criteria()
            self._project._touch()
            self._case_table.refresh()
            self._ranking.set_criteria(self._project.criteria)
            self._ranking.set_cases(self._project.cases)
            # グラフウィジェットに基準線を反映
            self._chart.set_criteria(self._project.criteria)
            self._compare_chart.set_criteria(self._project.criteria)
            self._envelope_chart.set_criteria(self._project.criteria)
            self._log.append_line(
                f"=== 目標性能基準を更新: {self._project.criteria.name} ==="
            )
            self.statusBar().showMessage("目標性能基準を更新しました")

    def _open_group_manager(self) -> None:
        """ケースグループ管理ダイアログを開きます。"""
        if self._project is None:
            return
        from PySide6.QtWidgets import (
            QDialog, QDialogButtonBox, QListWidget, QListWidgetItem,
            QInputDialog, QHBoxLayout, QPushButton,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("ケースグループ管理")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel("グループ一覧:"))
        group_list = QListWidget()
        for gname, cids in self._project.case_groups.items():
            count = len(cids)
            item = QListWidgetItem(f"{gname}  ({count} ケース)")
            item.setData(Qt.UserRole, gname)
            group_list.addItem(item)
        layout.addWidget(group_list)

        btn_row = QHBoxLayout()

        btn_add = QPushButton("＋ 追加")
        def _add_group():
            name, ok = QInputDialog.getText(dlg, "新規グループ", "グループ名:")
            if ok and name.strip():
                name = name.strip()
                if name not in self._project.case_groups:
                    self._project.case_groups[name] = []
                    item = QListWidgetItem(f"{name}  (0 ケース)")
                    item.setData(Qt.UserRole, name)
                    group_list.addItem(item)
                    self._project._touch()
        btn_add.clicked.connect(_add_group)
        btn_row.addWidget(btn_add)

        btn_rename = QPushButton("名前変更")
        def _rename_group():
            current = group_list.currentItem()
            if current is None:
                return
            old_name = current.data(Qt.UserRole)
            new_name, ok = QInputDialog.getText(
                dlg, "名前変更", "新しいグループ名:", text=old_name
            )
            if ok and new_name.strip() and new_name.strip() != old_name:
                new_name = new_name.strip()
                self._project.case_groups[new_name] = self._project.case_groups.pop(old_name)
                count = len(self._project.case_groups[new_name])
                current.setText(f"{new_name}  ({count} ケース)")
                current.setData(Qt.UserRole, new_name)
                self._project._touch()
        btn_rename.clicked.connect(_rename_group)
        btn_row.addWidget(btn_rename)

        btn_del = QPushButton("－ 削除")
        def _delete_group():
            current = group_list.currentItem()
            if current is None:
                return
            gname = current.data(Qt.UserRole)
            reply = QMessageBox.question(
                dlg, "確認",
                f"グループ「{gname}」を削除しますか？\n（ケース自体は削除されません）",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                del self._project.case_groups[gname]
                group_list.takeItem(group_list.row(current))
                self._project._touch()
        btn_del.clicked.connect(_delete_group)
        btn_row.addWidget(btn_del)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        btn_box = QDialogButtonBox(QDialogButtonBox.Close)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        dlg.exec()
        self._case_table.refresh()
        self._ranking.set_case_groups(self._project.case_groups)

    # ------------------------------------------------------------------
    # 最適化ダイアログ
    # ------------------------------------------------------------------

    def _open_optimizer_dialog(self) -> None:
        """ダンパー最適化ダイアログを開きます。"""
        if self._project is None:
            return
        # 選択中のケースをベースケースとして使う
        base_case = None
        case_id = self._case_table.selected_case_id()
        if case_id:
            base_case = self._project.get_case(case_id)

        from .optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog(
            base_case=base_case,
            criteria=self._project.criteria,
            snap_exe_path=self._project.snap_exe_path,
            snap_work_dir=self._project.snap_work_dir,
            parent=self,
        )
        if dlg.exec():
            # 最良解をケースとして追加
            best_params = dlg.best_params
            result = dlg.result
            if best_params and result and result.best:
                from app.models import AnalysisCase
                # パラメータ文字列を生成
                param_str = ", ".join(
                    f"{k}={v:.4g}" for k, v in best_params.items()
                )
                case = AnalysisCase(
                    name=f"最適化結果 ({result.config.damper_type})",
                    parameters=best_params,
                    notes=f"最適化結果: {result.config.objective_label} = "
                          f"{result.best.objective_value:.6g}\n"
                          f"パラメータ: {param_str}\n"
                          f"探索手法: {result.config.method}\n"
                          f"評価数: {len(result.all_candidates)}",
                )
                if base_case and base_case.model_path:
                    case.model_path = base_case.model_path
                self._project.add_case(case)
                self._case_table.refresh()
                self._project._touch()
                self._log.append_line(
                    f"=== 最適化結果をケース追加: {case.name} "
                    f"({param_str}) ==="
                )
                self.statusBar().showMessage(
                    f"最適化結果をケース「{case.name}」として追加しました"
                )

    def _open_irdt_wizard(self) -> None:
        """iRDT 設計ウィザードを開きます。"""
        # PeriodXbnReader があればそこからモード情報を取得
        period_reader = None
        floor_masses = None
        for e in self._binary_result._entries.values():
            if e.loader:
                try:
                    pr = e.loader.period_reader()
                    if pr and pr.modes:
                        period_reader = pr
                        break
                except Exception:
                    pass
        # 層質量: プロジェクトの s8i モデルから取得を試みる
        if self._project and hasattr(self._project, "model") and self._project.model:
            model = self._project.model
            if hasattr(model, "floor_masses"):
                floor_masses = model.floor_masses

        # ベース .s8i パスを取得
        base_s8i_path = None
        if self._project and hasattr(self._project, "cases") and self._project.cases:
            for case in self._project.cases:
                if case.model_path and Path(case.model_path).exists():
                    base_s8i_path = case.model_path
                    break

        from .irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog(
            period_reader=period_reader,
            floor_masses=floor_masses,
            base_s8i_path=base_s8i_path,
            parent=self,
        )
        dlg.designCompleted.connect(self._on_irdt_design_completed)
        dlg.exec()

    def _on_irdt_design_completed(self, plan) -> None:
        """iRDT 設計結果を受け取ります。"""
        self._log.append_line(f"=== iRDT 設計完了: モード{plan.target_mode}, "
                              f"μ={plan.total_mass_ratio:.4f} ===")
        self.statusBar().showMessage(
            f"iRDT 設計完了 — モード{plan.target_mode}, "
            f"質量比 μ={plan.total_mass_ratio:.4f}",
            8000,
        )

    def _open_minimizer_dialog(self) -> None:
        """ダンパー本数最小化ダイアログを開きます。"""
        if self._project is None:
            return

        # 選択中のケースから .s8i パスを取得
        base_case = None
        case_id = self._case_table.selected_case_id()
        if case_id:
            base_case = self._project.get_case(case_id)

        model_path = (base_case.model_path or "") if base_case else ""
        if not model_path:
            QMessageBox.warning(
                self, "モデル未選択",
                "解析ケースを選択してからダンパー本数最小化を実行してください。",
            )
            return

        # .s8i からフロア→RD要素マッピングを構築
        from app.services.snap_evaluator import build_floor_rd_map
        try:
            floor_rd_map, current_quantities, floor_keys = build_floor_rd_map(model_path)
        except Exception as e:
            QMessageBox.critical(
                self, "モデル読み込みエラー",
                f".s8i ファイルの読み込みに失敗しました:\n{e}",
            )
            return

        if not floor_keys:
            QMessageBox.warning(
                self, "ダンパーなし",
                "選択したモデルにダンパー要素（RD行）がありません。",
            )
            return

        # 最大本数 = 現在の本数 * 2（余裕を持たせる）
        max_quantities = {k: max(v * 2, 10) for k, v in current_quantities.items()}

        # SnapEvaluator ベースの evaluate_fn を構築
        evaluate_fn = None
        if self._project.snap_exe_path and self._project.criteria:
            from app.services.snap_evaluator import create_minimizer_evaluate_fn
            evaluate_fn = create_minimizer_evaluate_fn(
                snap_exe_path=self._project.snap_exe_path,
                base_s8i_path=model_path,
                criteria=self._project.criteria,
                floor_rd_map=floor_rd_map,
                log_callback=lambda msg: self._log.append_line(msg),
                snap_work_dir=self._project.snap_work_dir,
            )
            if evaluate_fn:
                self._log.append_line(
                    f"[INFO] SNAP実評価関数でダンパー本数最小化を実行します。"
                    f"（{len(floor_keys)}階, 合計{sum(current_quantities.values())}本）"
                )

        from .minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=floor_keys,
            current_quantities=current_quantities,
            max_quantities=max_quantities,
            evaluate_fn=evaluate_fn,
            parent=self,
            model_path=model_path,
            floor_rd_map=floor_rd_map,
        )
        dlg.minimizationCompleted.connect(self._on_minimizer_completed)
        dlg.exec()

    def _on_minimizer_completed(self, result) -> None:
        """ダンパー本数最小化結果を受け取ります。"""
        self._log.append_line(
            f"=== ダンパー最小化完了: 合計{result.final_count}本, "
            f"マージン={result.final_margin:.4f} ==="
        )
        self.statusBar().showMessage(
            f"ダンパー最小化完了 — {result.final_count}本配置",
            8000,
        )

    def _open_damper_injector(self) -> None:
        """iRDT/iOD ダンパー挿入ダイアログを開きます。"""
        base_case = None
        if self._project:
            case_id = self._case_table.selected_case_id()
            if case_id:
                base_case = self._project.get_case(case_id)

        from .damper_injector_dialog import DamperInjectorDialog
        dlg = DamperInjectorDialog(base_case=base_case, parent=self)
        if dlg.exec():
            new_case = dlg.accepted_case
            if new_case and self._project:
                self._project.add_case(new_case)
                self._case_table.refresh()
                self._log.append_line(
                    f"=== ダンパー挿入完了: ケース「{new_case.name}」を追加 ==="
                )
                self.statusBar().showMessage(
                    f"ダンパー挿入完了 — ケース「{new_case.name}」",
                    8000,
                )

    def _open_case_compare(self) -> None:
        """2ケース詳細比較ダイアログを開きます。"""
        if self._project is None:
            return
        from app.models import AnalysisCaseStatus
        completed = [
            c for c in self._project.cases
            if c.status == AnalysisCaseStatus.COMPLETED and c.result_summary
        ]
        if len(completed) < 2:
            QMessageBox.information(
                self, "情報",
                "比較するには完了済みケースが2つ以上必要です。",
            )
            return
        # 選択中のケースをケースAとして使う
        initial_a = None
        case_id = self._case_table.selected_case_id()
        if case_id:
            initial_a = self._project.get_case(case_id)
        from .case_compare_dialog import CaseCompareDialog
        dlg = CaseCompareDialog(
            cases=self._project.cases,
            initial_a=initial_a,
            parent=self,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    # カタログ・テンプレート
    # ------------------------------------------------------------------

    def _open_damper_catalog(self) -> None:
        """ダンパーカタログダイアログを開きます。"""
        from .damper_catalog_dialog import DamperCatalogDialog
        dlg = DamperCatalogDialog(parent=self)
        dlg.specSelected.connect(self._on_catalog_spec_selected)
        dlg.exec()

    def _on_catalog_spec_selected(self, spec) -> None:
        """カタログからダンパー仕様が選択された際の処理。"""
        if self._project is None:
            return
        # 選択中のケースに適用、なければ新規ケース作成
        case_id = self._case_table.selected_case_id()
        if case_id:
            case = self._project.get_case(case_id)
        else:
            from app.models import AnalysisCase
            case = AnalysisCase(name=f"{spec.name} ケース")
            self._project.add_case(case)
            self._case_table.refresh()

        if case:
            # カタログのパラメータをケースに適用
            if spec.snap_keyword:
                case.damper_params[spec.name] = dict(spec.parameters)
            self._project._touch()
            self._case_table.refresh()
            self._log.append_line(
                f"=== カタログから適用: {spec.name} → ケース「{case.name}」 ==="
            )
            self.statusBar().showMessage(f"カタログ「{spec.name}」をケースに適用しました")

    def _open_template_dialog(self) -> None:
        """テンプレート管理ダイアログを開きます。"""
        if self._project is None:
            return
        from .template_dialog import TemplateDialog
        dlg = TemplateDialog(
            template_manager=self._template_manager,
            parent=self,
        )
        dlg.templateApplied.connect(self._on_template_applied)
        dlg.exec()

    def _on_template_applied(self, template) -> None:
        """テンプレートが適用された際の処理。"""
        if self._project is None:
            return
        # 選択中のケースに適用、なければ新規ケース作成
        case_id = self._case_table.selected_case_id()
        if case_id:
            case = self._project.get_case(case_id)
        else:
            from app.models import AnalysisCase
            case = AnalysisCase(name=f"{template.name} ケース")
            self._project.add_case(case)

        if case:
            # テンプレートのパラメータをケースにマージ
            case.parameters.update(template.parameters)
            case.damper_params.update(template.damper_params)
            self._project._touch()
            self._case_table.refresh()
            self._log.append_line(
                f"=== テンプレート適用: {template.name} → ケース「{case.name}」 ==="
            )
            self.statusBar().showMessage(
                f"テンプレート「{template.name}」をケース「{case.name}」に適用しました"
            )

    def _save_as_template(self) -> None:
        """選択中のケースをテンプレートとして保存します。"""
        if self._project is None:
            return
        case_id = self._case_table.selected_case_id()
        if case_id is None:
            QMessageBox.information(
                self, "情報",
                "テンプレートとして保存するケースを選択してください。",
            )
            return
        case = self._project.get_case(case_id)
        if case is None:
            return

        from .template_dialog import SaveTemplateDialog
        dlg = SaveTemplateDialog(case=case, parent=self)
        if dlg.exec():
            tpl = dlg.get_template()
            if tpl:
                path = self._template_manager.add(tpl)
                self._log.append_line(
                    f"=== テンプレート保存: {tpl.name} → {path} ==="
                )
                self.statusBar().showMessage(
                    f"テンプレート「{tpl.name}」を保存しました"
                )

    # ------------------------------------------------------------------
    # 入力チェック
    # ------------------------------------------------------------------

    def _validate_selected(self) -> None:
        """選択中のケースの入力チェックを行います。"""
        if self._project is None:
            return
        case_id = self._case_table.selected_case_id()
        if case_id is None:
            QMessageBox.information(self, "情報", "チェックするケースを選択してください。")
            return
        case = self._project.get_case(case_id)
        if case is None:
            return
        from app.services.validation import validate_case
        from .validation_dialog import ValidationDialog
        result = validate_case(
            case,
            snap_exe_path=self._project.snap_exe_path,
            s8i_model=self._project.s8i_model,
        )
        dlg = ValidationDialog(result, case_name=case.name, parent=self)
        dlg.exec()
        self._log.append_line(result.get_display_text())

    def _validate_all(self) -> None:
        """全ケースの入力チェックを行います。"""
        if self._project is None or not self._project.cases:
            QMessageBox.information(self, "情報", "ケースがありません。")
            return
        from app.services.validation import validate_case
        from .validation_dialog import BatchValidationDialog
        results_map = {}
        for case in self._project.cases:
            result = validate_case(
                case,
                snap_exe_path=self._project.snap_exe_path,
                s8i_model=self._project.s8i_model,
            )
            results_map[case.name] = result
        dlg = BatchValidationDialog(results_map, parent=self)
        dlg.exec()
