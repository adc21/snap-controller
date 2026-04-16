"""
app/models/s8i_parser.py
SNAP ver.8 入力ファイル (.s8i) パーサー。

.s8i ファイルを読み込み、以下の情報を抽出します:
  - モデル基本情報 (タイトル, バージョン)
  - 節点 (ND)
  - 層 (FL)
  - 剛体/剛床 (RG)
  - 制振ブレース (SR)
  - 免制振装置 (RD)
  - ダンパー定義 (DVOD, DSD, 等)

.s8i 形式:
  各行は ``KEYWORD / value1, value2, ...`` の形式です。
  ``REM / ...`` はコメント行です。
  Shift-JIS エンコーディングで保存されます。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """節点 (ND)。"""
    id: int
    x: float
    y: float
    z: float
    mass: float = 0.0
    support: str = ""       # 支持条件名 (e.g. "S1")
    x_grid: str = ""
    y_grid: str = ""
    z_grid: str = ""
    raw: str = ""           # 生の行テキスト


@dataclass
class Floor:
    """層 (FL)。"""
    name: str               # e.g. "F1", "F21"
    rigid_group: str = ""   # 参照する剛体グループ (e.g. "R1")
    raw: str = ""


@dataclass
class DamperDefinition:
    """ダンパー定義 (DVOD, DSD 等)。

    架構 - 免制振装置 で設定されるダンパーパラメータセットです。
    """
    keyword: str            # "DVOD", "DSD", etc.
    name: str               # 定義名 (e.g. "C1", "IOD", "FR1600")
    values: List[str]       # 全フィールド値（名前を含む）
    raw: str = ""           # 生の行テキスト
    line_no: int = 0        # ファイル内行番号

    @property
    def display_label(self) -> str:
        """表示用ラベル。"""
        type_labels = {
            "DVOD": "粘性/オイルダンパー",
            "DSD": "鋼材ダンパー",
            "DIS": "免震支承材",
            "DISD": "免震用履歴型ダンパー",
            "DVD": "粘性ダンパー（減衰こま）",
            "DVED": "粘弾性ダンパー",
            "DOD": "オイルダンパー",
            "DVHY": "履歴型ダンパー",
            "DVBI": "バイリニア型",
            "DVSL": "すべり型",
            "DVFR": "摩擦ダンパー",
            "DVTF": "粘弾性ダンパー",
            "DVMS": "マスダンパー",
        }
        return f"{type_labels.get(self.keyword, self.keyword)}: {self.name}"


@dataclass
class DamperBrace:
    """制振ブレース (SR)。

    ダンパーを取り付けるためのブレース部材。
    """
    member_id: int          # 部材番号
    node: int               # 接続先節点番号
    values: List[str]       # 全フィールド値
    raw: str = ""
    line_no: int = 0


@dataclass
class DamperElement:
    """免制振装置 (RD)。

    ダンパーの配置を定義します。2つの節点を結びます。

    SNAP テキストデータ仕様 (RD レコード) フィールド対応:
      index 0  : 名称
      index 1  : 節点I
      index 2  : 節点J
      index 3  : 種別 (damper_type)  0=鋼材/摩擦, 1=粘性/オイル, 2=オイル, 3=粘性, 4=粘弾性
      index 4  : 装置名 (damper_def_name)
      index 5  : 装置剛性
      index 6  : 取付け剛性
      index 7  : アスペクト比
      index 8  : 付加重量1
      index 9  : 付加重量2
      index 10 : 倍数/基数 (quantity) ← ここが基数の正しい位置
      index 11 : 方向
      ...
    """
    name: str               # 名称/タイプ名
    node_i: int             # 節点I（始端）
    node_j: int             # 節点J（終端）
    quantity: int           # 基数/倍数 (index 10 = フィールド11)
    damper_def_name: str    # 装置名 (index 4 = フィールド5)
    values: List[str]       # 全フィールド値（書き戻し用）
    damper_type: int = 0    # 種別 (index 3 = フィールド4); default=0 for backward compat
    raw: str = ""
    line_no: int = 0

    @property
    def display_label(self) -> str:
        """表示用ラベル。"""
        return f"{self.name} ({self.node_i}→{self.node_j}, ×{self.quantity}, {self.damper_def_name})"

    @property
    def damper_type_label(self) -> str:
        """種別の日本語ラベルを返します。"""
        return _RD_DAMPER_TYPE_LABELS.get(self.damper_type, f"種別{self.damper_type}")


@dataclass
class DydRecord:
    """応答解析条件 (DYD)。

    .s8i ファイル内の DYD レコードに対応します。

    フィールド対応 (0-based index):
      0:  解析方法 (Newmark-β法) = 0.25
      1:  履歴結果の出力指定 - すべての節点 (0/1)
      2:  すべてのはり (0/1)
      3:  すべての柱 (0/1)
      4:  すべてのトラス (0/1)
      5:  すべての壁 (0/1)
      6:  すべての平板 (0/1)
      7:  すべての仕口パネル (0/1)
      8:  すべてのスプリング (0/1)
      9:  すべての曲げせん断棒 (0/1)
      10: すべてのダンパー (0/1)
      11: すべての免震支承材と免震用履歴型ダンパー (0/1)
      12: 不釣合力を出力する (0/1)
      13: 既存架構・補強架構・トグル制震ブレース毎に集計する (0/1)
      14-17: イテレーション条件
      18-21: 解析中止条件
    """
    values: List[str]       # 全フィールド値
    raw: str = ""
    line_no: int = 0

    # 履歴結果の出力指定フィールド (0-indexed)
    HISTORY_OUTPUT_FIELDS: tuple = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
    HISTORY_OUTPUT_LABELS: tuple = (
        "すべての節点",
        "すべてのはり",
        "すべての柱",
        "すべてのトラス",
        "すべての壁",
        "すべての平板",
        "すべての仕口パネル",
        "すべてのスプリング",
        "すべての曲げせん断棒",
        "すべてのダンパー",
        "すべての免震支承材と免震用履歴型ダンパー",
    )


@dataclass
class DycCase:
    """応答解析ケース (DYC)。

    .s8i ファイル内に記述された動的解析ケース1行に対応します。

    SNAP は DYC 行の記述順にフォルダ ``D1, D2, ...`` を作成し、
    ``run_flag == 1`` のケースのみ解析を実行して結果を格納します。

    フィールド対応 (0-based index):
      0: ケース名
      1: run_flag (0=解析しない, 1=解析する)
      2: 地震動波数
      ...
    """
    case_no: int          # s8i 内での連番 (1始まり) = D{N} フォルダ番号
    name: str             # ケース名 (e.g. "BCJL2-MIX")
    run_flag: int         # 0=解析しない, 1=解析する
    num_waves: int        # 地震動波数
    values: List[str]     # 全フィールド値
    raw: str = ""
    line_no: int = 0

    @property
    def is_run(self) -> bool:
        """解析対象ケースかどうかを返します。

        SNAP では run_flag=0 のみ「解析しない」扱いです。
        1 (通常実行) や 2 (一部モード) など 0 以外はすべて解析対象です。
        """
        return self.run_flag != 0

    @property
    def folder_name(self) -> str:
        """SNAP が作成する作業フォルダ名 (例: "D4")。"""
        return f"D{self.case_no}"

    @property
    def display_label(self) -> str:
        """UI 表示用ラベル。"""
        if self.is_run:
            flag_str = f"✓({self.run_flag})"
        else:
            flag_str = "–"
        return f"[{flag_str}] D{self.case_no}: {self.name}"


@dataclass
class S8iModel:
    """パース結果を格納するモデルオブジェクト。"""
    file_path: str = ""
    title: str = ""
    version: str = ""
    # 節点
    nodes: Dict[int, Node] = field(default_factory=dict)
    # 層
    floors: List[Floor] = field(default_factory=list)
    # ダンパー定義 (DVOD, DSD, etc.)
    damper_defs: List[DamperDefinition] = field(default_factory=list)
    # 制振ブレース (SR)
    damper_braces: List[DamperBrace] = field(default_factory=list)
    # 免制振装置 (RD)
    damper_elements: List[DamperElement] = field(default_factory=list)
    # 応答解析条件 (DYD)
    dyd_record: Optional[DydRecord] = None
    # 応答解析ケース (DYC)
    dyc_cases: List[DycCase] = field(default_factory=list)
    # 全行（書き戻し用）
    _lines: List[str] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def num_floors(self) -> int:
        return len(self.floors)

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def num_dampers(self) -> int:
        return len(self.damper_elements)

    @property
    def total_damper_units(self) -> int:
        """ダンパー装置の合計基数。"""
        return sum(d.quantity for d in self.damper_elements)

    def get_node(self, node_id: int) -> Optional[Node]:
        return self.nodes.get(node_id)

    def get_damper_def(self, name: str) -> Optional[DamperDefinition]:
        """名前でダンパー定義を検索。"""
        for d in self.damper_defs:
            if d.name == name:
                return d
        return None

    def get_floor_nodes(self) -> Dict[str, List[int]]:
        """層ごとの節点IDリストを返します（Z座標でグルーピング）。"""
        from collections import defaultdict
        z_groups: Dict[float, List[int]] = defaultdict(list)
        for node in self.nodes.values():
            z_groups[node.z].append(node.id)
        # Z座標でソートして返す
        result: Dict[str, List[int]] = {}
        for i, (z, node_ids) in enumerate(sorted(z_groups.items())):
            floor_name = self.floors[i].name if i < len(self.floors) else f"Z={z}"
            result[floor_name] = sorted(node_ids)
        return result

    # ------------------------------------------------------------------
    # 書き戻し
    # ------------------------------------------------------------------

    def write(self, output_path: str) -> None:
        """変更を反映した .s8i ファイルを書き出します。

        手順:
        1. 既存行をすべてインプレース更新（行数は変わらない）
        2. 新規行を挿入位置と共に収集
        3. 下から上へ挿入（上の行番号に影響しない）
        """
        lines = list(self._lines)  # コピー

        # ---- Phase 1: 既存行のインプレース更新 ----
        for ddef in self.damper_defs:
            if ddef.line_no > 0 and ddef.line_no <= len(lines):
                lines[ddef.line_no - 1] = f"{ddef.keyword} / {','.join(ddef.values)}"

        for brace in self.damper_braces:
            if brace.line_no > 0 and brace.line_no <= len(lines):
                lines[brace.line_no - 1] = f"SR / {','.join(brace.values)}"

        for elem in self.damper_elements:
            if elem.line_no > 0 and elem.line_no <= len(lines):
                lines[elem.line_no - 1] = f"RD / {','.join(elem.values)}"

        if self.dyd_record and self.dyd_record.line_no > 0 and self.dyd_record.line_no <= len(lines):
            lines[self.dyd_record.line_no - 1] = f"DYD / {','.join(self.dyd_record.values)}"

        for dyc in self.dyc_cases:
            if dyc.line_no > 0 and dyc.line_no <= len(lines):
                lines[dyc.line_no - 1] = f"DYC / {','.join(dyc.values)}"

        # ---- Phase 2: 新規行の挿入位置を決定 ----
        # (insert_pos, line_text) のリストを収集し、下から挿入する
        insertions: list = []  # [(0-indexed insert position, line_text)]

        # 新規ダンパー定義
        new_defs = [d for d in self.damper_defs if d.line_no == 0]
        if new_defs:
            last_def_line = 0
            for ddef in self.damper_defs:
                if ddef.line_no > last_def_line:
                    last_def_line = ddef.line_no
            for brace in self.damper_braces:
                if brace.line_no > 0 and (last_def_line == 0 or brace.line_no < last_def_line):
                    if last_def_line == 0:
                        last_def_line = brace.line_no - 1
            for elem in self.damper_elements:
                if elem.line_no > 0 and (last_def_line == 0 or elem.line_no < last_def_line):
                    if last_def_line == 0:
                        last_def_line = elem.line_no - 1
            pos = last_def_line  # 0-indexed
            for new_def in new_defs:
                insertions.append((pos, f"{new_def.keyword} / {','.join(new_def.values)}"))
                pos += 1  # 同じブロック内で連続挿入

        # 新規 RD 要素
        new_elems = [e for e in self.damper_elements if e.line_no == 0]
        if new_elems:
            last_rd_line = 0
            for elem in self.damper_elements:
                if elem.line_no > last_rd_line:
                    last_rd_line = elem.line_no
            if last_rd_line == 0:
                for brace in self.damper_braces:
                    if brace.line_no > last_rd_line:
                        last_rd_line = brace.line_no
            if last_rd_line == 0:
                # ダンパー定義の直後
                for ddef in self.damper_defs:
                    if ddef.line_no > last_rd_line:
                        last_rd_line = ddef.line_no
            pos = last_rd_line  # 0-indexed
            for new_elem in new_elems:
                insertions.append((pos, f"RD / {','.join(new_elem.values)}"))
                pos += 1

        # ---- Phase 3: 下から上へ挿入 ----
        # 挿入位置が大きい順にソートして挿入（上の行番号に影響しない）
        insertions.sort(key=lambda x: x[0], reverse=True)
        for insert_pos, line_text in insertions:
            lines.insert(insert_pos, line_text)

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="shift_jis", errors="replace") as f:
            f.write("\n".join(lines))
            if lines and lines[-1]:
                f.write("\n")

    def update_damper_def_values(self, name: str, new_values: List[str]) -> bool:
        """ダンパー定義の値を更新します。"""
        for ddef in self.damper_defs:
            if ddef.name == name:
                ddef.values = new_values
                return True
        return False

    def add_damper_def_copy(
        self,
        base_name: str,
        new_name: str,
        overrides: Optional[dict] = None,
    ) -> Optional["DamperDefinition"]:
        """既存のダンパー定義をコピーして新規定義を追加します。

        Parameters
        ----------
        base_name : str
            コピー元の定義名。
        new_name : str
            新しい定義名（重複不可）。
        overrides : dict, optional
            上書きするフィールド。キーは 1-indexed 文字列、値は文字列。
            例: {"8": "800000", "9": "200"}

        Returns
        -------
        DamperDefinition or None
            追加した定義。base_name が見つからない場合は None。
        """
        # 同名が既にある場合は上書き更新
        for existing in self.damper_defs:
            if existing.name == new_name:
                if overrides:
                    for idx_str, val in overrides.items():
                        idx = int(idx_str) - 1
                        while len(existing.values) <= idx:
                            existing.values.append("")
                        existing.values[idx] = str(val)
                return existing

        base = self.get_damper_def(base_name)
        if base is None:
            return None

        new_values = list(base.values)
        new_values[0] = new_name  # index 0 は定義名

        if overrides:
            for idx_str, val in overrides.items():
                idx = int(idx_str) - 1
                while len(new_values) <= idx:
                    new_values.append("")
                new_values[idx] = str(val)

        new_def = DamperDefinition(
            keyword=base.keyword,
            name=new_name,
            values=new_values,
            raw="",
            line_no=0,
        )
        self.damper_defs.append(new_def)
        return new_def

    def add_damper_def_new(
        self,
        keyword: str,
        new_name: str,
        num_fields: int = 22,
        overrides: Optional[dict] = None,
    ) -> "DamperDefinition":
        """空のダンパー定義を新規作成して追加します。

        Parameters
        ----------
        keyword : str
            SNAP キーワード（"DVOD", "DSD" 等）。
        new_name : str
            定義名。
        num_fields : int
            フィールド数（名前を除く）。
        overrides : dict, optional
            初期値を上書きするフィールド。キーは 1-indexed 文字列。
            "1" → values[1]（最初のSNAPフィールド）に対応。
            values[0] は定義名であり上書き対象外。

        Returns
        -------
        DamperDefinition
            追加した定義。
        """
        # 同名が既にある場合は上書き更新
        for existing in self.damper_defs:
            if existing.name == new_name:
                if overrides:
                    for idx_str, val in overrides.items():
                        idx = int(idx_str)  # "1" → values[1]
                        while len(existing.values) <= idx:
                            existing.values.append("")
                        existing.values[idx] = str(val)
                return existing

        new_values = [new_name] + ["0"] * num_fields
        if overrides:
            for idx_str, val in overrides.items():
                idx = int(idx_str)  # "1" → values[1]（values[0]=名前は保持）
                while len(new_values) <= idx:
                    new_values.append("")
                new_values[idx] = str(val)

        new_def = DamperDefinition(
            keyword=keyword,
            name=new_name,
            values=new_values,
            raw="",
            line_no=0,
        )
        self.damper_defs.append(new_def)
        return new_def

    def update_damper_element(
        self,
        index: int,
        *,
        node_i: Optional[int] = None,
        node_j: Optional[int] = None,
        quantity: Optional[int] = None,
        damper_def_name: Optional[str] = None,
    ) -> bool:
        """免制振装置の配置・基数・装置定義を更新します。

        Notes
        -----
        SNAP の RD レコードでは「倍数（基数）」は index 10（フィールド 11）です。
        index 3 は「種別」であり、基数ではありません。
        """
        if index < 0 or index >= len(self.damper_elements):
            return False
        elem = self.damper_elements[index]

        # ---- 節点 ----
        if node_i is not None:
            elem.node_i = node_i
            if len(elem.values) > 1:
                elem.values[1] = str(node_i)
        if node_j is not None:
            elem.node_j = node_j
            if len(elem.values) > 2:
                elem.values[2] = str(node_j)

        # ---- 基数（倍数）: フィールド 11 = index 10 ----
        if quantity is not None:
            elem.quantity = quantity
            # values リストが短い場合は "0" で埋めて伸ばす
            while len(elem.values) <= _RD_QUANTITY_IDX:
                elem.values.append("0")
            elem.values[_RD_QUANTITY_IDX] = str(quantity)

        # ---- 装置定義名: フィールド 5 = index 4 ----
        if damper_def_name is not None:
            elem.damper_def_name = damper_def_name
            if len(elem.values) > _RD_DEF_NAME_IDX:
                elem.values[_RD_DEF_NAME_IDX] = damper_def_name

        return True


# ---------------------------------------------------------------------------
# RD 種別ラベル
# ---------------------------------------------------------------------------

#: RD レコードのフィールド index 3 = 「種別」の値に対応する日本語ラベル
_RD_DAMPER_TYPE_LABELS: Dict[int, str] = {
    0: "鋼材/摩擦ダンパー",
    1: "粘性/オイルダンパー",
    2: "オイルダンパー",
    3: "粘性ダンパー",
    4: "粘弾性ダンパー",
}

#: RD レコードにおける「倍数（基数）」フィールドの index (0-based)
_RD_QUANTITY_IDX: int = 10

#: RD レコードにおける「装置名」フィールドの index (0-based)
_RD_DEF_NAME_IDX: int = 4

#: RD レコードにおける「種別」フィールドの index (0-based)
_RD_TYPE_IDX: int = 3


# ---------------------------------------------------------------------------
# ダンパー関連キーワード一覧
# ---------------------------------------------------------------------------

# 架構 - 免制振装置で使用されるキーワード
_DAMPER_DEF_KEYWORDS = {
    "DVOD",   # 粘性/オイルダンパー
    "DSD",    # 鋼材ダンパー
    "DIS",    # 免震支承材
    "DISD",   # 免震用履歴型ダンパー
    "DVD",    # 粘性ダンパー（減衰こま）
    "DVED",   # 粘弾性ダンパー
    "DOD",    # オイルダンパー
    "DVHY",   # 履歴型ダンパー
    "DVBI",   # バイリニア型
    "DVSL",   # すべり型
    "DVFR",   # 摩擦ダンパー
    "DVTF",   # 粘弾性ダンパー
    "DVMS",   # マスダンパー
}


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _split_values(value_str: str) -> List[str]:
    """カンマ区切りのフィールド値を分割します。"""
    return [v.strip() for v in value_str.split(",")]


def _safe_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


def _safe_float(s: str, default: float = 0.0) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def parse_s8i(file_path: str) -> S8iModel:
    """
    .s8i ファイルを読み込んでパースします。

    Parameters
    ----------
    file_path : str
        .s8i ファイルのパス。

    Returns
    -------
    S8iModel
        パース結果。
    """
    path = Path(file_path)

    # エンコーディング: まず shift_jis を試し、失敗したら utf-8
    for enc in ("shift_jis", "cp932", "utf-8"):
        try:
            text = path.read_text(encoding=enc, errors="replace")
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    lines = text.splitlines()
    model = S8iModel(file_path=str(path))
    model._lines = lines

    for line_no_0, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue

        # KEYWORD / values の形式を解析
        match = re.match(r"^(\w+)\s*/\s*(.*)", line)
        if not match:
            continue

        keyword = match.group(1).upper()
        value_str = match.group(2)
        vals = _split_values(value_str)
        line_no = line_no_0 + 1  # 1-indexed

        if keyword == "REM":
            continue

        elif keyword == "TTL":
            # タイトル: TTL / type,dim,...,title,...
            if len(vals) >= 6:
                model.title = vals[5]

        elif keyword == "VER":
            model.version = vals[0] if vals else ""

        elif keyword == "ND":
            _parse_node(model, vals, raw_line)

        elif keyword == "FL":
            _parse_floor(model, vals, raw_line)

        elif keyword in _DAMPER_DEF_KEYWORDS:
            _parse_damper_def(model, keyword, vals, raw_line, line_no)

        elif keyword == "SR":
            _parse_damper_brace(model, vals, raw_line, line_no)

        elif keyword == "RD":
            _parse_damper_element(model, vals, raw_line, line_no)

        elif keyword == "DYD":
            model.dyd_record = DydRecord(
                values=vals,
                raw=raw_line,
                line_no=line_no,
            )

        elif keyword == "DYC":
            _parse_dyc_case(model, vals, raw_line, line_no)

    return model


def _parse_node(model: S8iModel, vals: List[str], raw: str) -> None:
    """ND 行をパース。"""
    if len(vals) < 4:
        return
    node = Node(
        id=_safe_int(vals[0]),
        x=_safe_float(vals[1]),
        y=_safe_float(vals[2]),
        z=_safe_float(vals[3]),
        mass=_safe_float(vals[5]) if len(vals) > 5 else 0.0,
        support=vals[6] if len(vals) > 6 else "",
        x_grid=vals[7] if len(vals) > 7 else "",
        y_grid=vals[8] if len(vals) > 8 else "",
        z_grid=vals[9] if len(vals) > 9 else "",
        raw=raw,
    )
    model.nodes[node.id] = node


def _parse_floor(model: S8iModel, vals: List[str], raw: str) -> None:
    """FL 行をパース。"""
    if not vals:
        return
    fl = Floor(
        name=vals[0],
        rigid_group=vals[7] if len(vals) > 7 else "",
        raw=raw,
    )
    model.floors.append(fl)


def _parse_damper_def(
    model: S8iModel,
    keyword: str,
    vals: List[str],
    raw: str,
    line_no: int,
) -> None:
    """ダンパー定義行 (DVOD, DSD 等) をパース。"""
    if not vals:
        return
    ddef = DamperDefinition(
        keyword=keyword,
        name=vals[0],
        values=vals,
        raw=raw,
        line_no=line_no,
    )
    model.damper_defs.append(ddef)


def _parse_damper_brace(
    model: S8iModel,
    vals: List[str],
    raw: str,
    line_no: int,
) -> None:
    """SR (制振ブレース) 行をパース。"""
    if len(vals) < 2:
        return
    brace = DamperBrace(
        member_id=_safe_int(vals[0]),
        node=_safe_int(vals[1]),
        values=vals,
        raw=raw,
        line_no=line_no,
    )
    model.damper_braces.append(brace)


def _parse_damper_element(
    model: S8iModel,
    vals: List[str],
    raw: str,
    line_no: int,
) -> None:
    """RD (免制振装置) 行をパース。

    SNAP RD レコードのフィールド対応 (index は 0-based):
      0: 名称
      1: 節点I
      2: 節点J
      3: 種別 (0=鋼材/摩擦, 1=粘性/オイル, 2=オイル, 3=粘性, 4=粘弾性)
      4: 装置名 (DVOD/DSD 等の定義名)
      5-9: 剛性・重量等の付加情報
      10: 倍数/基数 (省略値=1)
      11+: 方向・座標系・出力設定等
    """
    if len(vals) < 3:
        return

    # 種別 (index 3)
    damper_type = _safe_int(vals[3], 0) if len(vals) > _RD_TYPE_IDX else 0

    # 装置名 (index 4)
    damper_def_name = vals[_RD_DEF_NAME_IDX].strip() if len(vals) > _RD_DEF_NAME_IDX else ""

    # 倍数/基数 (index 10, 省略値=1)
    # ※ 旧コードでは誤って index 3 (種別) を基数として読んでいた
    quantity = _safe_int(vals[_RD_QUANTITY_IDX], 1) if len(vals) > _RD_QUANTITY_IDX else 1

    elem = DamperElement(
        name=vals[0],
        node_i=_safe_int(vals[1]),
        node_j=_safe_int(vals[2]),
        quantity=quantity,
        damper_def_name=damper_def_name,
        damper_type=damper_type,
        values=vals,
        raw=raw,
        line_no=line_no,
    )
    model.damper_elements.append(elem)


def _parse_dyc_case(
    model: S8iModel,
    vals: List[str],
    raw: str,
    line_no: int,
) -> None:
    """DYC (応答解析ケース) 行をパース。

    フィールド対応 (0-based index):
      0: ケース名
      1: run_flag (0=解析しない, 1=解析する)
      2: 地震動波数
      3以降: その他解析パラメータ
    """
    if not vals:
        return
    case_no = len(model.dyc_cases) + 1  # 連番 (1始まり) = D{N} フォルダ番号
    dyc = DycCase(
        case_no=case_no,
        name=vals[0].strip(),
        run_flag=_safe_int(vals[1], 0) if len(vals) > 1 else 0,
        num_waves=_safe_int(vals[2], 1) if len(vals) > 2 else 1,
        values=vals,
        raw=raw,
        line_no=line_no,
    )
    model.dyc_cases.append(dyc)
