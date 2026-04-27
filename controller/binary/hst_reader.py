"""
controller/binary/hst_reader.py
===============================

SNAP ``.hst`` 時刻歴ファイルリーダー。

ファイル形式（example_3D/D4 サンプルから観測）
----------------------------------------------

::

    offset  size         content
    ----------------------------------------------------------------
    0       16 (4 int32) ヘッダ
                         int[0] = magic/version（ファイルごとに異なる）
                         int[1] = num_steps（時刻ステップ数）
                         int[2] = step_size（1 ステップあたりの float 数）
                         int[3] = num_records（レコード数、例: 層数）
    16      meta_total   レコードヘッダ (num_records × meta_per float)
                         meta_per はファイル種別ごとに固定:
                           Floor.hst  : 4
                           Story.hst  : 6
                           Damper.hst : 1
                           Rigid.hst  : 3
                           Spring.hst : 2
                           Node.hst   : 2
                           Column.hst : 3
                           Energy.hst : 0
                         （ファイル全体サイズから自動算出）
    +...    num_steps    時刻ステップデータ
            × step_size  各ステップは float32 配列:
            × 4 bytes     [0]          : ステップ番号 (int を float 化)
                          [1..step_size]: 1..step_size-1 番目のデータ値

時刻 t はステップ番号 × dt で求めます。dt はファイル内に直接格納されていない
ため、呼び出し側で指定（デフォルト 0.005 秒）してください。SNAP の時刻刻み
は一般に .s8i の入力条件から決定されます。

step_size のレイアウト（観測例）:
  - Floor.hst : step_size = 295 → 1 + 21 * 14 (1 ヘッダ + 21 層 × 14 成分)
  - Story.hst : step_size = 561 → 1 + 20 * 28 (1 ヘッダ + 20 層 × 28 成分)
  - Damper.hst: step_size = 481 → 1 + 60 * 8  (1 ヘッダ + 60 ダンパー × 8)
  - Energy.hst: step_size = 16  → エネルギー集計値 16 成分

本リーダーは各ステップの全 float を素のまま配列として保持し、
`time_series(record_index, field_index)` で任意の成分を取り出せます。
成分のラベル（Dx, Vx, Ax 等）の割り当ては呼び出し側の責務です。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np


# ファイル種別ごとの既知メタサイズ（float 数 / レコード）
_KNOWN_META_PER: Dict[str, int] = {
    "Floor.hst": 4,
    "Story.hst": 6,
    "Damper.hst": 1,
    "Rigid.hst": 3,
    "Spring.hst": 2,
    "Node.hst": 2,
    "Column.hst": 3,
    "Beam.hst": 3,
    "Energy.hst": 0,
    "Truss.hst": 3,
}

# Floor.hst / Story.hst / Damper.hst の成分レイアウト（観測ベース推定）
# step 先頭の float[0] はステップ番号。以降がレコードごとの成分。
#
# Floor.hst (14 成分 / 層): 変位・速度・加速度 ほか
# Story.hst (28 成分 / 層): せん断・変形・モーメント ほか
#
# レイアウトの詳細確証は未取得のため、フィールド名はあくまで「候補」です。
# NOTE: フィールド名の割当は、実サンプル (example_3D/D4) で
# 非ゼロ値を持つインデックスと物理量オーダーから逆算した「暫定ラベル」です。
# SNAP の公式ドキュメント取得後に確定値へ差し替える想定のため、
# デフォルトでは汎用 f0, f1, ... 名を返し、既知の明瞭なケース
# (Damper, Spring) のみ物理名を付けています。
# ファイル名 → { fpr → ラベルリスト }
# fpr が一致するレイアウトを使い、一致しなければ f0,f1,... にフォールバック。
_FIELD_LAYOUTS_BY_FPR: Dict[str, Dict[int, List[str]]] = {
    "Damper.hst": {
        # 2D 簡易ダンパー: fpr=4 は [F, D, Energy, V] の順。
        # (f2 は単調増加、f3 は振動 → F=0, D=1, E=2, V=3)
        4: ["Force", "Disp", "Energy", "Vel"],
        # 3D 立体モデル: fpr=8。V 成分は出力されず、
        # 先頭 2 組 (f0/f1, f2/f3, f5/f6) は異なるサブ要素の F/D 対。
        # 末尾 (f7) が累積エネルギー (単調増加)。
        8: ["F", "D", "F2", "D2", "f4", "F3", "D3", "Energy"],
        # iRDT ダンパー: fpr=11。F=f1, D=f2, V=f4, E=f9 (末尾ではない)。
        11: [
            "f0", "Force", "Disp", "cumD",
            "Vel", "Fv", "cumV",
            "F2", "D2", "Energy", "f10",
        ],
    },
    "Spring.hst": {
        5: ["Force", "Disp", "Vel", "Energy", "f4"],
    },
}

# 後方互換用（field_labels() 内で _FIELD_LAYOUTS_BY_FPR を優先使用）
_FIELD_LAYOUTS: Dict[str, List[str]] = {}


# 観測結果をもとにした Floor.hst / Story.hst のフィールド対応の「ヒント」:
#   Floor[21F] で非ゼロとなるインデックス
#     0 (推定: 何らかの大きめ量、未確定)
#     4 (推定: 変位か速度の Y 成分)
#     6, 8 (推定: 絶対加速度 X, 絶対加速度 合成)
#     9, 10 (推定: 応答量の追加成分)
#   このフィールド構造は SNAP バージョン依存の可能性があるため、
#   UI では `f0 .. f13` と表示し、ユーザーが目視確認できるようにします。


@dataclass
class HstHeader:
    """.hst ファイルの 4 整数ヘッダ + 派生情報。

    iOD など、同じ Damper.hst 内でレコードごとに fpr が異なるケース
    （例: 22 レコード × fpr=4 + 22 レコード × fpr=11）に対応するため
    ``per_record_fpr`` / ``per_record_offset`` を保持する。
    None の場合は全レコード一律 fpr = ``fields_per_record``。
    """
    magic: int
    num_steps: int
    step_size: int
    num_records: int
    meta_per_record: int
    fields_per_record: int
    file_size: int
    per_record_fpr: Optional[List[int]] = None
    per_record_offset: Optional[List[int]] = None

    def summary(self) -> str:
        base = (
            f"magic=0x{self.magic:08x}, steps={self.num_steps}, "
            f"step_size={self.step_size}, num_records={self.num_records}, "
            f"meta_per_record={self.meta_per_record}, "
            f"fields_per_record={self.fields_per_record}"
        )
        if self.per_record_fpr is not None:
            uniq = sorted(set(self.per_record_fpr))
            base += f", mixed_fpr={uniq}"
        return base


class HstReader:
    """SNAP ``.hst`` 時刻歴ファイルパーサー。

    Usage
    -----
    >>> reader = HstReader("Floor.hst", dt=0.005)
    >>> reader.header.num_records
    21
    >>> times = reader.times()              # shape: (num_steps,)
    >>> values = reader.time_series(0, 0)    # 層1 の field 0 全ステップ
    """

    def __init__(
        self,
        hst_file: str | Path,
        dt: float = 0.005,
        *,
        lazy: bool = True,
    ) -> None:
        self.hst_file = Path(hst_file)
        self.dt = dt
        self.header: Optional[HstHeader] = None
        self._raw: Optional[np.ndarray] = None  # shape: (num_steps, step_size)
        self._data_offset: int = 0  # 4 int header + meta の後ろの float インデックス
        self._meta_raw: Optional[np.ndarray] = None  # shape: (num_records, meta_per)
        self._step_header_size: int = 1  # ステップ先頭のヘッダ float 数（通常 1 = ステップ番号）

        if self.hst_file.exists():
            self._parse_header()
            if not lazy:
                self._load_data()

    # ------------------------------------------------------------------
    # Header parsing
    # ------------------------------------------------------------------

    def _parse_header(self) -> None:
        file_size = self.hst_file.stat().st_size
        if file_size < 16:
            return

        with open(self.hst_file, "rb") as f:
            head = f.read(16)
        magic, num_steps, step_size, num_records = struct.unpack("<4i", head)

        # 基本的な妥当性チェック
        if num_steps <= 0 or step_size <= 0 or num_records < 0:
            return

        total_floats = file_size // 4
        # data floats = num_steps * step_size
        data_floats = num_steps * step_size
        meta_floats = total_floats - 4 - data_floats
        if meta_floats < 0:
            # ヘッダ値が信頼できない – meta_floatsを 0 に補正
            meta_floats = 0
        if num_records > 0:
            meta_per = meta_floats // num_records
        else:
            meta_per = 0

        # --------------------------------------------------------
        # fields_per_record の検出
        # step_size = step_header + num_records * fields_per_record を仮定
        # step_header は通常 1 (ステップ番号 float) だが
        # SNAPバージョンによっては 2 など異なる場合がある。
        # 0..4 の範囲で step_header を総当たりし、最初に割り切れる組合せを採用。
        # --------------------------------------------------------
        fields_per_record = 0
        self._step_header_size: int = 1  # デフォルト
        per_record_fpr: Optional[List[int]] = None
        per_record_offset: Optional[List[int]] = None
        if num_records > 0 and step_size > 0:
            # 既知メタサイズからヒントを取得（ファイル名ベース）
            known_meta = _KNOWN_META_PER.get(self.hst_file.name, -1)
            # known_meta が指定されている場合のみ meta_per を上書き
            # ただし ファイルに十分なメタ領域がある場合に限る
            if known_meta >= 0 and meta_floats >= num_records * known_meta:
                meta_per = known_meta
            # SNAP .hst は先頭 float にステップ番号を持つため sh=1 が標準。
            # sh=0 から始めると num_records=1 の場合に常に sh=0 が選ばれ、
            # ステップ番号がデータとして誤読されるバグを引き起こす。
            for sh in (1, 2, 3, 4, 0):
                remainder = step_size - sh
                if remainder > 0 and remainder % num_records == 0:
                    fpr = remainder // num_records
                    if fpr > 0:
                        fields_per_record = fpr
                        self._step_header_size = sh
                        break

            # 一律 fpr で解釈できない場合、混在 fpr レイアウトを試す
            # (例: iOD Damper.hst = 22×fpr4 + 22×fpr11 + sh=1 = 331)
            if fields_per_record == 0:
                seg = self._detect_segmented_fpr(
                    step_size, num_records, meta_per
                )
                if seg is not None:
                    sh, fprs, offsets = seg
                    self._step_header_size = sh
                    per_record_fpr = fprs
                    per_record_offset = offsets
                    # 境界チェック用に最大値を保持 (time_series で使用)
                    fields_per_record = max(fprs)

        self.header = HstHeader(
            magic=magic,
            num_steps=num_steps,
            step_size=step_size,
            num_records=num_records,
            meta_per_record=meta_per,
            fields_per_record=fields_per_record,
            file_size=file_size,
            per_record_fpr=per_record_fpr,
            per_record_offset=per_record_offset,
        )
        self._data_offset = 4 + num_records * meta_per

    # ------------------------------------------------------------------
    # 混在 fpr 検出
    # ------------------------------------------------------------------

    def _detect_segmented_fpr(
        self,
        step_size: int,
        num_records: int,
        meta_per: int,
    ) -> Optional[tuple[int, List[int], List[int]]]:
        """meta 値をキーに 2 セグメント混在 fpr レイアウトを検出する。

        Damper.hst (iOD) では、レコードごとに meta 値（int32 解釈）が
        サブ要素種別を表すらしく、同じ meta を持つ連続レコード群は
        同じ fpr を共有する。ファイルから直接 meta を読み出し、
        以下の条件を満たすセグメント分割を探す:

        * meta を int32 解釈した key 配列が最大 2 値で構成される
        * 配列が単調に切り替わる (先頭 N_a 個が type_a, 残り N_b 個が type_b)
        * ``sh + N_a * fpr_a + N_b * fpr_b == step_size``
          を満たす既知 fpr 候補 (4, 5, 8, 11) の組合せが存在する

        Returns
        -------
        (step_header_size, per_record_fpr, per_record_offset) または ``None``
        """
        if meta_per < 1 or num_records < 2:
            return None
        try:
            with open(self.hst_file, "rb") as f:
                f.seek(16)  # 4-int ヘッダの後
                meta_bytes = f.read(num_records * meta_per * 4)
        except OSError:
            return None
        if len(meta_bytes) < num_records * meta_per * 4:
            return None
        meta_arr = np.frombuffer(meta_bytes, dtype=np.int32).reshape(
            num_records, meta_per
        )
        key = meta_arr[:, 0]
        uniq = sorted({int(v) for v in key})
        if len(uniq) != 2:
            return None
        # 単調切替（先頭群 / 末尾群）を確認
        first_change = None
        for i in range(1, num_records):
            if key[i] != key[0]:
                first_change = i
                break
        if first_change is None:
            return None
        head = key[:first_change]
        tail = key[first_change:]
        if not (np.all(head == key[0]) and np.all(tail == key[-1])):
            return None
        na, nb = first_change, num_records - first_change
        fpr_candidates = (4, 5, 8, 11)
        for sh in (1, 2, 3, 4, 0):
            for fpr_a in fpr_candidates:
                for fpr_b in fpr_candidates:
                    if fpr_a == fpr_b:
                        continue
                    if sh + na * fpr_a + nb * fpr_b != step_size:
                        continue
                    fprs = [fpr_a] * na + [fpr_b] * nb
                    offsets: List[int] = []
                    cur = sh
                    for fpr in fprs:
                        offsets.append(cur)
                        cur += fpr
                    return sh, fprs, offsets
        return None

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_data(self) -> None:
        if self.header is None or self._raw is not None:
            return

        h = self.header
        # numpy で mmap 読み込み（巨大ファイル対策）
        total_floats = h.file_size // 4
        arr = np.memmap(self.hst_file, dtype=np.float32, mode="r",
                        shape=(total_floats,))
        # メタ部分
        if h.meta_per_record > 0 and h.num_records > 0:
            meta_flat = arr[4:4 + h.num_records * h.meta_per_record]
            self._meta_raw = np.array(meta_flat).reshape(
                h.num_records, h.meta_per_record
            )
        else:
            self._meta_raw = np.zeros((h.num_records, 0), dtype=np.float32)

        data_start = self._data_offset
        data_end = data_start + h.num_steps * h.step_size
        data_flat = arr[data_start:data_end]
        # ファイルが予想より短い場合は完全なステップ分のみ使用（reshape エラー回避）
        expected = h.num_steps * h.step_size
        if len(data_flat) < expected:
            complete_steps = len(data_flat) // h.step_size
            if complete_steps == 0:
                self._raw = np.empty((0, h.step_size), dtype=np.float32)
                return
            data_flat = data_flat[:complete_steps * h.step_size]
            self._raw = np.array(data_flat).reshape(complete_steps, h.step_size)
        else:
            self._raw = np.array(data_flat[:expected]).reshape(h.num_steps, h.step_size)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        return self._raw is not None

    def ensure_loaded(self) -> None:
        if not self.is_loaded:
            self._load_data()

    def times(self) -> np.ndarray:
        """時刻配列（秒）を返します。"""
        if self.header is None:
            return np.zeros(0, dtype=np.float32)
        n = self.header.num_steps
        return np.arange(n, dtype=np.float32) * float(self.dt)

    def step_numbers(self) -> np.ndarray:
        """各ステップ番号の配列。.hst は先頭 float に step# を持つため、それを返します。"""
        self.ensure_loaded()
        if self._raw is None:
            return np.zeros(0, dtype=np.int32)
        # int ビットパターンで解釈
        return self._raw[:, 0].view(np.int32).copy()

    def fpr_for_record(self, record_index: int) -> int:
        """指定レコードの fields_per_record を返す。

        混在 fpr レイアウト（per_record_fpr が設定されている場合）では
        レコードごとの fpr を返す。そうでなければ header.fields_per_record。
        """
        if self.header is None:
            return 0
        h = self.header
        if h.per_record_fpr is not None and 0 <= record_index < len(h.per_record_fpr):
            return int(h.per_record_fpr[record_index])
        return h.fields_per_record

    def record_offset(self, record_index: int) -> int:
        """指定レコードのステップ内カラム先頭オフセットを返す。"""
        if self.header is None:
            return 0
        h = self.header
        if h.per_record_offset is not None and 0 <= record_index < len(h.per_record_offset):
            return int(h.per_record_offset[record_index])
        sh = getattr(self, "_step_header_size", 1)
        return sh + record_index * h.fields_per_record

    def time_series(self, record_index: int, field_index: int) -> np.ndarray:
        """
        指定レコード (例: 層 index 0=1F, 1=2F ...) の
        指定フィールド（例: 0=Dx）の時刻歴を取得。
        """
        self.ensure_loaded()
        if self._raw is None or self.header is None:
            return np.zeros(0, dtype=np.float32)
        h = self.header
        if h.fields_per_record <= 0:
            raise ValueError(
                f"fields_per_record が未確定です。"
                f"step_size={h.step_size}, num_records={h.num_records} で "
                "step_size が step_header + num_records*N の形に割り切れません。"
            )
        if not (0 <= record_index < h.num_records):
            raise IndexError(f"record_index {record_index} out of range "
                             f"(num_records={h.num_records})")
        fpr_this = self.fpr_for_record(record_index)
        if not (0 <= field_index < fpr_this):
            raise IndexError(f"field_index {field_index} out of range "
                             f"(fpr_for_record({record_index})={fpr_this})")

        offset = self.record_offset(record_index) + field_index
        return self._raw[:, offset].copy()

    def raw_field(self, absolute_field_index: int) -> np.ndarray:
        """step_size 配列内の絶対インデックスで時刻歴を取得（デバッグ用）。"""
        self.ensure_loaded()
        if self._raw is None:
            return np.zeros(0, dtype=np.float32)
        return self._raw[:, absolute_field_index].copy()

    def field_labels(self) -> List[str]:
        """既知レイアウトからフィールド名候補を返します（レコード 0 基準）。"""
        return self.field_labels_for_record(0)

    def field_labels_for_record(self, record_index: int) -> List[str]:
        """指定レコードの fpr に応じたフィールド名候補を返します。

        混在 fpr レイアウト (iOD Damper.hst 等) では、レコードごとに
        fpr が異なるためラベルもレコード依存で返す必要がある。
        """
        if self.header is None:
            return []
        name = self.hst_file.name
        fpr = self.fpr_for_record(record_index)
        # fpr 別レイアウトを優先
        by_fpr = _FIELD_LAYOUTS_BY_FPR.get(name)
        if by_fpr:
            layout = by_fpr.get(fpr)
            if layout:
                return list(layout)
        # 旧形式フォールバック
        layout = _FIELD_LAYOUTS.get(name)
        if layout and len(layout) == fpr:
            return list(layout)
        # 汎用フォールバック: f0, f1, ...
        return [f"f{i}" for i in range(fpr)]

    def summary(self) -> str:
        if self.header is None:
            return f"HstReader: {self.hst_file.name}（ヘッダ解析失敗）"
        return f"HstReader: {self.hst_file.name}\n  {self.header.summary()}"

    def peak_per_record(self, field_index: int) -> np.ndarray:
        """
        各レコードの該当フィールドの絶対値最大（ピーク）配列を返します。
        shape=(num_records,)

        混在 fpr レイアウトでは、指定 field_index を持たないレコードは 0 を返す。
        """
        self.ensure_loaded()
        if self._raw is None or self.header is None:
            return np.zeros(0, dtype=np.float32)
        h = self.header
        peaks = np.zeros(h.num_records, dtype=np.float32)
        for r in range(h.num_records):
            fpr_this = self.fpr_for_record(r)
            if field_index >= fpr_this:
                continue
            offset = self.record_offset(r) + field_index
            peaks[r] = float(np.max(np.abs(self._raw[:, offset])))
        return peaks
