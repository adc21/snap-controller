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


def tvmd_optimal_damped(
    mass_ratio: float,
    damping_ratio_primary: float = 0.0,
) -> tuple[float, float]:
    """
    減衰を有する主構造に対する TVMD 最適同調パラメータ（拡張定点理論）。

    Den Hartog の定点理論は無減衰主構造を仮定するが、実構造には固有減衰がある。
    Asami & Nishihara (2002) および Ikago et al. (2012) の知見に基づき、
    主構造減衰比 ζ_s を考慮した補正式を適用する。

    補正式（近似）:
        f_opt  ≈ f_DH × (1 − ζ_s × √(μ / (1 + μ)))
        ζ_opt  ≈ ζ_DH + ζ_s / (2√(1 + μ))

    ここで f_DH, ζ_DH は Den Hartog の無減衰最適値。

    Parameters
    ----------
    mass_ratio : float
        μ = m_d / M_s (> 0)
    damping_ratio_primary : float
        ζ_s = C / (2Mω_s)  主構造の減衰比（0 のとき Den Hartog と一致）

    Returns
    -------
    (f_opt, zeta_opt)

    References
    ----------
    Asami T, Nishihara O (2002) "Closed-form exact solution to H∞
    optimization of dynamic vibration absorbers", JSME Int J, Ser C.
    Ikago K, Saito K, Inoue N (2012) "Seismic control of SDOF structure
    using tuned viscous mass damper", EESD, 41(3): 453-474.
    """
    if mass_ratio <= 0:
        raise ValueError(f"mass_ratio must be positive, got {mass_ratio}")
    if damping_ratio_primary < 0:
        raise ValueError("damping_ratio_primary must be non-negative")

    f_dh, z_dh = fixed_point_optimal(mass_ratio)

    if damping_ratio_primary == 0.0:
        return f_dh, z_dh

    zs = damping_ratio_primary
    mu = mass_ratio

    # 周波数比の補正: 主構造に減衰があると最適同調は若干低周波側にシフト
    f_opt = f_dh * (1.0 - zs * math.sqrt(mu / (1.0 + mu)))

    # 減衰比の補正: 主構造減衰が加わる分、TVMD に必要な減衰比が増加
    z_opt = z_dh + zs / (2.0 * math.sqrt(1.0 + mu))

    return f_opt, z_opt


@dataclass
class MdofModePerformance:
    """多モード性能チェック結果。"""

    mode: int
    period: float
    modal_mass: float
    effective_mass_ratio: float
    eta: float  # 応答低減率
    peak_bare: float
    peak_controlled: float
    is_target: bool = False


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


# ---------------------------------------------------------------------------
# 拡張定点理論による設計 (減衰主構造対応)
# ---------------------------------------------------------------------------


def design_irdt_sdof_extended(
    primary_mass: float,
    primary_period: float,
    mass_ratio: float,
    damping_ratio_primary: float = 0.0,
    note: str = "",
) -> IrdtParameters:
    """
    減衰を有する主構造に対する iRDT 最適パラメータを算出します。

    damping_ratio_primary > 0 の場合、tvmd_optimal_damped() による
    補正式を適用します。damping_ratio_primary = 0 のとき
    design_irdt_sdof() と同じ結果を返します。

    Parameters
    ----------
    primary_mass : float
        主構造の等価質量 M_s [kg]
    primary_period : float
        主構造の固有周期 T_s [s]
    mass_ratio : float
        質量比 μ = m_d / M_s
    damping_ratio_primary : float
        主構造の減衰比 ζ_s
    note : str
        備考
    """
    if primary_mass <= 0:
        raise ValueError("primary_mass must be positive")
    if primary_period <= 0:
        raise ValueError("primary_period must be positive")

    omega_s = 2.0 * math.pi / primary_period

    if damping_ratio_primary > 0:
        f_opt, zeta_opt = tvmd_optimal_damped(mass_ratio, damping_ratio_primary)
    else:
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
# 感度解析 (sensitivity analysis)
# ---------------------------------------------------------------------------


