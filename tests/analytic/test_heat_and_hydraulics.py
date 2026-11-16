"""Analytic fixtures for heat balance and hydraulics.

Expected values are derived by hand in the comments, or come from a second formula that does not
share code with the function under test.
"""

from __future__ import annotations

import math

import pytest

from r3chain.coupling.streams import (
    enthalpy_change_constant_cp_j_kg,
    heat_absorbed_w,
    heat_balance_residual_w,
    heat_released_w,
    required_mass_flow_kg_s,
)
from r3chain.reference.hydraulics import (
    colebrook_residual,
    darcy_weisbach_pressure_drop_pa,
    friction_factor_laminar,
    friction_factor_swamee_jain,
    hagen_poiseuille_pressure_drop_pa,
    hydrostatic_pressure_difference_pa,
    reynolds_number,
    static_head_around_loop_pa,
)


@pytest.mark.requirement("A01")
def test_exchanger_energy_balance_closes() -> None:
    # Hand derivation with an illustrative constant heat capacity of 4180 J/(kg K).
    #
    # Geothermal side: 10 kg/s cooling from 363.15 K to 313.15 K, a drop of 50 K.
    #   Q = 10 * 4180 * 50 = 2 090 000 W
    # District heating side: warming from 308.15 K to 348.15 K, a rise of 40 K.
    #   enthalpy rise = 4180 * 40 = 167 200 J/kg
    #   mass flow     = 2 090 000 / 167 200 = 12.5 kg/s
    heat_capacity = 4180.0
    geothermal_drop = enthalpy_change_constant_cp_j_kg(heat_capacity, 313.15, 363.15)
    released = heat_released_w(10.0, geothermal_drop, 0.0)
    assert released == pytest.approx(2_090_000.0, rel=1e-12)

    network_rise = enthalpy_change_constant_cp_j_kg(heat_capacity, 308.15, 348.15)
    assert network_rise == pytest.approx(167_200.0, rel=1e-12)
    network_flow = required_mass_flow_kg_s(released, network_rise)
    assert network_flow == pytest.approx(12.5, rel=1e-12)

    absorbed = heat_absorbed_w(network_flow, 0.0, network_rise)
    assert heat_balance_residual_w(released, absorbed) == pytest.approx(0.0, abs=1e-6)
    # A declared loss is part of the balance. It is not hidden heat.
    assert heat_balance_residual_w(released, absorbed - 5_000.0, losses_w=5_000.0) == pytest.approx(
        0.0, abs=1e-6
    )


def test_stream_heat_rejects_negative_flow() -> None:
    with pytest.raises(ValueError, match="negative"):
        heat_released_w(-1.0, 10.0, 0.0)
    with pytest.raises(ValueError, match="positive"):
        required_mass_flow_kg_s(1000.0, 0.0)


def test_laminar_darcy_weisbach_matches_hagen_poiseuille() -> None:
    # Water like fluid: density 1000 kg/m3, viscosity 0.001 Pa s.
    # Pipe: diameter 0.1 m, length 100 m, velocity 0.01 m/s.
    #   Re = 1000 * 0.01 * 0.1 / 0.001 = 1000, which is laminar
    #   f  = 64 / 1000 = 0.064
    #   Darcy-Weisbach:   0.064 * (100 / 0.1) * 1000 * 0.01^2 / 2 = 3.2 Pa
    #   Hagen-Poiseuille: 32 * 0.001 * 100 * 0.01 / 0.1^2        = 3.2 Pa
    reynolds = reynolds_number(1000.0, 0.01, 0.1, 0.001)
    assert reynolds == pytest.approx(1000.0)
    factor = friction_factor_laminar(reynolds)
    assert factor == pytest.approx(0.064)
    darcy = darcy_weisbach_pressure_drop_pa(factor, 100.0, 0.1, 1000.0, 0.01)
    poiseuille = hagen_poiseuille_pressure_drop_pa(0.001, 100.0, 0.01, 0.1)
    assert darcy == pytest.approx(3.2, rel=1e-12)
    assert poiseuille == pytest.approx(3.2, rel=1e-12)


def test_swamee_jain_satisfies_colebrook() -> None:
    # For Re = 1e5 and relative roughness 1e-4 the Moody chart gives f of about 0.0185.
    factor = friction_factor_swamee_jain(1.0e5, 1.0e-4)
    assert factor == pytest.approx(0.0185, abs=5e-4)
    # The explicit value must nearly solve the implicit Colebrook equation.
    residual = colebrook_residual(factor, 1.0e5, 1.0e-4)
    assert abs(residual) * math.sqrt(factor) < 0.02


def test_friction_relations_reject_values_outside_their_range() -> None:
    with pytest.raises(ValueError):
        friction_factor_laminar(5000.0)
    with pytest.raises(ValueError):
        friction_factor_swamee_jain(1000.0, 1.0e-4)
    with pytest.raises(ValueError):
        friction_factor_swamee_jain(1.0e5, 0.5)


@pytest.mark.requirement("A04")
def test_hydrostatic_pressure_difference() -> None:
    # Constant density 1000 kg/m3, standard gravity 9.80665 m/s2, height difference 50 m.
    #   p_low - p_high = 1000 * 9.80665 * 50 = 490 332.5 Pa, about 4.9 bar
    assert hydrostatic_pressure_difference_pa(1000.0, 50.0) == pytest.approx(490_332.5, rel=1e-12)


@pytest.mark.requirement("A04")
def test_static_head_cancels_around_a_closed_loop() -> None:
    # The supply circuit of the synthetic network climbs from 150 m to 210 m and the return
    # circuit comes back down. Around the closed loop every rise is matched by a fall, so the
    # static contributions cancel. Elevation changes local pressure but adds nothing to the
    # circulation pump head.
    supply_then_return = [160.0, 150.0, 175.0, 195.0, 210.0, 195.0, 175.0, 150.0]
    assert static_head_around_loop_pa(1000.0, supply_then_return) == pytest.approx(0.0, abs=1e-6)
    # The local pressure difference between the lowest and highest node is still large.
    assert hydrostatic_pressure_difference_pa(1000.0, 210.0 - 150.0) == pytest.approx(
        588_399.0, rel=1e-12
    )
