"""Enthalpy models of liquid streams (specification 10.2).

Heat is always calculated from specific enthalpy::

    Q = m (h(T_in) - h(T_out))

Two models are provided.

ConstantHeatCapacity
    The approximation ``h = cp (T - T_ref)``. It is only acceptable when the value has been
    reviewed for the fluid and the temperature range, so it must state its basis.

TabulatedHeatCapacity
    A heat capacity given at tabulated temperatures and interpolated linearly between them. The
    enthalpy is the exact integral of that function. Temperatures outside the table are rejected.
    They are never extrapolated.

In a coupled calculation each engine owns the heat accounting of its own fluid. The plant
therefore takes the mean heat capacity of a stream from the engine that reports it, with
``mean_heat_capacity_of_stream``. That keeps one energy basis on each side of the plant.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol


class EnthalpyModel(Protocol):
    """Specific enthalpy of a liquid as a function of temperature, and its inverse."""

    @property
    def basis(self) -> str:
        """Where the property data comes from and what it is valid for."""
        ...

    def enthalpy_j_kg(self, temperature_k: float) -> float: ...

    def temperature_k(self, enthalpy_j_kg: float) -> float: ...


@dataclass(frozen=True)
class ConstantHeatCapacity:
    """``h = cp (T - T_ref)``. An approximation that must name its basis."""

    heat_capacity_j_kg_k: float
    basis: str
    reference_temperature_k: float = 273.15

    def __post_init__(self) -> None:
        if not math.isfinite(self.heat_capacity_j_kg_k) or self.heat_capacity_j_kg_k <= 0.0:
            raise ValueError("heat capacity must be positive")
        if not self.basis.strip():
            raise ValueError("a constant heat capacity must state its basis")

    def enthalpy_j_kg(self, temperature_k: float) -> float:
        return self.heat_capacity_j_kg_k * (temperature_k - self.reference_temperature_k)

    def temperature_k(self, enthalpy_j_kg: float) -> float:
        return self.reference_temperature_k + enthalpy_j_kg / self.heat_capacity_j_kg_k


@dataclass(frozen=True)
class TabulatedHeatCapacity:
    """Piecewise linear heat capacity, integrated exactly. Zero enthalpy at the first node."""

    temperatures_k: tuple[float, ...]
    heat_capacities_j_kg_k: tuple[float, ...]
    basis: str

    def __post_init__(self) -> None:
        if len(self.temperatures_k) < 2 or len(self.temperatures_k) != len(
            self.heat_capacities_j_kg_k
        ):
            raise ValueError("the table needs at least two temperatures and one value for each")
        if any(second <= first for first, second in pairwise(self.temperatures_k)):
            raise ValueError("table temperatures must increase strictly")
        if any(value <= 0.0 for value in self.heat_capacities_j_kg_k):
            raise ValueError("heat capacities must be positive")
        if not self.basis.strip():
            raise ValueError("a heat capacity table must state its basis")

    def _node_enthalpies(self) -> list[float]:
        totals = [0.0]
        nodes = zip(self.temperatures_k, self.heat_capacities_j_kg_k, strict=True)
        for (t0, c0), (t1, c1) in pairwise(nodes):
            totals.append(totals[-1] + 0.5 * (c0 + c1) * (t1 - t0))
        return totals

    def _segment(self, temperature_k: float) -> int:
        low, high = self.temperatures_k[0], self.temperatures_k[-1]
        if not low <= temperature_k <= high:
            raise ValueError(
                f"temperature {temperature_k:.2f} K is outside the property table "
                f"({low:.2f} K to {high:.2f} K). Values are not extrapolated"
            )
        return min(bisect_right(self.temperatures_k, temperature_k), len(self.temperatures_k) - 1)

    def heat_capacity_j_kg_k(self, temperature_k: float) -> float:
        upper = self._segment(temperature_k)
        t0, t1 = self.temperatures_k[upper - 1], self.temperatures_k[upper]
        c0, c1 = self.heat_capacities_j_kg_k[upper - 1], self.heat_capacities_j_kg_k[upper]
        return c0 + (c1 - c0) * (temperature_k - t0) / (t1 - t0)

    def enthalpy_j_kg(self, temperature_k: float) -> float:
        upper = self._segment(temperature_k)
        t0 = self.temperatures_k[upper - 1]
        c0 = self.heat_capacities_j_kg_k[upper - 1]
        capacity = self.heat_capacity_j_kg_k(temperature_k)
        return self._node_enthalpies()[upper - 1] + 0.5 * (c0 + capacity) * (temperature_k - t0)

    def temperature_k(self, enthalpy_j_kg: float) -> float:
        totals = self._node_enthalpies()
        if not totals[0] <= enthalpy_j_kg <= totals[-1]:
            raise ValueError("enthalpy is outside the property table. Values are not extrapolated")
        upper = min(bisect_right(totals, enthalpy_j_kg), len(totals) - 1)
        t0, t1 = self.temperatures_k[upper - 1], self.temperatures_k[upper]
        c0, c1 = self.heat_capacities_j_kg_k[upper - 1], self.heat_capacities_j_kg_k[upper]
        remaining = enthalpy_j_kg - totals[upper - 1]
        slope = (c1 - c0) / (t1 - t0)
        if abs(slope) < 1e-12:
            return t0 + remaining / c0
        # Solve 0.5 slope x^2 + c0 x - remaining = 0 for the step x above t0, in the form that
        # stays accurate when the slope is small.
        return t0 + 2.0 * remaining / (c0 + math.sqrt(c0 * c0 + 2.0 * slope * remaining))


def mean_heat_capacity_j_kg_k(
    fluid: EnthalpyModel, first_temperature_k: float, second_temperature_k: float
) -> float:
    """Enthalpy difference divided by temperature difference between two states."""
    difference = second_temperature_k - first_temperature_k
    if abs(difference) < 1e-9:
        step = 1e-3
        return (
            fluid.enthalpy_j_kg(first_temperature_k + step)
            - fluid.enthalpy_j_kg(first_temperature_k - step)
        ) / (2.0 * step)
    return (
        fluid.enthalpy_j_kg(second_temperature_k) - fluid.enthalpy_j_kg(first_temperature_k)
    ) / difference


def mean_heat_capacity_of_stream(
    *,
    heat_w: float,
    mass_flow_kg_s: float,
    hot_temperature_k: float,
    cold_temperature_k: float,
    reported_by: str,
) -> ConstantHeatCapacity:
    """The mean heat capacity that an engine used for one of its streams.

    An engine reports a heat rate, a mass flow and the two temperatures of a stream. Their ratio
    is the mean heat capacity on that engine's own energy basis.
    """
    difference = hot_temperature_k - cold_temperature_k
    if mass_flow_kg_s <= 0.0 or difference <= 0.0 or heat_w <= 0.0:
        raise ValueError(
            "a mean heat capacity needs a positive heat rate, mass flow and temperature difference"
        )
    return ConstantHeatCapacity(
        heat_capacity_j_kg_k=heat_w / (mass_flow_kg_s * difference),
        basis=(
            f"mean heat capacity between {cold_temperature_k:.2f} K and "
            f"{hot_temperature_k:.2f} K as accounted by {reported_by}"
        ),
    )
