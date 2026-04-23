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
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from .result_loader import SnapResultLoader


# ---------------------------------------------------------------------------
# フィールドインデックス定数（Spring 用の従来デフォルト）
# ---------------------------------------------------------------------------

FIELD_FORCE = 0    # 荷重 F
FIELD_DISP = 1     # 変位 D
FIELD_VEL = 2      # 速度 V (Spring.hst fpr=5)
FIELD_ENERGY = 3   # 累積エネルギー (Spring.hst fpr=5)


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
    """
    if fields_per_record == 4:
        return {"F": 0, "D": 1, "E": 2, "V": 3}
    if fields_per_record == 8:
        # 3D ダンパー: 速度フィールドなし、末尾が Energy
        return {"F": 0, "D": 1, "E": 7}
    if fields_per_record == 11:
        # iRDT ダンパー: F/D は f1/f2、V は f4、E は f9 (末尾ではない)
        return {"F": 1, "D": 2, "V": 4, "E": 9}
    # 未知 fpr: 先頭 2 つを F/D、末尾を E として推定
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
    """カテゴリと fpr に応じた累積エネルギーのフィールドインデックスを返す。

    ``category_field_map`` のラッパー。過去バージョンとの互換のため残置。
    """
    fmap = category_field_map(category, fields_per_record)
    return fmap.get("E", fields_per_record - 1)


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def fetch_hysteresis_data(
    loader: SnapResultLoader,
    category: str,
    rec_idx: int,
    dt: float,
) -> Optional[Dict[str, np.ndarray]]:
    """指定カテゴリ・レコードの時刻歴データを取得する。

    Parameters
    ----------
    loader   : SnapResultLoader
    category : "Damper" または "Spring"
    rec_idx  : レコードインデックス（0-based）
    dt       : 時刻刻み [s]

    Returns
    -------
    dict with keys ``"t"``, ``"F"``, ``"D"``, ``"V"``, ``"E"``
    または読み取り失敗時は ``None``。
    速度フィールドを持たないレイアウト (Damper fpr=8 等) では
    ``V`` は ``D`` を dt で数値微分した配列を返す (F–V ループを近似表示可能)。
    """
    bc = loader.get(category)
    if bc is None or bc.hst is None or bc.hst.header is None:
        return None

    hst = bc.hst
    hst.dt = dt
    h = hst.header
    if rec_idx >= h.num_records:
        return None
    if h.fields_per_record < 2:
        # 最低限 F/D が揃わない
        return None

    fmap = category_field_map(category, h.fields_per_record)
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
        if v_idx is not None and v_idx < h.fields_per_record:
            V = hst.time_series(rec_idx, v_idx)
        else:
            V = _numerical_derivative(D, dt)
            v_derived = True
        if e_idx is not None and e_idx < h.fields_per_record:
            E = hst.time_series(rec_idx, e_idx)
        else:
            E = np.zeros_like(F)
    except (IndexError, ValueError):
        return None

    return {"t": t, "F": F, "D": D, "V": V, "E": E, "v_derived": v_derived}


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
    data : dict with keys ``"F"``, ``"D"``, ``"V"``, ``"E"``

    Returns
    -------
    dict with keys:
        max_F   : 最大荷重絶対値
        max_D   : 最大変位絶対値
        max_V   : 最大速度絶対値
        max_E   : 最大累積エネルギー絶対値
        work    : 仕事量 ∮F dD（台形積分）
    """
    stats: Dict[str, float] = {}
    for key in ("F", "D", "V", "E"):
        arr = data.get(key, np.zeros(1))
        stats[f"max_{key}"] = float(np.max(np.abs(arr))) if arr.size else 0.0

    # 力–変位仕事量（台形積分）
    # np.trapezoid は numpy >= 2.0、np.trapz は numpy < 2.0 の互換ラッパー
    _trapz = getattr(np, "trapezoid", None) or getattr(np, "trapz", None)
    try:
        stats["work"] = float(_trapz(data["F"], data["D"]))
    except Exception:
        stats["work"] = 0.0
    return stats
