"""
app/ui/sweep_dialog.py
バッチパラメータスイープダイアログ。

指定パラメータの値域（min, max, step）を設定して、
自動的に複数の解析ケースを生成します。

使い方:
  1. ベースとなるケース設定を入力
  2. スイープするパラメータのキー・最小値・最大値・刻み幅を指定
     - 「パラメータ追加」で最大4つまでの同時スイープが可能（グリッドサーチ）
  3. 「生成」ボタンでプレビュー → 「OK」でプロジェクトに追加

マルチパラメータスイープでは全パラメータの値の直積（グリッド）を生成します。
例: パラメータ A に 3 値、パラメータ B に 4 値 → 12 ケース

UX改善（新）: リアルタイムケース数プレビュー。
  パラメータの最小値・最大値・刻み幅を変更するたびに、
  「生成（プレビュー）」ボタンを押す前に推定生成ケース数をリアルタイムで表示します。
  大量ケースになる前に気づけるようになり、操作ミスを防ぎます。

UX改善（新⑤）: 3段階警告レベル + 推定解析時間表示。
  ケース数に応じて警告色とアイコンを段階的に変化させます:
  - 1〜20件: 緑（✅ 安全範囲）
  - 21〜50件: 青（ℹ️ 中程度）
  - 51〜99件: 橙（⚠ 時間がかかります）
  - 100件以上: 赤（🔴 上限 — 解析時間が非常に長くなります）
  また、1ケースあたりの解析時間（デフォルト: 30秒）から推定所要時間を計算し、
  「推定解析時間: 約X分」を常時表示します。
  ユーザーが「このまま生成して大丈夫か？」を判断しやすくなります。
"""

from __future__ import annotations

import itertools
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
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
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase

logger = logging.getLogger(__name__)


# よく使うスイープ対象パラメータのプリセット (汎用モード用)
_PRESET_PARAMS = [
    ("DAMPING", "減衰定数", 0.01, 0.10, 0.01),
    ("DT", "解析時間刻み", 0.001, 0.01, 0.001),
    ("Cd", "ダンパー減衰係数", 100.0, 1000.0, 100.0),
    ("alpha", "速度依存指数", 0.1, 1.0, 0.1),
    ("Kd", "ダンパー剛性", 500.0, 5000.0, 500.0),
    ("Qd", "降伏荷重", 50.0, 500.0, 50.0),
]

# 最大同時スイープパラメータ数
_MAX_SWEEP_PARAMS = 4

# パラメータ種別
PARAM_TYPE_DAMPER_FIELD = "damper_field"  # ダンパー定義フィールド (.s8i のダンパー物理パラメータ)
PARAM_TYPE_FLOOR_COUNT = "floor_count"    # ダンパー基数 (RD要素 quantity)
PARAM_TYPE_GENERIC = "generic"            # 汎用 (parameters 辞書に保存、SNAPには直接反映されない)


