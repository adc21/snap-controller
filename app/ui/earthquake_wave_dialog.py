"""
app/ui/earthquake_wave_dialog.py
地震波選択ダイアログ。

建築構造設計で使用される地震波を選択・管理するためのダイアログです。
組み込みの代表的な観測波・告示波に加え、ユーザーが独自の地震波を追加できます。

レイアウト:
  ┌─────────────────────────────────────────────────────────┐
  │ [検索]  [カテゴリフィルタ]                              │
  ├─────────────────────────────────────────────────────────┤
  │ 地震波リスト                  │ 詳細情報               │
  │ ┌─────────────────────────┐  │ 名称: El Centro NS     │
  │ │ 📡 El Centro NS 1940   │  │ カテゴリ: 観測波        │
  │ │ 📡 El Centro EW 1940   │  │ 最大加速度: 341.7cm/s²  │
  │ │ 📡 Taft NS 1952        │  │ 継続時間: 53.76s        │
  │ │ 📐 告示波 第1種 (極稀)  │  │ 時間刻み: 0.02s        │
  │ │ ...                     │  │ 説明: ...               │
  │ └─────────────────────────┘  │                         │
  ├─────────────────────────────────────────────────────────┤
  │ [方向: X ▼] [倍率: 1.0] [カスタム追加] [適用] [閉じる]│
  └─────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models.earthquake_wave import (
    EarthquakeWave,
    EarthquakeWaveCatalog,
    WAVE_CATEGORIES,
    get_wave_catalog,
)


class EarthquakeWaveDialog(QDialog):
    """
    地震波選択ダイアログ。

    Signals
    -------
    waveSelected(wave: EarthquakeWave)
        地震波が選択された（「適用」ボタン押下）ときに発火。
    wavesSelected(waves: list)
        複数地震波が選択されたときに発火。
    """

    waveSelected = Signal(object)
    wavesSelected = Signal(list)

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        multi_select: bool = False,
    ) -> None:
        super().__init__(parent)
        self._catalog = get_wave_catalog()
        self._multi_select = multi_select
        self._selected_wave: Optional[EarthquakeWave] = None
        self.setWindowTitle("地震波の選択")
        self.setMinimumWidth(800)
        self.setMinimumHeight(550)
        self._setup_ui()
        self._refresh_list()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def selected_wave(self) -> Optional[EarthquakeWave]:
        """最後に選択された地震波。"""
        return self._selected_wave

    def get_selected_waves(self) -> List[EarthquakeWave]:
        """選択中の全地震波を返します（マルチ選択モード用）。"""
        waves = []
        for i in range(self._wave_list.count()):
            item = self._wave_list.item(i)
            if item.isSelected():
                wave = item.data(Qt.UserRole)
                if wave:
                    waves.append(wave)
        return waves

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- 検索・フィルタ行 ----
        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel("検索:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("地震波名、説明を検索...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._search_edit)

        filter_row.addWidget(QLabel("カテゴリ:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("すべて", "")
        for key, info in WAVE_CATEGORIES.items():
            self._cat_combo.addItem(
                f"{info['icon']} {info['label']}", key
            )
        self._cat_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._cat_combo)

        layout.addLayout(filter_row)

        # ---- メインエリア: リスト（左）+ 詳細（右） ----
        splitter = QSplitter(Qt.Horizontal)

        # 地震波リスト
        list_group = QGroupBox("地震波一覧")
        list_layout = QVBoxLayout(list_group)
        self._wave_list = QListWidget()
        if self._multi_select:
            self._wave_list.setSelectionMode(QAbstractItemView.MultiSelection)
        else:
            self._wave_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._wave_list.currentItemChanged.connect(self._on_wave_selected)
        list_layout.addWidget(self._wave_list)

        # 件数ラベル
        self._count_label = QLabel("")
        list_layout.addWidget(self._count_label)
        splitter.addWidget(list_group)

        # 詳細パネル
        detail_group = QGroupBox("地震波情報")
        detail_layout = QVBoxLayout(detail_group)

        self._detail_name = QLabel("<b>地震波を選択してください</b>")
        self._detail_name.setWordWrap(True)
        detail_layout.addWidget(self._detail_name)

        # 情報フォーム
        form = QFormLayout()
        self._detail_category = QLabel("")
        form.addRow("カテゴリ:", self._detail_category)
        self._detail_max_acc = QLabel("")
        form.addRow("最大加速度:", self._detail_max_acc)
        self._detail_duration = QLabel("")
        form.addRow("継続時間:", self._detail_duration)
        self._detail_dt = QLabel("")
        form.addRow("時間刻み:", self._detail_dt)
        self._detail_source = QLabel("")
        self._detail_source.setWordWrap(True)
        form.addRow("出典:", self._detail_source)
        detail_layout.addLayout(form)

        # 説明テキスト
        detail_layout.addWidget(QLabel("説明:"))
        self._detail_desc = QTextEdit()
        self._detail_desc.setReadOnly(True)
        self._detail_desc.setMaximumHeight(120)
        detail_layout.addWidget(self._detail_desc)

        detail_layout.addStretch()
        splitter.addWidget(detail_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, stretch=1)

        # ---- 入力設定行 ----
        settings_group = QGroupBox("入力設定")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("入力方向:"))
        self._direction_combo = QComboBox()
        self._direction_combo.addItems(["X", "Y", "Z", "XY"])
        settings_layout.addWidget(self._direction_combo)

        settings_layout.addWidget(QLabel("倍率:"))
        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setDecimals(3)
        self._scale_spin.setRange(0.001, 100.0)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setSingleStep(0.1)
        settings_layout.addWidget(self._scale_spin)

        settings_layout.addStretch()

        add_custom_btn = QPushButton("カスタム地震波を追加...")
        add_custom_btn.clicked.connect(self._add_custom_wave)
        settings_layout.addWidget(add_custom_btn)

        layout.addWidget(settings_group)

        # ---- ボタン ----
        btn_box = QDialogButtonBox()
        self._apply_btn = QPushButton("ケースに適用")
        self._apply_btn.setDefault(True)
        self._apply_btn.setEnabled(False)
        btn_box.addButton(self._apply_btn, QDialogButtonBox.AcceptRole)
        btn_box.addButton(QDialogButtonBox.Close)
        btn_box.accepted.connect(self._on_apply)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # List management
    # ------------------------------------------------------------------

    def _refresh_list(self) -> None:
        """フィルタ条件に基づいて地震波リストを更新します。"""
        self._wave_list.clear()
        search_text = self._search_edit.text().strip().lower()
        cat_filter = self._cat_combo.currentData()

        waves = self._catalog.all_waves
        if cat_filter:
            waves = [w for w in waves if w.category == cat_filter]
        if search_text:
            waves = [
                w for w in waves
                if search_text in w.name.lower()
                or search_text in w.description.lower()
                or search_text in w.source.lower()
            ]

        for wave in waves:
            cat_info = WAVE_CATEGORIES.get(wave.category, {})
            icon = cat_info.get("icon", "📂")
            text = f"{icon} {wave.name}"
            if wave.max_acc > 0:
                text += f"  ({wave.max_acc:.0f} cm/s²)"

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, wave)

            # 組み込みかカスタムかを色分け
            if not wave.is_builtin:
                item.setForeground(QColor("#17becf"))

            self._wave_list.addItem(item)

        self._count_label.setText(f"{len(waves)} 件")

    def _on_filter_changed(self) -> None:
        self._refresh_list()

    def _on_wave_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:
        if current is None:
            self._apply_btn.setEnabled(False)
            return

        wave: EarthquakeWave = current.data(Qt.UserRole)
        if wave is None:
            return

        self._selected_wave = wave
        self._apply_btn.setEnabled(True)

        # 詳細パネルを更新
        self._detail_name.setText(f"<b>{wave.name}</b>")
        cat_info = WAVE_CATEGORIES.get(wave.category, {})
        self._detail_category.setText(
            f"{cat_info.get('icon', '')} {cat_info.get('label', wave.category)}"
        )
        self._detail_max_acc.setText(
            f"{wave.max_acc:.1f} cm/s²" if wave.max_acc > 0 else "—"
        )
        self._detail_duration.setText(
            f"{wave.duration:.2f} sec" if wave.duration > 0 else "—"
        )
        self._detail_dt.setText(f"{wave.dt:.4f} sec" if wave.dt > 0 else "—")
        self._detail_source.setText(wave.source or "—")
        self._detail_desc.setPlainText(wave.description)

    # ------------------------------------------------------------------
    # Custom wave
    # ------------------------------------------------------------------

    def _add_custom_wave(self) -> None:
        """カスタム地震波追加ダイアログを表示します。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("カスタム地震波の追加")
        dlg.setMinimumWidth(450)
        layout = QVBoxLayout(dlg)

        form = QFormLayout()

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("例: サイト波 Level2-1")
        form.addRow("名称:", name_edit)

        cat_combo = QComboBox()
        cat_combo.addItem("サイト波", "site_specific")
        cat_combo.addItem("カスタム", "custom")
        cat_combo.addItem("観測波", "observed")
        cat_combo.addItem("模擬地震波", "synthetic")
        form.addRow("カテゴリ:", cat_combo)

        file_row = QHBoxLayout()
        file_edit = QLineEdit()
        file_edit.setPlaceholderText("地震波データファイル")
        file_btn = QPushButton("参照...")
        file_btn.setMaximumWidth(64)

        def _browse():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "地震波ファイルを選択", "",
                "地震波データ (*.csv *.txt *.dat *.acc);;すべてのファイル (*)"
            )
            if path:
                file_edit.setText(path)
        file_btn.clicked.connect(_browse)
        file_row.addWidget(file_edit)
        file_row.addWidget(file_btn)
        form.addRow("ファイル:", file_row)

        max_acc_spin = QDoubleSpinBox()
        max_acc_spin.setDecimals(1)
        max_acc_spin.setRange(0, 99999)
        max_acc_spin.setSuffix(" cm/s²")
        form.addRow("最大加速度:", max_acc_spin)

        duration_spin = QDoubleSpinBox()
        duration_spin.setDecimals(2)
        duration_spin.setRange(0, 9999)
        duration_spin.setSuffix(" sec")
        form.addRow("継続時間:", duration_spin)

        dt_spin = QDoubleSpinBox()
        dt_spin.setDecimals(4)
        dt_spin.setRange(0.0001, 1.0)
        dt_spin.setValue(0.01)
        dt_spin.setSuffix(" sec")
        form.addRow("時間刻み:", dt_spin)

        desc_edit = QTextEdit()
        desc_edit.setMaximumHeight(80)
        desc_edit.setPlaceholderText("説明（任意）")
        form.addRow("説明:", desc_edit)

        source_edit = QLineEdit()
        source_edit.setPlaceholderText("データ出典（任意）")
        form.addRow("出典:", source_edit)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("追加")
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec():
            name = name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "入力エラー", "名称を入力してください。")
                return

            wave = EarthquakeWave(
                id=f"custom_{uuid.uuid4().hex[:8]}",
                name=name,
                category=cat_combo.currentData(),
                description=desc_edit.toPlainText().strip(),
                file_path=file_edit.text().strip(),
                max_acc=max_acc_spin.value(),
                duration=duration_spin.value(),
                dt=dt_spin.value(),
                source=source_edit.text().strip(),
                is_builtin=False,
            )
            self._catalog.add_custom(wave)
            self._refresh_list()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _on_apply(self) -> None:
        """選択された地震波を適用します。"""
        if self._multi_select:
            waves = self.get_selected_waves()
            if not waves:
                QMessageBox.warning(self, "情報", "地震波を選択してください。")
                return
            # 方向と倍率を設定
            for w in waves:
                w.direction = self._direction_combo.currentText()
                w.scale_factor = self._scale_spin.value()
            self.wavesSelected.emit(waves)
        else:
            if self._selected_wave is None:
                QMessageBox.warning(self, "情報", "地震波を選択してください。")
                return
            self._selected_wave.direction = self._direction_combo.currentText()
            self._selected_wave.scale_factor = self._scale_spin.value()
            self.waveSelected.emit(self._selected_wave)

        self.accept()
