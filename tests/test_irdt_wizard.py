"""
tests/test_irdt_wizard.py
=========================

IrdtWizardDialog のロジックテスト。

UI（PySide6）部分はヘッドレス環境でテストできないため、
ウィザードが使用するサービス層（irdt_designer）の
ロジックを中心にテストします。

テスト内容:
- 定点理論の最適値が数学的に正しいか
- 各分布戦略で配置計画が正しく生成されるか
- モード形状近似の実装が正しいか（IrdtWizardDialog._step3 相当）
- AnalysisCase が正しく構築されるか（ウィザードの完了ロジック）
"""

from __future__ import annotations

import math
import sys
import os
from typing import List
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# PySide6 mock — QT が利用不能な CI 環境でもインポートを通す
# (test_optimizer.py と同じパターン)
# ---------------------------------------------------------------------------
try:
    from PySide6.QtCore import QObject  # noqa: F401
except (ImportError, OSError):
    _mock_qtcore = MagicMock()

    class _FakeSignal:
        def __init__(self, *args, **kwargs): pass
        def emit(self, *a, **kw): pass
        def connect(self, *a, **kw): pass

    _mock_qtcore.Signal = _FakeSignal
    _mock_qtcore.QObject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
    _mock_qtcore.QThread = type("QThread", (), {
        "__init__": lambda self, *a, **kw: None,
        "start": lambda self: None,
        "isRunning": lambda self: False,
        "wait": lambda self, *a: None,
        "terminate": lambda self: None,
    })
    sys.modules.setdefault("PySide6", MagicMock())
    sys.modules["PySide6.QtCore"] = _mock_qtcore
    sys.modules.setdefault("PySide6.QtWidgets", MagicMock())
    sys.modules.setdefault("PySide6.QtGui", MagicMock())

from app.services.irdt_designer import (
    IrdtParameters,
    IrdtPlacementPlan,
    fixed_point_optimal,
    design_irdt_sdof,
    design_irdt_placement,
)
from app.models.analysis_case import AnalysisCase


# ---------------------------------------------------------------------------
# ヘルパー: モード形状近似
# ---------------------------------------------------------------------------

def _linear_mode_shape(n: int) -> List[float]:
    phi = [(k + 1) / n for k in range(n)]
    amax = max(abs(v) for v in phi)
    return [v / amax for v in phi]


def _sinusoidal_mode_shape(n: int) -> List[float]:
    phi = [math.sin((2 * (k + 1) - 1) * math.pi / (2 * n + 1)) for k in range(n)]
    amax = max(abs(v) for v in phi)
    return [v / amax for v in phi]


def _uniform_mode_shape(n: int) -> List[float]:
    return [1.0] * n


# ---------------------------------------------------------------------------
# 定点理論テスト
# ---------------------------------------------------------------------------

class TestFixedPointOptimal:
    def test_typical_mu_005(self):
        f_opt, zeta_opt = fixed_point_optimal(0.05)
        # f_opt = 1 / (1 + 0.05) ≈ 0.9524
        assert abs(f_opt - 1.0 / 1.05) < 1e-9
        assert f_opt > 0
        assert zeta_opt > 0

    def test_typical_mu_010(self):
        f_opt, zeta_opt = fixed_point_optimal(0.10)
        assert abs(f_opt - 1.0 / 1.10) < 1e-9
        expected_zeta = math.sqrt(3 * 0.10 / (8 * (1.10) ** 3))
        assert abs(zeta_opt - expected_zeta) < 1e-9

    def test_f_opt_decreases_with_larger_mu(self):
        f1, _ = fixed_point_optimal(0.02)
        f2, _ = fixed_point_optimal(0.10)
        f3, _ = fixed_point_optimal(0.20)
        assert f1 > f2 > f3

    def test_zeta_opt_increases_with_mu(self):
        _, z1 = fixed_point_optimal(0.01)
        _, z2 = fixed_point_optimal(0.10)
        assert z2 > z1

    def test_invalid_mu_raises(self):
        with pytest.raises(ValueError):
            fixed_point_optimal(0.0)
        with pytest.raises(ValueError):
            fixed_point_optimal(-0.05)


# ---------------------------------------------------------------------------
# SDOF 設計テスト
# ---------------------------------------------------------------------------

