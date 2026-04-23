"""
controller/binary/hysteresis_analysis.py
=========================================

ダンパー・バネ履歴ループ解析のための純粋データ処理ユーティリティ（PySide6 不要）。

app.ui.hysteresis_widget と tests/ の双方から import される
共有ロジックをここに切り出す。

フィールドレイアウトについて
----------------------------

Damper.hst / Spring.hst の fields_per_record (fpr) は解析種別により異なり、
F / D / V / E の位置も fpr ごとに変わる。実データ検証 (例: 質点X D3 = fpr=4、
example_3D/D4 = fpr=8、example_shear_iRDT/D1 = fpr=11) に基づく対応表:

- Spring.hst fpr=5: [F, D, V, E, ?] (従来どおり)
- Damper.hst fpr=4 (2D簡易ダンパー): [F, D, **E**, **V**]
  ※ 従来コードは [F, D, V, E] と誤解していたため、F–V ループが
    「F vs 累積エネルギー」となり単調増加の直線しか表示されない状態だった。
- Damper.hst fpr=8 (3D 立体モデル): V フィールドなし、末尾 (f7) がエネルギー。
- Damper.hst fpr=11 (iRDT 型): F=f1, D=f2, V=f4, E=f9 (末尾ではない点に注意)。
- Damper.hst fpr=11 (iOD 型): サブ要素ごとの (F, 運動学) 対が格納される。
  * スプリング    : F=f1, D=f2  (線形, 無ヒステリシス)
  * 質量（イナーター）: F=f4, A=f5  (F = m·A, 線形)
  * ダッシュポット : F=f7, V=f8  (ヒステリシス)
  * エネルギー   : f9           (単調増加)
  iOD ファイルは fpr=4 (全体) と fpr=11 (サブ要素) が混在する特徴がある。
  iRDT ファイルは fpr=11 の一様レイアウトなので、
  ``is_iod_layout(per_record_fpr)`` で判別する。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from .result_loader import SnapResultLoader


# ---------------------------------------------------------------------------
# フィールドインデックス定数（Spring 用の従来デフォルト）
# ---------------------------------------------------------------------------

FIELD_FORCE = 0    # 荷重 F
FIELD_DISP = 1     # 変位 D
FIELD_VEL = 2      # 速度 V (Spring.hst fpr=5)
FIELD_ENERGY = 3   # 累積エネルギー (Spring.hst fpr=5)


# ---------------------------------------------------------------------------
# iOD サブ要素識別子
# ---------------------------------------------------------------------------
#
# iOD (IOD12) の構成は「スプリング」の直列下に「質量 || ダッシュポット」が
# 並列接続される。力の恒等式:
#   F_total = F_mass + F_dashpot  （質量とダッシュポットが並列）
#   F_total = F_spring            （スプリングは直列なので全体と同じ）
# つまり fpr=11 レコードで f1 = f4 + f7 が確認される。

SUB_ELEMENT_AUTO = "auto"        # レコードの fpr から自動判定
SUB_ELEMENT_WHOLE = "whole"      # 全体（F-D）
SUB_ELEMENT_MASS = "mass"        # 質量/イナーター（F-A）
SUB_ELEMENT_DASHPOT = "dashpot"  # ダッシュポット（F-V）

SUB_ELEMENT_LABELS: Dict[str, str] = {
    SUB_ELEMENT_AUTO: "自動",
    SUB_ELEMENT_WHOLE: "全体",
    SUB_ELEMENT_MASS: "質量",
    SUB_ELEMENT_DASHPOT: "ダッシュポット",
}

# サブ要素ごとの主変数（x軸）の種別
SUB_ELEMENT_PRIMARY_KIND: Dict[str, str] = {
    SUB_ELEMENT_WHOLE: "D",
    SUB_ELEMENT_MASS: "A",
    SUB_ELEMENT_DASHPOT: "V",
}


def damper_field_map(fields_per_record: int) -> Dict[str, int]:
    """Damper.hst の fpr に応じた F/D/V/E フィールドインデックスを返す。

    Returns
    -------
    dict with keys among ``"F"``, ``"D"``, ``"V"``, ``"E"``.
    速度が格納されないレイアウトでは ``"V"`` キー自体を省く。

    Notes
    -----
    fpr=4 は最後の 2 フィールドが [Energy, Vel] の順である。
    従来コードの [Vel, Energy] 前提は誤り (F–V ループが直線化する原因)。
    fpr=8 は 3D 立体モデルで V 成分が存在しない (F-D ループのみ)。
    fpr=11 は iRDT 型で E が末尾ではなく f9 に置かれる。
    iOD 型 fpr=11 は別関数 ``iod_fpr11_sub_element_map`` を使う。
    """
    if fields_per_record == 4:
        return {"F": 0, "D": 1, "E": 2, "V": 3}
    if fields_per_record == 8:
        return {"F": 0, "D": 1, "E": 7}
    if fields_per_record == 11:
        return {"F": 1, "D": 2, "V": 4, "E": 9}
    return {"F": 0, "D": 1, "E": fields_per_record - 1}


def spring_field_map(fields_per_record: int) -> Dict[str, int]:
    """Spring.hst の fpr に応じた F/D/V/E フィールドインデックスを返す。

    Spring.hst fpr=5 は [F, D, V, E, ?] の従来レイアウトで確定。
    """
    return {
        "F": FIELD_FORCE,
        "D": FIELD_DISP,
        "V": FIELD_VEL,
        "E": FIELD_ENERGY,
    }


def category_field_map(category: str, fields_per_record: int) -> Dict[str, int]:
    """カテゴリ名 + fpr から F/D/V/E フィールドインデックスを返す。"""
    if category == "Damper":
        return damper_field_map(fields_per_record)
    return spring_field_map(fields_per_record)


def energy_field_index(category: str, fields_per_record: int) -> int:
    """カテゴリと fpr に応じた累積エネルギーのフィールドインデックスを返す。"""
    fmap = category_field_map(category, fields_per_record)
    return fmap.get("E", fields_per_record - 1)


# ---------------------------------------------------------------------------
# iOD レイアウト判定・サブ要素マップ
# ---------------------------------------------------------------------------

def is_iod_layout(per_record_fpr: Optional[Sequence[int]]) -> bool:
    """iOD 型（fpr=4 と fpr=11 が混在）を判定する。

    iOD 複合制振装置は、ダンパー 1 台につき「全体 F-D 用 (fpr=4)」と
    「サブ要素パック (fpr=11)」の 2 レコードを出力するため、
    単一 Damper.hst 内に両 fpr が混在する。iRDT は fpr=11 一様なので、
    この関数は False を返す。
    """
    if not per_record_fpr:
        return False
    try:
        distinct = {int(f) for f in per_record_fpr}
    except (TypeError, ValueError):
        return False
    return 4 in distinct and 11 in distinct


def iod_fpr11_sub_element_map(sub_element: str) -> Dict[str, int]:
    """iOD fpr=11 のサブ要素別フィールドインデックス。

    iOD 複合制振装置 fpr=11 レコードの実データ検証 (相関解析 + ユーザー
    提供 SNAP 参照図 + 恒等式 ``f1 = f4 + f7`` から確定したレイアウト)::

      f1 = F_total  [kN],  f2 = D_spring [m]      (全体力, f1=k·f2 線形)
      f4 = F_mass   [kN],  f5 = A_mass   [m/s^2]  (F = m·A, 線形)
      f7 = F_dash   [kN],  f8 = V_dash   [m/s]    (非線形, ヒステリシス)
      f9 = E (累積エネルギー, 単調増加)

    ``f1 = f4 + f7`` は質量 || ダッシュポット並列構成による全体力
    の合力を表す恒等式である。

    Parameters
    ----------
    sub_element : str
        ``"whole"`` / ``"mass"`` / ``"dashpot"`` のいずれか。

    Returns
    -------
    dict with keys among ``"F"``, ``"D"``, ``"V"``, ``"A"``, ``"E"``.
    """
    if sub_element == SUB_ELEMENT_MASS:
        return {"F": 4, "A": 5, "E": 9}
    if sub_element == SUB_ELEMENT_DASHPOT:
        return {"F": 7, "V": 8, "E": 9}
    # whole / default
    return {"F": 1, "D": 2, "E": 9}


def sub_element_applies_to_fpr(sub_element: str, fpr: int, iod: bool) -> bool:
    """指定サブ要素がこの (fpr, iod) レコードに適用可能か判定。

    - fpr=4 は iOD の場合「全体」のみ、iRDT/通常では常に「全体」相当。
    - fpr=11 iOD は全サブ要素に対応。
    - fpr=11 非 iOD（iRDT）は「全体」のみ対応（現行通り）。
    - ``"auto"`` は常に True。
    """
    if sub_element == SUB_ELEMENT_AUTO:
        return True
    if sub_element == SUB_ELEMENT_WHOLE:
        return True
    if not iod:
        return False
    if fpr == 11:
        return sub_element in (SUB_ELEMENT_MASS, SUB_ELEMENT_DASHPOT)
    # fpr=4 iOD: サブ要素データなし
    return False


def resolve_sub_element(sub_element: str, fpr: int, iod: bool) -> str:
    """auto を具体的サブ要素にフォールバックする。"""
    if sub_element != SUB_ELEMENT_AUTO:
        return sub_element
    return SUB_ELEMENT_WHOLE


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def fetch_hysteresis_data(
    loader: SnapResultLoader,
    category: str,
    rec_idx: int,
    dt: float,
    sub_element: str = SUB_ELEMENT_AUTO,
) -> Optional[Dict[str, np.ndarray]]:
    """指定カテゴリ・レコードの時刻歴データを取得する。

    Parameters
    ----------
    loader   : SnapResultLoader
    category : "Damper" または "Spring"
    rec_idx  : レコードインデックス（0-based）
    dt       : 時刻刻み [s]
    sub_element : iOD のサブ要素指定
        ``"auto"`` (既定): fpr から妥当なレイアウトを選択。
        ``"whole"`` : ダンパー全体（F-D）。
        ``"spring"`` : 弾性スプリング（F-D, iOD fpr=11 のみ）。
        ``"mass"`` : 質量/イナーター（F-A, iOD fpr=11 のみ）。
        ``"dashpot"`` : ダッシュポット（F-V, iOD fpr=11 のみ）。

    Returns
    -------
    dict with keys:
        t, F, D, V, A, E, v_derived, sub_element,
        x_kind ("D"|"V"|"A"), applies (bool)
    または読み取り失敗時は ``None``。
    """
    bc = loader.get(category)
    if bc is None or bc.hst is None or bc.hst.header is None:
        return None

    hst = bc.hst
    hst.dt = dt
    h = hst.header
    if rec_idx >= h.num_records:
        return None

    per_record_fpr = getattr(h, "per_record_fpr", None)
    if isinstance(per_record_fpr, (list, tuple)) and rec_idx < len(per_record_fpr):
        fpr_this = int(per_record_fpr[rec_idx])
    else:
        fpr_this = h.fields_per_record
    if fpr_this < 2:
        return None

    iod = is_iod_layout(per_record_fpr) if category == "Damper" else False

    if not sub_element_applies_to_fpr(sub_element, fpr_this, iod):
        return {
            "t": np.zeros(0), "F": np.zeros(0), "D": np.zeros(0),
            "V": np.zeros(0), "A": np.zeros(0), "E": np.zeros(0),
            "v_derived": False, "sub_element": sub_element,
            "x_kind": "D", "applies": False,
        }

    effective = resolve_sub_element(sub_element, fpr_this, iod)

    # サブ要素ごとに読み取り分岐
    if category == "Damper" and iod and fpr_this == 11 and effective in (
        SUB_ELEMENT_MASS, SUB_ELEMENT_DASHPOT, SUB_ELEMENT_WHOLE
    ):
        return _fetch_iod_fpr11(hst, rec_idx, fpr_this, dt, effective)

    # 非 iOD または fpr=4: 従来の category_field_map
    fmap = category_field_map(category, fpr_this)
    return _fetch_standard(hst, rec_idx, fpr_this, dt, fmap, effective)


def _fetch_iod_fpr11(
    hst, rec_idx: int, fpr: int, dt: float, sub_element: str
) -> Optional[Dict[str, np.ndarray]]:
    """iOD fpr=11 のサブ要素データを読み取る。"""
    fmap = iod_fpr11_sub_element_map(sub_element)
    try:
        t = hst.times()
        F = hst.time_series(rec_idx, fmap["F"])
        D = np.zeros_like(F)
        V = np.zeros_like(F)
        A = np.zeros_like(F)
        x_kind = SUB_ELEMENT_PRIMARY_KIND.get(sub_element, "D")

        if "D" in fmap and fmap["D"] < fpr:
            D = hst.time_series(rec_idx, fmap["D"])
            V = _numerical_derivative(D, dt)
            A = _numerical_derivative(V, dt)
        elif "V" in fmap and fmap["V"] < fpr:
            V = hst.time_series(rec_idx, fmap["V"])
            A = _numerical_derivative(V, dt)
        elif "A" in fmap and fmap["A"] < fpr:
            A = hst.time_series(rec_idx, fmap["A"])

        E = (hst.time_series(rec_idx, fmap["E"])
             if "E" in fmap and fmap["E"] < fpr else np.zeros_like(F))
    except (IndexError, ValueError):
        return None

    return {
        "t": t, "F": F, "D": D, "V": V, "A": A, "E": E,
        "v_derived": x_kind != "V",
        "sub_element": sub_element,
        "x_kind": x_kind,
        "applies": True,
    }


def _fetch_standard(
    hst, rec_idx: int, fpr: int, dt: float, fmap: Dict[str, int], sub_element: str
) -> Optional[Dict[str, np.ndarray]]:
    """通常レイアウト (非 iOD fpr=11 または fpr=4/5/8) のデータ読み取り。"""
    f_idx = fmap.get("F")
    d_idx = fmap.get("D")
    v_idx = fmap.get("V")
    e_idx = fmap.get("E")

    if f_idx is None or d_idx is None:
        return None

    try:
        t = hst.times()
        F = hst.time_series(rec_idx, f_idx)
        D = hst.time_series(rec_idx, d_idx)
        v_derived = False
        if v_idx is not None and v_idx < fpr:
            V = hst.time_series(rec_idx, v_idx)
        else:
            V = _numerical_derivative(D, dt)
            v_derived = True
        A = _numerical_derivative(V, dt)
        if e_idx is not None and e_idx < fpr:
            E = hst.time_series(rec_idx, e_idx)
        else:
            E = np.zeros_like(F)
    except (IndexError, ValueError):
        return None

    return {
        "t": t, "F": F, "D": D, "V": V, "A": A, "E": E,
        "v_derived": v_derived,
        "sub_element": sub_element,
        "x_kind": "D",
        "applies": True,
    }


def _numerical_derivative(y: np.ndarray, dt: float) -> np.ndarray:
    """中心差分で数値微分する。端点は片側差分。"""
    if y.size < 2 or dt <= 0:
        return np.zeros_like(y)
    dy = np.empty_like(y)
    dy[1:-1] = (y[2:] - y[:-2]) / (2.0 * dt)
    dy[0] = (y[1] - y[0]) / dt
    dy[-1] = (y[-1] - y[-2]) / dt
    return dy


# ---------------------------------------------------------------------------
# 統計計算
# ---------------------------------------------------------------------------

def compute_peak_stats(data: Dict[str, np.ndarray]) -> Dict[str, float]:
    """時刻歴データからピーク統計を計算する。

    Parameters
    ----------
    data : dict with keys ``"F"``, ``"D"``, ``"V"``, ``"A"`` (optional), ``"E"``

    Returns
    -------
    dict with keys:
        max_F   : 最大荷重絶対値
        max_D   : 最大変位絶対値
        max_V   : 最大速度絶対値
        max_A   : 最大加速度絶対値
        max_E   : 最大累積エネルギー絶対値
        work    : 仕事量 ∮F dD（台形積分）
    """
    stats: Dict[str, float] = {}
    for key in ("F", "D", "V", "A", "E"):
        arr = data.get(key, np.zeros(1))
        stats[f"max_{key}"] = float(np.max(np.abs(arr))) if arr.size else 0.0

    _trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz", None)
    try:
        stats["work"] = float(_trapz(data["F"], data["D"]))
    except Exception:
        stats["work"] = 0.0
    return stats