def sensitivity_analysis(
    primary_mass: float,
    primary_period: float,
    base_mass_ratio: float,
    damping_ratio_primary: float = 0.02,
    variation_pct: float = 20.0,
    n_steps: int = 5,
) -> Dict[str, List]:
    """
    質量比 μ を ±variation_pct% 変動させたときの応答低減率の変化を計算する。

    Parameters
    ----------
    primary_mass : float
        主構造の等価質量 M_s [kg]
    primary_period : float
        主構造の固有周期 T_s [s]
    base_mass_ratio : float
        基準質量比 μ
    damping_ratio_primary : float
        主構造の減衰比 ζ_s
    variation_pct : float
        変動幅 [%]（デフォルト ±20%）
    n_steps : int
        片側ステップ数（全 2*n_steps+1 点）

    Returns
    -------
    dict with keys:
        - mu_values: List[float] — 質量比の値
        - eta_values: List[float] — 応答低減率
        - peak_bare_values: List[float] — 制振なしピーク
        - peak_tvmd_values: List[float] — 制振ありピーク
        - reduction_pct_values: List[float] — 応答低減率 [%]
        - base_index: int — 基準値のインデックス
    """
    factor_lo = 1.0 - variation_pct / 100.0
    factor_hi = 1.0 + variation_pct / 100.0

    mu_values = []
    eta_values = []
    peak_bare_values = []
    peak_tvmd_values = []
    reduction_pct_values = []

    mu_range = np.linspace(
        base_mass_ratio * factor_lo,
        base_mass_ratio * factor_hi,
        2 * n_steps + 1,
    )

    for mu in mu_range:
        if mu <= 0:
            continue
        params = design_irdt_sdof_extended(
            primary_mass=primary_mass,
            primary_period=primary_period,
            mass_ratio=float(mu),
            damping_ratio_primary=damping_ratio_primary,
        )
        perf = compute_irdt_performance(params, damping_ratio_primary)
        mu_values.append(float(mu))
        eta_values.append(perf["eta"])
        peak_bare_values.append(perf["peak_bare"])
        peak_tvmd_values.append(perf["peak_tvmd"])
        reduction_pct_values.append(perf["reduction_pct"])

    base_index = n_steps  # 中央が基準値

    return {
        "mu_values": mu_values,
        "eta_values": eta_values,
        "peak_bare_values": peak_bare_values,
        "peak_tvmd_values": peak_tvmd_values,
        "reduction_pct_values": reduction_pct_values,
        "base_index": base_index,
    }


@dataclass
class MultiParamSensitivityEntry:
    """多パラメータ感度解析の1エントリ。"""
    param_name: str
    param_label: str
    variation_values: List[float]
    eta_values: List[float]
    reduction_pct_values: List[float]
    base_index: int