class TestDesignIrdtSdof:
    def test_basic_design(self):
        # 1000 ton, T=2.0s, μ=0.05
        params = design_irdt_sdof(
            primary_mass=1.0e6,  # 1000 ton in kg
            primary_period=2.0,
            mass_ratio=0.05,
        )
        assert isinstance(params, IrdtParameters)
        assert params.inertance == pytest.approx(0.05 * 1.0e6, rel=1e-6)
        assert params.support_stiffness > 0
        assert params.damping > 0

    def test_mass_ratio_consistency(self):
        M = 5.0e5
        mu = 0.08
        params = design_irdt_sdof(M, 1.5, mu)
        assert params.inertance == pytest.approx(mu * M, rel=1e-6)

    def test_frequency_ratio_from_den_hartog(self):
        params = design_irdt_sdof(1.0e6, 2.0, 0.05)
        f_expected = 1.0 / (1.0 + 0.05)
        assert params.frequency_ratio == pytest.approx(f_expected, rel=1e-6)

    def test_invalid_inputs(self):
        with pytest.raises(ValueError):
            design_irdt_sdof(0, 2.0, 0.05)
        with pytest.raises(ValueError):
            design_irdt_sdof(1e6, 0.0, 0.05)

    def test_kb_formula(self):
        """k_b = m_d * (f_opt * ω_s)^2"""
        M = 1.0e6
        T = 2.0
        mu = 0.05
        params = design_irdt_sdof(M, T, mu)
        omega_s = 2 * math.pi / T
        omega_d = params.frequency_ratio * omega_s
        k_b_expected = params.inertance * omega_d ** 2
        assert params.support_stiffness == pytest.approx(k_b_expected, rel=1e-6)


# ---------------------------------------------------------------------------
# 多層配置テスト
# ---------------------------------------------------------------------------

class TestDesignIrdtPlacement:
    @pytest.fixture
    def five_floor_masses(self):
        return [1000.0e3] * 5  # 1000 ton per floor × 5

    def test_interstory_distribution(self, five_floor_masses):
        phi = _sinusoidal_mode_shape(5)
        plan = design_irdt_placement(
            masses=five_floor_masses,
            mode_shape=phi,
            target_period=1.5,
            total_mass_ratio=0.05,
            distribution="interstory",
        )
        assert isinstance(plan, IrdtPlacementPlan)
        assert len(plan.floor_plan) == 5
        # 総慣性質量の合計が total_mass_ratio × total_mass に一致するか
        total_md = sum(a.inertance for a in plan.floor_plan)
        expected_total_md = 0.05 * sum(five_floor_masses)
        assert total_md == pytest.approx(expected_total_md, rel=1e-5)

    def test_amplitude_distribution(self, five_floor_masses):
        phi = _linear_mode_shape(5)
        plan = design_irdt_placement(
            masses=five_floor_masses,
            mode_shape=phi,
            target_period=2.0,
            total_mass_ratio=0.03,
            distribution="amplitude",
        )
        assert sum(a.inertance for a in plan.floor_plan) == pytest.approx(
            0.03 * sum(five_floor_masses), rel=1e-5
        )

    def test_uniform_distribution(self, five_floor_masses):
        phi = _uniform_mode_shape(5)
        plan = design_irdt_placement(
            masses=five_floor_masses,
            mode_shape=phi,
            target_period=1.0,
            total_mass_ratio=0.05,
            distribution="uniform",
        )
        # 均等配分では各層の慣性質量が等しい
        mds = [a.inertance for a in plan.floor_plan]
        for md in mds:
            assert md == pytest.approx(mds[0], rel=1e-5)

    def test_floor_count_matches(self):
        n = 10
        masses = [1e6] * n
        phi = _sinusoidal_mode_shape(n)
        plan = design_irdt_placement(
            masses=masses, mode_shape=phi,
            target_period=2.0, total_mass_ratio=0.05,
        )
        assert len(plan.floor_plan) == n

    def test_floor_numbering(self, five_floor_masses):
        phi = _sinusoidal_mode_shape(5)
        plan = design_irdt_placement(
            masses=five_floor_masses, mode_shape=phi,
            target_period=1.5, total_mass_ratio=0.05,
        )
        floors = [a.floor for a in plan.floor_plan]
        assert floors == [1, 2, 3, 4, 5]

    def test_kb_cd_scale_with_md(self):
        """k_b と c_d は m_d に比例する（同じ f_opt, ζ_opt を使用）"""
        masses = [1e6] * 5
        phi = _sinusoidal_mode_shape(5)
        plan = design_irdt_placement(
            masses=masses, mode_shape=phi,
            target_period=2.0, total_mass_ratio=0.05,
            distribution="interstory",
        )
        for a in plan.floor_plan:
            if a.inertance > 1e-12:
                # k_b / m_d が一定であることを確認
                kb_ratio = a.support_stiffness / a.inertance
                cd_ratio = a.damping / a.inertance
                break
        for a in plan.floor_plan:
            if a.inertance > 1e-12:
                assert a.support_stiffness / a.inertance == pytest.approx(kb_ratio, rel=1e-5)
                assert a.damping / a.inertance == pytest.approx(cd_ratio, rel=1e-5)

    def test_invalid_distribution(self):
        with pytest.raises(ValueError):
            design_irdt_placement(
                masses=[1e6], mode_shape=[1.0],
                target_period=1.0, total_mass_ratio=0.05,
                distribution="invalid_strategy",
            )

    def test_summary_text_contains_mode(self, five_floor_masses):
        phi = _sinusoidal_mode_shape(5)
        plan = design_irdt_placement(
            masses=five_floor_masses, mode_shape=phi,
            target_period=1.5, total_mass_ratio=0.05,
            target_mode=1,
        )
        text = plan.summary_text()
        assert "iRDT" in text
        assert "1" in text  # モード番号


