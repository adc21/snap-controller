"""
app/ui/damper_placement_widget.py
ダンパー配置エディタウィジェット。

各層にダンパーを配置するための視覚的エディタです。
層ごとにダンパーの種類・本数を指定し、
建物の断面図イメージで配置状況を確認できます。

レイアウト:
  ┌───────────────────────────────────────────────┐
  │ [階数設定] [プリセット適用] [クリア]          │
  ├──────────────────────┬────────────────────────┤
  │ 配置テーブル         │ 断面ビジュアル         │
  │ ┌───┬────┬────┬───┐  │                        │
  │ │層 │種類│本数│備考│  │   ┌─── 5F ─────┐     │
  │ ├───┼────┼────┼───┤  │   │ ●● ●●       │     │
  │ │5F │油圧│4  │   │  │   ├─── 4F ─────┤     │
  │ │4F │鋼材│2  │   │  │   │ ▲▲          │     │
  │ │...│    │   │   │  │   ├─── ...  ─────┤     │
  │ └───┴────┴────┴───┘  │   └──────────────┘     │
  └──────────────────────┴────────────────────────┘

UX改善（新）: 配置バランスサマリー + 偏りアラート。
  テーブルとビジュアルの下部に「配置サマリーバー」を追加します。
  - 「合計 X 本 / Y層に配置 / 1層あたり平均 Z 本」のサマリーラベル
  - 最大層本数が平均の2倍以上の場合に「⚠ 配置が偏っています」警告を表示
  - テーブルやスピンボックスを更新するたびにリアルタイムで再計算
  配置が均等かどうかを直感的に確認でき、耐震上バランスのとれた
  制振計画を立てるヒントを提供します。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QKeyCombination
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .snap_params import DAMPER_TYPES

logger = logging.getLogger(__name__)

# 配置テーブルの列
_COL_FLOOR = 0
_COL_TYPE = 1
_COL_COUNT = 2
_COL_DIRECTION = 3
_COL_NOTES = 4
_COLUMNS = ["層", "ダンパー種類", "本数", "方向", "備考"]

# 方向選択肢
_DIRECTIONS = ["X方向", "Y方向", "X+Y方向", "全方向"]

# ダンパー種類ごとの描画シンボルと色
_DAMPER_SYMBOLS: Dict[str, tuple[str, str]] = {
    "なし":                     ("—",  "#888888"),
    "油圧ダンパー":             ("●",  "#1f77b4"),
    "オイルダンパー（速度依存型）": ("◆", "#ff7f0e"),
    "鋼材ダンパー":             ("▲",  "#2ca02c"),
    "積層ゴム支承（免震）":      ("■",  "#d62728"),
    "鉛プラグ入り積層ゴム（LRB）": ("★", "#9467bd"),
    "すべり支承":               ("◇",  "#8c564b"),
    "カスタム":                 ("✦",  "#7f7f7f"),
}


@dataclass
class FloorDamperConfig:
    """1層のダンパー配置設定。"""
    floor: int = 1
    damper_type: str = "なし"
    count: int = 0
    direction: str = "X方向"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FloorDamperConfig":
        return cls(**data)


class _BuildingVisualWidget(QWidget):
    """建物断面のダンパー配置ビジュアル表示ウィジェット。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._configs: List[FloorDamperConfig] = []
        self.setMinimumWidth(250)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_configs(self, configs: List[FloorDamperConfig]) -> None:
        self._configs = configs
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        margin = 30
        n_floors = len(self._configs)

        colors = self._get_theme_colors()

        if n_floors == 0:
            painter.setPen(QPen(QColor("#888888")))
            painter.drawText(
                self.rect(), Qt.AlignCenter,
                "階数を設定してください"
            )
            painter.end()
            return

        layout = self._compute_building_layout(w, h, margin, n_floors)
        self._paint_building_frame(painter, layout, colors)

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        for i, config in enumerate(reversed(self._configs)):
            floor_idx = n_floors - 1 - i
            self._paint_floor_layer(painter, config, floor_idx, layout, colors, font)

        self._paint_legend(painter, w, h, margin, layout["building_bottom"], colors["text_color"])

        painter.end()

    @staticmethod
    def _compute_building_layout(w: int, h: int, margin: int, n_floors: int) -> dict:
        building_left = margin + 30
        building_right = w - margin - 20
        building_top = margin
        building_bottom = h - margin
        building_w = building_right - building_left
        floor_h = (building_bottom - building_top) / n_floors
        return {
            "building_left": building_left,
            "building_right": building_right,
            "building_top": building_top,
            "building_bottom": building_bottom,
            "building_w": building_w,
            "floor_h": floor_h,
        }

    def _get_theme_colors(self) -> dict:
        bg_color = self.palette().color(self.backgroundRole())
        is_dark = bg_color.lightnessF() < 0.5
        return {
            "is_dark": is_dark,
            "line_color": QColor("#cccccc") if is_dark else QColor("#444444"),
            "text_color": QColor("#dddddd") if is_dark else QColor("#333333"),
            "floor_bg": QColor(60, 60, 80, 40) if is_dark else QColor(200, 220, 240, 60),
        }

    @staticmethod
    def _paint_building_frame(painter: QPainter, layout: dict, colors: dict) -> None:
        painter.setPen(QPen(colors["line_color"], 2))
        painter.setBrush(QBrush(colors["floor_bg"]))
        painter.drawRect(
            int(layout["building_left"]), int(layout["building_top"]),
            int(layout["building_w"]),
            int(layout["building_bottom"] - layout["building_top"])
        )

    @staticmethod
    def _paint_floor_layer(
        painter: QPainter,
        config: "FloorDamperConfig",
        floor_idx: int,
        layout: dict,
        colors: dict,
        font: QFont,
    ) -> None:
        from PySide6.QtCore import QRectF

        building_left = layout["building_left"]
        building_right = layout["building_right"]
        building_w = layout["building_w"]
        floor_h = layout["floor_h"]

        y_top = layout["building_top"] + floor_idx * floor_h
        y_bottom = y_top + floor_h

        painter.setPen(QPen(colors["line_color"], 1, Qt.DashLine))
        painter.drawLine(
            int(building_left), int(y_bottom),
            int(building_right), int(y_bottom)
        )

        painter.setPen(QPen(colors["text_color"]))
        painter.drawText(
            QRectF(0, y_top, building_left - 5, floor_h),
            Qt.AlignRight | Qt.AlignVCenter,
            f"{config.floor}F"
        )

        if config.damper_type == "なし" or config.count <= 0:
            return

        symbol, color_str = _DAMPER_SYMBOLS.get(
            config.damper_type, ("?", "#888888")
        )
        painter.setPen(QPen(QColor(color_str)))
        symbol_font = QFont()
        symbol_font.setPointSize(max(8, min(14, int(floor_h * 0.5))))
        symbol_font.setBold(True)
        painter.setFont(symbol_font)

        count = min(config.count, 8)
        spacing = building_w / (count + 1)
        y_center = y_top + floor_h / 2

        for j in range(count):
            x = building_left + spacing * (j + 1)
            painter.drawText(
                QRectF(x - 10, y_center - 10, 20, 20),
                Qt.AlignCenter,
                symbol
            )

        if config.count > 8:
            painter.setFont(font)
            painter.setPen(QPen(colors["text_color"]))
            painter.drawText(
                QRectF(building_right + 3, y_top, 40, floor_h),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"×{config.count}"
            )

        painter.setFont(font)

    def _paint_legend(
        self,
        painter: QPainter,
        w: int,
        h: int,
        margin: int,
        building_bottom: float,
        text_color: QColor,
    ) -> None:
        legend_y = building_bottom + 5
        if legend_y + 15 >= h:
            return

        painter.setPen(QPen(text_color))
        legend_font = QFont()
        legend_font.setPointSize(7)
        painter.setFont(legend_font)

        used_types = {c.damper_type for c in self._configs if c.damper_type != "なし"}
        x_offset = margin
        for dtype in used_types:
            symbol, color_str = _DAMPER_SYMBOLS.get(dtype, ("?", "#888"))
            painter.setPen(QPen(QColor(color_str)))
            text = f"{symbol} {dtype}"
            painter.drawText(int(x_offset), int(legend_y + 12), text)
            x_offset += len(text) * 8 + 10
            if x_offset > w - margin:
                break


