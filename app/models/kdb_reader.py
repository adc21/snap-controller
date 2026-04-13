"""
app/models/kdb_reader.py
k-DB（構造部材データベース）ファイルの読み込みモジュール。

k-DB は kozosystem が提供する建築構造部材データベースです。
制振ダンパー・免震装置・鋼材断面などのメーカー製品データが
Shift-JIS エンコードの CSV 形式 (.kdt ファイル) で格納されています。

対応カテゴリ:
  DAMPER  - 制振ダンパー（オイル・粘性・鋼材ブレース等）
  ISOLATOR - 免震装置（積層ゴム・鉛プラグ・すべり系等）

k-DB ファイルの命名規則:
  D{section}-{product_num}.KDT  (例: D072-005001.KDT)
  I{section}-{product_num}.KDT  (例: I101-004001.KDT)

section 番号は SNAP の DVOD 種別フィールドと対応:
  52: 免震用オイルダンパー  53: 免震用粘性ダンパー
  72: 制振用オイルダンパー  73: 制振用粘性ダンパー
  1-51: 鋼材系ダンパー（DSD）

SNAP パラメータ対応:
  DVOD フィールド 2 → k-DB 会社番号
  DVOD フィールド 3 → k-DB 製品番号
  DVOD フィールド 4 → k-DB 型番

単位系:
  k-DB と SNAP .s8i は同一の単位系を使用しています:
    - 剛性: kN/mm
    - 減衰係数: kN·sec/mm = kN/[mm/s]
    - 力: kN
    - 長さ: mm
  ※SNAP 内部計算も kN/[mm/s] を使用（SNAP_T.pdf 5.5 章注記参照）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# section 番号 → SNAP キーワード・ダンパー種別ラベルのマッピング
# ---------------------------------------------------------------------------

#: DAMPER section 番号 → (snap_keyword, 種別ラベル)
SECTION_TO_SNAP: Dict[int, Tuple[str, str]] = {
    1:   ("DSD",  "鋼材ブレース（座屈補剛）"),
    2:   ("DSD",  "制振間柱"),
    3:   ("DSD",  "鋼材ブレース（その他）"),
    4:   ("DSD",  "平鋼ブレース（FB型）"),
    5:   ("DSD",  "鋼材ダンパー"),
    51:  ("DISD", "免震用履歴型ダンパー"),
    52:  ("DVOD", "免震用オイルダンパー"),
    53:  ("DVOD", "免震用粘性ダンパー"),
    54:  ("DVD",  "免震用粘性ダンパー（減衰こま）"),
    72:  ("DVOD", "制振用オイルダンパー"),
    73:  ("DVOD", "制振用粘性ダンパー"),
    74:  ("DVD",  "制振用粘性ダンパー（減衰こま）"),
    75:  ("DVED", "制振用粘弾性ダンパー"),
}

#: ISOLATOR section 番号 → (snap_keyword, 種別ラベル)
ISOLATOR_SECTION_TO_LABEL: Dict[int, str] = {
    101: "天然ゴム系積層ゴム（NRB）",
    102: "高減衰ゴム系積層ゴム（HDR）",
    103: "鉛プラグ入り積層ゴム（LRB）",
    104: "弾性滑り支承（CLB）",
    121: "積層ゴム（汎用）",
    122: "平面すべり系",
    123: "凹面摺動系（FPS）",
    124: "弾性滑り支承（CLB 矩形）",
}


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class KdbRecord:
    """
    k-DB の1製品レコード（型番単位）。

    Attributes
    ----------
    model_number : str
        型番（例: "BDH250120-B1-30"）。
    model_name : str
        製品名称（長い形式）。
    raw_values : List[str]
        元の CSV カラム値（0-indexed）。
    snap_fields : Dict[int, Any]
        SNAP パラメータフィールド番号（1-indexed）→ 値 の対応。
    extra : Dict[str, Any]
        その他の補足情報（長さ・ストローク等）。
    """
    model_number: str = ""
    model_name: str = ""
    raw_values: List[str] = field(default_factory=list)
    snap_fields: Dict[int, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class KdbProduct:
    """
    k-DB の1ファイルに相当する製品シリーズ。

    Attributes
    ----------
    filepath : str
        元ファイルパス。
    category : str
        "DAMPER" または "ISOLATOR"。
    section_num : int
        k-DB セクション番号（D072 なら 72）。
    product_code : str
        製品コード文字列（例: "005001"）。
    series_name : str
        製品シリーズ名称。
    manufacturer_id : int
        k-DB 会社番号。
    certification_num : str
        大臣認定番号等。
    description : str
        製品概要。
    snap_keyword : str
        対応する SNAP キーワード（"DVOD", "DSD", "ISO" 等）。
    category_label : str
        日本語カテゴリラベル。
    records : List[KdbRecord]
        製品レコード一覧。
    """
    filepath: str = ""
    category: str = ""
    section_num: int = 0
    product_code: str = ""
    series_name: str = ""
    manufacturer_id: int = 0
    certification_num: str = ""
    description: str = ""
    snap_keyword: str = ""
    category_label: str = ""
    records: List[KdbRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# パーサー
# ---------------------------------------------------------------------------

def _safe_float(s: str) -> Optional[float]:
    """文字列を float に変換。失敗時は None を返す。"""
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def _parse_header(lines: List[str]) -> Tuple[int, str, int, str, str, str]:
    """
    KDT ファイルのヘッダー行を解析します。

    Returns
    -------
    (version_str, count, section_num, product_code, series_name,
     manufacturer_id, certification_num, description)
    """
    for line in lines:
        line = line.strip()
        if line.startswith("'"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        # バージョン番号が浮動小数点の可能性あり
        try:
            float(parts[0])
        except ValueError:
            continue
        try:
            count = int(parts[1])
        except ValueError:
            count = 0
        try:
            section_num = int(parts[2])
        except ValueError:
            section_num = 0
        product_code = parts[3].strip()
        series_name = parts[4].strip() if len(parts) > 4 else ""
        certification_num = parts[6].strip() if len(parts) > 6 else ""
        description = parts[8].strip() if len(parts) > 8 else ""
        return count, section_num, product_code, series_name, certification_num, description
    return 0, 0, "", "", "", ""


def _find_column_header(lines: List[str]) -> Optional[str]:
    """
    列ヘッダー行（コロン番号付き: '1:xxx,2:xxx,...）を探して返します。
    """
    for line in lines:
        s = line.strip()
        if s.startswith("'") and re.search(r"\d+:", s):
            return s[1:]  # 先頭の ' を除去
    return None


def _parse_dvod_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DVOD 系（オイル/粘性ダンパー）レコードを解析します。

    k-DB DVOD カラム構成（D072/D073/D052/D054 系）:
      0: 検索フラグ
      1: 型番
      2: 装置長さ (mm)
      3: 減衰モデル (0:ダッシュポット, 1:Voigt, 2:Maxwell)
      4: 種別 (0:線形, 1:バイリニア)
      5: C1 減衰係数 (kN/[mm/s])
      6: Fy リリーフ力 (kN)
      7: β 速度指数
      8: K1 バネ剛性 (kN/mm)
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number  # DVOD ファイルに独立した長名称列はない

    # 装置長さ (mm → m)
    length_mm = _safe_float(parts[2]) if len(parts) > 2 else None
    if length_mm is not None:
        rec.extra["device_length_mm"] = length_mm

    damping_model = _safe_float(parts[3]) if len(parts) > 3 else None
    char_type = _safe_float(parts[4]) if len(parts) > 4 else None
    c1 = _safe_float(parts[5]) if len(parts) > 5 else None
    fy = _safe_float(parts[6]) if len(parts) > 6 else None
    beta = _safe_float(parts[7]) if len(parts) > 7 else None
    k1 = _safe_float(parts[8]) if len(parts) > 8 else None

    # SNAP DVOD フィールド設定（1-indexed）
    sf = rec.snap_fields
    sf[1] = section_num            # 種別 (52/53/72/73)
    sf[2] = manufacturer_id        # k-DB 会社番号
    sf[3] = _safe_int(product_code)# k-DB 製品番号
    sf[4] = rec.model_number       # k-DB 型番

    if damping_model is not None:
        sf[5] = int(damping_model)  # 減衰モデル

    if char_type is not None:
        sf[7] = int(char_type)      # 装置特性種別 (0:線形, 1:バイリニア)

    # C1: k-DB は kN/[mm/s] = kN·sec/mm で格納 → SNAP .s8i も同単位
    if c1 is not None:
        sf[8] = c1                  # C0 (kN·sec/mm)
        rec.extra["C1_kN_per_mm_per_s"] = c1

    if fy is not None and fy != 0:
        sf[9] = fy                  # Fc リリーフ力 (kN)

    if beta is not None:
        sf[12] = beta               # α 速度指数

    # K1: k-DB は kN/mm で格納 → SNAP .s8i も同単位
    if k1 is not None and k1 != 0:
        sf[14] = k1                 # 剛性 (kN/mm)
        rec.extra["K1_kN_per_mm"] = k1

    # 装置長さ: k-DB は mm で格納 → SNAP .s8i も mm
    if length_mm is not None:
        sf[16] = length_mm           # 装置高さ (mm)

    return rec


def _parse_disd_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DISD 系（免震用履歴型ダンパー D051）レコードを解析します。

    k-DB D051 カラム構成:
      0: 検索フラグ
      1: 型番
      2: 復元力特性種別 (0:BL2, 1:RO3, 2:TL3)
      3: 数量
      4: 外径 (mm)
      5: (予備)
      6: K0 初期剛性 (kN/mm)
      7: Qc (kN)
      8: Qy 降伏荷重 (kN)
      9: α 2次剛性比
      10: β
      11: p1
      12: p2
      13: 重量 (kN)
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number

    char_type = _safe_float(parts[2]) if len(parts) > 2 else None
    outer_dia  = _safe_float(parts[4]) if len(parts) > 4 else None
    k0         = _safe_float(parts[6]) if len(parts) > 6 else None
    qc         = _safe_float(parts[7]) if len(parts) > 7 else None
    qy         = _safe_float(parts[8]) if len(parts) > 8 else None
    alpha      = _safe_float(parts[9]) if len(parts) > 9 else None
    beta       = _safe_float(parts[10]) if len(parts) > 10 else None
    p1         = _safe_float(parts[11]) if len(parts) > 11 else None
    p2         = _safe_float(parts[12]) if len(parts) > 12 else None

    sf = rec.snap_fields
    sf[1] = section_num         # 種別 (51)
    sf[2] = manufacturer_id     # k-DB 会社番号
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    if char_type is not None:
        sf[5] = int(char_type)  # 復元力特性種別

    # K0: k-DB は kN/mm で格納 → SNAP .s8i も同単位
    if k0 is not None and k0 != 0:
        sf[6] = k0                  # K0 初期剛性 (kN/mm)
        rec.extra["K0_kN_per_mm"] = k0

    if qc is not None and qc != 0:
        sf[7] = qc              # Qc (kN)

    if qy is not None and qy != 0:
        sf[8] = qy              # Qy (kN)
        rec.extra["Qy_kN"] = qy

    if alpha is not None:
        sf[9] = alpha           # α 2次剛性比

    if beta is not None:
        sf[10] = beta           # β

    if p1 is not None:
        sf[11] = p1

    if p2 is not None:
        sf[12] = p2

    if outer_dia:
        rec.extra["outer_diameter_mm"] = outer_dia

    return rec


def _parse_dvd_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DVD 系（粘性ダンパー・減衰こま D054/D074）レコードを解析します。

    k-DB D054/D074 カラム構成（RDT型 回転慣性ダンパー）:
      0: 検索フラグ
      1: 型番
      2: 装置長さ (mm)
      3: rv (mm) 回転半径
      4: Ld (mm)
      5: η25 (cst) 動粘度
      6: d (mm)
      7: As (mm²) せん断断面積
      8: μsi 減衰係数
      ...

    SNAP DVD パラメータは k-DB 物性値から間接的に計算が必要なため
    (C0 = a(f,A)·μ(f,t)·As/d — SNAP_T.pdf 式5.5.1-11)、
    k-DB の物性値を extra に格納し、識別情報を snap_fields に設定します。
    ※ユーザーが T, p1=a1, f を入力後に計算する必要があります。
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number

    device_length = _safe_float(parts[2]) if len(parts) > 2 else None
    rv = _safe_float(parts[3]) if len(parts) > 3 else None       # 内筒外半径 (mm)
    ld = _safe_float(parts[4]) if len(parts) > 4 else None       # リード長 (mm)
    eta25 = _safe_float(parts[5]) if len(parts) > 5 else None    # 25℃時動粘度 (cSt)
    d_gap = _safe_float(parts[6]) if len(parts) > 6 else None    # せん断隙間 (mm)
    as_area = _safe_float(parts[7]) if len(parts) > 7 else None  # せん断有効断面積 (mm²)
    mu_si = _safe_float(parts[8]) if len(parts) > 8 else None    # シール材摩擦 (kN/m)

    sf = rec.snap_fields
    sf[1] = section_num         # 種別 (54:免震用, 74:制振用)
    sf[2] = manufacturer_id
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    # k-DB 物性値を extra に保存（DVD モデル計算に必要）
    if device_length is not None:
        rec.extra["device_length_mm"] = device_length
    if rv is not None:
        rec.extra["rv_mm"] = rv
    if ld is not None:
        rec.extra["Ld_mm"] = ld
    if eta25 is not None:
        rec.extra["eta25_cSt"] = eta25
    if d_gap is not None:
        rec.extra["d_mm"] = d_gap
    if as_area is not None:
        rec.extra["As_mm2"] = as_area
    if mu_si is not None:
        rec.extra["mu_si_kN_per_m"] = mu_si

    return rec


def _parse_dved_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DVED 系（制振用粘弾性ダンパー D075）レコードを解析します。

    k-DB D075 カラム構成:
      0: 検索フラグ
      1: 型番
      2: 種別 (0:VEY, 1:VET, 2:VS1, ..., 9:VEJ, 10:VS5)
      3: 粘弾性体面積 (mm²)
      4: 粘弾性体厚さ (mm)
      5: G (ゴム弾性係数)
      ...
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number

    dev_type = _safe_float(parts[2]) if len(parts) > 2 else None
    area_mm2 = _safe_float(parts[3]) if len(parts) > 3 else None
    thick_mm = _safe_float(parts[4]) if len(parts) > 4 else None

    sf = rec.snap_fields
    sf[1] = section_num         # 種別 (75)
    sf[2] = manufacturer_id
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    if dev_type is not None:
        sf[5] = int(dev_type)   # 装置特性 種別

    if area_mm2 is not None and area_mm2 != 0:
        sf[6] = area_mm2        # 粘弾性体面積 (mm²)
        rec.extra["area_mm2"] = area_mm2

    if thick_mm is not None and thick_mm != 0:
        sf[7] = thick_mm        # 粘弾性体厚さ (mm)
        rec.extra["thickness_mm"] = thick_mm

    return rec


def _parse_dsd_flat_bar_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DSD 系（平鋼ブレース FB型 D004）レコードを解析します。

    k-DB カラム構成:
      0: 検索フラグ
      1: 型番
      2: Kd 剛性 (kN/mm)
      3: 種別
      4: Fy 降伏荷重 (kN)
      5: β
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number

    kd = _safe_float(parts[2]) if len(parts) > 2 else None
    char_type = _safe_float(parts[3]) if len(parts) > 3 else None
    fy = _safe_float(parts[4]) if len(parts) > 4 else None

    sf = rec.snap_fields
    sf[1] = 1                        # DSD 種別: ブレース
    sf[2] = manufacturer_id
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    # Kd: k-DB は kN/mm で格納 → SNAP .s8i も同単位
    if kd is not None:
        sf[7] = kd                   # K0 初期剛性 (kN/mm)
        rec.extra["Kd_kN_per_mm"] = kd

    if fy is not None:
        sf[9] = fy                   # Fy 降伏荷重 (kN)

    return rec


def _parse_dsd_brace_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    DSD 系（座屈補剛ブレース D001/D003）レコードを解析します。

    k-DB カラム構成（D003 詳細版）:
      0: 検索フラグ
      1: 型番（短）
      2: 型番（詳細）
      3-4: ???
      5: E ヤング率 (kN/mm2)
      6: Fy 降伏荷重 (kN)
      7: Ldmax 最大変位 (mm)
      ...
    """
    rec = KdbRecord(raw_values=parts)
    # D003 形式かどうか判定（カラム2が詳細型番を含む可能性）
    if len(parts) > 2 and "(" in parts[2]:
        rec.model_number = parts[1].strip()
        rec.model_name = parts[2].strip()
        e_idx, fy_idx = 5, 6
    else:
        # D001 形式: 型番,E,Ad,Fy,...
        rec.model_number = parts[1].strip()
        rec.model_name = parts[1].strip()
        e_idx, fy_idx = 2, 4  # E=col2(index), Ad=col3, Fy=col4

    fy = _safe_float(parts[fy_idx]) if len(parts) > fy_idx else None

    sf = rec.snap_fields
    sf[1] = 1                        # DSD 種別: ブレース
    sf[2] = manufacturer_id
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    if fy is not None:
        sf[9] = fy                   # Fy 降伏荷重 (kN)

    # E と Ad があれば参考値として保存
    e = _safe_float(parts[e_idx]) if len(parts) > e_idx else None
    if e is not None:
        rec.extra["E_kN_per_mm2"] = e
    if fy is not None:
        rec.extra["Fy_kN"] = fy

    return rec


