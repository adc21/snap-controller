"""
app/services/irdt_designer.py

iRDT（慣性質量ダンパー）自動最適設計モジュール
===============================================

質点系に対して、定点理論（Den Hartog 1956 の Fixed-Point Theory）を適用し、
iRDT（並列に「慣性質量要素 + 粘性要素」、直列に「取付剛性」を持つダンパー）
の最適パラメータを自動算出します。

想定するダンパーモデル
----------------------
主構造（SDOF 相当、もしくは目標モードに縮約した等価 SDOF）:
    M_s : 主構造の等価質量（もしくはモード質量）
    K_s : 主構造の等価剛性
    ω_s = √(K_s / M_s)

iRDT:
    m_d : 慣性質量（inertance / apparent mass）
    c_d : 粘性減衰係数
    k_b : 取付（支持）剛性

無次元化パラメータ:
    μ = m_d / M_s         （質量比）
    f = ω_d / ω_s         （周波数比）ここで ω_d = √(k_b / m_d)
    ζ = c_d / (2 m_d ω_d) （ダンパー減衰比）

定点理論の最適設計式（Den Hartog, ``Mechanical Vibrations``, 4th ed., 1956）:
    f_opt  = 1 / (1 + μ)
    ζ_opt  = √( 3μ / ( 8 (1 + μ)^3 ) )

本モジュールでは、固有値解析から得られる等価 SDOF（モード質量・モード固有周期）
に対してこの最適設計式を適用し、更に多層建物に対してはモード形に応じた
各層配置量を計算します。

参考: Den Hartog J.P., "Mechanical Vibrations", McGraw-Hill, 1956.
      池永ら, "同調粘性質量ダンパーを用いた SDOF 構造物の制振設計",
      日本建築学会構造系論文集, 2012.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


@dataclass
class IrdtParameters:
    """単一 iRDT の最適設計値。"""

    mass_ratio: float        # μ = m_d / M_s
    inertance: float         # m_d [kg]  (= 慣性質量)
    damping: float           # c_d [N·s/m]
    support_stiffness: float # k_b [N/m]
    frequency_ratio: float   # f_opt
    damping_ratio: float     # ζ_opt
    target_omega: float      # ω_s [rad/s]
    target_mass: float       # M_s [kg] (等価質量またはモード質量)
    target_period: float     # T_s [s]
    note: str = ""

    def to_dict(self) -> Dict[str, float]:
        return {
            "mass_ratio": self.mass_ratio,
            "inertance_kg": self.inertance,
            "damping_Ns_per_m": self.damping,
            "support_stiffness_N_per_m": self.support_stiffness,
            "frequency_ratio": self.frequency_ratio,
            "damping_ratio": self.damping_ratio,
            "target_omega_rad_s": self.target_omega,
            "target_mass_kg": self.target_mass,
            "target_period_s": self.target_period,
        }

    def summary_text(self) -> str:
        lines = [
            f"対象モード周期 T_s      = {self.target_period:.4f} [s]",
            f"対象等価質量   M_s      = {self.target_mass:.3e} [kg]",
            f"質量比         μ       = {self.mass_ratio:.4f}",
            f"慣性質量       m_d     = {self.inertance:.3e} [kg]",
            f"最適周波数比   f_opt   = {self.frequency_ratio:.4f}",
            f"最適減衰比     ζ_opt   = {self.damping_ratio:.4f}",
            f"支持剛性       k_b     = {self.support_stiffness:.3e} [N/m]",
            f"粘性減衰係数   c_d     = {self.damping:.3e} [N·s/m]",
        ]
        if self.note:
            lines.append(f"備考: {self.note}")
        return "\n".join(lines)


@dataclass
class IrdtFloorAssignment:
    """多層建物における各層配置の詳細。"""

    floor: int
    mode_amplitude: float
    inter_story_mode: float
    mass_ratio_effective: float
    inertance: float
    damping: float
    support_stiffness: float


@dataclass
class IrdtPlacementPlan:
    """多層建物に対する iRDT 配置計画。"""

    target_mode: int
    target_period: float
    target_frequency_hz: float
    modal_mass: float
    total_mass_ratio: float
    floor_plan: List[IrdtFloorAssignment] = field(default_factory=list)
    base_parameters: Optional[IrdtParameters] = None

    def summary_text(self) -> str:
        lines = [
            f"=== iRDT 最適配置計画（定点理論） ===",
            f"対象モード     : {self.target_mode}",
            f"対象モード周期 : {self.target_period:.4f} [s]",
            f"対象モード振動数: {self.target_frequency_hz:.4f} [Hz]",
            f"モード質量 M*  : {self.modal_mass:.3e} [kg]",
            f"総質量比 μ_tot : {self.total_mass_ratio:.4f}",
            "",
        ]
        if self.base_parameters is not None:
            lines.append("--- 等価 SDOF 基準設計値 ---")
            lines.append(self.base_parameters.summary_text())
            lines.append("")
        lines.append("--- 各層への配分 ---")
        lines.append(
            " 層 | φ(k)     | Δφ(k)    | μ_eff    | m_d        | c_d        | k_b"
        )
        for a in self.floor_plan:
            lines.append(
                f" {a.floor:>2} | {a.mode_amplitude:>+8.4f} | {a.inter_story_mode:>+8.4f} | "
                f"{a.mass_ratio_effective:>8.5f} | {a.inertance:>10.3e} | "
                f"{a.damping:>10.3e} | {a.support_stiffness:>10.3e}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 定点理論の核
# ---------------------------------------------------------------------------


def fixed_point_optimal(mass_ratio: float) -> tuple[float, float]:
    """
    Den Hartog 定点理論による最適周波数比・最適減衰比を返します。

    Parameters
    ----------
    mass_ratio : float
        μ = m_d / M_s (> 0)

    Returns
    -------
    (f_opt, zeta_opt)
    """
    if mass_ratio <= 0:
        raise ValueError(f"mass_ratio must be positive, got {mass_ratio}")
    f_opt = 1.0 / (1.0 + mass_ratio)
    zeta_opt = math.sqrt(3.0 * mass_ratio / (8.0 * (1.0 + mass_ratio) ** 3))
    return f_opt, zeta_opt


def design_irdt_sdof(
    primary_mass: float,
    primary_period: float,
    mass_ratio: float,
    note: str = "",
) -> IrdtParameters:
    """
    SDOF 等価系に対する iRDT 最適パラメータを算出します。

    Parameters
    ----------
    primary_mass : float
        主構造の等価質量 M_s [kg]
    primary_period : float
        主構造の固有周期 T_s [s]
    mass_ratio : float
        質量比 μ = m_d / M_s (典型値 0.01 ~ 0.10)
    note : str
        備考（計画表に表示）

    Returns
    -------
    IrdtParameters
    """
    if primary_mass <= 0:
        raise ValueError("primary_mass must be positive")
    if primary_period <= 0:
        raise ValueError("primary_period must be positive")

    omega_s = 2.0 * math.pi / primary_period
    f_opt, zeta_opt = fixed_point_optimal(mass_ratio)

    m_d = mass_ratio * primary_mass
    omega_d = f_opt * omega_s
    k_b = m_d * omega_d ** 2
    c_d = 2.0 * zeta_opt * m_d * omega_d

    return IrdtParameters(
        mass_ratio=mass_ratio,
        inertance=m_d,
        damping=c_d,
        support_stiffness=k_b,
        frequency_ratio=f_opt,
        damping_ratio=zeta_opt,
        target_omega=omega_s,
        target_mass=primary_mass,
        target_period=primary_period,
        note=note,
    )


# ---------------------------------------------------------------------------
# 多層建物への配分（モード縮約ベース）
# ---------------------------------------------------------------------------


def _modal_mass(masses: Sequence[float], mode_shape: Sequence[float]) -> float:
    """一般化（モード）質量 M* = φᵀ M φ を計算。"""
    if len(masses) != len(mode_shape):
        raise ValueError("length of masses and mode_shape must match")
    return sum(m * phi * phi for m, phi in zip(masses, mode_shape))


def _normalize_mode_shape(mode_shape: Sequence[float]) -> List[float]:
    """モード形を最大絶対値で 1.0 に正規化。"""
    amax = max(abs(v) for v in mode_shape)
    if amax == 0.0:
        raise ValueError("mode_shape is all zero")
    return [v / amax for v in mode_shape]


def design_irdt_placement(
    masses: Sequence[float],
    mode_shape: Sequence[float],
    target_period: float,
    total_mass_ratio: float,
    target_mode: int = 1,
    distribution: str = "interstory",
) -> IrdtPlacementPlan:
    """
    多層建物に対する iRDT 最適配置計画を作成します。

    手順:
      1. 指定モードのモード質量 M* = φᵀMφ と固有円振動数から等価 SDOF を構築。
      2. 定点理論で (f_opt, ζ_opt) を算出し、総質量比 μ_tot のときの
         基準 iRDT パラメータを取得。
      3. 各層の配分は ``distribution`` に従って重み付け:
         - ``"interstory"`` : 層間モード変位 Δφ(k) = φ(k) - φ(k-1) の
           二乗に比例（iRDT の仕事量は層間速度の二乗に比例するため）。
         - ``"amplitude"``  : モード振幅 φ(k) の二乗に比例。
         - ``"uniform"``    : 全層均等配分。

    Parameters
    ----------
    masses : Sequence[float]
        各層の質量 [kg]（下階から順）
    mode_shape : Sequence[float]
        対象モードのモード形（下階から順）
    target_period : float
        対象モードの固有周期 [s]
    total_mass_ratio : float
        全層合計の質量比 μ_tot = Σm_d,k / Σm_k
    target_mode : int
        表示用モード番号
    distribution : {"interstory", "amplitude", "uniform"}
        層配分戦略
    """
    if len(masses) != len(mode_shape):
        raise ValueError("masses and mode_shape must have the same length")
    if total_mass_ratio <= 0:
        raise ValueError("total_mass_ratio must be positive")
    if target_period <= 0:
        raise ValueError("target_period must be positive")

    mode_norm = _normalize_mode_shape(mode_shape)
    modal_mass = _modal_mass(masses, mode_norm)
    total_mass = sum(masses)
    total_inertance = total_mass_ratio * total_mass

    # 等価 SDOF 基準設計（モード質量に対する）
    mu_modal = total_inertance / modal_mass if modal_mass > 0 else total_mass_ratio
    base = design_irdt_sdof(
        primary_mass=modal_mass,
        primary_period=target_period,
        mass_ratio=mu_modal,
        note="モード質量に基づく基準値。各層への配分は distribution に従う。",
    )

    # --- 層配分 ---
    num_floors = len(masses)
    weights = [0.0] * num_floors
    inter_mode = [0.0] * num_floors

    if distribution == "interstory":
        prev = 0.0
        for i, phi in enumerate(mode_norm):
            d = phi - prev
            inter_mode[i] = d
            weights[i] = d * d
            prev = phi
    elif distribution == "amplitude":
        for i, phi in enumerate(mode_norm):
            inter_mode[i] = phi - (mode_norm[i - 1] if i > 0 else 0.0)
            weights[i] = phi * phi
    elif distribution == "uniform":
        for i, phi in enumerate(mode_norm):
            inter_mode[i] = phi - (mode_norm[i - 1] if i > 0 else 0.0)
            weights[i] = 1.0
    else:
        raise ValueError(f"unknown distribution: {distribution}")

    wsum = sum(weights)
    if wsum <= 0:
        raise ValueError("all distribution weights are zero")
    weights = [w / wsum for w in weights]

    # 各層の慣性質量 m_d,k = weight_k * total_inertance
    # 各層の k_b, c_d は「同一 f_opt, ζ_opt を各層で満たす」として
    # 基準値からスケールする (k_b ∝ m_d, c_d ∝ m_d)
    omega_d = base.frequency_ratio * base.target_omega
    floor_plan: List[IrdtFloorAssignment] = []
    for i in range(num_floors):
        m_d_k = weights[i] * total_inertance
        k_b_k = m_d_k * omega_d ** 2
        c_d_k = 2.0 * base.damping_ratio * m_d_k * omega_d
        mu_eff = m_d_k / masses[i] if masses[i] > 0 else 0.0
        floor_plan.append(
            IrdtFloorAssignment(
                floor=i + 1,
                mode_amplitude=mode_norm[i],
                inter_story_mode=inter_mode[i],
                mass_ratio_effective=mu_eff,
                inertance=m_d_k,
                damping=c_d_k,
                support_stiffness=k_b_k,
            )
        )

    frequency_hz = 1.0 / target_period
    return IrdtPlacementPlan(
        target_mode=target_mode,
        target_period=target_period,
        target_frequency_hz=frequency_hz,
        modal_mass=modal_mass,
        total_mass_ratio=total_mass_ratio,
        base_parameters=base,
        floor_plan=floor_plan,
    )


# ---------------------------------------------------------------------------
# 便利関数: PeriodReader から直接設計
# ---------------------------------------------------------------------------


def design_from_period_reader(
    period_reader,
    masses: Sequence[float],
    total_mass_ratio: float,
    target_mode: int = 1,
    distribution: str = "interstory",
    mode_shape: Optional[Sequence[float]] = None,
) -> IrdtPlacementPlan:
    """
    ``PeriodReader`` インスタンスから直接 iRDT 配置計画を作成するヘルパー。

    モード形が固有値ファイルに含まれていない場合は ``mode_shape`` 引数で
    明示指定してください（質点系では線形または sin(kπ/2N) 近似がよく用いられる）。
    """
    if target_mode not in period_reader.periods:
        raise ValueError(f"mode {target_mode} not found in Period data")

    period = period_reader.periods[target_mode]

    if mode_shape is None:
        # 固有値ファイルにモード形が無い場合のフォールバック:
        # 1 次モードの代表値として sin((2k-1)π / (2N+1)) を用いる
        n = len(masses)
        mode_shape = [
            math.sin((2 * (k + 1) - 1) * math.pi / (2 * n + 1)) for k in range(n)
        ]

    return design_irdt_placement(
        masses=masses,
        mode_shape=mode_shape,
        target_period=period,
        total_mass_ratio=total_mass_ratio,
        target_mode=target_mode,
        distribution=distribution,
    )


# ---------------------------------------------------------------------------
# 周波数応答関数 (FRF) と応答低減率 η
# ---------------------------------------------------------------------------
#
# 参考: Ikago K, Saito K, Inoue N (2012)
#       "Seismic Control of Single-Degree-of-Freedom Structure Using
#        Tuned Viscous Mass Damper", EESD, 41(3): 453-474.
#
# TVMD (Tuned Viscous Mass Damper) の SDOF モデル:
#   主構造:  M·ü + C·u̇ + K·u + k_d·(u − w) = −M·ü_g
#   TVMD:    m_d·ẅ + c_d·ẇ + k_d·(w − u)    = 0
#
# ここで w は TVMD 内部自由度（inerter + dashpot の変位）。
# 調和入力に対する定常応答の振幅比 |H(r)| を計算する。


def compute_frf_sdof_tvmd(
    mass_ratio: float,
    freq_ratio: float,
    damping_ratio_tvmd: float,
    damping_ratio_primary: float = 0.0,
    r_min: float = 0.01,
    r_max: float = 3.0,
    n_points: int = 1000,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    SDOF + TVMD 系の変位伝達関数 |H(r)| を計算する。

    H(r) = |U / U_st| （U_st = F_0/K は静的変位）

    Parameters
    ----------
    mass_ratio : float
        μ = m_d / M
    freq_ratio : float
        f = ω_d / ω_s  (ω_d = √(k_d/m_d))
    damping_ratio_tvmd : float
        ζ_d = c_d / (2·m_d·ω_d)
    damping_ratio_primary : float
        ζ_s = C / (2·M·ω_s)  主構造の減衰比（デフォルト 0）
    r_min, r_max : float
        振動数比 r = ω/ω_s の範囲
    n_points : int
        計算点数

    Returns
    -------
    (r_array, H_array)
        振動数比と伝達関数の振幅
    """
    mu = mass_ratio
    g = freq_ratio      # f = ω_d / ω_s
    zd = damping_ratio_tvmd
    zs = damping_ratio_primary

    r = np.linspace(r_min, r_max, n_points)

    # 分子: TVMD 側の動剛性
    # N(r) = g² - r² + 2i·ζ_d·g·r
    N = g**2 - r**2 + 2j * zd * g * r

    # 分母: 2×2 系の行列式
    # D₁₁ = 1 - r² + 2i·ζ_s·r + μ·g²  (主構造 + 支持剛性)
    #   ※ k_d/K = μ·g² (∵ k_d = m_d·ω_d² = μ·M·(g·ω_s)² = μ·g²·K)
    # D₂₂ = g² - r² + 2i·ζ_d·g·r   = N(r)
    # D₁₂ = D₂₁ = -μ·g²
    # det(D) = D₁₁·D₂₂ - D₁₂²
    D11 = 1 - r**2 + 2j * zs * r + mu * g**2
    D12 = -mu * g**2
    det_D = D11 * N - D12**2

    # H(r) = |N(r) / det(D)|
    H = np.abs(N / det_D)

    return r, H