def multi_param_sensitivity_analysis(
    primary_mass: float,
    primary_period: float,
    base_mass_ratio: float,
    damping_ratio_primary: float = 0.02,
    variation_pct: float = 20.0,
    n_steps: int = 5,
) -> List[MultiParamSensitivityEntry]:
    """
    複数パラメータ（μ, ζ_d, f_opt）の感度を同時に解析する。

    各パラメータを独立に ±variation_pct% 変動させ、応答低減率への影響を評価。
    設計者はどのパラメータが性能に最も影響するか把握できる。

    Parameters
    ----------
    primary_mass : float
        主構造の等価質量 M_s [kg]
    primary_period : float
        主構造の固有周期 T_s [s]
    base_mass_ratio : float
        基準質量比 μ
    damping_ratio_primary : float
        主構造の減衰比 ζ_s
    variation_pct : float
        変動幅 [%]（デフォルト ±20%）
    n_steps : int
        片側ステップ数（全 2*n_steps+1 点）

    Returns
    -------
    list of MultiParamSensitivityEntry
        各パラメータの感度解析結果。
    """
    omega_s = 2.0 * math.pi / primary_period

    # 基準設計値を取得
    base_params = design_irdt_sdof_extended(
        primary_mass=primary_mass,
        primary_period=primary_period,
        mass_ratio=base_mass_ratio,
        damping_ratio_primary=damping_ratio_primary,
    )
    base_f_opt = base_params.frequency_ratio
    base_zeta_d = base_params.damping_ratio

    factor_lo = 1.0 - variation_pct / 100.0
    factor_hi = 1.0 + variation_pct / 100.0
    n_total = 2 * n_steps + 1

    results: List[MultiParamSensitivityEntry] = []

    # --- (1) μ (質量比) の感度 ---
    mu_range = np.linspace(
        base_mass_ratio * factor_lo, base_mass_ratio * factor_hi, n_total,
    )
    mu_eta = []
    mu_red = []
    mu_vals = []
    for mu in mu_range:
        if mu <= 0:
            continue
        params = design_irdt_sdof_extended(
            primary_mass=primary_mass,
            primary_period=primary_period,
            mass_ratio=float(mu),
            damping_ratio_primary=damping_ratio_primary,
        )
        perf = compute_irdt_performance(params, damping_ratio_primary)
        mu_vals.append(float(mu))
        mu_eta.append(perf["eta"])
        mu_red.append(perf["reduction_pct"])

    results.append(MultiParamSensitivityEntry(
        param_name="mu", param_label="質量比 μ",
        variation_values=mu_vals, eta_values=mu_eta,
        reduction_pct_values=mu_red, base_index=n_steps,
    ))

    # --- (2) ζ_d (ダンパー減衰比) の感度 ---
    zeta_range = np.linspace(
        base_zeta_d * factor_lo, base_zeta_d * factor_hi, n_total,
    )
    zeta_eta = []
    zeta_red = []
    zeta_vals = []
    for zd in zeta_range:
        if zd <= 0:
            continue
        _, H_bare = compute_frf_sdof(damping_ratio=damping_ratio_primary, n_points=2000)
        _, H_tvmd = compute_frf_sdof_tvmd(
            mass_ratio=base_mass_ratio,
            freq_ratio=base_f_opt,
            damping_ratio_tvmd=float(zd),
            damping_ratio_primary=damping_ratio_primary,
            n_points=2000,
        )
        peak_bare = float(np.max(H_bare))
        peak_tvmd = float(np.max(H_tvmd))
        eta = peak_tvmd / peak_bare if peak_bare > 0 else 1.0
        zeta_vals.append(float(zd))
        zeta_eta.append(eta)
        zeta_red.append((1.0 - eta) * 100.0)

    results.append(MultiParamSensitivityEntry(
        param_name="zeta_d", param_label="ダンパー減衰比 ζ_d",
        variation_values=zeta_vals, eta_values=zeta_eta,
        reduction_pct_values=zeta_red, base_index=n_steps,
    ))

    # --- (3) f_opt (同調比) の感度 ---
    f_range = np.linspace(
        base_f_opt * factor_lo, base_f_opt * factor_hi, n_total,
    )
    f_eta = []
    f_red = []
    f_vals = []
    for f in f_range:
        if f <= 0:
            continue
        _, H_bare = compute_frf_sdof(damping_ratio=damping_ratio_primary, n_points=2000)
        _, H_tvmd = compute_frf_sdof_tvmd(
            mass_ratio=base_mass_ratio,
            freq_ratio=float(f),
            damping_ratio_tvmd=base_zeta_d,
            damping_ratio_primary=damping_ratio_primary,
            n_points=2000,
        )
        peak_bare = float(np.max(H_bare))
        peak_tvmd = float(np.max(H_tvmd))
        eta = peak_tvmd / peak_bare if peak_bare > 0 else 1.0
        f_vals.append(float(f))
        f_eta.append(eta)
        f_red.append((1.0 - eta) * 100.0)

    results.append(MultiParamSensitivityEntry(
        param_name="f_opt", param_label="同調比 f_opt",
        variation_values=f_vals, eta_values=f_eta,
        reduction_pct_values=f_red, base_index=n_steps,
    ))

    return results


# ---------------------------------------------------------------------------
# MDOF 多モード性能チェック
# ---------------------------------------------------------------------------