def _parse_isolator_record(
    parts: List[str],
    section_num: int,
    manufacturer_id: int,
    product_code: str,
) -> KdbRecord:
    """
    ISOLATOR 系（積層ゴム I101/I102/I103 等）レコードを解析します。

    k-DB カラム構成（I101 天然ゴム系）:
      0: 検索フラグ
      1: 型番
      2: 積層ゴムの形状 (0:円形, 1:正方形)
      3: 外径 (mm)
      4: 断面積 (mm2)
      5: 積層ゴム総厚 (mm)
      6-9: Kh0, Kh1, Kh1, Kh2... 水平剛性 (kN/mm)
      12: Kv 鉛直剛性 (kN/mm)
      ...
    """
    rec = KdbRecord(raw_values=parts)
    rec.model_number = parts[1].strip() if len(parts) > 1 else ""
    rec.model_name = rec.model_number

    sf = rec.snap_fields
    sf[1] = section_num
    sf[2] = manufacturer_id
    sf[3] = _safe_int(product_code)
    sf[4] = rec.model_number

    # 外径・断面積・積層厚など補足情報
    outer_dia = _safe_float(parts[3]) if len(parts) > 3 else None
    area = _safe_float(parts[4]) if len(parts) > 4 else None
    total_rubber_h = _safe_float(parts[5]) if len(parts) > 5 else None
    kh0 = _safe_float(parts[6]) if len(parts) > 6 else None
    fy = _safe_float(parts[7]) if len(parts) > 7 else None  # Qd or K0

    if outer_dia:
        rec.extra["outer_diameter_mm"] = outer_dia
    if area:
        rec.extra["area_mm2"] = area
    if total_rubber_h:
        rec.extra["rubber_height_mm"] = total_rubber_h
    if kh0:
        rec.extra["Kh0_kN_per_mm"] = kh0
        sf[8] = kh0             # 剛性 K0 (kN/mm) — SNAP .s8i も同単位
    if fy:
        rec.extra["Fy_kN"] = fy

    return rec


