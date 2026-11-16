"""Counterflow heat exchanger (specification 10.2).

The heat a counterflow exchanger transfers is the smallest of several limits. Each limit is
calculated on its own, and the one that binds is reported by name, so that a shortfall can
always be traced to its cause.

Demand
    The cold stream is heated to its target temperature and no further.
Hot end approach
    The cold outlet stays at least the minimum approach below the hot inlet.
Cold end approach
    The hot outlet stays at least the minimum approach above the cold inlet.
Minimum hot outlet
    The hot stream is not cooled below a stated temperature, for example a reinjection limit.
Rating
    The heat the exchanger surface can transfer, from the effectiveness and NTU relation of a
    counterflow exchanger with its conductance UA.

The minimum approach is a design rule. It is applied in addition to the rating, so a very large
UA never lets the two streams come closer than the approach.

Heat is taken from specific enthalpy on both sides. The effectiveness relation needs heat
capacity rates. These use the mean heat capacity of each stream between its inlet and outlet,
found by iteration, which is exact for a constant heat capacity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from r3chain.coupling.fluids import EnthalpyModel, mean_heat_capacity_j_kg_k
from r3chain.domain.base import NonNegativeReal, PositiveReal, Real, Record
from r3chain.domain.records.runs import Check, CheckOutcome

# The mean heat capacities of the rating settle within a few passes for any liquid.
_MAX_PROPERTY_PASSES = 50
_PROPERTY_TOLERANCE_FRACTION = 1e-12


class ExchangerLimit(StrEnum):
    DEMAND = "demand"
    HOT_END_APPROACH = "hot_end_approach"
    COLD_END_APPROACH = "cold_end_approach"
    MIN_HOT_OUTLET = "min_hot_outlet_temperature"
    RATING = "rating"


@dataclass(frozen=True)
class Stream:
    """A liquid stream entering an exchanger."""

    mass_flow_kg_s: float
    inlet_temperature_k: float
    fluid: EnthalpyModel

    def heat_to_reach_w(self, temperature_k: float) -> float:
        """Heat the stream takes up (positive) or gives off (negative) to reach a temperature."""
        return self.mass_flow_kg_s * (
            self.fluid.enthalpy_j_kg(temperature_k)
            - self.fluid.enthalpy_j_kg(self.inlet_temperature_k)
        )

    def temperature_after_k(self, heat_absorbed_w: float) -> float:
        return self.fluid.temperature_k(
            self.fluid.enthalpy_j_kg(self.inlet_temperature_k)
            + heat_absorbed_w / self.mass_flow_kg_s
        )


class ExchangerPoint(Record):
    """One operating point of a counterflow exchanger."""

    heat_w: NonNegativeReal
    binding_limit: ExchangerLimit
    hot_mass_flow_kg_s: PositiveReal
    hot_inlet_temperature_k: Real
    hot_outlet_temperature_k: Real
    cold_mass_flow_kg_s: PositiveReal
    cold_inlet_temperature_k: Real
    cold_outlet_temperature_k: Real
    # Temperature difference between the two streams at each end of the exchanger.
    hot_end_approach_delta_k: Real
    cold_end_approach_delta_k: Real
    min_approach_delta_k: NonNegativeReal
    ua_w_k: PositiveReal
    # The most the surface could transfer between these two inlet states.
    rating_heat_w: NonNegativeReal
    effectiveness_fraction: NonNegativeReal
    # Heat given off by the hot stream minus heat taken up by the cold stream.
    balance_residual_w: Real


def counterflow_effectiveness(ntu: float, capacity_ratio: float) -> float:
    """Effectiveness of a counterflow exchanger.

    ``ntu`` is UA divided by the smaller heat capacity rate. ``capacity_ratio`` is the smaller
    heat capacity rate divided by the larger one.
    """
    if ntu < 0.0 or not 0.0 <= capacity_ratio <= 1.0:
        raise ValueError("NTU must not be negative and the capacity ratio must lie in 0 to 1")
    if abs(1.0 - capacity_ratio) < 1e-9:
        return ntu / (1.0 + ntu)
    decay = math.exp(-ntu * (1.0 - capacity_ratio))
    return (1.0 - decay) / (1.0 - capacity_ratio * decay)


def _rating_heat_w(hot: Stream, cold: Stream, ua_w_k: float) -> tuple[float, float]:
    """Heat the surface can transfer, and the effectiveness, for two inlet states."""
    span = hot.inlet_temperature_k - cold.inlet_temperature_k
    if span <= 0.0:
        return 0.0, 0.0
    hot_outlet, cold_outlet = cold.inlet_temperature_k, hot.inlet_temperature_k
    heat = effectiveness = 0.0
    for _ in range(_MAX_PROPERTY_PASSES):
        hot_rate = hot.mass_flow_kg_s * mean_heat_capacity_j_kg_k(
            hot.fluid, hot.inlet_temperature_k, hot_outlet
        )
        cold_rate = cold.mass_flow_kg_s * mean_heat_capacity_j_kg_k(
            cold.fluid, cold.inlet_temperature_k, cold_outlet
        )
        smaller, larger = min(hot_rate, cold_rate), max(hot_rate, cold_rate)
        effectiveness = counterflow_effectiveness(ua_w_k / smaller, smaller / larger)
        updated = effectiveness * smaller * span
        hot_outlet = hot.temperature_after_k(-updated)
        cold_outlet = cold.temperature_after_k(updated)
        settled = abs(updated - heat) <= _PROPERTY_TOLERANCE_FRACTION * max(updated, 1.0)
        heat = updated
        if settled:
            break
    return heat, effectiveness


def rate_counterflow(
    hot: Stream,
    cold: Stream,
    *,
    ua_w_k: float,
    min_approach_delta_k: float,
    cold_outlet_target_k: float | None = None,
    min_hot_outlet_temperature_k: float | None = None,
) -> ExchangerPoint:
    """Operating point of a counterflow exchanger between two inlet streams."""
    if hot.mass_flow_kg_s <= 0.0 or cold.mass_flow_kg_s <= 0.0:
        raise ValueError("both streams of an exchanger need a positive mass flow")
    if ua_w_k <= 0.0 or min_approach_delta_k < 0.0:
        raise ValueError("UA must be positive and the minimum approach must not be negative")

    rating, effectiveness = _rating_heat_w(hot, cold, ua_w_k)
    limits: list[tuple[ExchangerLimit, float]] = []
    if cold_outlet_target_k is not None:
        limits.append((ExchangerLimit.DEMAND, cold.heat_to_reach_w(cold_outlet_target_k)))
    limits.append(
        (
            ExchangerLimit.HOT_END_APPROACH,
            cold.heat_to_reach_w(hot.inlet_temperature_k - min_approach_delta_k),
        )
    )
    limits.append(
        (
            ExchangerLimit.COLD_END_APPROACH,
            -hot.heat_to_reach_w(cold.inlet_temperature_k + min_approach_delta_k),
        )
    )
    if min_hot_outlet_temperature_k is not None:
        limits.append(
            (ExchangerLimit.MIN_HOT_OUTLET, -hot.heat_to_reach_w(min_hot_outlet_temperature_k))
        )
    limits.append((ExchangerLimit.RATING, rating))

    # The first limit in the list wins a tie, so a demand that is exactly met reads as met.
    binding, heat = min(limits, key=lambda item: item[1])
    heat = max(heat, 0.0)
    hot_outlet = hot.temperature_after_k(-heat)
    cold_outlet = cold.temperature_after_k(heat)
    released = -hot.heat_to_reach_w(hot_outlet)
    absorbed = cold.heat_to_reach_w(cold_outlet)
    return ExchangerPoint(
        heat_w=heat,
        binding_limit=binding,
        hot_mass_flow_kg_s=hot.mass_flow_kg_s,
        hot_inlet_temperature_k=hot.inlet_temperature_k,
        hot_outlet_temperature_k=hot_outlet,
        cold_mass_flow_kg_s=cold.mass_flow_kg_s,
        cold_inlet_temperature_k=cold.inlet_temperature_k,
        cold_outlet_temperature_k=cold_outlet,
        hot_end_approach_delta_k=hot.inlet_temperature_k - cold_outlet,
        cold_end_approach_delta_k=hot_outlet - cold.inlet_temperature_k,
        min_approach_delta_k=min_approach_delta_k,
        ua_w_k=ua_w_k,
        rating_heat_w=rating,
        effectiveness_fraction=effectiveness,
        balance_residual_w=released - absorbed,
    )


def passes(margin: float, limit: float) -> bool:
    """A margin passes when it is not negative. Floating point rounding is absorbed."""
    return margin >= -1e-9 * max(abs(limit), 1.0)


def terminal_checks(
    hot: Stream,
    cold: Stream,
    *,
    required_cold_outlet_k: float,
    min_approach_delta_k: float,
    threshold_origin: str,
    prefix: str = "exchanger",
) -> tuple[Check, Check, float]:
    """Check both ends of a counterflow exchanger for a required duty (acceptance test A02).

    Hot end
        The hot inlet must exceed the required cold outlet by the minimum approach. This does
        not depend on the flows. It fails when the source is not hot enough.
    Cold end
        The hot outlet must stay above the cold inlet by the minimum approach. It is checked for
        the duty that the hot end permits, so that a hot end failure is not reported a second
        time as a cold end failure. It fails when the hot stream carries too little heat.

    Returns the two checks and the heat that both terminals permit.
    """
    required = max(cold.heat_to_reach_w(required_cold_outlet_k), 0.0)
    hot_end = hot.inlet_temperature_k - required_cold_outlet_k
    hot_end_heat = max(cold.heat_to_reach_w(hot.inlet_temperature_k - min_approach_delta_k), 0.0)
    duty = min(required, hot_end_heat)
    cold_end = hot.temperature_after_k(-duty) - cold.inlet_temperature_k
    cold_end_heat = max(-hot.heat_to_reach_w(cold.inlet_temperature_k + min_approach_delta_k), 0.0)
    checks: list[Check] = []
    for name, where, observed in (
        ("hot_end", "hot inlet minus the required cold outlet", hot_end),
        ("cold_end", "hot outlet minus cold inlet, at the duty the hot end permits", cold_end),
    ):
        margin = observed - min_approach_delta_k
        checks.append(
            Check(
                check_id=f"{prefix}_{name}_approach",
                description=f"{name.replace('_', ' ')} temperature difference: {where}",
                outcome=(
                    CheckOutcome.PASS if passes(margin, min_approach_delta_k) else CheckOutcome.FAIL
                ),
                observed=observed,
                limit=min_approach_delta_k,
                margin=margin,
                unit="K (difference)",
                threshold_origin=threshold_origin,
            )
        )
    return checks[0], checks[1], min(duty, cold_end_heat)


def pressure_loss_pa(
    rated_pressure_loss_pa: float, rated_mass_flow_kg_s: float, mass_flow_kg_s: float
) -> float:
    """Pressure loss of one side, scaled from its rated point with the square of the flow."""
    if rated_pressure_loss_pa < 0.0 or rated_mass_flow_kg_s <= 0.0:
        raise ValueError("a rated pressure loss needs a positive rated mass flow")
    return rated_pressure_loss_pa * (mass_flow_kg_s / rated_mass_flow_kg_s) ** 2
