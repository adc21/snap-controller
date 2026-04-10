"""
tests/test_damper_catalog.py
Unit tests for DamperCatalog — iRDT / iOD entries and SNAP element mapping.
"""

import math
import pytest

from app.models.damper_catalog import (
    DamperCatalog,
    DamperSpec,
    DAMPER_CATEGORIES,
    get_catalog,
)


class TestIRDTAndIODCategories:
    """カテゴリ定義に iRDT / iOD が存在すること。"""

    def test_irdt_category_exists(self):
        assert "irdt" in DAMPER_CATEGORIES
        assert "iRDT" in DAMPER_CATEGORIES["irdt"]["label"]

    def test_iod_category_exists(self):
        assert "iod" in DAMPER_CATEGORIES
        assert "iOD" in DAMPER_CATEGORIES["iod"]["label"]


class TestIRDTCatalogEntries:
    def setup_method(self):
        self.catalog = DamperCatalog()

    def test_irdt_standard_exists(self):
        spec = self.catalog.get_by_id("irdt_standard")
        assert spec is not None
        assert spec.category == "irdt"
        assert "iRDT" in spec.name

    def test_irdt_high_mass_exists(self):
        spec = self.catalog.get_by_id("irdt_high_mass")
        assert spec is not None
        assert spec.category == "irdt"

    def test_irdt_has_required_params(self):
        spec = self.catalog.get_by_id("irdt_standard")
        assert "7" in spec.parameters   # 質量比 μ
        assert "8" in spec.parameters   # 振動数比 f
        assert "9" in spec.parameters   # 減衰定数 ζ_d
        assert "10" in spec.parameters  # 支持バネ k_b

    def test_get_by_category_irdt(self):
        specs = self.catalog.get_by_category("irdt")
        assert len(specs) >= 2
        assert all(s.category == "irdt" for s in specs)


class TestIODCatalogEntries:
    def setup_method(self):
        self.catalog = DamperCatalog()

    def test_iod_standard_exists(self):
        spec = self.catalog.get_by_id("iod_standard")
        assert spec is not None
        assert spec.category == "iod"

    def test_iod_with_spring_exists(self):
        spec = self.catalog.get_by_id("iod_with_spring")
        assert spec is not None
        assert spec.category == "iod"
        assert "iHGD" in spec.name or "バネ" in spec.name

    def test_iod_standard_no_spring(self):
        spec = self.catalog.get_by_id("iod_standard")
        k_b = float(spec.parameters.get("10", "0"))
        assert k_b == 0.0, "iOD標準型は支持バネ k_b=0"

    def test_get_by_category_iod(self):
        specs = self.catalog.get_by_category("iod")
        assert len(specs) >= 2


class TestComputeIRDTSnapElements:
    """SNAP要素値変換ヘルパーのテスト。"""

    def test_basic_computation(self):
        result = DamperCatalog.compute_irdt_snap_elements(
            total_mass=1000.0,
            omega_1=2.0 * math.pi,  # 1Hz → 2π rad/s
            mu=0.02,
            f_ratio=1.0,
            zeta_d=0.05,
        )
        assert "mass_d" in result
        assert "dashpot_c" in result
        assert "spring_k" in result

        # m_d = μ * M = 0.02 * 1000 = 20 t
        assert result["mass_d"] == pytest.approx(20.0)

        # ω_d = f * ω_1 = 1.0 * 2π
        omega_d = 2.0 * math.pi
        # k_b = m_d * ω_d^2 = 20 * (2π)^2
        assert result["spring_k"] == pytest.approx(20.0 * omega_d ** 2, rel=1e-6)

        # c_d = 2 * ζ * m_d * ω_d = 2 * 0.05 * 20 * 2π
        assert result["dashpot_c"] == pytest.approx(2.0 * 0.05 * 20.0 * omega_d, rel=1e-6)

    def test_iod_no_spring(self):
        """iODモード (f_ratio=0): バネ=0, 減衰は ω_1 ベース。"""
        result = DamperCatalog.compute_irdt_snap_elements(
            total_mass=500.0,
            omega_1=3.0,
            mu=0.03,
            f_ratio=0.0,
            zeta_d=0.10,
        )
        assert result["spring_k"] == 0.0
        m_d = 0.03 * 500.0
        assert result["mass_d"] == pytest.approx(m_d)
        # c_d = 2 * ζ * m_d * ω_1 (fallback when f_ratio=0)
        assert result["dashpot_c"] == pytest.approx(2.0 * 0.10 * m_d * 3.0, rel=1e-6)

    def test_zero_mass_ratio(self):
        result = DamperCatalog.compute_irdt_snap_elements(
            total_mass=1000.0, omega_1=2.0, mu=0.0, f_ratio=1.0, zeta_d=0.05,
        )
        assert result["mass_d"] == 0.0
        assert result["spring_k"] == 0.0
        assert result["dashpot_c"] == 0.0


class TestSearchIncludesNewEntries:
    def setup_method(self):
        self.catalog = DamperCatalog()

    def test_search_irdt(self):
        results = self.catalog.search("iRDT")
        assert len(results) >= 1

    def test_search_iod(self):
        results = self.catalog.search("iOD")
        assert len(results) >= 1

    def test_search_inertial(self):
        results = self.catalog.search("慣性質量")
        assert len(results) >= 2