def _safe_int(s: str) -> int:
    """文字列を int に変換。失敗時は 0 を返す。"""
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


# ---------------------------------------------------------------------------
# KdbReader メインクラス
# ---------------------------------------------------------------------------

class KdbReader:
    """
    k-DB インストールディレクトリを走査して製品データを読み込むクラス。

    Parameters
    ----------
    kdb_dir : str or Path
        k-DB のインストールディレクトリ（例: "C:/Program Files (x86)/k-DB"）。
    user_dir : str or Path, optional
        k-DB ユーザーデータディレクトリ（例: "D:/Kakemoto/kozosystem/k-DB/user/SHEET"）。
        指定した場合、DAMPER/ISOLATOR サブフォルダを探します。
    """

    def __init__(
        self,
        kdb_dir: str | Path,
        user_dir: Optional[str | Path] = None,
    ) -> None:
        self._kdb_dir = Path(kdb_dir)
        self._user_dir = Path(user_dir) if user_dir else None
        self._products: List[KdbProduct] = []
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> "KdbReader":
        """k-DB ファイルを読み込みます。"""
        self._products = []
        base_kdb = self._kdb_dir / "KDB"
        if base_kdb.exists():
            self._scan_dir(base_kdb / "DAMPER", "DAMPER")
            self._scan_dir(base_kdb / "ISOLATOR", "ISOLATOR")
        # ユーザーディレクトリも走査
        if self._user_dir and self._user_dir.exists():
            self._scan_dir(self._user_dir / "DAMPER", "DAMPER")
            self._scan_dir(self._user_dir / "ISOLATOR", "ISOLATOR")
        self._loaded = True
        return self

    @property
    def products(self) -> List[KdbProduct]:
        """読み込んだ全製品リストを返します。"""
        if not self._loaded:
            self.load()
        return self._products

    def get_dampers(self) -> List[KdbProduct]:
        """制振ダンパー製品のみ返します。"""
        return [p for p in self.products if p.category == "DAMPER"]

    def get_isolators(self) -> List[KdbProduct]:
        """免震装置製品のみ返します。"""
        return [p for p in self.products if p.category == "ISOLATOR"]

    def get_by_snap_keyword(self, keyword: str) -> List[KdbProduct]:
        """指定した SNAP キーワードに対応する製品を返します。"""
        return [p for p in self.products if p.snap_keyword == keyword]

    def get_by_category_label(self, label: str) -> List[KdbProduct]:
        """カテゴリラベルで絞り込みます。"""
        return [p for p in self.products if label in p.category_label]

    def search(self, query: str) -> List[Tuple[KdbProduct, KdbRecord]]:
        """
        型番・シリーズ名でレコードを全文検索します。

        Parameters
        ----------
        query : str
            検索キーワード（大文字小文字不問）。

        Returns
        -------
        List of (product, record) tuples.
        """
        q = query.lower()
        results = []
        for prod in self.products:
            if q in prod.series_name.lower():
                for rec in prod.records:
                    results.append((prod, rec))
            else:
                for rec in prod.records:
                    if q in rec.model_number.lower() or q in rec.model_name.lower():
                        results.append((prod, rec))
        return results

    def all_records_flat(self) -> List[Tuple[KdbProduct, KdbRecord]]:
        """全レコードを (product, record) ペアとして返します。"""
        result = []
        for prod in self.products:
            for rec in prod.records:
                result.append((prod, rec))
        return result

    # ------------------------------------------------------------------
    # Internal: file scanning & parsing
    # ------------------------------------------------------------------

    def _scan_dir(self, directory: Path, category: str) -> None:
        """指定ディレクトリ内の全 .kdt/.KDT ファイルを読み込みます。"""
        if not directory.exists():
            return
        for fname in sorted(os.listdir(directory)):
            if not fname.lower().endswith(".kdt"):
                continue
            fpath = directory / fname
            try:
                prod = self._parse_kdt_file(fpath, category)
                if prod and prod.records:
                    self._products.append(prod)
            except Exception:
                logger.debug("KDTファイル解析失敗: %s", fname)

    def _parse_kdt_file(self, filepath: Path, category: str) -> Optional[KdbProduct]:
        """1つの .kdt ファイルを解析して KdbProduct を返します。"""
        with open(filepath, encoding="shift_jis", errors="replace") as f:
            lines = f.readlines()

        # ヘッダー解析
        count, section_num, product_code, series_name, certification_num, description = \
            _parse_header(lines)

        if section_num == 0 and not product_code:
            return None

        # section_num が 0 の場合はファイル名から推定
        if section_num == 0:
            m = re.match(r"[DI](\d+)-", filepath.name, re.IGNORECASE)
            if m:
                section_num = int(m.group(1))

        # manufacturer_id をファイル名のプロダクトコードから推定
        # ファイル名: D072-005001.KDT → mfr=005, serial=001
        manufacturer_id = 0
        m = re.match(r"[DI]\d+-(\d+)\.kdt", filepath.name, re.IGNORECASE)
        if m:
            code_str = m.group(1)
            if len(code_str) >= 6:
                try:
                    manufacturer_id = int(code_str[:3])
                except ValueError:
                    logger.debug("メーカーID解析失敗: %s", code_str[:3])

        # SNAP キーワード・ラベル決定
        if category == "DAMPER":
            snap_kw, cat_label = SECTION_TO_SNAP.get(section_num, ("", "不明"))
        else:
            snap_kw = "DIS"   # SNAP の免震支承材キーワード
            cat_label = ISOLATOR_SECTION_TO_LABEL.get(section_num, "免震装置")

        prod = KdbProduct(
            filepath=str(filepath),
            category=category,
            section_num=section_num,
            product_code=product_code,
            series_name=series_name,
            manufacturer_id=manufacturer_id,
            certification_num=certification_num,
            description=description,
            snap_keyword=snap_kw,
            category_label=cat_label,
        )

        # データ行の解析
        header_found = False  # ヘッダー行（バージョン行）は1度だけスキップ
        for line in lines:
            s = line.strip()
            if not s or s.startswith("'"):
                continue
            parts = [p.strip() for p in s.split(",")]
            if not parts or parts[0] != "1":
                continue
            if len(parts) < 2:
                continue

            # ヘッダー行の除外: parts[1] が純粋な整数 → 件数カラム（ヘッダー行）
            # 例: "1,30,72,5001,シリーズ名,..." → parts[1]="30" は件数であり型番ではない
            if not header_found:
                try:
                    int(parts[1])
                    # 整数ならヘッダー行としてスキップ
                    header_found = True
                    continue
                except ValueError:
                    logger.debug("ヘッダー判定: parts[1]=%s は整数でない→データ行", parts[1])

            try:
                rec = self._parse_record(parts, category, section_num,
                                         manufacturer_id, product_code)
                if rec and rec.model_number:
                    prod.records.append(rec)
            except Exception:
                logger.debug("レコード解析失敗: section=%s", section_num)

        return prod

    def _parse_record(
        self,
        parts: List[str],
        category: str,
        section_num: int,
        manufacturer_id: int,
        product_code: str,
    ) -> Optional[KdbRecord]:
        """カテゴリ・セクションに応じたレコードパーサーを選択して実行します。"""
        if category == "ISOLATOR":
            return _parse_isolator_record(parts, section_num, manufacturer_id, product_code)

        # DAMPER: セクション番号で適切なパーサーを選択
        if section_num == 51:
            return _parse_disd_record(parts, section_num, manufacturer_id, product_code)
        elif section_num in (52, 53, 72, 73):
            return _parse_dvod_record(parts, section_num, manufacturer_id, product_code)
        elif section_num in (54, 74):
            return _parse_dvd_record(parts, section_num, manufacturer_id, product_code)
        elif section_num == 75:
            return _parse_dved_record(parts, section_num, manufacturer_id, product_code)
        elif section_num == 4:
            return _parse_dsd_flat_bar_record(parts, section_num, manufacturer_id, product_code)
        elif section_num in (1, 2, 3, 5):
            return _parse_dsd_brace_record(parts, section_num, manufacturer_id, product_code)
        else:
            # 不明なセクションは DVOD として試みる
            return _parse_dvod_record(parts, section_num, manufacturer_id, product_code)


# ---------------------------------------------------------------------------
# Singleton / convenience
# ---------------------------------------------------------------------------

_default_reader: Optional[KdbReader] = None


def get_kdb_reader(
    kdb_dir: str = r"C:\Program Files (x86)\k-DB",
    user_dir: Optional[str] = None,
) -> KdbReader:
    """
    デフォルト KdbReader インスタンスを返します（遅延読み込み）。

    Parameters
    ----------
    kdb_dir : str
        k-DB インストールディレクトリ。
    user_dir : str, optional
        k-DB ユーザーデータディレクトリ。
    """
    global _default_reader
    if _default_reader is None:
        _default_reader = KdbReader(kdb_dir, user_dir)
        _default_reader.load()
    return _default_reader


def reset_kdb_reader() -> None:
    """キャッシュをリセットして次回の get_kdb_reader() で再読み込みさせます。"""
    global _default_reader
    _default_reader = None