def compute_frf_sdof(
    damping_ratio: float = 0.0,
    r_min: float = 0.01,
    r_max: float = 3.0,
    n_points: int = 1000,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    制振なし SDOF 系の変位伝達関数 |H(r)| を計算する。

    H(r) = 1 / |1 - r² + 2i·ζ·r|
    """
    r = np.linspace(r_min, r_max, n_points)
    H = 1.0 / np.abs(1 - r**2 + 2j * damping_ratio * r)
    return r, H


def response_reduction_ratio(
    mass_ratio: float,
    freq_ratio: Optional[float] = None,
    damping_ratio_tvmd: Optional[float] = None,
    damping_ratio_primary: float = 0.02,
) -> float:
    """
    応答低減率 η = peak(H_with_TVMD) / peak(H_without_TVMD) を計算する。

    η < 1 であれば応答が低減していることを示す。
    η の値が小さいほど制振効果が大きい。

    Parameters
    ----------
    mass_ratio : float
        μ = m_d / M
    freq_ratio : float or None
        f = ω_d / ω_s。None の場合は定点理論最適値を使用。
    damping_ratio_tvmd : float or None
        ζ_d。None の場合は定点理論最適値を使用。
    damping_ratio_primary : float
        ζ_s 主構造の減衰比（デフォルト 2%: RC 構造の一般値）

    Returns
    -------
    float
        応答低減率 η (0 < η < 1 が典型)
    """
    if freq_ratio is None or damping_ratio_tvmd is None:
        f_opt, z_opt = fixed_point_optimal(mass_ratio)
        if freq_ratio is None:
            freq_ratio = f_opt
        if damping_ratio_tvmd is None:
            damping_ratio_tvmd = z_opt

    # 制振なし
    _, H_bare = compute_frf_sdof(
        damping_ratio=damping_ratio_primary,
        r_min=0.01,
        r_max=3.0,
        n_points=2000,
    )
    # 制振あり
    _, H_tvmd = compute_frf_sdof_tvmd(
        mass_ratio=mass_ratio,
        freq_ratio=freq_ratio,
        damping_ratio_tvmd=damping_ratio_tvmd,
        damping_ratio_primary=damping_ratio_primary,
        r_min=0.01,
        r_max=3.0,
        n_points=2000,
    )

    peak_bare = float(np.max(H_bare))
    peak_tvmd = float(np.max(H_tvmd))

    if peak_bare <= 0:
        return 1.0
    return peak_tvmd / peak_bare


def compute_irdt_performance(
    params: IrdtParameters,
    damping_ratio_primary: float = 0.02,
) -> Dict[str, float]:
    """
    iRDT 設計結果の性能指標を計算する。

    Returns
    -------
    dict with keys:
        - eta: 応答低減率
        - peak_bare: 制振なしピーク倍率
        - peak_tvmd: 制振ありピーク倍率
        - reduction_pct: 応答低減率 [%] = (1 - η) * 100
    """
    mu = params.mass_ratio
    f = params.frequency_ratio
    zd = params.damping_ratio

    _, H_bare = compute_frf_sdof(damping_ratio=damping_ratio_primary, n_points=2000)
    _, H_tvmd = compute_frf_sdof_tvmd(
        mass_ratio=mu,
        freq_ratio=f,
        damping_ratio_tvmd=zd,
        damping_ratio_primary=damping_ratio_primary,
        n_points=2000,
    )

    peak_bare = float(np.max(H_bare))
    peak_tvmd = float(np.max(H_tvmd))
    eta = peak_tvmd / peak_bare if peak_bare > 0 else 1.0

    return {
        "eta": eta,
        "peak_bare": peak_bare,
        "peak_tvmd": peak_tvmd,
        "reduction_pct": (1.0 - eta) * 100.0,
    }


__all__ = [
    "IrdtParameters",
    "IrdtFloorAssignment",
    "IrdtPlacementPlan",
    "fixed_point_optimal",
    "design_irdt_sdof",
    "design_irdt_placement",
    "design_from_period_reader",
    "compute_frf_sdof_tvmd",
    "compute_frf_sdof",
    "response_reduction_ratio",
    "compute_irdt_performance",
]
