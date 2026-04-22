"""
app/ui/impulse_response_dialog.py
インパルス応答解析ケース作成ダイアログ。

- 既存の ``AnalysisCase`` をベースに選択
- 対象 DYC ケース（.s8i 内）を選択
- インパルス波パラメータを入力 (amax, dt, num_points, impulse_index)
- OK で新しい ``AnalysisCase`` を生成（呼び出し側で ``project.add_case()``）

伝達関数 = (各節点応答 FFT) / (入力インパルス FFT) を結果ビューアで比較できる。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.analysis_case import AnalysisCase
from app.models.project import Project
from app.services.impulse_case_builder import (
    ImpulseCaseSpec,
    build_impulse_case,
    list_dyc_cases,
)
from app.services.impulse_wave_writer import (
    DEFAULT_DT,
    DEFAULT_IMPULSE_INDEX,
    DEFAULT_NUM_POINTS,
)

logger = logging.getLogger(__name__)


class ImpulseResponseDialog(QDialog):
    """インパルス応答解析ケース作成ダイアログ。

    使い方::

        dlg = ImpulseResponseDialog(project=proj, parent=main_window)
        if dlg.exec():
            new_case = dlg.created_case
            if new_case:
                project.add_case(new_case)
    """

    def __init__(
        self,
        project: Project,
        base_case: Optional[AnalysisCase] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._project = project
        self._base_case_initial = base_case
        self._created_case: Optional[AnalysisCase] = None

        self.setWindowTitle("インパルス応答解析ケースを作成")
        self.setModal(True)
        self.resize(520, 520)

        self._setup_ui()
        self._populate_base_cases()
        if base_case:
            self._select_base_case_by_id(base_case.id)
        self._on_base_case_changed()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def created_case(self) -> Optional[AnalysisCase]:
        """OK クリック後に生成された新ケース。"""
        return self._created_case

    # ------------------------------------------------------------------
    # UI 構築
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(
            "既存の解析ケースを元に、指定した解析ケース（DYC）を\n"
            "インパルス波入力に差し替えた新しい .s8i を作成し、\n"
            "解析ケースとしてプロジェクトに追加します。\n"
            "他の DYC ケースは実行しない設定になります。"
        )
        intro.setStyleSheet("color: #444; padding: 4px;")
        root.addWidget(intro)

        root.addWidget(self._build_base_group())
        root.addWidget(self._build_impulse_group())
        root.addWidget(self._build_wave_dir_group())
        root.addWidget(self._build_name_group())

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_base_group(self) -> QGroupBox:
        gb = QGroupBox("ベース解析ケース")
        form = QFormLayout(gb)

        self._base_case_combo = QComboBox()
        self._base_case_combo.setToolTip(
            "このケースの .s8i をコピーして、DYC ケースだけを差し替えます"
        )
        self._base_case_combo.currentIndexChanged.connect(self._on_base_case_changed)
        form.addRow("ベースケース:", self._base_case_combo)

        self._dyc_case_combo = QComboBox()
        self._dyc_case_combo.setToolTip(
            "選択した DYC ケース（.s8i 内の応答解析ケース）を\n"
            "インパルス波入力に差し替えます"
        )
        form.addRow("対象 DYC ケース:", self._dyc_case_combo)

        return gb

    def _build_impulse_group(self) -> QGroupBox:
        gb = QGroupBox("インパルス波パラメータ")
        form = QFormLayout(gb)

        self._amax_spin = QDoubleSpinBox()
        self._amax_spin.setRange(-1.0e6, 1.0e6)
        self._amax_spin.setDecimals(3)
        self._amax_spin.setValue(1000.0)
        self._amax_spin.setSuffix(" gal")
        self._amax_spin.setToolTip("インパルス振幅（負値も可）")
        form.addRow("加速度振幅 amax:", self._amax_spin)

        self._dt_spin = QDoubleSpinBox()
        self._dt_spin.setRange(0.0001, 1.0)
        self._dt_spin.setDecimals(4)
        self._dt_spin.setSingleStep(0.001)
        self._dt_spin.setValue(DEFAULT_DT)
        self._dt_spin.setSuffix(" s")
        form.addRow("時間刻み dt:", self._dt_spin)

        self._num_points_spin = QSpinBox()
        self._num_points_spin.setRange(64, 1_000_000)
        self._num_points_spin.setValue(DEFAULT_NUM_POINTS)
        self._num_points_spin.setSingleStep(1024)
        form.addRow("データ点数:", self._num_points_spin)

        self._impulse_index_spin = QSpinBox()
        self._impulse_index_spin.setRange(0, 100_000)
        self._impulse_index_spin.setValue(DEFAULT_IMPULSE_INDEX)
        self._impulse_index_spin.setToolTip(
            "インパルスが発生するサンプル位置 (0-indexed)。\n"
            "最初のデータ点は index=0 です"
        )
        form.addRow("インパルス位置:", self._impulse_index_spin)

        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(-1000.0, 1000.0)
        self._scale_spin.setDecimals(3)
        self._scale_spin.setValue(1.0)
        self._scale_spin.setToolTip("DYC 行の倍率 (加力方向 倍率)")
        form.addRow("DYC 倍率:", self._scale_spin)

        return gb

    def _build_wave_dir_group(self) -> QGroupBox:
        gb = QGroupBox("SNAP wave フォルダ")
        layout = QHBoxLayout(gb)

        self._wave_dir_edit = QLineEdit()
        self._wave_dir_edit.setText(self._project.snap_wave_dir or "")
        self._wave_dir_edit.setPlaceholderText(
            r"例: D:\Kakemoto\kozosystem\SNAPV8\wave"
        )
        layout.addWidget(self._wave_dir_edit, stretch=1)

        browse = QPushButton("参照…")
        browse.clicked.connect(self._on_browse_wave_dir)
        layout.addWidget(browse)

        return gb

    def _build_name_group(self) -> QGroupBox:
        gb = QGroupBox("新ケース名（任意）")
        layout = QVBoxLayout(gb)

        self._case_name_edit = QLineEdit()
        self._case_name_edit.setPlaceholderText(
            "空欄の場合は自動生成: 「<ベース> [インパルス D<番号>]」"
        )
        layout.addWidget(self._case_name_edit)

        return gb

    # ------------------------------------------------------------------
    # データ投入
    # ------------------------------------------------------------------

    def _populate_base_cases(self) -> None:
        self._base_case_combo.blockSignals(True)
        self._base_case_combo.clear()
        for case in self._project.cases:
            label = case.name
            if case.model_path:
                label += f" ({Path(case.model_path).name})"
            self._base_case_combo.addItem(label, case.id)
        self._base_case_combo.blockSignals(False)

    def _select_base_case_by_id(self, case_id: str) -> None:
        for i in range(self._base_case_combo.count()):
            if self._base_case_combo.itemData(i) == case_id:
                self._base_case_combo.setCurrentIndex(i)
                return

    def _on_base_case_changed(self) -> None:
        self._dyc_case_combo.clear()
        base = self._current_base_case()
        if base is None or not base.model_path:
            return
        try:
            dyc_list = list_dyc_cases(base.model_path)
        except Exception as e:
            logger.exception("DYC ケース一覧の取得に失敗")
            self._dyc_case_combo.addItem(f"(読込失敗: {e})", None)
            return

        for dyc in dyc_list:
            flag = "✓" if dyc.is_run else " "
            label = f"[{flag}] D{dyc.case_no}: {dyc.name}"
            self._dyc_case_combo.addItem(label, dyc.case_no)

        # デフォルト選択: 最初の実行対象ケース
        for i in range(self._dyc_case_combo.count()):
            data = self._dyc_case_combo.itemData(i)
            if data is not None:
                dyc = dyc_list[i]
                if dyc.is_run:
                    self._dyc_case_combo.setCurrentIndex(i)
                    break

    def _current_base_case(self) -> Optional[AnalysisCase]:
        case_id = self._base_case_combo.currentData()
        if not case_id:
            return None
        return self._project.get_case(case_id)

    # ------------------------------------------------------------------
    # イベントハンドラ
    # ------------------------------------------------------------------

    def _on_browse_wave_dir(self) -> None:
        start = self._wave_dir_edit.text().strip() or ""
        if not start:
            # snap_work_dir の隣をヒントに
            if self._project.snap_work_dir:
                start = str(Path(self._project.snap_work_dir).parent)
        d = QFileDialog.getExistingDirectory(
            self, "SNAP wave フォルダを選択", start,
        )
        if d:
            self._wave_dir_edit.setText(d)

    def _on_accept(self) -> None:
        try:
            spec = self._build_spec()
        except (ValueError, FileNotFoundError) as e:
            QMessageBox.warning(self, "入力エラー", str(e))
            return

        try:
            new_case = build_impulse_case(spec)
        except Exception as e:
            logger.exception("インパルスケース生成に失敗")
            QMessageBox.critical(
                self, "生成エラー",
                f"インパルス応答ケースの生成に失敗しました:\n{e}",
            )
            return

        # wave dir を project に保存（次回以降デフォルトになる）
        wave_dir = self._wave_dir_edit.text().strip()
        if wave_dir and wave_dir != self._project.snap_wave_dir:
            self._project.snap_wave_dir = wave_dir

        self._created_case = new_case
        self.accept()

    # ------------------------------------------------------------------
    # 入力検証 → ImpulseCaseSpec 生成
    # ------------------------------------------------------------------

    def _build_spec(self) -> ImpulseCaseSpec:
        base = self._current_base_case()
        if base is None:
            raise ValueError("ベースケースを選択してください")

        dyc_no = self._dyc_case_combo.currentData()
        if dyc_no is None:
            raise ValueError("対象 DYC ケースを選択してください")

        wave_dir = self._wave_dir_edit.text().strip()
        if not wave_dir:
            raise ValueError("SNAP wave フォルダを指定してください")

        return ImpulseCaseSpec(
            base_case=base,
            target_case_no=int(dyc_no),
            snap_wave_dir=wave_dir,
            amax=self._amax_spin.value(),
            dt=self._dt_spin.value(),
            num_points=self._num_points_spin.value(),
            impulse_index=self._impulse_index_spin.value(),
            wave_scale=self._scale_spin.value(),
            case_name=self._case_name_edit.text().strip() or None,
        )
