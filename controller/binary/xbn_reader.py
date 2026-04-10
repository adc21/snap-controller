"""
controller/binary/xbn_reader.py
===============================

SNAP ``.xbn`` （最大値・集約結果・固有値）ファイルの汎用リーダー。

.xbn ファイルは .hst と同じ 4 整数ヘッダを持ちますが、
時刻歴ではなく「各レコードごとの集約値」（最大値、最小値、初期値 など）
を格納します。num_steps に相当するカウント (int[1]) は
ファイル種別によって 1, 2, ... と小さい値を取ります。

ファイル種別（観測例）
----------------------

- Floor.xbn  : 各層の最大応答値（Dx/Dy/Vx/Vy/Ax/Ay 等）
- Story.xbn  : 各層の最大せん断・モーメント・変形
- Damper.xbn : 各ダンパー最大荷重・変形・エネルギー
- Period.xbn : 固有値解析結果（専用リーダー period_xbn_reader.py を使用）

汎用リーダーとしては、レコード数と 1 レコードあたりの値数を返し、
2 次元配列 (num_records × values_per_record) としてアクセス可能にします。

ヘッダ構造::

    int[0] = magic
    int[1] = sub_count        # 多くの場合 2（出力フォーマット種別？）
    int[2] = total_data_count  # 全レコード合計の値数
    int[3] = num_records
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


# ファイル種別ごとのフィールド名候補
_XBN_FIELD_LABELS: Dict[str, List[str]] = {
    "Floor.xbn": [
        # 24 fields per floor: 最大 Dx, Dy, Vx, Vy, Ax, Ay, RAx, RAy,
        # その他の最大・平均・ピーク時刻 など（詳細は未確定）
        "MaxDx", "MaxDy", "MaxVx", "MaxVy", "MaxAx", "MaxAy",
        "MaxRAx", "MaxRAy",
        "MinDx", "MinDy", "MinVx", "MinVy", "MinAx", "MinAy",
        "MinRAx", "MinRAy",
        "f16", "f17", "f18", "f19", "f20", "f21", "f22", "f23",
    ],
    "Story.xbn": [
        # ≒ 28 fields per story
        "MaxSx", "MaxSy", "MaxQx", "MaxQy", "MaxCx", "MaxCy",
        "MaxMx", "MaxMy", "MaxDrx", "MaxDry",
        "MinSx", "MinSy", "MinQx", "MinQy", "MinCx", "MinCy",
        "MinMx", "MinMy", "MinDrx", "MinDry",
        "f20", "f21", "f22", "f23", "f24", "f25", "f26", "f27",
    ],
    "Damper.xbn": [
        "MaxForce", "MaxDisp", "MaxVel", "MaxEnergy",
        "MinForce", "MinDisp", "MinVel", "MinEnergy",
    ],
    # モード応答値（Modal Displacement）—— 各モードの変形形状
    # フィールド構造は SNAP バージョン依存のため、6 DOF 暫定ラベル
    "MDFloor.xbn": [
        "Dx", "Dy", "Dz", "Rx", "Ry", "Rz",
        "f6", "f7", "f8", "f9", "f10", "f11",
    ],
    "MDNode.xbn": [
        "Dx", "Dy", "Dz", "Rx", "Ry", "Rz",
        "f6", "f7", "f8", "f9", "f10", "f11",
    ],
}


class XbnReader:
    """SNAP .xbn 汎用リーダー。"""

    def __init__(self, xbn_file: str | Path) -> None:
        self.xbn_file = Path(xbn_file)
        self.magic: int = 0
        self.sub_count: int = 0
        self.total_data_count: int = 0
        self.num_records: int = 0
        self.values_per_record: int = 0
        self.meta_per_record: int = 0
        self._records: Optional[np.ndarray] = None  # (num_records, values_per_record)
        self._meta: Optional[np.ndarray] = None     # (num_records, meta_per_record)

        if self.xbn_file.exists():
            self._parse()

    # ------------------------------------------------------------------
    def _parse(self) -> None:
        file_size = self.xbn_file.stat().st_size
        if file_size < 16:
            return

        with open(self.xbn_file, "rb") as f:
            data = f.read()

        self.magic, self.sub_count, self.total_data_count, self.num_records = (
            struct.unpack("<4i", data[:16])
        )

        if self.num_records <= 0:
            return

        total_floats = file_size // 4
        arr = np.frombuffer(data, dtype=np.float32)

        # レイアウト候補:
        #   - Floor.xbn / Story.xbn : メタなし + 値データ (num_rec × N)
        #   - Damper.xbn 等          : メタあり (num_rec × M) + 値データ (num_rec × N)
        #
        # total_data_count は「最終値データ領域の float 合計」を示すケースが多い。
        # そこで、値データの float 数 = total_data_count と仮定し、残りをメタへ。
        data_floats = max(0, total_floats - 4)
        value_floats = self.total_data_count
        if value_floats <= 0 or value_floats > data_floats:
            value_floats = data_floats

        meta_floats = data_floats - value_floats
        if meta_floats < 0:
            meta_floats = 0

        if self.num_records > 0:
            self.meta_per_record = meta_floats // self.num_records
            self.values_per_record = value_floats // self.num_records

        # メタ部
        meta_offset = 4
        meta_end = meta_offset + self.num_records * self.meta_per_record
        if self.meta_per_record > 0:
            self._meta = arr[meta_offset:meta_end].copy().reshape(
                self.num_records, self.meta_per_record
            )

        val_end = meta_end + self.num_records * self.values_per_record
        if self.values_per_record > 0 and val_end <= total_floats:
            self._records = arr[meta_end:val_end].copy().reshape(
                self.num_records, self.values_per_record
            )

    # ------------------------------------------------------------------
    @property
    def records(self) -> Optional[np.ndarray]:
        return self._records

    @property
    def meta(self) -> Optional[np.ndarray]:
        return self._meta

    def field_labels(self) -> List[str]:
        name = self.xbn_file.name
        labels = _XBN_FIELD_LABELS.get(name)
        if labels and len(labels) >= self.values_per_record:
            return labels[: self.values_per_record]
        return [f"f{i}" for i in range(self.values_per_record)]

    def summary(self) -> str:
        return (
            f"XbnReader: {self.xbn_file.name}\n"
            f"  magic=0x{self.magic:08x}, sub_count={self.sub_count}, "
            f"num_records={self.num_records}, "
            f"values_per_record={self.values_per_record}, "
            f"meta_per_record={self.meta_per_record}"
        )
