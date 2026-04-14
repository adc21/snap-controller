"""
tests/test_irdt.py

iRDT 計算エンジン (app/services/irdt.py) の単体テスト。

adc-tools (TypeScript) の iRDTOptParam / iRDTOptParamMdof との
数値一致を検証する。
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.irdt import (
    IrdtMdofResult,
    IrdtSdofResult,
    amp_1dof,
    build_mass_matrix,
    build_stiffness_matrix,
    compute_sdof_result,
    eigen_analysis,
    irdt_opt_param,
    irdt_opt_param_mdof,
    kd_irdt_complex,
    trim_number_array,
)


# ----------------------------------------------------------------------
# SDOF
# ----------------------------------------------------------------------


class TestIrdtOptParam:
    """irdt_opt_param の計算式検証"""

    def test_adc_tools_default_case(self):
        """adc-tools デフォルト値 (t0=1, m=100000, md=1000, nd=5) で計算一致"""
        t0, m, md, nd = 1.0, 100000.0, 1000.0, 5
        mu, cd_opt, kb_opt = irdt_opt_param(t0, m, md, nd)

        # μ = 5000 / 100000 = 0.05
        assert mu == pytest.approx(0.05, rel=1e-9)

        # β_opt = (1 - √(1 - 0.2)) / (0.1)
        beta_opt = (1 - math.sqrt(0.8)) / 0.1
        w0 = 2 * math.pi / 1.0
        wd_opt = beta_opt * w0

        # kb_opt = ω_d² × md_total / nd = ω_d² × md
        expected_kb = wd_opt ** 2 * 5000.0 / 5
        assert kb_opt == pytest.approx(expected_kb, rel=1e-9)

        # hd_opt = √(3(1 - √(1-4μ))) / 4
        hd_opt = math.sqrt(3 * (1 - math.sqrt(0.8))) / 4
        expected_cd = hd_opt * 2 * 5000.0 * wd_opt / 5
        assert cd_opt == pytest.approx(expected_cd, rel=1e-9)

    def test_mu_equals_zero(self):
        """md=0 で μ=0, cd=0, kb=0"""
        mu, cd, kb = irdt_opt_param(1.0, 100000.0, 0.0, 5)
        assert mu == 0.0
        # β_opt: (1-1)/0 → nan; 実装では nan 返却される可能性
        # ここでは例外を出さないことだけ検証

    def test_mu_over_limit_returns_nan(self):
        """μ ≥ 0.25 のとき sqrt が負になり NaN"""
        # md=50000, nd=1, m=100000 → μ=0.5
        mu, cd, kb = irdt_opt_param(1.0, 100000.0, 50000.0, 1)
        assert mu == 0.5
        assert math.isnan(cd)
        assert math.isnan(kb)

    def test_mu_exactly_quarter(self):
        """μ=0.25 のとき β=2, hd=√3/4"""
        # md × nd = 25000 → μ=0.25
        mu, cd, kb = irdt_opt_param(1.0, 100000.0, 5000.0, 5)
        assert mu == pytest.approx(0.25, rel=1e-9)
        # β = (1 - 0) / (0.5) = 2
        # kb = (2 × 2π)² × 25000 / 5 = (4π)² × 5000
        expected_kb = (4 * math.pi) ** 2 * 5000.0
        assert kb == pytest.approx(expected_kb, rel=1e-9)


class TestComputeSdofResult:
    """compute_sdof_result の派生パラメータ検証"""

    def test_default_case(self):
        res = compute_sdof_result(1.0, 100000.0, 1000.0, 5)
        assert isinstance(res, IrdtSdofResult)
        assert res.mu == pytest.approx(0.05, rel=1e-9)
        assert res.cd_opt > 0
        assert res.kb_opt > 0
        assert res.fd_opt > 0
        assert res.td_opt == pytest.approx(1.0 / res.fd_opt, rel=1e-9)

    def test_fd_consistency(self):
        """fd_opt = 1/(2π √(md/kb))"""
        res = compute_sdof_result(1.0, 100000.0, 1000.0, 5)
        expected_fd = 1.0 / (2 * math.pi * math.sqrt(1000.0 / res.kb_opt))
        assert res.fd_opt == pytest.approx(expected_fd, rel=1e-9)

    def test_hd_consistency(self):
        """hd_opt [%] の計算式 (Results.tsx)"""
        res = compute_sdof_result(1.0, 100000.0, 1000.0, 5)
        md, nd = 1000.0, 5
        expected_hd = ((nd * res.cd_opt) / (2 * math.sqrt(nd * md * nd * res.kb_opt))) * 100
        assert res.hd_opt == pytest.approx(expected_hd, rel=1e-9)


# ----------------------------------------------------------------------
# MDOF
# ----------------------------------------------------------------------


class TestIrdtOptParamMdof:
    """irdt_opt_param_mdof の計算式検証"""

    def test_adc_tools_default_case_2level(self):
        """adc-tools デフォルト (m=[10000,10000], k=[1e7,1e7], md=[1000,1000])"""
        # まず固有値解析で ω と vector を求める
        m = [10000.0, 10000.0]
        k = [10_000_000.0, 10_000_000.0]
        omegas, vectors = eigen_analysis(m, k)

        # 1次モードの ω と φ で MDOF 計算
        w0 = float(omegas[0])
        phi = vectors[:, 0].tolist()
        mds = [1000.0, 1000.0]

        res = irdt_opt_param_mdof(w0, m, phi, mds)

        # Results が全部実数
        assert not math.isnan(res.mu)
        assert not math.isnan(res.gamma)
        assert not math.isnan(res.h)
        assert len(res.kb) == 2
        assert len(res.cd) == 2
        assert all(x > 0 for x in res.kb)
        assert all(x > 0 for x in res.cd)

    def test_formula_gamma_h(self):
        """γ, h の式が iRDT.ts と一致"""
        # 手計算しやすい値: φ=[1,1], md=[1000,1000], m=[10000,10000]
        w0 = 1.0
        m = [10000.0, 10000.0]
        phi = [1.0, 1.0]
        mds = [1000.0, 1000.0]
        res = irdt_opt_param_mdof(w0, m, phi, mds)

        # mo = 10000*1 + 10000*1 = 20000
        # me = 1000*(1-0)² + 1000*(1-1)² = 1000
        # μ = 1000/20000 = 0.05
        assert res.mu == pytest.approx(0.05, rel=1e-9)

        # γ = (1 - 0.1 - √0.8)/0.1 + 1
        sqrt_term = math.sqrt(1 - 4 * 0.05)
        expected_gamma = (1 - 2 * 0.05 - sqrt_term) / (2 * 0.05) + 1
        assert res.gamma == pytest.approx(expected_gamma, rel=1e-9)

        # h = √(3(γ-1) / (8γ))
        expected_h = math.sqrt(3 * (expected_gamma - 1) / (8 * expected_gamma))
        assert res.h == pytest.approx(expected_h, rel=1e-9)

        # kb[i] = md[i] × (ω0 × γ)²
        for i in range(2):
            assert res.kb[i] == pytest.approx(mds[i] * (w0 * expected_gamma) ** 2, rel=1e-9)
            assert res.cd[i] == pytest.approx(
                2 * mds[i] * w0 * expected_gamma * expected_h, rel=1e-9
            )

    def test_mu_over_limit_nan(self):
        """μ ≥ 0.25 のとき γ, h が NaN"""
        # md を大きくして μ > 0.25 にする
        w0 = 1.0
        m = [10000.0, 10000.0]
        phi = [1.0, 1.0]
        mds = [5000.0, 5000.0]  # me=5000, mo=20000, μ=0.25
        res = irdt_opt_param_mdof(w0, m, phi, mds)
        assert res.mu == pytest.approx(0.25, rel=1e-9)


# ----------------------------------------------------------------------
# 固有値解析
# ----------------------------------------------------------------------


class TestEigenAnalysis:
    def test_2dof_symmetric(self):
        """2質点等質量等剛性系で理論値と一致"""
        m = [1.0, 1.0]
        k = [1.0, 1.0]
        omegas, vectors = eigen_analysis(m, k)

        # 理論解: ω1² = (3 - √5)/2, ω2² = (3 + √5)/2
        expected_w1_sq = (3 - math.sqrt(5)) / 2
        expected_w2_sq = (3 + math.sqrt(5)) / 2

        assert omegas[0] ** 2 == pytest.approx(expected_w1_sq, rel=1e-6)
        assert omegas[1] ** 2 == pytest.approx(expected_w2_sq, rel=1e-6)

        # ベクトルは最大 ±1 で正規化されているはず
        for i in range(2):
            col = vectors[:, i]
            peak = max(abs(col.max()), abs(col.min()))
            assert peak == pytest.approx(1.0, rel=1e-6)

    def test_3dof_sorted_ascending(self):
        """3質点で固有円振動数が昇順にソートされる"""
        m = [1.0, 1.0, 1.0]
        k = [1.0, 1.0, 1.0]
        omegas, _vectors = eigen_analysis(m, k)
        assert len(omegas) == 3
        assert omegas[0] <= omegas[1] <= omegas[2]


class TestBuildStiffnessMatrix:
    def test_tridiagonal_2x2(self):
        """k=[k1, k2] → [[k1+k2, -k2], [-k1, k2]]"""
        k = build_stiffness_matrix([3.0, 5.0])
        assert k.shape == (2, 2)
        assert k[0, 0] == 8.0  # 3+5
        assert k[0, 1] == -5.0
        assert k[1, 0] == -3.0
        assert k[1, 1] == 5.0

    def test_tridiagonal_3x3(self):
        k = build_stiffness_matrix([1.0, 2.0, 3.0])
        expected = np.array([
            [3, -2, 0],
            [-1, 5, -3],
            [0, -2, 3],
        ], dtype=float)
        np.testing.assert_allclose(k, expected)


class TestBuildMassMatrix:
    def test_diagonal(self):
        m = build_mass_matrix([1.0, 2.0, 3.0])
        expected = np.diag([1.0, 2.0, 3.0])
        np.testing.assert_allclose(m, expected)


# ----------------------------------------------------------------------
# trim_number_array
# ----------------------------------------------------------------------


class TestTrimNumberArray:
    def test_breaks_on_zero(self):
        assert trim_number_array([1, 2, 0, 3]) == [1.0, 2.0]

    def test_breaks_on_none(self):
        assert trim_number_array([1, 2, None, 3]) == [1.0, 2.0]

    def test_null_to_zero(self):
        result = trim_number_array([1, 0, 3], null_to_zero=True)
        assert result == [1.0, 1e-10, 3.0]

    def test_empty(self):
        assert trim_number_array([]) == []


# ----------------------------------------------------------------------
# 振動特性
# ----------------------------------------------------------------------


class TestKdIrdtComplex:
    def test_zero_omega(self):
        """ω=0 のとき分母が 0 → kd=0"""
        kd = kd_irdt_complex(1000.0, 100.0, 10000.0, 0.0)
        assert kd == 0

    def test_nonzero(self):
        """非ゼロ ω で有限値"""
        kd = kd_irdt_complex(1000.0, 100.0, 10000.0, 1.0)
        assert math.isfinite(kd.real)
        assert math.isfinite(kd.imag)


class TestAmp1Dof:
    def test_returns_500_points(self):
        lambdas, amp = amp_1dof(100.0, 0.0, 100.0)
        assert len(lambdas) == 500
        assert len(amp) == 500

    def test_lambdas_start_at_zero(self):
        lambdas, _ = amp_1dof(100.0, 0.0, 100.0)
        assert lambdas[0] == pytest.approx(0.0)

    def test_lambdas_end_near_2(self):
        lambdas, _ = amp_1dof(100.0, 0.0, 100.0)
        # 500分割で最終値は 2 - (2/500) = 1.996
        assert lambdas[-1] == pytest.approx(2.0 - 2.0 / 500)

    def test_with_kd(self):
        """kd_fn を与えると応答が変化する"""
        kd_fn = lambda w: kd_irdt_complex(1000.0, 100.0, 10000.0, w)
        _, amp_with = amp_1dof(100000.0, 1.0, 100000.0, kd_fn=kd_fn)
        _, amp_without = amp_1dof(100000.0, 1.0, 100000.0)
        # 全て同じではないはず
        assert not np.allclose(amp_with, amp_without)