def mdof_multimode_check(
    masses: Sequence[float],
    mode_shapes: Dict[int, Sequence[float]],
    periods: Dict[int, float],
    target_mode: int,
    total_mass_ratio: float,
    damping_ratio_primary: float = 0.02,
    distribution: str = "interstory",
) -> List[MdofModePerformance]:
    """
    TVMD を配置した際の多モード性能チェック。

    対象モード（target_mode）に対して最適設計された TVMD が、
    他のモードに対してどの程度の効果を持つかを評価する。

    Parameters
    ----------
    masses : Sequence[float]
        各層の質量 [kg]
    mode_shapes : Dict[int, Sequence[float]]
        各モードのモード形 {mode_no: [φ_1, φ_2, ...]}
    periods : Dict[int, float]
        各モードの固有周期 {mode_no: T}
    target_mode : int
        設計対象モード番号
    total_mass_ratio : float
        総質量比 μ_tot
    damping_ratio_primary : float
        主構造の減衰比 ζ_s
    distribution : str
        配分戦略

    Returns
    -------
    List[MdofModePerformance]
        各モードの性能チェック結果（target_mode を含む）
    """
    if target_mode not in mode_shapes or target_mode not in periods:
        raise ValueError(f"target_mode {target_mode} not found in mode data")

    total_mass = sum(masses)
    total_inertance = total_mass_ratio * total_mass

    # 対象モードで TVMD を最適設計
    target_shape = _normalize_mode_shape(mode_shapes[target_mode])
    target_modal_mass = _modal_mass(masses, target_shape)
    target_period = periods[target_mode]
    mu_modal_target = total_inertance / target_modal_mass if target_modal_mass > 0 else total_mass_ratio
    f_opt, z_opt = tvmd_optimal_damped(mu_modal_target, damping_ratio_primary)

    results: List[MdofModePerformance] = []

    for mode_no in sorted(mode_shapes.keys()):
        if mode_no not in periods:
            continue

        shape = _normalize_mode_shape(mode_shapes[mode_no])
        modal_mass = _modal_mass(masses, shape)
        period = periods[mode_no]

        # このモードに対する有効質量比
        # TVMD の同調は target_mode で設計されているが、
        # 各モードに対する等価的な効果を FRF で評価
        mu_eff = total_inertance / modal_mass if modal_mass > 0 else 0.0

        # 対象モードで最適化された f_opt, z_opt をそのまま使い、
        # 各モードの応答を評価（非対象モードでは同調が外れるため効果が低い）
        omega_j = 2.0 * math.pi / period
        omega_target = 2.0 * math.pi / target_period

        # 周波数比を対象モード基準から各モード基準に変換
        # f_opt は ω_d / ω_target で設計されているので、
        # モード j に対する実効的な周波数比は f_eff = (f_opt * ω_target) / ω_j
        f_eff = f_opt * omega_target / omega_j

        _, H_bare = compute_frf_sdof(
            damping_ratio=damping_ratio_primary, n_points=2000,
        )
        _, H_ctrl = compute_frf_sdof_tvmd(
            mass_ratio=mu_eff,
            freq_ratio=f_eff,
            damping_ratio_tvmd=z_opt,
            damping_ratio_primary=damping_ratio_primary,
            n_points=2000,
        )

        peak_bare = float(np.max(H_bare))
        peak_ctrl = float(np.max(H_ctrl))
        eta = peak_ctrl / peak_bare if peak_bare > 0 else 1.0

        results.append(MdofModePerformance(
            mode=mode_no,
            period=period,
            modal_mass=modal_mass,
            effective_mass_ratio=mu_eff,
            eta=eta,
            peak_bare=peak_bare,
            peak_controlled=peak_ctrl,
            is_target=(mode_no == target_mode),
        ))

    return results


__all__ = [
    "IrdtParameters",
    "IrdtFloorAssignment",
    "IrdtPlacementPlan",
    "MdofModePerformance",
    "fixed_point_optimal",
    "tvmd_optimal_damped",
    "design_irdt_sdof",
    "design_irdt_sdof_extended",
    "design_irdt_placement",
    "design_from_period_reader",
    "compute_frf_sdof_tvmd",
    "compute_frf_sdof",
    "response_reduction_ratio",
    "compute_irdt_performance",
    "sensitivity_analysis",
    "MultiParamSensitivityEntry",
    "multi_param_sensitivity_analysis",
    "mdof_multimode_check",
]
