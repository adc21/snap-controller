"""
app/ui/multi_wave_dialog.py
複数地震波一括解析ダイアログ。

同一のダンパー構成（ベースケース）に対して複数の地震波を一括適用し、
ケースを自動生成・実行する機能を提供します。

レイアウト:
  ┌──────────────────────────────────────────────────┐
  │ ベースケース: [ケース名表示]                      │
  ├──────────────────────────────────────────────────┤
  │ 地震波選択                                        │
  │ [カテゴリフィルタ]                                │
  │ ☑ El Centro NS 1940                              │
  │ ☑ Taft NS 1952                                   │
  │ ☑ 八戸 NS 1968                                   │
  │ ☐ JMA神戸 NS 1995                                │
  │ ☑ 告示波 第1種地盤 (極めて稀)                     │
  │ …                                                │
  ├──────────────────────────────────────────────────┤
  │ 生成オプション                                    │
  │ [ケース名プレフィックス: _________]               │
  │ [方向: X / Y / XY ]                               │
  │ [倍率: ____]                                      │
  │ ☐ 生成後に自動実行（デモモード）                  │
  ├──────────────────────────────────────────────────┤
  │ [全選択] [全解除] [標準セット]                     │
  │                    [生成] [キャンセル]             │
  └──────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.models.analysis_case import AnalysisCase
from app.models.earthquake_wave import (
    EarthquakeWave,
    EarthquakeWaveCatalog,
    WAVE_CATEGORIES,
    get_wave_catalog,
)


# 標準入力波セット（設計実務で頻繁に使用される組み合わせ）
_STANDARD_SETS = {
    "告示波標準セット（3波）": [
        "kokujihado_1", "kokujihado_2", "kokujihado_3",
    ],
    "観測波標準セット（3波）": [
        "el_centro_ns", "taft_ns", "hachinohe_ns",
    ],
    "基本6波セット": [
        "el_centro_ns", "taft_ns", "hachinohe_ns",
        "kokujihado_1", "kokujihado_2", "kokujihado_3",
    ],
    "全告示波セット（6波）": [
        "kokujihado_1", "kokujihado_2", "kokujihado_3",
        "kokujihado_rare_1", "kokujihado_rare_2", "kokujihado_rare_3",
    ],
    "レベル2 全波セット": [
        "el_centro_ns", "taft_ns", "hachinohe_ns",
        "kobe_ns",
        "kokujihado_1", "kokujihado_2", "kokujihado_3",
    ],
}


class MultiWaveDialog(QDialog):
    """
    複数地震波一括解析ダイアログ。

    Parameters
    ----------
    base_case : AnalysisCase, optional
        ベースとなるケース。None の場合は空のケースが使用されます。
    parent : QWidget, optional
    """

    def __init__(
        self,
        base_case: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("複数地震波一括解析")
        self.setMinimumSize(560, 600)

        self._base_case = base_case
        self._catalog = get_wave_catalog()
        self._wave_checkboxes: List[tuple[QCheckBox, EarthquakeWave]] = []
        self._generated_cases: List[AnalysisCase] = []
        self._auto_run: bool = False

        self._setup_ui()
        self._populate_waves()

    @property
    def generated_cases(self) -> List[AnalysisCase]:
        """生成されたケースリスト。"""
        return self._generated_cases

    @property
    def auto_run_requested(self) -> bool:
        """生成後に自動デモ実行が要求されたかどうか。"""
        return self._auto_run

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- ベースケース情報 ----
        base_group = QGroupBox("ベースケース")
        base_layout = QVBoxLayout(base_group)
        if self._base_case:
            name = self._base_case.name
            params_count = len(self._base_case.parameters)
            damper_count = len(self._base_case.damper_params)
            base_layout.addWidget(QLabel(
                f"ケース名: <b>{name}</b>  |  "
                f"パラメータ: {params_count}個  |  "
                f"ダンパー設定: {damper_count}個"
            ))
        else:
            base_layout.addWidget(QLabel(
                "<i>ベースケースが選択されていません（デフォルト設定で生成）</i>"
            ))
        layout.addWidget(base_group)

        # ---- カテゴリフィルタ ----
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("カテゴリ:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("すべて", "")
        for key, info in WAVE_CATEGORIES.items():
            self._cat_combo.addItem(f"{info['icon']} {info['label']}", key)
        self._cat_combo.currentIndexChanged.connect(self._on_category_changed)
        filter_row.addWidget(self._cat_combo, stretch=1)
        layout.addLayout(filter_row)

        # ---- 地震波チェックリスト ----
        wave_group = QGroupBox("入力地震波を選択")
        wave_layout = QVBoxLayout(wave_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._wave_container = QWidget()
        self._wave_list_layout = QVBoxLayout(self._wave_container)
        self._wave_list_layout.setAlignment(Qt.AlignTop)
        self._wave_list_layout.setSpacing(2)
        scroll.setWidget(self._wave_container)
        wave_layout.addWidget(scroll)

        # 選択ボタン行
        sel_row = QHBoxLayout()
        btn_all = QPushButton("全選択")
        btn_all.setMaximumWidth(70)
        btn_all.clicked.connect(self._select_all)
        sel_row.addWidget(btn_all)

        btn_none = QPushButton("全解除")
        btn_none.setMaximumWidth(70)
        btn_none.clicked.connect(self._deselect_all)
        sel_row.addWidget(btn_none)

        # 標準セット選択
        self._set_combo = QComboBox()
        self._set_combo.addItem("標準セットを選択…")
        for set_name in _STANDARD_SETS:
            self._set_combo.addItem(set_name)
        self._set_combo.currentIndexChanged.connect(self._on_set_selected)
        sel_row.addWidget(self._set_combo, stretch=1)

        sel_row.addStretch()
        wave_layout.addLayout(sel_row)
        layout.addWidget(wave_group, stretch=1)

        # ---- 生成オプション ----
        opt_group = QGroupBox("生成オプション")
        opt_form = QFormLayout(opt_group)

        self._prefix_edit = QLineEdit()
        default_prefix = self._base_case.name if self._base_case else "Case"
        self._prefix_edit.setText(default_prefix)
        self._prefix_edit.setPlaceholderText("ケース名のプレフィックス")
        opt_form.addRow("ケース名プレフィックス:", self._prefix_edit)

        self._dir_combo = QComboBox()
        self._dir_combo.addItems(["X", "Y", "XY"])
        opt_form.addRow("入力方向:", self._dir_combo)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(0.01, 100.0)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setDecimals(2)
        opt_form.addRow("倍率:", self._scale_spin)

        self._auto_run_check = QCheckBox("生成後にデモ実行する")
        self._auto_run_check.setToolTip(
            "生成されたケースをモックデータで自動実行します（SNAP不要）"
        )
        opt_form.addRow(self._auto_run_check)

        layout.addWidget(opt_group)

        # ---- 情報ラベル ----
        self._info_label = QLabel()
        self._info_label.setStyleSheet("color: gray;")
        layout.addWidget(self._info_label)
        self._update_info_label()

        # ---- ボタン ----
        btn_box = QDialogButtonBox()
        self._generate_btn = QPushButton("ケースを生成")
        self._generate_btn.setDefault(True)
        btn_box.addButton(self._generate_btn, QDialogButtonBox.AcceptRole)
        btn_box.addButton(QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_generate)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Wave list
    # ------------------------------------------------------------------

    def _populate_waves(self) -> None:
        """地震波のチェックボックスリストを構築します。"""
        for cb, _ in self._wave_checkboxes:
            cb.deleteLater()
        self._wave_checkboxes.clear()

        for wave in self._catalog.all_waves:
            cat_info = WAVE_CATEGORIES.get(wave.category, {})
            icon = cat_info.get("icon", "📁")
            label = f"{icon} {wave.name}"
            if wave.max_acc > 0:
                label += f"  ({wave.max_acc:.0f} cm/s²)"

            cb = QCheckBox(label)
            cb.setToolTip(wave.description)
            cb.setProperty("wave_id", wave.id)
            cb.setProperty("wave_category", wave.category)
            cb.toggled.connect(self._update_info_label)
            self._wave_list_layout.addWidget(cb)
            self._wave_checkboxes.append((cb, wave))

    def _on_category_changed(self) -> None:
        """カテゴリフィルタ変更時の処理。"""
        cat_key = self._cat_combo.currentData()
        for cb, wave in self._wave_checkboxes:
            if not cat_key:
                cb.setVisible(True)
            else:
                cb.setVisible(wave.category == cat_key)

    def _select_all(self) -> None:
        for cb, _ in self._wave_checkboxes:
            if cb.isVisible():
                cb.setChecked(True)

    def _deselect_all(self) -> None:
        for cb, _ in self._wave_checkboxes:
            cb.setChecked(False)

    def _on_set_selected(self, index: int) -> None:
        """標準セット選択時の処理。"""
        if index <= 0:
            return
        set_name = self._set_combo.currentText()
        wave_ids = _STANDARD_SETS.get(set_name, [])
        if not wave_ids:
            return

        # まず全解除
        for cb, _ in self._wave_checkboxes:
            cb.setChecked(False)
        # セットに含まれる波を選択
        for cb, wave in self._wave_checkboxes:
            if wave.id in wave_ids:
                cb.setChecked(True)
        # コンボボックスをリセット
        self._set_combo.setCurrentIndex(0)

    def _get_selected_waves(self) -> List[EarthquakeWave]:
        """選択中の地震波リストを返します。"""
        return [wave for cb, wave in self._wave_checkboxes if cb.isChecked()]

    def _update_info_label(self) -> None:
        """選択状況の情報ラベルを更新します。"""
        selected = self._get_selected_waves()
        self._info_label.setText(
            f"選択中: {len(selected)} 波  →  {len(selected)} ケースが生成されます"
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def _on_generate(self) -> None:
        """ケースを生成します。"""
        selected_waves = self._get_selected_waves()
        if not selected_waves:
            QMessageBox.warning(
                self, "警告",
                "少なくとも1つの地震波を選択してください。",
            )
            return

        prefix = self._prefix_edit.text().strip() or "Case"
        direction = self._dir_combo.currentText()
        scale = self._scale_spin.value()

        self._generated_cases.clear()

        for wave in selected_waves:
            # ベースケースからクローン（あればパラメータとダンパー設定を継承）
            if self._base_case:
                case = self._base_case.clone()
            else:
                case = AnalysisCase()

            # ケース名を地震波名で上書き
            case.name = f"{prefix} - {wave.name}"

            # 地震波パラメータを設定
            case.parameters["EQ_WAVE"] = wave.name
            case.parameters["EQ_WAVE_ID"] = wave.id
            case.parameters["EQ_DIRECTION"] = direction
            case.parameters["EQSCALE"] = str(scale * wave.scale_factor)
            if wave.file_path:
                case.parameters["EQFILE"] = wave.file_path

            # 地震波の物理パラメータもメタ情報として保存
            case.parameters["_wave_max_acc"] = wave.max_acc
            case.parameters["_wave_duration"] = wave.duration
            case.parameters["_wave_dt"] = wave.dt
            case.parameters["_wave_category"] = wave.category

            # メモに地震波情報を記載
            case.notes = (
                f"地震波: {wave.name}\n"
                f"カテゴリ: {wave.category}\n"
                f"最大加速度: {wave.max_acc} cm/s²\n"
                f"方向: {direction}, 倍率: {scale}\n"
                f"{wave.description}"
            )

            self._generated_cases.append(case)

        self._auto_run = self._auto_run_check.isChecked()

        QMessageBox.information(
            self, "完了",
            f"{len(self._generated_cases)} ケースを生成しました。",
        )
        self.accept()
