"""Source performance envelope (specification 9.3).

An envelope is the performance of one source on a grid of mass flow and injection temperature.
It lets a coupling loop or an uncertainty study ask for source performance many times without
calling the engine every time.

It is only an approximation inside its validated domain:

* A grid cell is validated when the engine completed at its four corners and at its centre.
* The approximation error is measured at every cell centre, where bilinear interpolation is
  least accurate, and recorded with the envelope.
* A request outside the validated domain is never extrapolated. It goes back to the engine.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import pairwise
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeReal,
    Real,
    Record,
    VersionedRecord,
)
from r3chain.domain.status import AdapterState
from r3chain.ports.source import SourceModel, SourceRequest, SourceResult


class EnvelopePoint(Record):
    mass_flow_kg_s: Real
    injection_temperature_k: Real
    state: AdapterState
    active_limits: tuple[str, ...] = ()
    wellhead_production_temperature_k: Real | None = None
    geothermal_extracted_heat_w: Real | None = None
    electrical_pump_power_w: Real | None = None


class ApproximationError(Record):
    """Largest difference between interpolation and engine, over all validated cell centres."""

    cells_checked_count: int
    max_abs_heat_error_w: NonNegativeReal
    max_rel_heat_error_fraction: NonNegativeReal
    max_abs_temperature_error_delta_k: NonNegativeReal
    max_rel_pump_power_error_fraction: NonNegativeReal


class InterpolatedPerformance(Record):
    """An approximate performance read from an envelope. It says so, and it carries its error."""

    approximation: Literal["bilinear_interpolation"] = "bilinear_interpolation"
    mass_flow_kg_s: Real
    injection_temperature_k: Real
    wellhead_production_temperature_k: Real
    geothermal_extracted_heat_w: Real
    electrical_pump_power_w: Real
    error: ApproximationError


class EnvelopeCell(Record):
    flow_index: int
    temperature_index: int


class SourceEnvelope(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "source_envelope"
    schema_version: Literal["0.1.0"] = "0.1.0"

    source_scenario_id: Identifier
    engine: NonEmptyStr
    engine_version: NonEmptyStr
    model_family: NonEmptyStr
    synthetic: bool
    # Hash of the request the grid was built from, at its first grid point.
    base_input_hash: str
    mass_flows_kg_s: tuple[Real, ...] = Field(min_length=2)
    injection_temperatures_k: tuple[Real, ...] = Field(min_length=2)
    # One point per grid node, ordered by flow first and injection temperature second.
    points: tuple[EnvelopePoint, ...]
    validated_cells: tuple[EnvelopeCell, ...] = ()
    approximation_error: ApproximationError | None = None

    @model_validator(mode="after")
    def _grid_is_consistent(self) -> SourceEnvelope:
        for name, axis in (
            ("mass_flows_kg_s", self.mass_flows_kg_s),
            ("injection_temperatures_k", self.injection_temperatures_k),
        ):
            if any(later <= earlier for earlier, later in pairwise(axis)):
                raise ValueError(f"{name} must be strictly increasing")
        expected = len(self.mass_flows_kg_s) * len(self.injection_temperatures_k)
        if len(self.points) != expected:
            raise ValueError(f"the grid needs {expected} points, not {len(self.points)}")
        return self

    def point(self, flow_index: int, temperature_index: int) -> EnvelopePoint:
        return self.points[flow_index * len(self.injection_temperatures_k) + temperature_index]

    def state_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(point.state.value for point in self.points).items()))

    def best_point(self) -> EnvelopePoint | None:
        """The completed grid point with the largest extracted heat."""
        completed = [
            point for point in self.points if point.geothermal_extracted_heat_w is not None
        ]
        if not completed:
            return None
        return max(completed, key=lambda point: point.geothermal_extracted_heat_w or 0.0)

    def _cell(self, mass_flow_kg_s: float, injection_temperature_k: float) -> EnvelopeCell | None:
        flow_index = _interval(self.mass_flows_kg_s, mass_flow_kg_s)
        temperature_index = _interval(self.injection_temperatures_k, injection_temperature_k)
        if flow_index is None or temperature_index is None:
            return None
        cell = EnvelopeCell(flow_index=flow_index, temperature_index=temperature_index)
        return cell if cell in self.validated_cells else None

    def interpolate(
        self, mass_flow_kg_s: float, injection_temperature_k: float
    ) -> InterpolatedPerformance | None:
        """Interpolate inside the validated domain. Returns None anywhere else."""
        cell = self._cell(mass_flow_kg_s, injection_temperature_k)
        if cell is None or self.approximation_error is None:
            return None
        return InterpolatedPerformance(
            mass_flow_kg_s=mass_flow_kg_s,
            injection_temperature_k=injection_temperature_k,
            wellhead_production_temperature_k=self._bilinear(
                cell, mass_flow_kg_s, injection_temperature_k, "wellhead_production_temperature_k"
            ),
            geothermal_extracted_heat_w=self._bilinear(
                cell, mass_flow_kg_s, injection_temperature_k, "geothermal_extracted_heat_w"
            ),
            electrical_pump_power_w=self._bilinear(
                cell, mass_flow_kg_s, injection_temperature_k, "electrical_pump_power_w"
            ),
            error=self.approximation_error,
        )

    def _bilinear(self, cell: EnvelopeCell, flow: float, temperature: float, field: str) -> float:
        i, j = cell.flow_index, cell.temperature_index
        flow_low, flow_high = self.mass_flows_kg_s[i], self.mass_flows_kg_s[i + 1]
        temp_low, temp_high = self.injection_temperatures_k[j], self.injection_temperatures_k[j + 1]
        u = (flow - flow_low) / (flow_high - flow_low)
        v = (temperature - temp_low) / (temp_high - temp_low)

        def value(flow_index: int, temperature_index: int) -> float:
            number = getattr(self.point(flow_index, temperature_index), field)
            assert number is not None  # corners of a validated cell are completed
            return float(number)

        return (
            (1 - u) * (1 - v) * value(i, j)
            + u * (1 - v) * value(i + 1, j)
            + (1 - u) * v * value(i, j + 1)
            + u * v * value(i + 1, j + 1)
        )


def _interval(axis: Sequence[float], value: float) -> int | None:
    """Index of the grid interval that contains a value, or None outside the grid."""
    if value < axis[0] or value > axis[-1]:
        return None
    for index in range(len(axis) - 1):
        if value <= axis[index + 1]:
            return index
    return None


def _point(request: SourceRequest, result: SourceResult) -> EnvelopePoint:
    performance = result.performance
    return EnvelopePoint(
        mass_flow_kg_s=request.operating_point.requested_mass_flow_kg_s,
        injection_temperature_k=request.operating_point.injection_temperature_k,
        state=result.state,
        active_limits=result.active_limits,
        wellhead_production_temperature_k=(
            performance.wellhead_production_temperature_k if performance else None
        ),
        geothermal_extracted_heat_w=(
            performance.geothermal_extracted_heat_w if performance else None
        ),
        electrical_pump_power_w=performance.electrical_pump_power_w if performance else None,
    )


def build_envelope(
    model: SourceModel,
    base_request: SourceRequest,
    mass_flows_kg_s: Sequence[float],
    injection_temperatures_k: Sequence[float],
) -> SourceEnvelope:
    """Evaluate the engine on a grid and measure the interpolation error at every cell centre."""
    capabilities = model.capabilities()
    points: list[EnvelopePoint] = []
    for flow in mass_flows_kg_s:
        for temperature in injection_temperatures_k:
            request = base_request.at(flow, temperature)
            points.append(_point(request, model.evaluate(request)))

    envelope = SourceEnvelope(
        source_scenario_id=base_request.source_scenario_id,
        engine=capabilities.engine,
        engine_version=capabilities.engine_version,
        model_family=base_request.model_family,
        synthetic=capabilities.synthetic,
        base_input_hash=base_request.at(
            mass_flows_kg_s[0], injection_temperatures_k[0]
        ).input_hash(),
        mass_flows_kg_s=tuple(mass_flows_kg_s),
        injection_temperatures_k=tuple(injection_temperatures_k),
        points=tuple(points),
    )

    cells: list[EnvelopeCell] = []
    heat_abs = heat_rel = temperature_abs = pump_rel = 0.0
    for i in range(len(mass_flows_kg_s) - 1):
        for j in range(len(injection_temperatures_k) - 1):
            corners = [envelope.point(i + a, j + b) for a in (0, 1) for b in (0, 1)]
            if any(corner.state is not AdapterState.COMPLETED for corner in corners):
                continue
            flow = (mass_flows_kg_s[i] + mass_flows_kg_s[i + 1]) / 2.0
            temperature = (injection_temperatures_k[j] + injection_temperatures_k[j + 1]) / 2.0
            exact = model.evaluate(base_request.at(flow, temperature)).performance
            if exact is None:
                continue
            cell = EnvelopeCell(flow_index=i, temperature_index=j)
            cells.append(cell)
            heat = envelope._bilinear(cell, flow, temperature, "geothermal_extracted_heat_w")
            wellhead = envelope._bilinear(
                cell, flow, temperature, "wellhead_production_temperature_k"
            )
            pump = envelope._bilinear(cell, flow, temperature, "electrical_pump_power_w")
            heat_abs = max(heat_abs, abs(heat - exact.geothermal_extracted_heat_w))
            heat_rel = max(
                heat_rel,
                abs(heat - exact.geothermal_extracted_heat_w) / exact.geothermal_extracted_heat_w,
            )
            temperature_abs = max(
                temperature_abs, abs(wellhead - exact.wellhead_production_temperature_k)
            )
            if exact.electrical_pump_power_w > 0.0:
                pump_rel = max(
                    pump_rel,
                    abs(pump - exact.electrical_pump_power_w) / exact.electrical_pump_power_w,
                )
    if not cells:
        return envelope
    return envelope.model_copy(
        update={
            "validated_cells": tuple(cells),
            "approximation_error": ApproximationError(
                cells_checked_count=len(cells),
                max_abs_heat_error_w=heat_abs,
                max_rel_heat_error_fraction=heat_rel,
                max_abs_temperature_error_delta_k=temperature_abs,
                max_rel_pump_power_error_fraction=pump_rel,
            ),
        }
    )


class EnvelopeLookup(Record):
    """Performance at one operating point, with where it came from."""

    origin: Literal["envelope", "engine"]
    interpolated: InterpolatedPerformance | None = None
    result: SourceResult | None = None


def lookup(
    model: SourceModel,
    envelope: SourceEnvelope,
    base_request: SourceRequest,
    mass_flow_kg_s: float,
    injection_temperature_k: float,
) -> EnvelopeLookup:
    """Use the envelope inside its validated domain and the engine everywhere else."""
    interpolated = envelope.interpolate(mass_flow_kg_s, injection_temperature_k)
    if interpolated is not None:
        return EnvelopeLookup(origin="envelope", interpolated=interpolated)
    result = model.evaluate(base_request.at(mass_flow_kg_s, injection_temperature_k))
    return EnvelopeLookup(origin="engine", result=result)