class DamperPlacementWidget(QWidget):
    """
    ダンパー配置エディタウィジェット。

    各層にダンパーの種類・本数・方向を設定できます。
    右側に建物断面のビジュアル表示があり、配置状況をリアルタイムで確認できます。

    Public API
    ----------
    set_floor_count(n)  — 階数を設定します
    get_configs()       — 全層の配置設定を取得します
    set_configs(list)   — 配置設定を一括設定します
    to_damper_params()  — AnalysisCase.damper_params 形式に変換します

    Signals
    -------
    configChanged()     — 配置設定が変更されたときに発火します
    """

    configChanged = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._configs: List[FloorDamperConfig] = []
        self._setup_ui()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_floor_count(self, n: int) -> None:
        """階数を設定し、テーブルを初期化します。"""
        self._floor_spin.setValue(n)
        self._apply_floor_count()

    def get_configs(self) -> List[FloorDamperConfig]:
        """全層の配置設定を取得します。"""
        self._read_table()
        return list(self._configs)

    def set_configs(self, configs: List[FloorDamperConfig]) -> None:
        """配置設定を一括設定します。"""
        self._configs = list(configs)
        if configs:
            self._floor_spin.setValue(len(configs))
        self._populate_table()

    def to_damper_params_list(self) -> List[Dict[str, Any]]:
        """
        AnalysisCase 用のダンパー配置パラメータリストに変換します。

        Returns
        -------
        list of dict
            各層の配置情報。AnalysisCase.damper_params["placement"] として保存可能。
        """
        self._read_table()
        return [c.to_dict() for c in self._configs if c.damper_type != "なし"]

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addLayout(self._build_ctrl_row())
        layout.addLayout(self._build_main_area(), stretch=1)
        layout.addWidget(self._build_summary_bar())
        self._apply_floor_count()

    def _build_ctrl_row(self) -> QHBoxLayout:
        ctrl_row = QHBoxLayout()

        ctrl_row.addWidget(QLabel("階数:"))
        self._floor_spin = QSpinBox()
        self._floor_spin.setRange(1, 60)
        self._floor_spin.setValue(5)
        self._floor_spin.setSuffix(" 層")
        ctrl_row.addWidget(self._floor_spin)

        apply_btn = QPushButton("階数を適用")
        apply_btn.clicked.connect(self._apply_floor_count)
        ctrl_row.addWidget(apply_btn)

        ctrl_row.addStretch()

        copy_btn = QPushButton("📋 Excelへコピー")
        copy_btn.setToolTip(
            "テーブルの内容をTSV形式でクリップボードにコピーします。\n"
            "Excelに貼り付けて編集できます。\n"
            "列順: ダンパー種類 | 本数 | 方向 | 備考（上の階から順）"
        )
        copy_btn.clicked.connect(self._copy_to_clipboard)
        ctrl_row.addWidget(copy_btn)

        paste_btn = QPushButton("📥 Excelから貼り付け (Ctrl+V)")
        paste_btn.setToolTip(
            "Excelからコピーしたデータを貼り付けます。\n"
            "列順: ダンパー種類 | 本数 | 方向 | 備考\n"
            "行の並び順はテーブルと同じ（上の階から）。\n"
            "選択中の行から開始します（未選択時は最上行から）。\n"
            "例: 油圧ダンパー → [Tab] → 4 → [Tab] → X方向"
        )
        paste_btn.clicked.connect(self._paste_from_clipboard)
        ctrl_row.addWidget(paste_btn)

        preset_btn = QPushButton("全層に一括設定")
        preset_btn.setToolTip("全ての層に同じダンパー設定を適用します")
        preset_btn.clicked.connect(self._apply_all_floors)
        ctrl_row.addWidget(preset_btn)

        clear_btn = QPushButton("クリア")
        clear_btn.clicked.connect(self._clear_all)
        ctrl_row.addWidget(clear_btn)

        return ctrl_row

    def _build_main_area(self) -> QHBoxLayout:
        main_row = QHBoxLayout()

        # テーブル
        table_group = QGroupBox("層別ダンパー配置")
        table_layout = QVBoxLayout(table_group)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_FLOOR, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_TYPE, QHeaderView.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_COUNT, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_DIRECTION, QHeaderView.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.cellChanged.connect(self._on_cell_changed)

        _paste_sc = QShortcut(QKeySequence("Ctrl+V"), self._table)
        _paste_sc.setContext(Qt.WidgetShortcut)
        _paste_sc.activated.connect(self._paste_from_clipboard)

        _hint_lbl = QLabel(
            "<small>💡 Excel列順: <b>ダンパー種類</b> | <b>本数</b> | <b>方向</b> | <b>備考</b>　"
            "（上の階から順に）　Ctrl+V で貼り付け可能</small>"
        )
        _hint_lbl.setTextFormat(Qt.RichText)
        _hint_lbl.setWordWrap(True)
        _hint_lbl.setStyleSheet("color: #666; padding: 2px 0;")
        table_layout.addWidget(_hint_lbl)
        table_layout.addWidget(self._table)

        main_row.addWidget(table_group, stretch=2)

        # ビジュアル
        visual_group = QGroupBox("配置プレビュー")
        visual_layout = QVBoxLayout(visual_group)
        self._visual = _BuildingVisualWidget()
        visual_layout.addWidget(self._visual)
        main_row.addWidget(visual_group, stretch=1)

        return main_row

    def _build_summary_bar(self) -> QFrame:
        summary_frame = QFrame()
        summary_frame.setFrameShape(QFrame.StyledPanel)
        summary_frame.setStyleSheet(
            "QFrame { background-color: palette(alternate-base); "
            "border: 1px solid palette(mid); border-radius: 4px; }"
        )
        summary_frame.setMaximumHeight(60)
        _sf_layout = QVBoxLayout(summary_frame)
        _sf_layout.setContentsMargins(8, 4, 8, 4)
        _sf_layout.setSpacing(2)

        _summary_row = QHBoxLayout()
        self._placement_summary_label = QLabel("配置なし")
        self._placement_summary_label.setStyleSheet("font-size: 11px;")
        _summary_row.addWidget(self._placement_summary_label)
        _summary_row.addStretch()
        _sf_layout.addLayout(_summary_row)

        self._balance_warning = QFrame()
        self._balance_warning.setStyleSheet(
            "QFrame { background-color: #fff3e0; border: 1px solid #fb8c00; border-radius: 3px; }"
        )
        self._balance_warning.setMaximumHeight(22)
        _warn_row = QHBoxLayout(self._balance_warning)
        _warn_row.setContentsMargins(6, 1, 6, 1)
        self._balance_warning_label = QLabel("")
        self._balance_warning_label.setStyleSheet(
            "color: #e65100; font-size: 10px; font-weight: bold; background: transparent;"
        )
        _warn_row.addWidget(self._balance_warning_label)
        self._balance_warning.setVisible(False)
        _sf_layout.addWidget(self._balance_warning)

        return summary_frame

    # ------------------------------------------------------------------
    # Table management
    # ------------------------------------------------------------------

    def _apply_floor_count(self) -> None:
        """階数スピンボックスの値でテーブルを初期化します。"""
        n = self._floor_spin.value()
        existing = {c.floor: c for c in self._configs}

        new_configs = []
        for i in range(1, n + 1):
            if i in existing:
                new_configs.append(existing[i])
            else:
                new_configs.append(FloorDamperConfig(floor=i))
        self._configs = new_configs
        self._populate_table()

    def _populate_table(self) -> None:
        """テーブルをリフレッシュします。"""
        self._table.blockSignals(True)
        self._table.setRowCount(0)

        for config in reversed(self._configs):  # 上の階から表示
            row = self._table.rowCount()
            self._table.insertRow(row)

            # 層番号（読み取り専用）
            floor_item = QTableWidgetItem(f"{config.floor}F")
            floor_item.setFlags(floor_item.flags() & ~Qt.ItemIsEditable)
            floor_item.setData(Qt.UserRole, config.floor)
            self._table.setItem(row, _COL_FLOOR, floor_item)

            # ダンパー種類（コンボボックス）
            type_combo = QComboBox()
            type_combo.addItems(DAMPER_TYPES)
            idx = type_combo.findText(config.damper_type)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            type_combo.currentTextChanged.connect(self._on_combo_changed)
            self._table.setCellWidget(row, _COL_TYPE, type_combo)

            # 本数
            count_spin = QSpinBox()
            count_spin.setRange(0, 100)
            count_spin.setValue(config.count)
            count_spin.valueChanged.connect(self._on_spin_changed)
            self._table.setCellWidget(row, _COL_COUNT, count_spin)

            # 方向
            dir_combo = QComboBox()
            dir_combo.addItems(_DIRECTIONS)
            d_idx = dir_combo.findText(config.direction)
            if d_idx >= 0:
                dir_combo.setCurrentIndex(d_idx)
            dir_combo.currentTextChanged.connect(self._on_combo_changed)
            self._table.setCellWidget(row, _COL_DIRECTION, dir_combo)

            # 備考
            notes_item = QTableWidgetItem(config.notes)
            self._table.setItem(row, _COL_NOTES, notes_item)

        self._table.blockSignals(False)
        self._update_visual()

    def _read_table(self) -> None:
        """テーブルの内容を self._configs に反映します。"""
        configs = []
        for row in range(self._table.rowCount()):
            floor_item = self._table.item(row, _COL_FLOOR)
            if floor_item is None:
                continue
            floor = floor_item.data(Qt.UserRole)

            type_combo = self._table.cellWidget(row, _COL_TYPE)
            count_spin = self._table.cellWidget(row, _COL_COUNT)
            dir_combo = self._table.cellWidget(row, _COL_DIRECTION)
            notes_item = self._table.item(row, _COL_NOTES)

            configs.append(FloorDamperConfig(
                floor=floor,
                damper_type=type_combo.currentText() if type_combo else "なし",
                count=count_spin.value() if count_spin else 0,
                direction=dir_combo.currentText() if dir_combo else "X方向",
                notes=notes_item.text() if notes_item else "",
            ))
        # 層番号順にソート
        configs.sort(key=lambda c: c.floor)
        self._configs = configs

    def _on_cell_changed(self, row: int, col: int) -> None:
        self._read_table()
        self._update_visual()
        self.configChanged.emit()

    def _on_combo_changed(self, text: str) -> None:
        self._read_table()
        self._update_visual()
        self.configChanged.emit()

    def _on_spin_changed(self, value: int) -> None:
        self._read_table()
        self._update_visual()
        self.configChanged.emit()

    def _update_visual(self) -> None:
        """ビジュアル表示を更新します。"""
        self._visual.set_configs(self._configs)
        # UX改善（新）: 配置バランスサマリーを更新
        if hasattr(self, "_placement_summary_label"):
            self._update_placement_summary()

    def _update_placement_summary(self) -> None:
        """
        UX改善（新）: 配置バランスサマリーバーを更新します。

        合計本数・配置層数・平均を計算し、偏りがある場合は警告を表示します。
        偏りの判定は「最大層本数 > 平均本数 × 2.0」とします。
        """
        placed = [(c.floor, c.count) for c in self._configs if c.count > 0 and c.damper_type != "なし"]
        total = sum(cnt for _, cnt in placed)
        n_floors = len(self._configs)
        n_placed = len(placed)

        if total == 0:
            self._placement_summary_label.setText(
                "配置なし　（テーブルでダンパーの種類・本数を設定してください）"
            )
            self._placement_summary_label.setStyleSheet("font-size: 11px; color: gray;")
            self._balance_warning.setVisible(False)
            return

        avg = total / n_placed if n_placed > 0 else 0
        max_count = max(cnt for _, cnt in placed) if placed else 0

        # サマリーテキスト
        summary = (
            f"合計 <b>{total}</b> 本　／　"
            f"{n_placed} 層に配置（全 {n_floors} 層）　／　"
            f"1層あたり平均 <b>{avg:.1f}</b> 本"
        )
        self._placement_summary_label.setText(summary)
        self._placement_summary_label.setTextFormat(Qt.RichText)
        self._placement_summary_label.setStyleSheet("font-size: 11px;")

        # 偏り検出: 最大本数が平均の2倍超
        if n_placed >= 2 and avg > 0 and max_count > avg * 2.0:
            offending_floors = [f for f, cnt in placed if cnt == max_count]
            floor_str = ", ".join(f"{f}F" for f in offending_floors)
            ratio = max_count / avg
            self._balance_warning_label.setText(
                f"⚠ 配置が偏っています（{floor_str}: {max_count}本 ≈ 平均の{ratio:.1f}倍）"
                "　均等な配置を検討してください。"
            )
            self._balance_warning.setVisible(True)
        else:
            self._balance_warning.setVisible(False)

    # ------------------------------------------------------------------
    # Preset / Clear
    # ------------------------------------------------------------------

    def _apply_all_floors(self) -> None:
        """全層に同じダンパー設定を適用するダイアログを表示します。"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle("全層に一括設定")
        dlg.setMinimumWidth(350)
        layout = QVBoxLayout(dlg)

        form = QFormLayout()
        type_combo = QComboBox()
        type_combo.addItems(DAMPER_TYPES)
        form.addRow("ダンパー種類:", type_combo)

        count_spin = QSpinBox()
        count_spin.setRange(0, 100)
        count_spin.setValue(4)
        form.addRow("本数:", count_spin)

        dir_combo = QComboBox()
        dir_combo.addItems(_DIRECTIONS)
        form.addRow("方向:", dir_combo)

        layout.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec():
            dtype = type_combo.currentText()
            count = count_spin.value()
            direction = dir_combo.currentText()
            for config in self._configs:
                config.damper_type = dtype
                config.count = count
                config.direction = direction
            self._populate_table()
            self.configChanged.emit()

    def _clear_all(self) -> None:
        """全層のダンパー設定をクリアします。"""
        for config in self._configs:
            config.damper_type = "なし"
            config.count = 0
            config.notes = ""
        self._populate_table()
        self.configChanged.emit()

    # ------------------------------------------------------------------
    # Excel互換 コピー / 貼り付け
    # ------------------------------------------------------------------

    def _copy_to_clipboard(self) -> None:
        """テーブルの内容をTSV形式でクリップボードにコピーします（Excel互換）。

        列順: ダンパー種類 | 本数 | 方向 | 備考
        行順: 上の階（テーブル表示と同じ）から下の階へ
        """
        self._read_table()
        lines = ["ダンパー種類\t本数\t方向\t備考"]
        # _populate_table が reversed(_configs) で表示しているので同じ順でコピー
        for config in reversed(self._configs):
            lines.append(
                f"{config.damper_type}\t{config.count}\t{config.direction}\t{config.notes}"
            )
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "コピー完了",
            f"{len(self._configs)} 行をクリップボードにコピーしました。\n"
            "Excel に貼り付けて編集し、「Excelから貼り付け」で戻してください。\n\n"
            "列順: ダンパー種類 | 本数 | 方向 | 備考",
        )

    def _paste_from_clipboard(self) -> None:
        """クリップボードのTSVデータをテーブルに貼り付けます（Excel互換）。

        Excel でコピーしたデータを貼り付けます。
        列順（左から）: ダンパー種類 | 本数 | 方向 | 備考
        ヘッダー行は自動スキップします。
        選択中の最上行から貼り付けを開始します（未選択時は先頭行から）。
        """
        text = QApplication.clipboard().text()
        if not text.strip():
            return

        lines = text.splitlines()
        if not lines:
            return

        data_lines = lines[self._detect_header_skip(lines):]
        if not data_lines:
            return

        start_row = self._paste_start_row()

        self._table.blockSignals(True)
        for i, line in enumerate(data_lines):
            row = start_row + i
            if row >= self._table.rowCount():
                break
            self._apply_paste_row(row, line.split("\t"))
        self._table.blockSignals(False)

        self._read_table()
        self._update_visual()
        self.configChanged.emit()

        actual_rows = min(len(data_lines), self._table.rowCount() - start_row)
        self._placement_summary_label.setText(
            f"✅ {actual_rows} 行を貼り付けました（行 {start_row + 1}〜{start_row + actual_rows}）"
        )

    @staticmethod
    def _detect_header_skip(lines: List[str]) -> int:
        """先頭行がヘッダーかを判定し、スキップすべき行数(0/1)を返す。"""
        first_cells = lines[0].split("\t")
        if not first_cells or first_cells[0].strip().lstrip("-").isdigit():
            return 0
        try:
            int(first_cells[0].strip())
            return 0
        except ValueError:
            if len(first_cells) >= 2:
                try:
                    int(first_cells[1].strip())
                except ValueError:
                    return 1
        return 0

    def _paste_start_row(self) -> int:
        """貼り付け開始行を決定する（選択中の最上行 or 0）。"""
        selected = self._table.selectedItems()
        return min(it.row() for it in selected) if selected else 0

    def _apply_paste_row(self, row: int, cells: List[str]) -> None:
        """1行分のセルをテーブルに反映する。"""
        if len(cells) >= 1:
            self._set_combo_cell(row, _COL_TYPE, cells[0].strip())
        if len(cells) >= 2:
            self._set_count_cell(row, cells[1].strip())
        if len(cells) >= 3:
            self._set_combo_cell(row, _COL_DIRECTION, cells[2].strip())
        if len(cells) >= 4:
            self._set_notes_cell(row, cells[3].strip())

    def _set_combo_cell(self, row: int, col: int, value: str) -> None:
        """ComboBoxセルに値を設定（完全一致→部分一致の順で検索）。"""
        if not value:
            return
        combo = self._table.cellWidget(row, col)
        if not combo:
            return
        idx = combo.findText(value)
        if idx < 0:
            for j in range(combo.count()):
                if value in combo.itemText(j):
                    idx = j
                    break
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _set_count_cell(self, row: int, value: str) -> None:
        """本数SpinBoxセルに値を設定。"""
        if not value:
            return
        spin = self._table.cellWidget(row, _COL_COUNT)
        if not spin:
            return
        try:
            spin.setValue(int(float(value)))
        except ValueError:
            logger.debug("貼り付け値の数値変換失敗: %s", value)

    def _set_notes_cell(self, row: int, value: str) -> None:
        """備考セルに値を設定。"""
        item = self._table.item(row, _COL_NOTES)
        if item is not None:
            item.setText(value)
        else:
            self._table.setItem(row, _COL_NOTES, QTableWidgetItem(value))
