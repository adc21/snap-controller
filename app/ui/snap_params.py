"""
app/ui/snap_params.py
SNAP ver8 パラメータ定義。

ケース編集ダイアログで選択肢として提示する
SNAP .s8i ファイルのキーワード・パラメータを定義します。

各パラメータは以下の形式で定義されます:
    (キーワード, 日本語ラベル, 説明/ツールチップ)

カテゴリごとにグループ化されており、コンボボックスの
区切り（セパレータ）として表示されます。

参考: SNAP ver.8 ヘルプ / 構造計画研究所
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Tuple


class ParamDef(NamedTuple):
    """パラメータ定義。"""
    keyword: str       # .s8i キーワード
    label: str         # 日本語表示名
    tooltip: str       # ツールチップ（説明）
    default: str = ""  # デフォルト値（任意）


# =====================================================================
# 解析パラメータ（解析制御・動的解析）
# =====================================================================

# (カテゴリ名, パラメータリスト)
ANALYSIS_PARAM_CATEGORIES: List[Tuple[str, List[ParamDef]]] = [
    ("解析制御", [
        ParamDef("TTL",    "タイトル/単位系",     "モデル名・単位系の設定 (TTL)"),
        ParamDef("VER",    "バージョン",           "SNAP バージョン番号 (VER)"),
    ]),
    ("動的解析制御", [
        ParamDef("RD",     "振動解析データ",       "振動解析の定義 — 解析タイプ・方向・減衰など (RD)"),
        ParamDef("DT",     "時間刻み",             "動的解析の時間増分 Δt [sec]"),
        ParamDef("NSTEP",  "ステップ数",           "動的解析の総ステップ数"),
        ParamDef("BETA",   "Newmark β値",         "Newmark β法のβ値 (通常 0.25)"),
        ParamDef("DAMP",   "減衰定数",             "構造減衰定数 h (例: 0.02 = 2%)", "0.02"),
    ]),
    ("地震波・外力", [
        ParamDef("EQ",     "地震波データ",         "入力地震波の定義 (EQ)"),
        ParamDef("EQSCALE","地震波倍率",           "入力地震波の倍率", "1.0"),
        ParamDef("EQFILE", "地震波ファイル",       "外部地震波ファイルパス"),
    ]),
    ("荷重条件", [
        ParamDef("LCV",    "荷重組合せ",           "荷重組合せの定義 (LCV)"),
        ParamDef("LLV",    "積載荷重",             "積載荷重値の定義 (LLV)"),
        ParamDef("MCL",    "荷重/質量の設定",      "荷重・質量の計算条件 (MCL)"),
    ]),
    ("材料・断面", [
        ParamDef("MCC",    "コンクリート条件",     "コンクリート共通条件 (MCC)"),
        ParamDef("MCS",    "鉄骨構造条件",         "鉄骨構造の計算条件 (MCS)"),
    ]),
    ("節点・支持", [
        ParamDef("ND",     "節点",                 "節点座標・質量の定義 (ND)"),
        ParamDef("SP",     "支持条件",             "支持条件（固定/ピン等）(SP)"),
        ParamDef("RG",     "剛床",                 "剛床の定義 (RG)"),
        ParamDef("FL",     "層",                   "層の定義 (FL)"),
    ]),
    ("計算条件", [
        ParamDef("CGR",    "RC/SRC はり計算条件",  "RC/SRC はり部材の計算条件 (CGR)"),
        ParamDef("CCL",    "RC/SRC 柱計算条件",    "RC/SRC 柱部材の計算条件 (CCL)"),
        ParamDef("CWL",    "RC/SRC 壁計算条件",    "RC/SRC 壁部材の計算条件 (CWL)"),
        ParamDef("CGRS",   "S はり計算条件",       "鉄骨はり部材の計算条件 (CGRS)"),
        ParamDef("CCLS",   "S/CFT 柱計算条件",     "鉄骨/CFT柱部材の計算条件 (CCLS)"),
        ParamDef("CTSS",   "S ブレース計算条件",   "鉄骨ブレースの計算条件 (CTSS)"),
    ]),
    ("出力制御", [
        ParamDef("TV",     "設計目標値",           "各層の設計目標値 — 層間変形角制限等 (TV)"),
        ParamDef("VSM",    "モデル図設定",         "モデル表示図の設定 (VSM)"),
        ParamDef("VSS",    "変形/応力図設定",      "変形/応力表示図の設定 (VSS)"),
    ]),
]


# =====================================================================
# 制振・免震装置パラメータ
# =====================================================================

# ダンパー種類ごとのパラメータプリセット
DAMPER_TYPE_PARAMS: Dict[str, List[ParamDef]] = {
    "なし": [],

    "油圧ダンパー": [
        ParamDef("DVOD",   "ダンパー定義",         "粘性オイルダンパーの定義行 (DVOD)"),
        ParamDef("Cd",     "減衰係数 Cd",          "減衰係数 [kN·s/mm]", "10.0"),
        ParamDef("α",      "速度指数 α",           "速度指数 α (1.0=線形, <1.0=非線形)", "1.0"),
        ParamDef("Vmax",   "最大速度",             "最大許容速度 [mm/s]", "500"),
        ParamDef("Fmax",   "最大減衰力",           "最大許容減衰力 [kN]"),
    ],

    "オイルダンパー（速度依存型）": [
        ParamDef("DVOD",   "ダンパー定義",         "粘性オイルダンパーの定義行 (DVOD)"),
        ParamDef("Cd1",    "減衰係数 Cd1 (低速)",  "低速域の減衰係数 [kN·s/mm]", "20.0"),
        ParamDef("Cd2",    "減衰係数 Cd2 (高速)",  "高速域の減衰係数 [kN·s/mm]", "5.0"),
        ParamDef("Vr",     "リリーフ速度 Vr",      "リリーフ速度（切替速度）[mm/s]", "100"),
        ParamDef("α",      "速度指数 α",           "速度指数 α", "0.5"),
    ],

    "鋼材ダンパー": [
        ParamDef("SR",     "ばね要素定義",         "ばね要素の定義行 (SR)"),
        ParamDef("K1",     "初期剛性 K1",          "弾性剛性 [kN/mm]", "100.0"),
        ParamDef("K2",     "降伏後剛性 K2",        "2次剛性 [kN/mm]（通常 K1×0.01〜0.05）"),
        ParamDef("Fy",     "降伏耐力 Fy",          "降伏耐力 [kN]", "500"),
        ParamDef("Dy",     "降伏変位 Dy",          "降伏変位 [mm]（= Fy / K1）"),
    ],

    "積層ゴム支承（免震）": [
        ParamDef("SR",     "ばね要素定義",         "ばね要素の定義行 (SR)"),
        ParamDef("Kh",     "水平剛性 Kh",          "水平等価剛性 [kN/mm]", "1.0"),
        ParamDef("Kv",     "鉛直剛性 Kv",          "鉛直剛性 [kN/mm]", "1000"),
        ParamDef("Gγ",     "せん断ひずみ依存",     "せん断弾性率のひずみ依存性"),
        ParamDef("heq",    "等価減衰定数 heq",     "等価粘性減衰定数", "0.05"),
        ParamDef("Dmax",   "最大許容変位",         "最大許容変位 [mm]", "400"),
    ],

    "鉛プラグ入り積層ゴム（LRB）": [
        ParamDef("SR",     "ばね要素定義",         "ばね要素の定義行 (SR)"),
        ParamDef("K1",     "初期剛性 K1",          "初期剛性（鉛プラグ分を含む）[kN/mm]", "10.0"),
        ParamDef("K2",     "2次剛性 K2",           "2次剛性（ゴム部分）[kN/mm]", "1.0"),
        ParamDef("Qd",     "切片荷重 Qd",          "降伏荷重（鉛プラグ荷重）[kN]", "100"),
        ParamDef("Kv",     "鉛直剛性 Kv",          "鉛直剛性 [kN/mm]", "1000"),
        ParamDef("heq",    "等価減衰定数 heq",     "等価粘性減衰定数", "0.15"),
        ParamDef("Dmax",   "最大許容変位",         "最大許容変位 [mm]", "400"),
    ],

    "すべり支承": [
        ParamDef("SR",     "ばね要素定義",         "ばね要素の定義行 (SR)"),
        ParamDef("μ",      "摩擦係数 μ",           "動摩擦係数", "0.02"),
        ParamDef("Kv",     "鉛直剛性 Kv",          "鉛直剛性 [kN/mm]", "5000"),
        ParamDef("Kh_ini", "初期水平剛性",         "滑り出し前の弾性剛性 [kN/mm]", "100"),
        ParamDef("W",      "面圧",                 "支承面圧 [kN]"),
    ],

    "カスタム": [],
}

# ダンパー種類の選択肢リスト（順序保証）
DAMPER_TYPES: List[str] = list(DAMPER_TYPE_PARAMS.keys())


# =====================================================================
# ヘルパー関数
# =====================================================================

def get_all_analysis_keywords() -> List[Tuple[str, str]]:
    """
    全解析パラメータを (keyword, "label (keyword)") のペアで返します。
    コンボボックスの選択肢生成に使用します。
    カテゴリ区切りは ("---", category_name) で表現します。
    """
    result: List[Tuple[str, str]] = []
    for cat_name, params in ANALYSIS_PARAM_CATEGORIES:
        result.append(("---", cat_name))
        for p in params:
            result.append((p.keyword, f"{p.label}  ({p.keyword})"))
    return result


def get_damper_params(damper_type: str) -> List[ParamDef]:
    """指定ダンパー種類のパラメータプリセットを返します。"""
    return DAMPER_TYPE_PARAMS.get(damper_type, [])


def find_param_def(keyword: str) -> ParamDef | None:
    """キーワードから ParamDef を検索します。"""
    for _, params in ANALYSIS_PARAM_CATEGORIES:
        for p in params:
            if p.keyword == keyword:
                return p
    for params in DAMPER_TYPE_PARAMS.values():
        for p in params:
            if p.keyword == keyword:
                return p
    return None
