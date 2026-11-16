"""Heat pump performance (specification 10.3).

Two performance models sit behind one interface.

MapHeatPump
    Reads the coefficient of performance and the capacity from a manufacturer or otherwise
    reviewed performance map, by bilinear interpolation. A point outside the map raises
    ``OutsideMapDomain``. It is never extrapolated.

CarnotScreeningHeatPump
    A fixed fraction of the Carnot coefficient of performance. This is a screening model. Its
    results are labelled as such and must never be reported as achieved equipment performance.

Energy balance (acceptance test A03)
    The coefficient of performance is condenser heat divided by compressor electricity. A stated
    fraction ``f`` of the compressor electricity is lost to the surroundings and does not reach
    the condenser::

        Q_condenser = Q_evaporator + (1 - f) W        W = Q_condenser / COP

    Condenser heat and evaporator heat are two sides of one machine. They are never added
    together as two heat deliveries.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from r3chain.domain.base import NonNegativeReal, PositiveReal, Real, Record
from r3chain.domain.records.plant import HeatPumpMap

PERFORMANCE_MAP = "performance_map"
SCREENING_MODEL = "screening_model"


class OutsideMapDomain(Exception):
    """The operating point lies outside the range a performance model covers.

    It names the quantity that is out of range, its value and the bound it violates, so that the
    failure can be reported as a check with an observed value, a limit and a margin.
    """

    def __init__(
        self, message: str, *, quantity: str, value: float, bound: float, unit: str
    ) -> None:
        super().__init__(message)
        self.quantity = quantity
        self.value = value
        self.bound = bound
        self.unit = unit


@dataclass(frozen=True)
class HeatPumpPerformance:
    heating_cop_ratio: float
    # None when the model states no capacity.
    max_condenser_heat_w: float | None


class HeatPumpModel(Protocol):
    @property
    def basis(self) -> str:
        """``performance_map`` or ``screening_model``."""
        ...

    @property
    def description(self) -> str: ...

    def performance(
        self, source_inlet_temperature_k: float, sink_outlet_temperature_k: float
    ) -> HeatPumpPerformance: ...


def _bracket(axis: tuple[float, ...], value: float, name: str) -> tuple[int, float]:
    """Index of the lower grid node and the position between it and the next one."""
    if not axis[0] <= value <= axis[-1]:
        raise OutsideMapDomain(
            f"{name} {value:.2f} K is outside the performance map "
            f"({axis[0]:.2f} K to {axis[-1]:.2f} K)",
            quantity=name,
            value=value,
            bound=axis[0] if value < axis[0] else axis[-1],
            unit="K",
        )
    lower = min(bisect_right(axis, value) - 1, len(axis) - 2)
    return lower, (value - axis[lower]) / (axis[lower + 1] - axis[lower])


def _bilinear(
    table: tuple[tuple[float, ...], ...], row: int, row_part: float, column: int, part: float
) -> float:
    first = table[row][column] * (1.0 - part) + table[row][column + 1] * part
    second = table[row + 1][column] * (1.0 - part) + table[row + 1][column + 1] * part
    return first * (1.0 - row_part) + second * row_part


@dataclass(frozen=True)
class MapHeatPump:
    """A heat pump described by a tabulated performance map."""

    map: HeatPumpMap

    @property
    def basis(self) -> str:
        return PERFORMANCE_MAP

    @property
    def description(self) -> str:
        return f"performance map {self.map.map_id}: {self.map.description}"

    def performance(
        self, source_inlet_temperature_k: float, sink_outlet_temperature_k: float
    ) -> HeatPumpPerformance:
        row, row_part = _bracket(
            self.map.source_inlet_temperatures_k,
            source_inlet_temperature_k,
            "source inlet temperature",
        )
        column, part = _bracket(
            self.map.sink_outlet_temperatures_k,
            sink_outlet_temperature_k,
            "sink outlet temperature",
        )
        return HeatPumpPerformance(
            heating_cop_ratio=_bilinear(self.map.heating_cop_ratio, row, row_part, column, part),
            max_condenser_heat_w=_bilinear(
                self.map.max_condenser_heat_w, row, row_part, column, part
            ),
        )


@dataclass(frozen=True)
class CarnotScreeningHeatPump:
    """A fixed fraction of the Carnot coefficient of performance. For screening only.

    The condensing temperature is the sink outlet temperature plus a stated difference. The
    evaporating temperature is the source inlet temperature minus a stated difference, which
    covers both the cooling of the source stream and the approach of the evaporator.
    """

    carnot_efficiency_fraction: float
    evaporator_temperature_difference_delta_k: float
    condenser_temperature_difference_delta_k: float
    max_condenser_heat_w: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.carnot_efficiency_fraction <= 1.0:
            raise ValueError("carnot_efficiency_fraction must lie above 0 and at most 1")
        if (
            self.evaporator_temperature_difference_delta_k < 0.0
            or self.condenser_temperature_difference_delta_k < 0.0
        ):
            raise ValueError(
                "the temperature differences of the screening model must not be negative"
            )
        if self.max_condenser_heat_w is not None and self.max_condenser_heat_w <= 0.0:
            raise ValueError("max_condenser_heat_w must be positive")

    @property
    def basis(self) -> str:
        return SCREENING_MODEL

    @property
    def description(self) -> str:
        return (
            f"screening model: {self.carnot_efficiency_fraction:g} of the Carnot coefficient of "
            "performance. Not equipment performance"
        )

    def performance(
        self, source_inlet_temperature_k: float, sink_outlet_temperature_k: float
    ) -> HeatPumpPerformance:
        condensing = sink_outlet_temperature_k + self.condenser_temperature_difference_delta_k
        evaporating = source_inlet_temperature_k - self.evaporator_temperature_difference_delta_k
        lift = condensing - evaporating
        if evaporating <= 0.0 or lift <= 0.0:
            raise OutsideMapDomain(
                "the screening model needs a condensing temperature above the evaporating "
                f"temperature. Here they are {condensing:.2f} K and {evaporating:.2f} K",
                quantity="temperature lift",
                value=lift,
                bound=0.0,
                unit="K (difference)",
            )
        cop = self.carnot_efficiency_fraction * condensing / lift
        if cop <= 1.0:
            raise OutsideMapDomain(
                f"the screening model gives a coefficient of performance of {cop:.2f}, which is "
                "not a heat pump operating point",
                quantity="coefficient of performance",
                value=cop,
                bound=1.0,
                unit="1",
            )
        return HeatPumpPerformance(
            heating_cop_ratio=cop, max_condenser_heat_w=self.max_condenser_heat_w
        )


class HeatPumpLimit(StrEnum):
    DEMAND = "demand"
    CAPACITY = "heat_pump_capacity"
    SOURCE_HEAT = "heat_pump_source_heat"


class HeatPumpPoint(Record):
    """One operating point of a heat pump, with its energy balance."""

    basis: str
    description: str
    binding_limit: HeatPumpLimit
    source_inlet_temperature_k: Real
    sink_outlet_temperature_k: Real
    heating_cop_ratio: PositiveReal
    max_condenser_heat_w: PositiveReal | None
    required_condenser_heat_w: NonNegativeReal
    condenser_heat_w: NonNegativeReal
    evaporator_heat_w: NonNegativeReal
    compressor_electricity_w: NonNegativeReal
    # Compressor electricity that is lost to the surroundings and does not reach the condenser.
    compressor_heat_loss_w: NonNegativeReal
    compressor_heat_loss_fraction: NonNegativeReal
    # Evaporator heat plus compressor electricity, minus condenser heat and the stated loss.
    balance_residual_w: Real


def operate_heat_pump(
    model: HeatPumpModel,
    *,
    source_inlet_temperature_k: float,
    sink_outlet_temperature_k: float,
    required_condenser_heat_w: float,
    max_evaporator_heat_w: float,
    compressor_heat_loss_fraction: float,
) -> HeatPumpPoint:
    """Operate a heat pump towards a required condenser heat.

    The condenser heat is the smallest of the requirement, the capacity of the machine and the
    heat that the source stream can give to the evaporator. Raises ``OutsideMapDomain`` when the
    performance model does not cover the operating point.
    """
    if not 0.0 <= compressor_heat_loss_fraction < 1.0:
        raise ValueError("compressor_heat_loss_fraction must lie in 0 to below 1")
    if required_condenser_heat_w < 0.0 or max_evaporator_heat_w < 0.0:
        raise ValueError("heat rates must not be negative")
    performance = model.performance(source_inlet_temperature_k, sink_outlet_temperature_k)
    cop = performance.heating_cop_ratio
    # Share of the condenser heat that comes from the evaporator.
    source_share = 1.0 - (1.0 - compressor_heat_loss_fraction) / cop

    limits: list[tuple[HeatPumpLimit, float]] = [(HeatPumpLimit.DEMAND, required_condenser_heat_w)]
    if performance.max_condenser_heat_w is not None:
        limits.append((HeatPumpLimit.CAPACITY, performance.max_condenser_heat_w))
    limits.append((HeatPumpLimit.SOURCE_HEAT, max_evaporator_heat_w / source_share))
    binding, condenser = min(limits, key=lambda item: item[1])

    electricity = condenser / cop
    evaporator = condenser * source_share
    loss = compressor_heat_loss_fraction * electricity
    return HeatPumpPoint(
        basis=model.basis,
        description=model.description,
        binding_limit=binding,
        source_inlet_temperature_k=source_inlet_temperature_k,
        sink_outlet_temperature_k=sink_outlet_temperature_k,
        heating_cop_ratio=cop,
        max_condenser_heat_w=performance.max_condenser_heat_w,
        required_condenser_heat_w=required_condenser_heat_w,
        condenser_heat_w=condenser,
        evaporator_heat_w=evaporator,
        compressor_electricity_w=electricity,
        compressor_heat_loss_w=loss,
        compressor_heat_loss_fraction=compressor_heat_loss_fraction,
        balance_residual_w=evaporator + electricity - condenser - loss,
    )
