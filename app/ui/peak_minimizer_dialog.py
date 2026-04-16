"""
app/ui/peak_minimizer_dialog.py

Phase 2-C: 伝達関数ピーク最小化ダイアログ

ダンパーパラメータを最適化して伝達関数のピークゲインを最小化する。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDoubleSpinBox,
    QSpinBox,
    QComboBox,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QGroupBox,
    QMessageBox,
)

from app.services.transfer_function_service import (
    TransferFunctionResult,
    TransferFunctionPeakMinimizer,
)


class PeakMinimizerDialog(QDialog):
    """伝達関数ピーク最小化ダイアログ。"""

    optimization_complete = Signal(dict)

    def __init__(
        self, transfer_function: TransferFunctionResult, parent: Optional[QDialog] = None
    ) -> None:
        super().__init__(parent)
        self.tf = transfer_function
        self.minimizer = TransferFunctionPeakMinimizer(transfer_function)
        self._result = None

        self.setWindowTitle("伝達関数ピーク最小化")
        self.setGeometry(100, 100, 700, 600)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()

        # 情報パネル
        info_text = (
            f"ダンパーパラメータを最適化してピークゲインを最小化します\n"
            f"初期ピークゲイン: {self.tf.peak_gain_db:.2f} dB\n"
            f"ピーク周波数: {self.tf.peak_freq:.2f} Hz"
        )
        info_label = QLabel(info_text)
        info_label.setStyleSheet("background-color: #f0f0f0; padding: 10px; border-radius: 5px;")
        layout.addWidget(info_label)

        # パラメータグループ
        params_group = QGroupBox("最適化パラメータ")
        params_layout = QVBoxLayout()

        # 減衰比範囲
        damping_layout = QHBoxLayout()
        damping_layout.addWidget(QLabel("減衰比の範囲:"))
        self._d_min_spin = QDoubleSpinBox()
        self._d_min_spin.setRange(0.001, 0.5)
        self._d_min_spin.setValue(0.01)
        self._d_min_spin.setSingleStep(0.01)
        damping_layout.addWidget(self._d_min_spin)
        damping_layout.addWidget(QLabel("~"))
        self._d_max_spin = QDoubleSpinBox()
        self._d_max_spin.setRange(0.001, 0.5)
        self._d_max_spin.setValue(0.30)
        self._d_max_spin.setSingleStep(0.01)
        damping_layout.addWidget(self._d_max_spin)
        params_layout.addLayout(damping_layout)

        # 剛性比範囲
        stiffness_layout = QHBoxLayout()
        stiffness_layout.addWidget(QLabel("剛性比の範囲:"))
        self._s_min_spin = QDoubleSpinBox()
        self._s_min_spin.setRange(0.001, 1.0)
        self._s_min_spin.setValue(0.01)
        self._s_min_spin.setSingleStep(0.01)
        stiffness_layout.addWidget(self._s_min_spin)
        stiffness_layout.addWidget(QLabel("~"))
        self._s_max_spin = QDoubleSpinBox()
        self._s_max_spin.setRange(0.001, 1.0)
        self._s_max_spin.setValue(0.50)
        self._s_max_spin.setSingleStep(0.01)
        stiffness_layout.addWidget(self._s_max_spin)
        params_layout.addLayout(stiffness_layout)

        # 手法選択
        method_layout = QHBoxLayout()
        method_layout.addWidget(QLabel("探索手法:"))
        self._method_combo = QComboBox()
        self._method_combo.addItems(["グリッドサーチ", "シンプレックス法 (L-BFGS-B)"])
        method_layout.addWidget(self._method_combo)
        params_layout.addLayout(method_layout)

        # グリッド分割数
        grid_layout = QHBoxLayout()
        grid_layout.addWidget(QLabel("グリッド分割数:"))
        self._grid_points_spin = QSpinBox()
        self._grid_points_spin.setRange(5, 50)
        self._grid_points_spin.setValue(15)
        grid_layout.addWidget(self._grid_points_spin)
        grid_layout.addStretch()
        params_layout.addLayout(grid_layout)

        params_group.setLayout(params_layout)
        layout.addWidget(params_group)

        # プログレスバー
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 結果表示
        results_group = QGroupBox("最適化結果")
        results_layout = QVBoxLayout()
        self._results_text = QTextEdit()
        self._results_text.setReadOnly(True)
        self._results_text.setMaximumHeight(150)
        results_layout.addWidget(self._results_text)
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        # ボタン
        button_layout = QHBoxLayout()
        self._run_btn = QPushButton("最適化を実行")
        self._run_btn.clicked.connect(self._on_run_optimization)
        button_layout.addWidget(self._run_btn)

        self._export_btn = QPushButton("結果をエクスポート")
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._on_export_results)
        button_layout.addWidget(self._export_btn)

        button_layout.addStretch()
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def _on_run_optimization(self) -> None:
        self._progress.setVisible(True)
        self._progress.setMaximum(0)
        self._run_btn.setEnabled(False)

        try:
            d_range = (self._d_min_spin.value(), self._d_max_spin.value())
            s_range = (self._s_min_spin.value(), self._s_max_spin.value())

            if d_range[0] >= d_range[1]:
                QMessageBox.warning(self, "設定エラー", "減衰比の最小値は最大値より小さくしてください。")
                return
            if s_range[0] >= s_range[1]:
                QMessageBox.warning(self, "設定エラー", "剛性比の最小値は最大値より小さくしてください。")
                return

            method = "grid" if self._method_combo.currentIndex() == 0 else "simplex"
            grid_points = self._grid_points_spin.value()

            self._result = self.minimizer.optimize(
                damping_range=d_range,
                stiffness_range=s_range,
                method=method,
                grid_points=grid_points,
            )

            self._results_text.setText(self._result.summary_text())
            self._export_btn.setEnabled(True)

            self.optimization_complete.emit(
                {
                    "optimal_damping_ratio": self._result.optimal_damping_ratio,
                    "optimal_stiffness_ratio": self._result.optimal_stiffness_ratio,
                    "initial_peak_gain_db": self._result.initial_peak_gain_db,
                    "optimized_peak_gain_db": self._result.optimized_peak_gain_db,
                    "peak_reduction_db": self._result.peak_reduction_db,
                }
            )

        except Exception as e:
            QMessageBox.critical(self, "最適化エラー", str(e))
        finally:
            self._progress.setVisible(False)
            self._run_btn.setEnabled(True)

    def _on_export_results(self) -> None:
        if self._result is None:
            return

        text = self._result.summary_text()
        QMessageBox.information(self, "最適化結果", text)

    def get_result(self):
        return self._result
