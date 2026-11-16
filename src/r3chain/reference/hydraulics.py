"""Independent hydraulic reference formulas.

These are textbook relations, kept separate from any network engine. They exist to check an
engine's results against a second, independent calculation (specification 20.3).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from r3chain.domain.units import STANDARD_GRAVITY_M_S2

LAMINAR_LIMIT = 2300.0


def reynolds_number(
    density_kg_m3: float, velocity_m_s: float, diameter_m: float, dynamic_viscosity_pa_s: float
) -> float:
    return density_kg_m3 * abs(velocity_m_s) * diameter_m / dynamic_viscosity_pa_s


def friction_factor_laminar(reynolds: float) -> float:
    """Darcy friction factor for laminar pipe flow."""
    if not 0.0 < reynolds <= LAMINAR_LIMIT:
        raise ValueError("the laminar relation holds for 0 < Re <= 2300")
    return 64.0 / reynolds


def friction_factor_swamee_jain(reynolds: float, relative_roughness: float) -> float:
    """Explicit approximation of the Colebrook equation for turbulent flow.

    Stated range of validity: 5e3 <= Re <= 1e8 and 1e-6 <= roughness / diameter <= 1e-2.
    """
    if not 5.0e3 <= reynolds <= 1.0e8:
        raise ValueError("Swamee-Jain holds for 5e3 <= Re <= 1e8")
    if not 1.0e-6 <= relative_roughness <= 1.0e-2:
        raise ValueError("Swamee-Jain holds for 1e-6 <= relative roughness <= 1e-2")
    return 0.25 / math.log10(relative_roughness / 3.7 + 5.74 / reynolds**0.9) ** 2


def colebrook_residual(friction_factor: float, reynolds: float, relative_roughness: float) -> float:
    """Residual of the implicit Colebrook equation. Zero for an exact solution."""
    root = math.sqrt(friction_factor)
    return 1.0 / root + 2.0 * math.log10(relative_roughness / 3.7 + 2.51 / (reynolds * root))


def darcy_weisbach_pressure_drop_pa(
    friction_factor: float,
    length_m: float,
    diameter_m: float,
    density_kg_m3: float,
    velocity_m_s: float,
) -> float:
    """Frictional pressure loss along a straight pipe."""
    return friction_factor * (length_m / diameter_m) * density_kg_m3 * velocity_m_s**2 / 2.0


def hagen_poiseuille_pressure_drop_pa(
    dynamic_viscosity_pa_s: float, length_m: float, velocity_m_s: float, diameter_m: float
) -> float:
    """Frictional pressure loss of laminar pipe flow, derived without a friction factor."""
    return 32.0 * dynamic_viscosity_pa_s * length_m * velocity_m_s / diameter_m**2


def hydrostatic_pressure_difference_pa(
    density_kg_m3: float, height_difference_m: float, gravity_m_s2: float = STANDARD_GRAVITY_M_S2
) -> float:
    """Pressure at the lower point minus pressure at the higher point, for constant density.

    p_low - p_high = rho g (z_high - z_low)
    """
    return density_kg_m3 * gravity_m_s2 * height_difference_m


def static_head_around_loop_pa(
    density_kg_m3: float,
    elevations_m: Sequence[float],
    gravity_m_s2: float = STANDARD_GRAVITY_M_S2,
) -> float:
    """Sum of the static pressure changes along a closed loop through the given elevations.

    The loop returns to its first point, so for constant density the sum is zero. Elevation
    changes the local pressure. It does not add to the circulation pump head.
    """
    if len(elevations_m) < 2:
        raise ValueError("a loop needs at least two points")
    closed = [*elevations_m, elevations_m[0]]
    return math.fsum(
        -density_kg_m3 * gravity_m_s2 * (closed[index + 1] - closed[index])
        for index in range(len(closed) - 1)
    )