class _SweepParamRow(QWidget):
    """1つのスイープパラメータの入力行ウィジェット。

    パラメータ種別 (ダンパー定義フィールド / ダンパー基数 / 汎用) を切替可能で、
    種別に応じた選択UI (ダンパー定義コンボ・フィールドコンボ・階コンボ) を表示します。
    ダンパー定義フィールドや基数として指定した場合、生成ケースの .s8i 書き換えに
    実際に反映されます (damper_params / _rd_overrides へ変換)。

    UX改善（新）: paramsChanged シグナルを通じてパラメータ変更をSweepDialogに通知し、
    リアルタイムのケース数プレビューを実現します。
    """

    paramsChanged = Signal()  # UX改善（新）: パラメータが変更されたときに発火

    def __init__(
        self,
        index: int,
        damper_defs: Optional[List[Any]] = None,
        floor_keys: Optional[List[str]] = None,
        field_labels_getter=None,
        field_units_getter=None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._damper_defs = damper_defs or []
        self._floor_keys = floor_keys or []
        self._field_labels_getter = field_labels_getter
        self._field_units_getter = field_units_getter
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        # ヘッダー行: タイトル + 種別 + 汎用モード時のプリセット
        header = QHBoxLayout()
        self._label = QLabel(f"<b>パラメータ {self._index + 1}</b>")
        header.addWidget(self._label)

        header.addWidget(QLabel("種別:"))
        self._type_combo = QComboBox()
        self._type_combo.addItem("ダンパー定義フィールド", PARAM_TYPE_DAMPER_FIELD)
        self._type_combo.addItem("ダンパー基数 (各階)", PARAM_TYPE_FLOOR_COUNT)
        self._type_combo.addItem("汎用 (SNAPには反映なし)", PARAM_TYPE_GENERIC)
        self._type_combo.setToolTip(
            "ダンパー定義フィールド: .s8i のダンパー物理パラメータ (C0, αなど)を書換\n"
            "ダンパー基数: 指定階の RD 要素 quantity を書換\n"
            "汎用: ケースメタデータに保存のみ (SNAPには直接反映されません)"
        )
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        header.addWidget(self._type_combo)

        # 汎用モード時のみ表示されるプリセット (一旦全て作る)
        self._preset_label = QLabel("プリセット:")
        header.addWidget(self._preset_label)
        self._preset_combo = QComboBox()
        self._preset_combo.addItem("（カスタム）")
        for key, label, *_ in _PRESET_PARAMS:
            self._preset_combo.addItem(f"{label} ({key})")
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        header.addWidget(self._preset_combo)
        header.addStretch()

        # 削除ボタン（index > 0 のもののみ）
        if self._index > 0:
            self._remove_btn = QPushButton("✕ 削除")
            self._remove_btn.setMaximumWidth(64)
            header.addWidget(self._remove_btn)
        else:
            self._remove_btn = None

        layout.addLayout(header)

        # 種別ごとの選択 UI (QStackedWidget)
        self._selector_stack = QStackedWidget()
        self._selector_stack.addWidget(self._build_damper_field_selector())
        self._selector_stack.addWidget(self._build_floor_count_selector())
        self._selector_stack.addWidget(self._build_generic_selector())
        layout.addWidget(self._selector_stack)

        # 値域入力行 (共通)
        form = QFormLayout()
        form.setContentsMargins(16, 0, 0, 0)

        self._min_spin = QDoubleSpinBox()
        self._min_spin.setDecimals(6)
        self._min_spin.setRange(-1e12, 1e12)
        self._min_spin.setValue(0.01)
        self._min_spin.valueChanged.connect(self.paramsChanged.emit)
        form.addRow("最小値:", self._min_spin)

        self._max_spin = QDoubleSpinBox()
        self._max_spin.setDecimals(6)
        self._max_spin.setRange(-1e12, 1e12)
        self._max_spin.setValue(0.10)
        self._max_spin.valueChanged.connect(self.paramsChanged.emit)
        form.addRow("最大値:", self._max_spin)

        self._step_spin = QDoubleSpinBox()
        self._step_spin.setDecimals(6)
        self._step_spin.setRange(1e-6, 1e12)
        self._step_spin.setValue(0.01)
        self._step_spin.valueChanged.connect(self.paramsChanged.emit)
        form.addRow("刻み幅:", self._step_spin)

        layout.addLayout(form)

        # 初期状態は .s8i が利用可能ならダンパー定義、そうでなければ汎用
        if self._damper_defs:
            self._type_combo.setCurrentIndex(0)
        else:
            # 型コンボの先頭2つを無効化
            for item_idx in (0, 1):
                self._type_combo.setItemData(
                    item_idx, False, Qt.UserRole - 1,  # 無効化フラグ
                )
            self._type_combo.setCurrentIndex(2)
        self._on_type_changed()

    def _build_damper_field_selector(self) -> QWidget:
        """ダンパー定義フィールド選択 UI。"""
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(16, 0, 0, 0)

        self._def_combo = QComboBox()
        for ddef in self._damper_defs:
            kw = getattr(ddef, "keyword", "")
            nm = getattr(ddef, "name", "")
            self._def_combo.addItem(f"{nm} ({kw})", (nm, kw))
        self._def_combo.currentIndexChanged.connect(self._on_damper_def_changed)
        form.addRow("ダンパー定義:", self._def_combo)

        self._field_combo = QComboBox()
        self._field_combo.currentIndexChanged.connect(lambda _i: self.paramsChanged.emit())
        form.addRow("フィールド:", self._field_combo)

        # 初期化
        if self._damper_defs:
            self._on_damper_def_changed()
        return w

    def _build_floor_count_selector(self) -> QWidget:
        """階別基数 (quantity) 選択 UI。"""
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(16, 0, 0, 0)

        self._floor_combo = QComboBox()
        for fk in self._floor_keys:
            self._floor_combo.addItem(fk, fk)
        self._floor_combo.currentIndexChanged.connect(lambda _i: self.paramsChanged.emit())
        form.addRow("階:", self._floor_combo)

        hint = QLabel(
            "<small>指定階の RD 要素 quantity を一括で同じ値に書き換えます。"
            "整数値での刻み幅推奨。</small>"
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _build_generic_selector(self) -> QWidget:
        """汎用パラメータ (キー入力) UI。"""
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(16, 0, 0, 0)

        self._param_key = QLineEdit()
        self._param_key.setPlaceholderText("例: DAMPING, Cd, alpha ...")
        # textChanged は str 引数を伴うので lambda で吸収
        self._param_key.textChanged.connect(lambda _t: self.paramsChanged.emit())
        form.addRow("パラメータキー:", self._param_key)

        hint = QLabel(
            "<small>⚠ 汎用パラメータは case.parameters に記録されるだけで、"
            "SNAP の .s8i 書き換えには反映されません。</small>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #e65100;")
        form.addRow(hint)
        return w

    def _on_type_changed(self) -> None:
        """種別コンボの変更ハンドラ。"""
        idx = self._type_combo.currentIndex()
        self._selector_stack.setCurrentIndex(idx)
        # 汎用モードのときのみプリセットを有効化
        is_generic = (self._type_combo.currentData() == PARAM_TYPE_GENERIC)
        self._preset_label.setVisible(is_generic)
        self._preset_combo.setVisible(is_generic)
        self.paramsChanged.emit()

    def _on_damper_def_changed(self) -> None:
        """ダンパー定義切替時: フィールド一覧を更新。"""
        self._field_combo.clear()
        data = self._def_combo.currentData()
        if not data:
            return
        _, keyword = data
        if self._field_labels_getter is None:
            return
        try:
            labels = self._field_labels_getter(keyword)
        except Exception as e:
            logger.debug("field labels 取得失敗: %s", e)
            return
        units = {}
        if self._field_units_getter is not None:
            try:
                units = self._field_units_getter(keyword)
            except Exception as e:
                logger.debug("field units 取得失敗: %s", e)

        # 最適化対象外のフィールドをスキップ
        _skip_keywords = (
            "種別", "k-DB", "番号", "型番", "モデル",
            "考慮", "初期解析", "疲労損傷", "重量種別",
            "計算", "しない", "する",
        )
        for field_idx_1based in sorted(labels.keys()):
            label_text = labels[field_idx_1based]
            if any(kw in label_text for kw in _skip_keywords):
                continue
            unit = units.get(field_idx_1based, "")
            unit_str = f" [{unit}]" if unit and unit != "—" else ""
            display = f"{label_text}{unit_str}"
            # data: (field_idx_1based, label_text)
            self._field_combo.addItem(display, (field_idx_1based, label_text))
        self.paramsChanged.emit()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def param_type(self) -> str:
        """現在のパラメータ種別 (PARAM_TYPE_*)。"""
        return self._type_combo.currentData() or PARAM_TYPE_GENERIC

    @property
    def param_key(self) -> str:
        """表示名としてのパラメータキー。ケース名生成用。

        - damper_field: "{def_name}.{field_label}"
        - floor_count: "F{floor_key}基数"
        - generic: LineEdit の内容
        """
        t = self.param_type
        if t == PARAM_TYPE_DAMPER_FIELD:
            def_data = self._def_combo.currentData()
            fld_data = self._field_combo.currentData()
            if def_data and fld_data:
                return f"{def_data[0]}.{fld_data[1]}"
            return ""
        if t == PARAM_TYPE_FLOOR_COUNT:
            fk = self._floor_combo.currentData()
            if fk:
                return f"{fk}基数"
            return ""
        return self._param_key.text().strip()

    @param_key.setter
    def param_key(self, value: str) -> None:
        # 汎用モードのキーのみセット可能
        self._param_key.setText(value)

    @property
    def damper_def_name(self) -> str:
        """(damper_field モード時) 選択中のダンパー定義名。"""
        data = self._def_combo.currentData()
        return data[0] if data else ""

    @property
    def damper_field_index_1based(self) -> int:
        """(damper_field モード時) 選択中のフィールド 1-based インデックス。

        .s8i の DamperDefinition.values は 0-based で values[0]=name, values[1]=field1...
        damper_params 辞書のキーは 1-indexed 文字列として格納。
        """
        data = self._field_combo.currentData()
        return int(data[0]) if data else 0

    @property
    def floor_key(self) -> str:
        """(floor_count モード時) 選択中の階キー。"""
        return self._floor_combo.currentData() or ""

    @property
    def min_val(self) -> float:
        return self._min_spin.value()

    @min_val.setter
    def min_val(self, v: float) -> None:
        self._min_spin.setValue(v)

    @property
    def max_val(self) -> float:
        return self._max_spin.value()

    @max_val.setter
    def max_val(self, v: float) -> None:
        self._max_spin.setValue(v)

    @property
    def step_val(self) -> float:
        return self._step_spin.value()

    @step_val.setter
    def step_val(self, v: float) -> None:
        self._step_spin.setValue(v)

    # ------------------------------------------------------------------
    # Values
    # ------------------------------------------------------------------

    def compute_values(self) -> List[float]:
        """スイープ値のリストを生成します。"""
        mn = self.min_val
        mx = self.max_val
        st = self.step_val

        if st <= 0:
            return []
        if mn > mx:
            mn, mx = mx, mn

        # 浮動小数点誤差に対応: 小さいεを加えてから切り捨て
        count = int(math.floor((mx - mn) / st + 0.5)) + 1
        count = min(count, 100)  # 安全上限

        values = []
        for i in range(count):
            v = mn + i * st
            if v > mx + st * 0.01:
                break
            values.append(round(v, 10))
        return values

    def is_valid(self) -> bool:
        """入力が有効かどうかを返します。"""
        return bool(self.param_key) and len(self.compute_values()) > 0

    # ------------------------------------------------------------------
    # Preset
    # ------------------------------------------------------------------

    def _on_preset_selected(self, index: int) -> None:
        if index <= 0:
            return
        preset = _PRESET_PARAMS[index - 1]
        key, label, min_val, max_val, step_val = preset
        if self.param_type == PARAM_TYPE_GENERIC:
            self._param_key.setText(key)
        self._min_spin.setValue(min_val)
        self._max_spin.setValue(max_val)
        self._step_spin.setValue(step_val)


class SweepDialog(QDialog):
    """
    パラメータスイープによる一括ケース生成ダイアログ。

    単一パラメータスイープに加え、複数パラメータのグリッドサーチ
    （全組み合わせ）にも対応しています。

    OK を返した場合、generated_cases プロパティで生成済みケースリストを取得できます。
    """

    def __init__(
        self,
        base_case: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._base_case = base_case
        self._generated: List[AnalysisCase] = []
        self._param_rows: List[_SweepParamRow] = []
        # .s8i を解析して取得 (ダンパー定義 / 階マッピング)
        self._damper_defs: List[Any] = []
        self._floor_rd_map: Dict[str, List[int]] = {}
        self._floor_keys: List[str] = []
        self._parse_base_model()
        self.setWindowTitle("パラメータスイープ — 一括ケース生成")
        self.setMinimumWidth(720)
        self.setMinimumHeight(620)
        self._setup_ui()
        if base_case:
            self._load_base_case(base_case)

    def _parse_base_model(self) -> None:
        """ベースケースの .s8i を解析し、ダンパー定義と階構成を取得する。

        解析失敗時も汎用モードは利用可能なのでエラーは握りつぶしてログのみ出す。
        """
        if not self._base_case or not self._base_case.model_path:
            return
        try:
            from app.models.s8i_parser import parse_s8i
            model = parse_s8i(self._base_case.model_path)
            self._damper_defs = list(model.damper_defs)
        except Exception as e:
            logger.debug("s8i 解析失敗: %s", e)

        try:
            from app.services.snap_evaluator import build_floor_rd_map
            frm, _qty, keys = build_floor_rd_map(self._base_case.model_path)
            self._floor_rd_map = dict(frm)
            self._floor_keys = list(keys)
        except Exception as e:
            logger.debug("floor_rd_map 構築失敗: %s", e)

    @property
    def generated_cases(self) -> List[AnalysisCase]:
        """生成されたケースのリスト。"""
        return self._generated

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(self._build_base_group())
        layout.addWidget(self._build_sweep_group())
        layout.addLayout(self._build_live_count_row())
        layout.addLayout(self._build_gen_row())
        layout.addWidget(self._build_preview_group())
        layout.addWidget(self._build_button_box())

    def _build_base_group(self) -> QGroupBox:
        """ベースケース設定グループ(プレフィックス+モデルファイル選択)。"""
        base_group = QGroupBox("ベースケース設定")
        base_form = QFormLayout(base_group)

        self._name_prefix = QLineEdit("スイープ")
        self._name_prefix.setPlaceholderText("生成ケース名のプレフィックス")
        base_form.addRow("ケース名プレフィックス:", self._name_prefix)

        model_row = QHBoxLayout()
        self._model_edit = QLineEdit()
        model_btn = QPushButton("…")
        model_btn.setMaximumWidth(32)
        model_btn.clicked.connect(self._browse_model)
        model_row.addWidget(self._model_edit)
        model_row.addWidget(model_btn)
        base_form.addRow("モデルファイル (.s8i):", model_row)
        return base_group

    def _build_sweep_group(self) -> QGroupBox:
        """スイープパラメータ設定グループ(追加ボタン + 初期1行)。"""
        sweep_group = QGroupBox("スイープパラメータ（複数パラメータのグリッドサーチ対応）")
        self._sweep_layout = QVBoxLayout(sweep_group)

        add_btn_row = QHBoxLayout()
        self._add_param_btn = QPushButton("＋ パラメータを追加（グリッドサーチ）")
        self._add_param_btn.setToolTip(
            f"最大 {_MAX_SWEEP_PARAMS} パラメータまで同時にスイープできます。\n"
            "全パラメータの値の全組み合わせ（直積）でケースが生成されます。"
        )
        self._add_param_btn.clicked.connect(self._on_add_param_clicked)
        add_btn_row.addWidget(self._add_param_btn)
        add_btn_row.addStretch()
        self._sweep_layout.addLayout(add_btn_row)

        # 最初のパラメータ行を追加（_add_param_btn 作成後に呼ぶ）
        self._add_param_row()
        return sweep_group

    def _build_live_count_row(self) -> QHBoxLayout:
        """リアルタイムケース数プレビューラベル行を構築。"""
        live_count_row = QHBoxLayout()
        live_count_row.setContentsMargins(0, 0, 0, 0)
        self._live_count_label = QLabel("")
        self._live_count_label.setStyleSheet(
            "color: #1976d2; font-size: 12px; font-weight: bold; padding: 2px 0;"
        )
        self._live_count_label.setToolTip(
            "パラメータ設定から推計されるケース生成数です。\n"
            "「ケースを生成（プレビュー）」を押す前に確認できます。\n"
            "100件に達すると上限でクリップされます。"
        )
        live_count_row.addWidget(self._live_count_label)
        live_count_row.addStretch()
        return live_count_row

    def _build_gen_row(self) -> QHBoxLayout:
        """生成ボタン行を構築。"""
        gen_row = QHBoxLayout()
        self._gen_btn = QPushButton("ケースを生成（プレビュー）")
        self._gen_btn.clicked.connect(self._generate_preview)
        gen_row.addWidget(self._gen_btn)
        self._count_label = QLabel("")
        gen_row.addWidget(self._count_label)
        gen_row.addStretch()
        return gen_row

    def _build_preview_group(self) -> QGroupBox:
        """生成プレビューテーブル群を構築。"""
        preview_group = QGroupBox("生成プレビュー")
        preview_layout = QVBoxLayout(preview_group)

        self._preview_table = QTableWidget(0, 2)
        self._preview_table.setHorizontalHeaderLabels(["ケース名", "パラメータ値"])
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self._preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._preview_table.verticalHeader().setVisible(False)
        preview_layout.addWidget(self._preview_table)
        return preview_group

    def _build_button_box(self) -> QDialogButtonBox:
        """OK/Cancel ボタンボックスを構築。"""
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.button(QDialogButtonBox.Ok).setText("プロジェクトに追加")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        return self._buttons

    # ------------------------------------------------------------------
    # Param row management
    # ------------------------------------------------------------------

    def _add_param_row(self) -> _SweepParamRow:
        """スイープパラメータ行を追加します。"""
        idx = len(self._param_rows)
        # ダンパーフィールド label/unit 取得関数を渡す (遅延 import で循環回避)
        try:
            from .damper_field_data import (
                get_damper_field_labels, get_damper_field_units,
            )
            labels_getter = get_damper_field_labels
            units_getter = get_damper_field_units
        except Exception as e:
            logger.debug("damper_field_data import 失敗: %s", e)
            labels_getter = None
            units_getter = None

        row = _SweepParamRow(
            idx,
            damper_defs=self._damper_defs,
            floor_keys=self._floor_keys,
            field_labels_getter=labels_getter,
            field_units_getter=units_getter,
            parent=self,
        )
        if row._remove_btn is not None:
            row._remove_btn.clicked.connect(lambda checked=False, r=row: self._remove_param_row(r))
        # UX改善（新）: パラメータ変更時にリアルタイムでケース数を更新
        row.paramsChanged.connect(self._update_live_count)
        self._param_rows.append(row)
        # 追加ボタン行の前に挿入
        insert_pos = self._sweep_layout.count() - 1  # 追加ボタン行の前
        if insert_pos < 0:
            insert_pos = 0
        self._sweep_layout.insertWidget(insert_pos, row)
        self._update_add_btn_state()
        # 行追加後もカウントを更新
        self._update_live_count()
        return row

    def _remove_param_row(self, row: _SweepParamRow) -> None:
        """スイープパラメータ行を削除します。"""
        if row in self._param_rows:
            self._param_rows.remove(row)
            self._sweep_layout.removeWidget(row)
            row.deleteLater()
            # インデックスを振り直す
            for i, r in enumerate(self._param_rows):
                r._index = i
                r._label.setText(f"<b>パラメータ {i + 1}</b>")
            self._update_add_btn_state()
            self._update_live_count()  # UX改善（新）: 行削除後にカウントを更新

    def _on_add_param_clicked(self) -> None:
        """パラメータ追加ボタンのクリック処理。"""
        if len(self._param_rows) >= _MAX_SWEEP_PARAMS:
            QMessageBox.information(
                self, "上限",
                f"同時スイープは最大 {_MAX_SWEEP_PARAMS} パラメータまでです。"
            )
            return
        self._add_param_row()

    def _update_add_btn_state(self) -> None:
        """追加ボタンの有効/無効を更新します。"""
        self._add_param_btn.setEnabled(len(self._param_rows) < _MAX_SWEEP_PARAMS)
        if len(self._param_rows) > 1:
            total_hint = self._estimate_case_count()
            self._add_param_btn.setToolTip(
                f"現在 {len(self._param_rows)} パラメータ（推定 {total_hint} ケース）\n"
                f"最大 {_MAX_SWEEP_PARAMS} パラメータまで追加可能"
            )

    def _estimate_case_count(self) -> int:
        """現在の設定から生成されるケース数を概算します。"""
        total = 1
        for row in self._param_rows:
            vals = row.compute_values()
            if vals:
                total *= len(vals)
        return total

    def _update_live_count(self) -> None:
        """
        UX改善（新）+ UX改善（新⑤）: リアルタイムケース数プレビューラベルを更新します。

        パラメータの最小値・最大値・刻み幅が変わるたびに呼び出され、
        「生成（プレビュー）」ボタンを押す前に推定ケース数と所要時間をユーザーに示します。

        警告レベル（UX改善新⑤）:
          1〜20件  : ✅ 緑 — 安全範囲
          21〜50件 : ℹ️ 青 — 中程度
          51〜99件 : ⚠ 橙 — 時間がかかります
          100件以上: 🔴 赤 — 上限・長時間警告
        """
        if not hasattr(self, "_live_count_label"):
            return
        count = self._estimate_case_count()
        has_valid_key = any(r.param_key for r in self._param_rows)

        # 推定解析時間（UX改善新⑤）: 1ケース30秒と仮定
        _SEC_PER_CASE = 30
        est_sec = count * _SEC_PER_CASE
        if est_sec < 60:
            time_str = f"約 {est_sec} 秒"
        elif est_sec < 3600:
            time_str = f"約 {est_sec // 60} 分"
        else:
            time_str = f"約 {est_sec // 3600} 時間 {(est_sec % 3600) // 60} 分"

        if not has_valid_key:
            self._live_count_label.setText(
                "⬅ パラメータキーを入力してください"
            )
            self._live_count_label.setStyleSheet("color: gray; font-size: 11px;")
        elif count <= 0:
            self._live_count_label.setText("⚠ 有効な値域が設定されていません（最大値 > 最小値にしてください）")
            self._live_count_label.setStyleSheet("color: #ff9800; font-size: 11px;")
        elif count >= 100:
            # UX改善新⑤: 赤 — 上限警告
            self._live_count_label.setText(
                f"🔴 推定 <b>{count}</b> 件（上限 100 件でクリップされます）"
                f" — 推定解析時間: <b>{time_str}</b>以上  "
                f"<i>パラメータ範囲を狭めるか刻み幅を大きくすることを推奨します</i>"
            )
            self._live_count_label.setStyleSheet(
                "color: #b71c1c; font-size: 11px; font-weight: bold;"
            )
        elif count > 50:
            # UX改善新⑤: 橙 — 時間がかかる
            self._live_count_label.setText(
                f"⚠ 推定 <b>{count}</b> 件のケースが生成されます"
                f" — 推定解析時間: <b>{time_str}</b>  "
                f"<i>解析に時間がかかります。PC を稼働したまま待機してください。</i>"
            )
            self._live_count_label.setStyleSheet(
                "color: #e65100; font-size: 11px; font-weight: bold;"
            )
        elif count > 20:
            # UX改善新⑤: 青 — 中程度
            self._live_count_label.setText(
                f"ℹ️ 推定 <b>{count}</b> 件のケースが生成されます"
                f" — 推定解析時間: <b>{time_str}</b>"
            )
            self._live_count_label.setStyleSheet(
                "color: #1565c0; font-size: 11px; font-weight: bold;"
            )
        else:
            # UX改善新⑤: 緑 — 安全範囲
            self._live_count_label.setText(
                f"✅ 推定 <b>{count}</b> 件のケースが生成されます"
                f" — 推定解析時間: <b>{time_str}</b>"
            )
            self._live_count_label.setStyleSheet(
                "color: #2e7d32; font-size: 11px; font-weight: bold;"
            )
        # ラベルはRichText形式
        from PySide6.QtCore import Qt as _Qt
        self._live_count_label.setTextFormat(_Qt.RichText)

    # ------------------------------------------------------------------
    # Base case loading
    # ------------------------------------------------------------------

    def _load_base_case(self, case: AnalysisCase) -> None:
        self._name_prefix.setText(case.name)
        if case.model_path:
            self._model_edit.setText(case.model_path)

    # ------------------------------------------------------------------
    # File browsers
    # ------------------------------------------------------------------

    def _browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "入力ファイルを選択", self._model_edit.text(),
            "SNAP 入力ファイル (*.s8i);;すべてのファイル (*)"
        )
        if path:
            self._model_edit.setText(path)

    # ------------------------------------------------------------------
    # Generation logic
    # ------------------------------------------------------------------

    def _generate_preview(self) -> None:
        """プレビューを生成します（グリッドサーチ対応）。"""
        # バリデーション
        valid_rows: List[_SweepParamRow] = []
        for row in self._param_rows:
            if not row.param_key:
                QMessageBox.warning(
                    self, "入力エラー",
                    f"パラメータ {row._index + 1} の設定 (ダンパー定義/フィールド/階/キー) を確認してください。"
                )
                return
            vals = row.compute_values()
            if not vals:
                QMessageBox.warning(
                    self, "入力エラー",
                    f"パラメータ {row._index + 1} ({row.param_key}) の"
                    "有効なスイープ範囲を指定してください。"
                )
                return
            valid_rows.append(row)

        # 重複キーチェック
        keys = [r.param_key for r in valid_rows]
        if len(keys) != len(set(keys)):
            QMessageBox.warning(
                self, "入力エラー",
                "同じパラメータが複数指定されています。\n"
                "各パラメータは異なる対象を指定してください。"
            )
            return

        # 各パラメータの値リストを取得
        param_values: List[Tuple[str, List[float]]] = []
        for row in valid_rows:
            param_values.append((row.param_key, row.compute_values()))

        # 全組み合わせ（直積）を生成
        all_value_lists = [vals for _, vals in param_values]
        all_keys = [key for key, _ in param_values]
        combinations = list(itertools.product(*all_value_lists))

        # 安全上限: 500ケースまで
        if len(combinations) > 500:
            reply = QMessageBox.question(
                self, "確認",
                f"合計 {len(combinations)} ケースが生成されます。\n"
                "500 ケースを超えています。最初の 500 ケースのみ生成しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            combinations = combinations[:500]

        prefix = self._name_prefix.text().strip() or "スイープ"
        model_path = self._model_edit.text().strip()

        self._generated.clear()
        self._preview_table.setRowCount(0)

        # テーブルのカラムを更新
        col_count = 1 + len(all_keys)
        self._preview_table.setColumnCount(col_count)
        headers = ["ケース名"] + [f"{k} の値" for k in all_keys]
        self._preview_table.setHorizontalHeaderLabels(headers)

        for combo in combinations:
            case = self._build_case_from_combo(
                prefix, model_path, valid_rows, combo,
            )
            self._generated.append(case)

            # テーブル行
            tblrow = self._preview_table.rowCount()
            self._preview_table.insertRow(tblrow)
            self._preview_table.setItem(tblrow, 0, QTableWidgetItem(case.name))
            for col_idx, val in enumerate(combo):
                self._preview_table.setItem(
                    tblrow, 1 + col_idx, QTableWidgetItem(str(val))
                )

        n = len(self._generated)
        grid_info = " × ".join(
            f"{k}({len(v)})" for k, v in param_values
        )
        self._count_label.setText(f"<b>{n}</b> ケース生成  [{grid_info}]")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(n > 0)

    def _build_case_from_combo(
        self,
        prefix: str,
        model_path: str,
        rows: List["_SweepParamRow"],
        combo: Tuple[float, ...],
    ) -> AnalysisCase:
        """1つのパラメータ組合せから AnalysisCase を生成する。

        パラメータ種別に応じて正しいフィールドに値を格納する:
          - damper_field → case.damper_params[def_name][idx_1based_str] = str(value)
          - floor_count  → case.parameters["_rd_overrides"][row_idx_str] = {"quantity": int}
          - generic      → case.parameters[param_key] = value
        これにより分析実行時に .s8i 書き換えに反映される。
        """
        # ベースからの引継ぎ
        if self._base_case:
            base_params = dict(self._base_case.parameters)
            base_damper_params: Dict[str, Dict[str, str]] = {
                k: dict(v) for k, v in (self._base_case.damper_params or {}).items()
            }
        else:
            base_params = {}
            base_damper_params = {}

        # _rd_overrides も (あれば) 引き継ぐが、コピーする
        base_rd_overrides = {}
        existing_rd = base_params.get("_rd_overrides")
        if isinstance(existing_rd, dict):
            base_rd_overrides = {k: dict(v) if isinstance(v, dict) else v
                                 for k, v in existing_rd.items()}

        generic_params: Dict[str, float] = {}
        damper_params = base_damper_params
        rd_overrides = base_rd_overrides

        # パラメータ種別ごとに振り分け
        for row, value in zip(rows, combo):
            t = row.param_type
            if t == PARAM_TYPE_DAMPER_FIELD:
                def_name = row.damper_def_name
                field_idx_1b = row.damper_field_index_1based
                if def_name and field_idx_1b > 0:
                    damper_params.setdefault(def_name, {})[str(field_idx_1b)] = str(value)
            elif t == PARAM_TYPE_FLOOR_COUNT:
                fk = row.floor_key
                row_indices = self._floor_rd_map.get(fk, [])
                qty = int(round(value))
                if row_indices:
                    n_elems = len(row_indices)
                    per = qty // n_elems
                    rem = qty - per * n_elems
                    for i, rid in enumerate(row_indices):
                        q = per + (1 if i < rem else 0)
                        rd_overrides[str(rid)] = {"quantity": q}
            else:  # PARAM_TYPE_GENERIC
                generic_params[row.param_key] = value

        # parameters 辞書構築 (ベース + 汎用 + _rd_overrides)
        merged_params = dict(base_params)
        merged_params.update(generic_params)
        if rd_overrides:
            merged_params["_rd_overrides"] = rd_overrides

        # ケース名
        parts = [f"{r.param_key}={v}" for r, v in zip(rows, combo)]
        case_name = f"{prefix}_{'_'.join(parts)}"

        # model_path の補完
        resolved_model_path = model_path
        if not resolved_model_path and self._base_case and self._base_case.model_path:
            resolved_model_path = self._base_case.model_path

        return AnalysisCase(
            name=case_name,
            model_path=resolved_model_path,
            parameters=merged_params,
            damper_params=damper_params,
        )

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        if not self._generated:
            QMessageBox.information(self, "情報", "まず「ケースを生成」ボタンを押してください。")
            return
        self.accept()
