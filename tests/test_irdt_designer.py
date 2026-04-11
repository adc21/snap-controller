"""Tests for app.services.irdt_designer (定点理論 iRDT 設計)."""

from __future__ import annotations

import math

import pytest

import numpy as np

from app.services.irdt_designer import (
    compute_frf_sdof,
    compute_frf_sdof_tvmd,
    compute_irdt_performance,
    design_irdt_placement,
    design_irdt_sdof,
    fixed_point_optimal,
    response_reduction_ratio,
)


class TestFixedPointOptimal:
    def test_mu_tends_to_zero(self):
        f, z = fixed_point_optimal(1e-6)
        assert f == pytest.approx(1.0, rel=1e-4)
        assert z == pytest.approx(0.0, abs=1e-3)

    def test_mu_5percent(self):
        # Den Hartog の標準値
        f, z = fixed_point_optimal(0.05)
        assert f == pytest.approx(1.0 / 1.05, rel=1e-6)
        # ζ = √(3*0.05 / (8 * 1.05^3)) = 0.1336...
        expected_z = math.sqrt(0.15 / (8 * 1.05 ** 3))
        assert z == pytest.approx(expected_z, rel=1e-6)

    def test_mu_10percent(self):
        f, z = fixed_point_optimal(0.10)
        assert f == pytest.approx(1.0 / 1.10, rel=1e-6)
        expected_z = math.sqrt(0.30 / (8 * 1.10 ** 3))
        assert z == pytest.approx(expected_z, rel=1e-6)

    def test_negative_mass_ratio_raises(self):
        with pytest.raises(ValueError):
            fixed_point_optimal(-0.1)


class TestDesignIrdtSdof:
    def test_basic_design(self):
        p = design_irdt_sdof(primary_mass=1000.0, primary_period=1.0, mass_ratio=0.05)
        omega_s = 2 * math.pi
        assert p.target_omega == pytest.approx(omega_s)
        assert p.inertance == pytest.approx(50.0)
        assert p.frequency_ratio == pytest.approx(1.0 / 1.05)
        # k_b = m_d * (f_opt * ω_s)^2
        expected_kb = 50.0 * (omega_s / 1.05) ** 2
        assert p.support_stiffness == pytest.approx(expected_kb)
        # c_d = 2 ζ m_d ω_d
        omega_d = omega_s / 1.05
        expected_cd = 2 * p.damping_ratio * 50.0 * omega_d
        assert p.damping == pytest.approx(expected_cd)

    def test_zero_mass_raises(self):
        with pytest.raises(ValueError):
            design_irdt_sdof(primary_mass=0.0, primary_period=1.0, mass_ratio=0.05)

    def test_zero_period_raises(self):
        with pytest.raises(ValueError):
            design_irdt_sdof(primary_mass=1000.0, primary_period=0.0, mass_ratio=0.05)


class TestDesignIrdtPlacement:
    def test_3floor_linear_mode(self):
        masses = [1000.0, 1000.0, 1000.0]
        mode = [1.0 / 3, 2.0 / 3, 1.0]  # 厳密線形モード
        plan = design_irdt_placement(
            masses=masses,
            mode_shape=mode,
            target_period=0.6,
            total_mass_ratio=0.06,
            distribution="interstory",
        )
        assert len(plan.floor_plan) == 3
        total_inertance = sum(f.inertance for f in plan.floor_plan)
        assert total_inertance == pytest.approx(0.06 * 3000.0)
        # 線形モードでは Δφ = 1/3 一定 -> 各層均等
        vals = [f.inertance for f in plan.floor_plan]
        assert max(vals) == pytest.approx(min(vals), rel=1e-9)

    def test_amplitude_distribution_concentrates_top(self):
        masses = [1000.0, 1000.0, 1000.0]
        mode = [0.33, 0.66, 1.00]
        plan = design_irdt_placement(
            masses=masses,
            mode_shape=mode,
            target_period=0.5,
            total_mass_ratio=0.05,
            distribution="amplitude",
        )
        # amplitude 分布 => 最上階 (φ=1.0) に最大
        assert plan.floor_plan[2].inertance > plan.floor_plan[0].inertance

    def test_uniform_distribution_equal(self):
        masses = [1000.0] * 4
        mode = [0.25, 0.5, 0.75, 1.0]
        plan = design_irdt_placement(
            masses=masses,
            mode_shape=mode,
            target_period=0.8,
            total_mass_ratio=0.08,
            distribution="uniform",
        )
        vals = [f.inertance for f in plan.floor_plan]
        assert max(vals) == pytest.approx(min(vals), rel=1e-9)
        assert sum(vals) == pytest.approx(0.08 * 4000.0)

    def test_mismatched_length_raises(self):
        with pytest.raises(ValueError):
            design_irdt_placement(
                masses=[1000.0, 1000.0],
                mode_shape=[1.0],
                target_period=1.0,
                total_mass_ratio=0.05,
            )

    def test_unknown_distribution_raises(self):
        with pytest.raises(ValueError):
            design_irdt_placement(
                masses=[1000.0],
                mode_shape=[1.0],
                target_period=1.0,
                total_mass_ratio=0.05,
                distribution="bogus",
            )

    def test_support_stiffness_scales_with_inertance(self):
        masses = [1000.0, 1000.0, 1000.0]
        mode = [0.3, 0.7, 1.0]
        plan = design_irdt_placement(
            masses=masses,
            mode_shape=mode,
            target_period=0.6,
            total_mass_ratio=0.06,
            distribution="amplitude",
        )
        # 各層で k_b / m_d = ω_d^2 = 一定
        ratios = [f.support_stiffness / f.inertance for f in plan.floor_plan if f.inertance > 0]
        assert max(ratios) == pytest.approx(min(ratios), rel=1e-9)