# ---------------------------------------------------------------------------
# モード形状近似テスト
# ---------------------------------------------------------------------------

class TestModeShapeApproximations:
    def test_linear_normalized(self):
        phi = _linear_mode_shape(5)
        assert max(abs(v) for v in phi) == pytest.approx(1.0, rel=1e-9)
        assert phi[-1] == pytest.approx(1.0, rel=1e-9)  # 最上階が 1.0
        assert phi[0] < phi[1]  # 単調増加

    def test_sinusoidal_normalized(self):
        phi = _sinusoidal_mode_shape(5)
        # max(abs) == 1.0 after normalization
        assert max(abs(v) for v in phi) == pytest.approx(1.0, rel=1e-9)
        # top floor is not necessarily 1.0; middle floor is the peak
        assert max(phi) == pytest.approx(1.0, rel=1e-9)

    def test_uniform_all_ones(self):
        phi = _uniform_mode_shape(5)
        assert all(v == pytest.approx(1.0) for v in phi)

    @pytest.mark.parametrize("n", [1, 3, 5, 10, 20])
    def test_sinusoidal_length(self, n):
        phi = _sinusoidal_mode_shape(n)
        assert len(phi) == n

    def test_sinusoidal_positive(self):
        """sin 近似では全成分が正（1次モード）。"""
        for n in [3, 5, 10]:
            phi = _sinusoidal_mode_shape(n)
            assert all(v > 0 for v in phi), f"n={n}: {phi}"


# ---------------------------------------------------------------------------
# AnalysisCase 構築テスト（ウィザード完了ロジック相当）
# ---------------------------------------------------------------------------

class TestWizardAcceptedCase:
    def _build_case(
        self,
        mode_no: int = 1,
        period: float = 1.5,
        mu: float = 0.05,
        distribution: str = "interstory",
        n_floors: int = 5,
    ) -> AnalysisCase:
        """ウィザードの _finish() ロジックを模倣してケースを構築する。"""
        masses = [1.0e6] * n_floors  # 1000 ton × n_floors
        phi = _sinusoidal_mode_shape(n_floors)

        plan = design_irdt_placement(
            masses=masses,
            mode_shape=phi,
            target_period=period,
            total_mass_ratio=mu,
            target_mode=mode_no,
            distribution=distribution,
        )

        case = AnalysisCase(
            name=f"iRDT_Mode{mode_no}_μ{mu:.3f}_{distribution[:5]}",
            notes=plan.summary_text(),
        )
        case.damper_params = {
            "type": "iRDT",
            "design_method": "fixed_point_theory",
            "target_mode": mode_no,
            "target_period": period,
            "total_mass_ratio_mu": mu,
            "distribution": distribution,
            "modal_mass": plan.modal_mass,
            "floor_plan": [
                {
                    "floor": a.floor,
                    "inertance_kg": a.inertance,
                    "damping_Ns_per_m": a.damping,
                    "support_stiffness_N_per_m": a.support_stiffness,
                }
                for a in plan.floor_plan
            ],
        }
        if plan.base_parameters:
            case.damper_params["base_sdof"] = plan.base_parameters.to_dict()
        return case

    def test_case_name_contains_mode_and_mu(self):
        case = self._build_case(mode_no=1, mu=0.05)
        assert "Mode1" in case.name
        assert "0.050" in case.name

    def test_damper_params_type(self):
        case = self._build_case()
        assert case.damper_params["type"] == "iRDT"

    def test_floor_plan_in_damper_params(self):
        n = 5
        case = self._build_case(n_floors=n)
        fp = case.damper_params["floor_plan"]
        assert len(fp) == n
        # 各層に必要なキーが存在するか
        for f in fp:
            assert "floor" in f
            assert "inertance_kg" in f
            assert "damping_Ns_per_m" in f
            assert "support_stiffness_N_per_m" in f

    def test_notes_contain_summary(self):
        case = self._build_case()
        assert "iRDT" in case.notes
        assert "m_d" in case.notes or "inertance" in case.notes

    def test_serialization_round_trip(self):
        case = self._build_case()
        d = case.to_dict()
        case2 = AnalysisCase.from_dict(d)
        assert case2.name == case.name
        assert case2.damper_params["type"] == "iRDT"
        assert len(case2.damper_params["floor_plan"]) == 5

    @pytest.mark.parametrize("distribution", ["interstory", "amplitude", "uniform"])
    def test_all_distributions_produce_valid_case(self, distribution):
        case = self._build_case(distribution=distribution)
        assert case.damper_params["distribution"] == distribution
        fp = case.damper_params["floor_plan"]
        total_md = sum(f["inertance_kg"] for f in fp)
        assert total_md > 0

    def test_base_sdof_in_damper_params(self):
        case = self._build_case()
        assert "base_sdof" in case.damper_params
        sdof = case.damper_params["base_sdof"]
        assert "inertance_kg" in sdof
        assert "support_stiffness_N_per_m" in sdof
