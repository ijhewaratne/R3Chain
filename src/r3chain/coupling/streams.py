"""Heat carried by a liquid stream (specification 10.2).

    Q = m_g (h_g,in - h_g,out) = m_DH (h_DH,out - h_DH,in)

Q is heat in W, m is mass flow in kg/s and h is specific enthalpy in J/kg. The constant heat
capacity form is an approximation that must be reviewed for the fluid and operating range.
"""

from __future__ import annotations


def heat_released_w(
    mass_flow_kg_s: float, inlet_enthalpy_j_kg: float, outlet_enthalpy_j_kg: float
) -> float:
    """Heat given up by a stream that cools from its inlet to its outlet state."""
    if mass_flow_kg_s < 0.0:
        raise ValueError("mass flow cannot be negative")
    return mass_flow_kg_s * (inlet_enthalpy_j_kg - outlet_enthalpy_j_kg)


def heat_absorbed_w(
    mass_flow_kg_s: float, inlet_enthalpy_j_kg: float, outlet_enthalpy_j_kg: float
) -> float:
    """Heat taken up by a stream that warms from its inlet to its outlet state."""
    return -heat_released_w(mass_flow_kg_s, inlet_enthalpy_j_kg, outlet_enthalpy_j_kg)


def enthalpy_change_constant_cp_j_kg(
    heat_capacity_j_kg_k: float, from_temperature_k: float, to_temperature_k: float
) -> float:
    """Specific enthalpy change of a liquid with constant heat capacity."""
    if heat_capacity_j_kg_k <= 0.0:
        raise ValueError("heat capacity must be positive")
    return heat_capacity_j_kg_k * (to_temperature_k - from_temperature_k)


def heat_balance_residual_w(
    heat_released_hot_w: float, heat_absorbed_cold_w: float, losses_w: float = 0.0
) -> float:
    """Heat that is neither transferred nor accounted for as a loss. Zero for a closed balance."""
    return heat_released_hot_w - heat_absorbed_cold_w - losses_w


def required_mass_flow_kg_s(heat_w: float, enthalpy_rise_j_kg: float) -> float:
    """Mass flow needed to carry a heat rate at a given specific enthalpy rise."""
    if enthalpy_rise_j_kg <= 0.0:
        raise ValueError("the enthalpy rise must be positive")
    return heat_w / enthalpy_rise_j_kg
