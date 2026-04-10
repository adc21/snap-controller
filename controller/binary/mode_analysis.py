"""
controller/binary/mode_analysis.py
====================================

固有モード解析のための純粋データ処理ユーティリティ（PySide6 不要）。

app.ui.mode_shape_widget と tests/ の双方から import される
共有ロジックをここに切り出す。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# MDFloor.xbn 構造推定
# ---------------------------------------------------------------------------

def estimate_mdfloor_structure(
    num_modes: int, values_per_record: int
) -> Tuple[int, List[str]]:
    """MDFloor.xbn における 1 レコードあたりの DOF 配置を推定する。

    SNAP は通常 6 DOF（Dx, Dy, Dz, Rx, Ry, Rz）×モード数の配列を持つが、
    バージョンによって異なる場合がある。
    ``num_modes × dof == values_per_record`` を満たす dof を探索する。

    Parameters
    ----------
    num_modes        : 固有モード数
    values_per_record: 1 レコードあたりの値数（XbnReader.values_per_record）

    Returns
    -------
    dof_per_mode : int
        1 モードあたりの DOF 数（割り切れない場合は values_per_record を返す）
    dof_labels   : list[str]
        DOF 名リスト（len == dof_per_mode）
    """
    if num_modes <= 0 or values_per_record <= 0:
        return 0, []

    _DOF_LABELS: dict = {
        6: ["Dx", "Dy", "Dz", "Rx", "Ry", "Rz"],
        4: ["Dx", "Dy", "Rx", "Ry"],
        3: ["Dx", "Dy", "Dz"],
        2: ["Dx", "Dy"],
        1: ["Dx"],
    }
    for dof in (6, 4, 3, 2, 1):
        if num_modes * dof == values_per_record:
            labels = _DOF_LABELS.get(dof, [f"f{i}" for i in range(dof)])
            return dof, labels

    # 割り切れない場合: 全フィールドを raw として返す
    return values_per_record, [f"f{i}" for i in range(values_per_record)]


def get_mdfloor_mode_series(
    xbn_records: np.ndarray,
    mode_idx: int,
    dof_idx: int,
    dof_per_mode: int,
) -> np.ndarray:
    """MDFloor.xbn records 配列から指定モード × DOF の各階振幅を返す。

    Parameters
    ----------
    xbn_records  : ndarray, shape (num_floors, values_per_record)
    mode_idx     : 0-based モードインデックス
    dof_idx      : 0-based DOF インデックス
    dof_per_mode : 1 モードあたりの DOF 数

    Returns
    -------
    ndarray, shape (num_floors,)
        各階の当該モード / DOF の振幅。列が範囲外の場合はゼロ配列。
    """
    if dof_per_mode <= 0:
        return np.zeros(xbn_records.shape[0], dtype=float)
    col = mode_idx * dof_per_mode + dof_idx
    if col >= xbn_records.shape[1]:
        return np.zeros(xbn_records.shape[0], dtype=float)
    return xbn_records[:, col].astype(float)