class TestComputeFrfSdof:
    def test_peak_at_resonance_undamped(self):
        r, H = compute_frf_sdof(damping_ratio=0.0, n_points=5000)
        # 無減衰 SDOF は r=1 で発散 → ピークは r≈1 付近で非常に大きい
        idx_peak = np.argmax(H)
        assert r[idx_peak] == pytest.approx(1.0, abs=0.05)
        assert H[idx_peak] > 100  # 十分大きい

    def test_peak_with_damping(self):
        r, H = compute_frf_sdof(damping_ratio=0.02, n_points=5000)
        # ζ=0.02 → ピーク ≈ 1/(2ζ) = 25
        peak = np.max(H)
        assert peak == pytest.approx(25.0, rel=0.05)

    def test_static_response(self):
        # r→0 で |H| → 1
        r, H = compute_frf_sdof(damping_ratio=0.05)
        assert H[0] == pytest.approx(1.0, rel=0.01)


class TestComputeFrfSdofTvmd:
    def test_two_peaks(self):
        """TVMD 付き系は共振点が 2 つに分裂する。"""
        r, H = compute_frf_sdof_tvmd(
            mass_ratio=0.05,
            freq_ratio=1.0 / 1.05,
            damping_ratio_tvmd=0.0,
            damping_ratio_primary=0.0,
            n_points=5000,
        )
        # 無減衰 TVMD → 2 つの急峻なピーク
        # ピークの位置を見つける（局所最大）
        local_max = (H[1:-1] > H[:-2]) & (H[1:-1] > H[2:])
        n_peaks = np.sum(local_max & (H[1:-1] > 5.0))
        assert n_peaks >= 2

    def test_optimal_reduces_peak(self):
        """最適パラメータで制振なしよりピークが下がる。"""
        f_opt, z_opt = fixed_point_optimal(0.05)
        _, H_tvmd = compute_frf_sdof_tvmd(
            mass_ratio=0.05,
            freq_ratio=f_opt,
            damping_ratio_tvmd=z_opt,
            damping_ratio_primary=0.02,
            n_points=2000,
        )
        _, H_bare = compute_frf_sdof(damping_ratio=0.02, n_points=2000)
        assert np.max(H_tvmd) < np.max(H_bare)

    def test_static_response(self):
        """r→0 で |H| → 1。"""
        r, H = compute_frf_sdof_tvmd(
            mass_ratio=0.05,
            freq_ratio=0.95,
            damping_ratio_tvmd=0.10,
            n_points=1000,
        )
        assert H[0] == pytest.approx(1.0, rel=0.05)


class TestResponseReductionRatio:
    def test_eta_less_than_one(self):
        """最適パラメータで η < 1（応答低減される）。"""
        eta = response_reduction_ratio(0.05, damping_ratio_primary=0.02)
        assert 0 < eta < 1.0

    def test_larger_mu_gives_lower_eta(self):
        """質量比が大きいほど η が小さい（制振効果が大きい）。"""
        eta_small = response_reduction_ratio(0.02, damping_ratio_primary=0.02)
        eta_large = response_reduction_ratio(0.10, damping_ratio_primary=0.02)
        assert eta_large < eta_small

    def test_uses_optimal_by_default(self):
        """freq_ratio と damping_ratio_tvmd を省略すると最適値を使う。"""
        f_opt, z_opt = fixed_point_optimal(0.05)
        eta_auto = response_reduction_ratio(0.05, damping_ratio_primary=0.02)
        eta_explicit = response_reduction_ratio(
            0.05,
            freq_ratio=f_opt,
            damping_ratio_tvmd=z_opt,
            damping_ratio_primary=0.02,
        )
        assert eta_auto == pytest.approx(eta_explicit, rel=1e-6)


class TestComputeIrdtPerformance:
    def test_returns_all_keys(self):
        p = design_irdt_sdof(primary_mass=1e6, primary_period=1.0, mass_ratio=0.05)
        perf = compute_irdt_performance(p)
        assert "eta" in perf
        assert "peak_bare" in perf
        assert "peak_tvmd" in perf
        assert "reduction_pct" in perf

    def test_reduction_positive(self):
        p = design_irdt_sdof(primary_mass=1e6, primary_period=1.0, mass_ratio=0.05)
        perf = compute_irdt_performance(p)
        assert perf["reduction_pct"] > 0
        assert perf["eta"] < 1.0
        assert perf["peak_tvmd"] < perf["peak_bare"]
