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
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.models import AnalysisCase


# よく使うスイープ対象パラメータのプリセット
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


class _SweepParamRow(QWidget):
    """1つのスイープパラメータの入力行ウィジェット。

    UX改善（新）: paramsChanged シグナルを通じてパラメータ変更をSweepDialogに通知し、
    リアルタイムのケース数プレビューを実現します。
    """

    paramsChanged = Signal()  # UX改善（新）: パラメータが変更されたときに発火

    def __init__(self, index: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._index = index
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        # ヘッダー行: タイトル + プリセット選択
        header = QHBoxLayout()
        self._label = QLabel(f"<b>パラメータ {self._index + 1}</b>")
        header.addWidget(self._label)

        header.addWidget(QLabel("プリセット:"))
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

        # パラメータ入力行
        form = QFormLayout()
        form.setContentsMargins(16, 0, 0, 0)

        self._param_key = QLineEdit()
        self._param_key.setPlaceholderText("例: DAMPING, Cd, alpha ...")
        form.addRow("パラメータキー:", self._param_key)

        self._min_spin = QDoubleSpinBox()
        self._min_spin.setDecimals(6)
        self._min_spin.setRange(-1e12, 1e12)
        self._min_spin.setValue(0.01)
        self._min_spin.valueChanged.connect(self.paramsChanged.emit)  # UX改善（新）
        form.addRow("最小値:", self._min_spin)

        self._max_spin = QDoubleSpinBox()
        self._max_spin.setDecimals(6)
        self._max_spin.setRange(-1e12, 1e12)
        self._max_spin.setValue(0.10)
        self._max_spin.valueChanged.connect(self.paramsChanged.emit)  # UX改善（新）
        form.addRow("最大値:", self._max_spin)

        self._step_spin = QDoubleSpinBox()
        self._step_spin.setDecimals(6)
        self._step_spin.setRange(1e-6, 1e12)
        self._step_spin.setValue(0.01)
        self._step_spin.valueChanged.connect(self.paramsChanged.emit)  # UX改善（新）
        form.addRow("刻み幅:", self._step_spin)

        self._param_key.textChanged.connect(self.paramsChanged.emit)  # UX改善（新）

        layout.addLayout(form)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def param_key(self) -> str:
        return self._param_key.text().strip()

    @param_key.setter
    def param_key(self, value: str) -> None:
        self._param_key.setText(value)

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
        self.setWindowTitle("パラメータスイープ — 一括ケース生成")
        self.setMinimumWidth(720)
        self.setMinimumHeight(620)
        self._setup_ui()
        if base_case:
            self._load_base_case(base_case)

    @property
    def generated_cases(self) -> List[AnalysisCase]:
        """生成されたケースのリスト。"""
        return self._generated

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- ベースケース設定 ----
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

        layout.addWidget(base_group)

        # ---- スイープパラメータ設定 ----
        sweep_group = QGroupBox("スイープパラメータ（複数パラメータのグリッドサーチ対応）")
        self._sweep_layout = QVBoxLayout(sweep_group)

        # パラメータ追加ボタン（_add_param_row より先に作成する）
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

        layout.addWidget(sweep_group)

        # ---- UX改善（新）: リアルタイムケース数プレビューラベル ----
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
        layout.addLayout(live_count_row)

        # ---- 生成ボタン行 ----
        gen_row = QHBoxLayout()
        self._gen_btn = QPushButton("ケースを生成（プレビュー）")
        self._gen_btn.clicked.connect(self._generate_preview)
        gen_row.addWidget(self._gen_btn)
        self._count_label = QLabel("")
        gen_row.addWidget(self._count_label)
        gen_row.addStretch()
        layout.addLayout(gen_row)

        # ---- プレビューテーブル ----
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

        layout.addWidget(preview_group)

        # ---- ボタン ----
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        self._buttons.button(QDialogButtonBox.Ok).setText("プロジェクトに追加")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # Param row management
    # ------------------------------------------------------------------

    def _add_param_row(self) -> _SweepParamRow:
        """スイープパラメータ行を追加します。"""
        idx = len(self._param_rows)
        row = _SweepParamRow(idx, parent=self)
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
                    f"パラメータ {row._index + 1} のキーを入力してください。"
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
                "同じパラメータキーが複数指定されています。\n"
                "各パラメータは異なるキーを指定してください。"
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
        col_count = 1 + len(all_keys)  # ケース名 + 各パラメータ値
        self._preview_table.setColumnCount(col_count)
        headers = ["ケース名"] + [f"{k} の値" for k in all_keys]
        self._preview_table.setHorizontalHeaderLabels(headers)

        for combo in combinations:
            # ケース名生成
            param_strs = [f"{k}={v}" for k, v in zip(all_keys, combo)]
            case_name = f"{prefix}_{'_'.join(param_strs)}"

            # パラメータ辞書
            params = dict(zip(all_keys, combo))

            case = AnalysisCase(
                name=case_name,
                model_path=model_path,
                parameters=params,
            )
            # ベースケースのパラメータを引き継ぎ
            if self._base_case:
                merged = dict(self._base_case.parameters)
                merged.update(params)
                case.parameters = merged
                if self._base_case.damper_params:
                    case.damper_params = dict(self._base_case.damper_params)
                if not model_path and self._base_case.model_path:
                    case.model_path = self._base_case.model_path

            self._generated.append(case)

            # テーブル行
            row = self._preview_table.rowCount()
            self._preview_table.insertRow(row)
            self._preview_table.setItem(row, 0, QTableWidgetItem(case_name))
            for col_idx, val in enumerate(combo):
                self._preview_table.setItem(
                    row, 1 + col_idx, QTableWidgetItem(str(val))
                )

        n = len(self._generated)
        grid_info = " × ".join(
            f"{k}({len(v)})" for k, v in param_values
        )
        self._count_label.setText(f"<b>{n}</b> ケース生成  [{grid_info}]")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(n > 0)

    # ------------------------------------------------------------------
    # Accept
    # ------------------------------------------------------------------

    def _on_accept(self) -> None:
        if not self._generated:
            QMessageBox.information(self, "情報", "まず「ケースを生成」ボタンを押してください。")
            return
        self.accept()
