"""
controller/binary/hysteresis_analysis.py
=========================================

ダンパー・バネ履歴ループ解析のための純粋データ処理ユーティリティ（PySide6 不要）。

app.ui.hysteresis_widget と tests/ の双方から import される
共有ロジックをここに切り出す。
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np

from .result_loader import SnapResultLoader


# ---------------------------------------------------------------------------
# フィールドインデックス定数
# ---------------------------------------------------------------------------

FIELD_FORCE = 0    # 荷重 F
FIELD_DISP = 1     # 変位 D
FIELD_VEL = 2      # 速度 V
FIELD_ENERGY = 3   # 累積エネルギー


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
    """
    bc = loader.get(category)
    if bc is None or bc.hst is None or bc.hst.header is None:
        return None

    hst = bc.hst
    hst.dt = dt
    h = hst.header
    if rec_idx >= h.num_records:
        return None
    if h.fields_per_record < 3:
        # F / D / V が揃わない
        return None

    try:
        t = hst.times()
        F = hst.time_series(rec_idx, FIELD_FORCE)
        D = hst.time_series(rec_idx, FIELD_DISP)
        V = hst.time_series(rec_idx, FIELD_VEL)
        E = (
            hst.time_series(rec_idx, FIELD_ENERGY)
            if h.fields_per_record > FIELD_ENERGY
            else np.zeros_like(F)
        )
    except (IndexError, ValueError):
        return None

    return {"t": t, "F": F, "D": D, "V": V, "E": E}


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
    _trapz = getattr(np, "trapezoid", np.trapz)
    try:
        stats["work"] = float(_trapz(data["F"], data["D"]))
    except Exception:
        stats["work"] = 0.0
    return stats
