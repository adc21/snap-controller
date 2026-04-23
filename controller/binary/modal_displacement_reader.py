"""
controller/binary/modal_displacement_reader.py
===============================================

SNAP の **MDFloor.xbn** / **MDNode.xbn** 専用リーダー。

固有モード解析で出力される「各階・各節点のモード形状（固有ベクトル）」
を保持するバイナリ。 ``XbnReader`` が想定する通常 XBN (Floor.xbn 等)
とは ``total_data_count`` の意味が異なるため、別クラスで扱う。

Binary layout (observed from SNAP 8.x)
--------------------------------------

::

    int32   magic            # 524296 (MDFloor) / 524292 (MDNode)
    int32   sub_count        # 1
    int32   values_per_mode  # 1 モード分の float 数 (= num_items × dof_per_item)
    int32   num_modes        # 出力モード数
    int32   mode_no[num_modes]  # モード番号 (1..N)
    ...     per-item meta / padding ...
    float32 data[num_modes × values_per_mode]  # 固有ベクトル本体

データ領域は末尾揃えで配置されており、先頭からのオフセットは
``data_start = total_floats - num_modes * values_per_mode`` で
決定できる。 間にある meta/padding は SNAP バージョンや節点数に
依存するため、本リーダーでは内容を問わず単にスキップする。

DOF slot の扱い
---------------

``values_per_mode`` は ``num_items × dof_per_item`` に分割され、
item (= 階 または 節点) ごとに固定 DOF 個の float が並ぶ。

- **MDFloor.xbn**: ``dof_per_item = 3`` (並進成分)
- **MDNode.xbn** : ``dof_per_item = 6`` (並進 + 回転)

DOF slot -> 物理量 の対応は構造形式 (``structure_type``) に依存する
ことが観測されている (planar は slot 0 が Dx、3D は slot 1 が Dx
を主成分として保持)。 この対応は :meth:`slot_for_direction` と
:meth:`direction_for_slot` が引き受ける。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# DOF slot マッピング
# ---------------------------------------------------------------------------

# (structure_type, dof_per_item) -> {slot_idx: direction_label}
#   structure_type: 0=立体(3D), 1=平面(planar)
#   dof_per_item  : 3=MDFloor, 6=MDNode
_SLOT_MAPS: Dict[Tuple[int, int], Dict[int, str]] = {
    # Planar (2D shear)
    (1, 3): {0: "Dx", 1: "Dy", 2: "Dz"},
    (1, 6): {0: "Dx", 1: "Dy", 2: "Dz", 3: "Rx", 4: "Ry", 5: "Rz"},
    # 3D
    (0, 3): {0: "Rz", 1: "Dx", 2: "Dy"},
    (0, 6): {0: "Rz", 1: "Dx", 2: "Dy", 3: "Dz", 4: "Rx", 5: "Ry"},
}

_DEFAULT_SLOT_MAP_3 = {0: "slot0", 1: "slot1", 2: "slot2"}
_DEFAULT_SLOT_MAP_6 = {i: f"slot{i}" for i in range(6)}


def _slot_map(structure_type: Optional[int], dof_per_item: int) -> Dict[int, str]:
    key = (1 if structure_type == 1 else 0, dof_per_item)
    m = _SLOT_MAPS.get(key)
    if m is not None:
        return m
    return _DEFAULT_SLOT_MAP_3 if dof_per_item == 3 else _DEFAULT_SLOT_MAP_6


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class ModalDisplacementReader:
    """MDFloor.xbn / MDNode.xbn 専用リーダー。

    Parameters
    ----------
    path : str | Path
        MDFloor.xbn または MDNode.xbn へのパス。
    dof_per_item : int, default=3
        1 項目あたりの DOF 数。MDFloor は 3、MDNode は 6。
    structure_type : int, optional
        TTL[0]: 0=立体、1=平面。DOF slot → Dx/Dy 等のマッピングに利用。
        ``None`` の場合は自動判定 (slot 分布から推定) を試み、失敗時は
        立体として扱う。

    Attributes
    ----------
    num_modes : int
        モード数。
    num_items : int
        1 モードあたりの項目数 (階数 or 節点数)。
    dof_per_item : int
        1 項目あたりの DOF 数。
    data : np.ndarray | None
        shape ``(num_modes, num_items, dof_per_item)``。
    mode_numbers : list[int]
        ヘッダから読み取ったモード番号列。
    """

    def __init__(
        self,
        path: str | Path,
        dof_per_item: int = 3,
        structure_type: Optional[int] = None,
    ) -> None:
        self.path = Path(path)
        self.dof_per_item = int(dof_per_item)
        self.structure_type = structure_type

        self.magic: int = 0
        self.num_modes: int = 0
        self.values_per_mode: int = 0
        self.num_items: int = 0
        self.data: Optional[np.ndarray] = None
        self.mode_numbers: List[int] = []

        if self.path.exists():
            self._parse()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self) -> None:
        raw = self.path.read_bytes()
        total = len(raw) // 4
        if total < 4:
            return

        ints = np.frombuffer(raw, dtype=np.int32)
        floats = np.frombuffer(raw, dtype=np.float32)

        self.magic = int(ints[0])
        self.values_per_mode = int(ints[2])
        self.num_modes = int(ints[3])

        if self.num_modes <= 0 or self.values_per_mode <= 0:
            return
        if self.values_per_mode % self.dof_per_item != 0:
            return
        self.num_items = self.values_per_mode // self.dof_per_item

        expected = self.num_modes * self.values_per_mode
        if expected > total:
            return
        data_start = total - expected

        mode_list_start = 4
        mode_list_end = mode_list_start + self.num_modes
        if mode_list_end <= data_start:
            self.mode_numbers = [int(ints[mode_list_start + i]) for i in range(self.num_modes)]
        else:
            self.mode_numbers = list(range(1, self.num_modes + 1))

        flat = np.asarray(floats[data_start:data_start + expected], dtype=np.float32)
        self.data = flat.reshape(
            self.num_modes, self.num_items, self.dof_per_item
        ).copy()

    # ------------------------------------------------------------------
    # Direction mapping helpers
    # ------------------------------------------------------------------

    def slot_map(self) -> Dict[int, str]:
        """Slot index -> direction label の辞書。"""
        return dict(_slot_map(self.structure_type, self.dof_per_item))

    def direction_for_slot(self, slot: int) -> str:
        m = self.slot_map()
        return m.get(slot, f"slot{slot}")

    def slot_for_direction(self, direction: str) -> Optional[int]:
        """Direction 文字列 (Dx, Dy, ...) -> slot index。存在しなければ None。"""
        target = direction.strip().upper()
        for s, lbl in self.slot_map().items():
            if lbl.upper() == target:
                return s
        return None

    def available_directions(self) -> List[str]:
        """データが実在する (slot 合計が有意) direction ラベル一覧。"""
        if self.data is None:
            return []
        slot_totals = np.abs(self.data).sum(axis=(0, 1))  # shape (dof,)
        labels: List[str] = []
        m = self.slot_map()
        for s in range(self.dof_per_item):
            if slot_totals[s] > 1e-6:
                labels.append(m.get(s, f"slot{s}"))
        return labels

    # ------------------------------------------------------------------
    # Mode data access
    # ------------------------------------------------------------------

    def mode_shape(self, mode_idx: int, direction: str = "Dx") -> np.ndarray:
        """指定モード・指定方向の各 item 振幅 (shape (num_items,))。

        direction が slot として無効な場合はゼロ配列を返す。
        """
        if self.data is None or not (0 <= mode_idx < self.num_modes):
            return np.zeros(self.num_items, dtype=float)
        slot = self.slot_for_direction(direction)
        if slot is None or not (0 <= slot < self.dof_per_item):
            return np.zeros(self.num_items, dtype=float)
        return self.data[mode_idx, :, slot].astype(float)

    def dominant_direction(self, mode_idx: int) -> str:
        """指定モードの最大振幅を持つ direction ラベルを返す。"""
        if self.data is None or not (0 <= mode_idx < self.num_modes):
            return "Dx"
        abs_sum = np.abs(self.data[mode_idx]).sum(axis=0)  # shape (dof,)
        if not np.any(abs_sum > 0):
            return "Dx"
        slot = int(np.argmax(abs_sum))
        return self.direction_for_slot(slot)

    def summary(self) -> str:
        return (
            f"ModalDisplacementReader: {self.path.name}\n"
            f"  magic=0x{self.magic:08x}, num_modes={self.num_modes}, "
            f"num_items={self.num_items}, dof_per_item={self.dof_per_item}, "
            f"structure_type={self.structure_type}"
        )
