"""
app/services/irdt.py

iRDT (慣性質量ダンパー) 最適解 計算エンジン。

adc-tools の `src/tools/iRDT.ts` および `src/tools/amplification.ts` を
Python に移植したもの。以下の2種類のツールで使用されます:

- iRDT最適解 - 1質点系 (SDOF): irdt_opt_param()
- iRDT最適解 - 多質点系 (MDOF): irdt_opt_param_mdof()

計算式:
    SDOF (Den Hartog):
      μ = md×nd / M
      β_opt = (1 - √(1 - 4μ)) / (2μ)
      h_opt = √(3(1 - √(1 - 4μ))) / 4
      ω_d = β_opt × ω0
      k_b = ω_d² × (md×nd) / nd = ω_d² × md
      c_d = 2 × h_opt × (md×nd) × ω_d / nd

    MDOF (多質点系 モード同調):
      mo = Σ m_i × φ_i²        (モーダル質量)
      me = Σ md_i × (φ_i - φ_{i-1})²  (有効ダンパー質量)
      μ = me / mo
      γ = (1 - 2μ - √(1 - 4μ)) / (2μ) + 1
      h = √(3(γ - 1) / (8γ))
      k_b[i] = md[i] × (ω0 × γ)²
      c_d[i] = 2 × md[i] × ω0 × γ × h
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np


# ----------------------------------------------------------------------
# SDOF (1質点系)
# ----------------------------------------------------------------------


@dataclass
class IrdtSdofResult:
    """SDOF iRDT 最適解の結果。"""
    mu: float        # 質量比 [-]
    cd_opt: float    # 一基あたり最適減衰係数 [kNs/m/基]
    kb_opt: float    # 一基あたり最適支持部材剛性 [kN/m/基]
    hd_opt: float    # 最適減衰定数 [%]
    fd_opt: float    # 最適ダンパー振動数 [Hz]
    td_opt: float    # 最適ダンパー周期 [s]


def irdt_opt_param(t0: float, m: float, md: float, nd: int) -> Tuple[float, float, float]:
    """
    iRDT 1質点系の最適解を計算します。

    Parameters
    ----------
    t0 : float
        卓越周期 [s]
    m : float
        1質点系の建物質量 [ton]
    md : float
        1基あたりのダンパー質量 [ton]
    nd : int
        ダンパー基数 [基]

    Returns
    -------
    (mu, cd_opt, kb_opt) : tuple of float
        質量比 [-]、一基当たりの最適減衰係数 [kNs/m/基]、
        最適支持部材剛性 [kN/m/基]

    Notes
    -----
    adc-tools `src/tools/iRDT.ts` の `iRDTOptParam` と完全一致する実装。
    μ >= 0.25 のとき sqrt が負になるため NaN を返す。
    """
    w0 = 2.0 * math.pi / t0
    md_total = md * nd
    mu = md_total / m

    sqrt_term = 1.0 - 4.0 * mu
    if sqrt_term < 0:
        return (mu, float("nan"), float("nan"))
    if mu == 0:
        return (mu, float("nan"), float("nan"))

    beta_opt = (1.0 - math.sqrt(sqrt_term)) / (2.0 * mu)
    hd_opt = math.sqrt(3.0 * (1.0 - math.sqrt(sqrt_term))) / 4.0
    wd_opt = beta_opt * w0
    kb_opt = wd_opt ** 2 * md_total / nd
    cd_opt = hd_opt * 2.0 * md_total * wd_opt / nd

    return (mu, cd_opt, kb_opt)


def compute_sdof_result(t0: float, m: float, md: float, nd: int) -> IrdtSdofResult:
    """SDOF最適解 + 派生パラメータ (hd_opt, fd_opt, td_opt) を計算します。"""
    mu, cd_opt, kb_opt = irdt_opt_param(t0, m, md, nd)

    # 派生値は Results.tsx の計算式に従う
    # hd_opt = ((nd × cdOpt) / (2 × √(nd × md × nd × kbOpt))) × 100
    if md > 0 and nd > 0 and kb_opt > 0:
        hd_opt = ((nd * cd_opt) / (2.0 * math.sqrt(nd * md * nd * kb_opt))) * 100.0
        # fd_opt = 1 / (2π × √(md / kb_opt))
        fd_opt = 1.0 / (2.0 * math.pi * math.sqrt(md / kb_opt))
        td_opt = 1.0 / fd_opt if fd_opt > 0 else 0.0
    else:
        hd_opt = 0.0
        fd_opt = 0.0
        td_opt = 0.0

    return IrdtSdofResult(
        mu=mu,
        cd_opt=cd_opt,
        kb_opt=kb_opt,
        hd_opt=hd_opt,
        fd_opt=fd_opt,
        td_opt=td_opt,
    )


# ----------------------------------------------------------------------
# MDOF (多質点系)
# ----------------------------------------------------------------------


@dataclass
class IrdtMdofResult:
    """MDOF iRDT 最適解の結果。"""
    mu: float                  # 有効質量比 [-]
    gamma: float               # ダンパー振動数比 [-]
    h: float                   # 減衰定数 [-]
    kb: List[float]            # 各階の最適支持部材剛性 [kN/m]
    cd: List[float]            # 各階の最適減衰係数 [kNs/m]


def irdt_opt_param_mdof(
    w0: float,
    ms: Sequence[float],
    vectors: Sequence[float],
    mds: Sequence[float],
) -> IrdtMdofResult:
    """
    iRDT 多質点系の最適解を計算します。

    Parameters
    ----------
    w0 : float
        対象モードの固有円振動数 [rad/s]
    ms : sequence of float
        各層の質量 [ton]
    vectors : sequence of float
        対象モードの固有ベクトル (または刺激関数) の各層成分 [-]
    mds : sequence of float
        各層のダンパー質量 [ton]

    Returns
    -------
    IrdtMdofResult

    Notes
    -----
    adc-tools `src/tools/iRDT.ts` の `iRDTOptParamMdof` と完全一致する実装。
    """
    n = min(len(ms), len(vectors), len(mds))

    # d_vectors2[i] = (φ_i - φ_{i-1})² ; φ_{-1} = 0
    d_vectors2: List[float] = []
    for i in range(n):
        pv = vectors[i - 1] if i > 0 else 0.0
        d_vectors2.append((vectors[i] - pv) ** 2)

    mo = sum(ms[i] * vectors[i] ** 2 for i in range(n))
    me = sum(mds[i] * d_vectors2[i] for i in range(n))

    if mo <= 0:
        return IrdtMdofResult(mu=float("nan"), gamma=float("nan"), h=float("nan"),
                              kb=[float("nan")] * n, cd=[float("nan")] * n)

    mu = me / mo
    sqrt_term = 1.0 - 4.0 * mu
    if sqrt_term < 0:
        return IrdtMdofResult(mu=mu, gamma=float("nan"), h=float("nan"),
                              kb=[float("nan")] * n, cd=[float("nan")] * n)

    gamma = (1.0 - 2.0 * mu - math.sqrt(sqrt_term)) / (2.0 * mu) + 1.0

    # h = √(3(γ - 1) / (8((γ - 1) + 1))) = √(3(γ - 1) / (8γ))
    denom = 8.0 * gamma
    numer = 3.0 * (gamma - 1.0)
    if denom <= 0 or numer < 0:
        h = float("nan")
    else:
        h = math.sqrt(numer / denom)

    kb = [mds[i] * (w0 * gamma) ** 2 for i in range(n)]
    cd = [2.0 * mds[i] * w0 * gamma * h for i in range(n)]

    return IrdtMdofResult(mu=mu, gamma=gamma, h=h, kb=kb, cd=cd)


# ----------------------------------------------------------------------
# 固有値解析 (MDOF用)
# ----------------------------------------------------------------------


def trim_number_array(array: Sequence, null_to_zero: bool = False) -> List[float]:
    """
    adc-tools `trimNumberArray` と同等。

    - null_to_zero=False: 0 または None/NaN で配列を打ち切る
    - null_to_zero=True : 0 または None/NaN を 1e-10 に置換し続行
    """
    result: List[float] = []
    for v in array:
        try:
            x = float(v) if v is not None else float("nan")
        except (TypeError, ValueError):
            x = float("nan")
        if not math.isnan(x) and x != 0:
            result.append(x)
        else:
            if null_to_zero:
                result.append(1e-10)
            else:
                break
    return result


def build_mass_matrix(m: Sequence[float]) -> np.ndarray:
    """質量対角行列を作成します。"""
    return np.diag(np.asarray(m, dtype=float))


def build_stiffness_matrix(k: Sequence[float]) -> np.ndarray:
    """
    層剛性配列から三重対角剛性行列を作成します。

    adc-tools `kMatrix` と同等の定式化:
      K[l][r] = k_l + k_{l+1}   if l == r
      K[l][r] = -k_{l+1}        if l + 1 == r
      K[l][r] = -k_l            if l == r + 1
      K[l][r] = 0               otherwise
    (k_{l+1} は範囲外では 0)
    """
    k = list(k)
    n = len(k)
    mat = np.zeros((n, n), dtype=float)
    for l in range(n):
        k1 = k[l]
        k2 = k[l + 1] if l + 1 < n else 0.0
        for r in range(n):
            if l == r:
                mat[l, r] = k1 + k2
            elif l + 1 == r:
                mat[l, r] = -k2
            elif l == r + 1:
                mat[l, r] = -k[r]
    return mat


def _normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    """各列（モード）を絶対値の最大値で正規化します。"""
    out = vectors.astype(float).copy()
    for i in range(out.shape[1]):
        col = out[:, i]
        peak = max(abs(col.max()), abs(col.min()))
        if peak > 0:
            out[:, i] = col / peak
    return out


def eigen_analysis(
    m: Sequence[float], k: Sequence[float]
) -> Tuple[np.ndarray, np.ndarray]:
    """
    固有値解析 H = inv(M) × K を行い (ω [rad/s], モードベクトル) を返します。

    Parameters
    ----------
    m : sequence of float
        各層の質量 [ton]
    k : sequence of float
        各層の層剛性 [kN/m]

    Returns
    -------
    (values, vectors) : tuple
        values : np.ndarray
            各モードの円振動数 ω_i [rad/s]、昇順ソート
        vectors : np.ndarray
            shape (n, n_modes) のモード形状行列 (列がモード)、最大値 ±1 で正規化
    """
    m_arr = np.asarray(m, dtype=float)
    k_arr = np.asarray(k, dtype=float)
    n = min(len(m_arr), len(k_arr))
    m_arr = m_arr[:n]
    k_arr = k_arr[:n]

    M = build_mass_matrix(m_arr)
    K = build_stiffness_matrix(k_arr)
    try:
        H = np.linalg.inv(M) @ K
        eig_vals, eig_vecs = np.linalg.eig(H)
        # 虚部は数値誤差とみなして除外
        eig_vals = np.real(eig_vals)
        eig_vecs = np.real(eig_vecs)

        # ω = √λ (負のλはNaN)
        omegas = np.where(eig_vals >= 0, np.sqrt(np.clip(eig_vals, 0, None)), np.nan)

        # 昇順ソート
        order = np.argsort(omegas)
        omegas = omegas[order]
        eig_vecs = eig_vecs[:, order]

        vectors_norm = _normalize_vectors(eig_vecs)
        return omegas, vectors_norm
    except np.linalg.LinAlgError:
        empty = np.zeros(0)
        return empty, np.zeros((n, 0))


# ----------------------------------------------------------------------
# 振動特性 (1DOF 周波数応答)
# ----------------------------------------------------------------------


LAMBDA_MIN = 0.0
LAMBDA_MAX = 2.0
NUM_DIV = 500


def kd_irdt_complex(md: float, cd: float, kb: float, w: float) -> complex:
    """
    iRDT ダンパーの複素インピーダンス kd(ω) を計算します。

    kd = 1 / ( 1/kb + 1 / ( -md×ω² + j×cd×ω ) )
    """
    j = 1j
    inner = -md * w ** 2 + cd * w * j
    # inner=0 の場合、TS では 1/0=Infinity となり結果が 0 に収束する
    if inner == 0:
        return 0.0 + 0.0j
    inv_inner = 1.0 / inner
    inv_kb = 1.0 / kb if kb != 0 else 0.0
    denom = inv_kb + inv_inner
    if denom == 0:
        return 0.0 + 0.0j
    return 1.0 / denom


def amp_1dof(
    m: float,
    c: float,
    k: float,
    kd_fn=None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    1質点系の変位応答倍率を計算します。

    Parameters
    ----------
    m, c, k : float
        質量 [ton]、減衰 [kNs/m]、剛性 [kN/m]
    kd_fn : callable or None
        ω を受け取り複素インピーダンスを返す関数。None なら 0。

    Returns
    -------
    (lambdas, amp) : (np.ndarray, np.ndarray)
        lambdas : 振動数比 ω/ω0 の配列 (長さ NUM_DIV)
        amp     : 変位応答倍率 |m×ω² / (k + c×j×ω - m×ω² + kd(ω))|
    """
    w0 = math.sqrt(k / m) if k > 0 and m > 0 else 0.0
    w_min = w0 * LAMBDA_MIN
    w_max = w0 * LAMBDA_MAX
    dw = (w_max - w_min) / NUM_DIV if NUM_DIV > 0 else 0.0

    ws = np.array([i * dw + w_min for i in range(NUM_DIV)], dtype=float)
    lambdas = np.where(w0 > 0, ws / w0, 0.0)

    amp = np.zeros(NUM_DIV, dtype=float)
    j = 1j
    for i, w in enumerate(ws):
        kd = kd_fn(w) if kd_fn is not None else 0.0
        denom = (-m * w ** 2) + (c * w * j) + k + kd
        if denom == 0:
            amp[i] = 0.0
        else:
            amp[i] = abs(m * w ** 2 / denom)

    return lambdas, amp
