"""The source port: what every geothermal source model must accept and return.

Specification sections 7.3, 7.4 and 9. The core never talks to a source engine directly. It
builds a ``SourceRequest``, hands it to a ``SourceModel`` and reads a ``SourceResult``. PyDoublet,
the synthetic test model and any later engine sit behind this one interface (extension point X4).

Rules that every adapter must keep:

* An engine declares what it supports. A request outside that returns ``unsupported_model``.
  It never returns a zero or a placeholder that looks like a result.
* Only a completed evaluation carries performance numbers.
* A numerical failure (``non_converged``) and a physical one (``physical_limit_exceeded``) are
  different states and are never merged.
* An adapter holds no state between calls. The same request always gives the same result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal, Protocol

from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeReal,
    PositiveReal,
    Real,
    Record,
    VersionedRecord,
)
from r3chain.domain.evidence import InputReader
from r3chain.domain.records.runs import Check
from r3chain.domain.records.site import WellRole, WellScenario
from r3chain.domain.status import AdapterState
from r3chain.domain.units import Pressure, require_unit_suffix

# --- request --------------------------------------------------------------------------------


class SourceOperatingPoint(Record):
    """The operating point asked for. Whether it is acceptable is for the engine to decide."""

    requested_mass_flow_kg_s: Real
    injection_temperature_k: Real


class SourceNumericalSettings(Record):
    max_iterations_count: int = Field(ge=1)
    temperature_tolerance_delta_k: PositiveReal


class SourceWell(Record):
    well_id: Identifier
    role: WellRole
    vertical_depth_m: Real
    measured_length_m: Real


class SourceRequest(VersionedRecord):
    """One evaluation request. Its hash identifies the inputs of the result."""

    SCHEMA_NAME: ClassVar[str] = "source_request"
    schema_version: Literal["0.1.0"] = "0.1.0"

    study_id: Identifier
    source_scenario_id: Identifier
    site_id: Identifier
    parameter_package_id: Identifier
    model_family: NonEmptyStr
    well_configuration_id: Identifier
    wells: tuple[SourceWell, ...]
    # Resolved values of the reservoir parameters. Every key carries its unit as a suffix.
    reservoir_parameters: dict[str, Real]
    operating_point: SourceOperatingPoint
    numerical: SourceNumericalSettings
    sample_id: NonEmptyStr = "deterministic"
    seed: int = 0

    @model_validator(mode="after")
    def _parameters_carry_units(self) -> SourceRequest:
        for name in self.reservoir_parameters:
            require_unit_suffix(name)
        return self

    def input_hash(self) -> str:
        return self.content_hash()

    def at(self, mass_flow_kg_s: float, injection_temperature_k: float) -> SourceRequest:
        """Return the same request at another operating point."""
        point = SourceOperatingPoint(
            requested_mass_flow_kg_s=mass_flow_kg_s,
            injection_temperature_k=injection_temperature_k,
        )
        return self.model_copy(update={"operating_point": point})


# --- result ---------------------------------------------------------------------------------


class OutputSemantics(StrEnum):
    """What a reported output means (specification 7.4)."""

    # The most the source can provide at this operating point.
    AVAILABLE_CAPACITY = "available_capacity"
    # What a dispatch decided to take. Never produced by a source model on its own.
    DISPATCHED_OUTPUT = "dispatched_output"
    # The requested value, which the solver achieved.
    REQUESTED_VALUE_ACHIEVED = "requested_value_achieved"


class SourcePerformance(Record):
    """Performance at the wellhead, the physical handover point.

    Wellbore losses and pumping are already inside these numbers. Nothing downstream may deduct
    them a second time.
    """

    mass_flow_kg_s: PositiveReal
    mass_flow_semantics: OutputSemantics
    wellhead_production_temperature_k: PositiveReal
    injection_temperature_k: PositiveReal
    geothermal_extracted_heat_w: PositiveReal
    heat_semantics: OutputSemantics
    electrical_pump_power_w: NonNegativeReal
    # Pressure differences, so they carry no absolute or gauge reference.
    production_drawdown_pa: NonNegativeReal | None = None
    injection_pressure_rise_pa: NonNegativeReal | None = None
    production_wellhead_pressure: Pressure | None = None
    injection_wellhead_pressure: Pressure | None = None


class SourceResult(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "source_result"
    schema_version: Literal["0.1.0"] = "0.1.0"

    engine: NonEmptyStr
    engine_version: NonEmptyStr
    fidelity: NonEmptyStr
    model_family: NonEmptyStr
    source_scenario_id: Identifier
    input_hash: str
    sample_id: NonEmptyStr
    state: AdapterState
    reason: str = ""
    performance: SourcePerformance | None = None
    # Limits that were evaluated, each with observed value, limit, margin and origin.
    checks: tuple[Check, ...] = ()
    active_limits: tuple[str, ...] = ()
    # Outputs of the contract that this engine does not calculate.
    not_calculated: tuple[str, ...] = ()
    # False means no upstream economics exist and the system ledger must supply every cost.
    economics_calculated: bool = False
    iterations_count: int | None = None
    warnings: tuple[str, ...] = ()
    # True when the engine is a test model. Such a result can never enter a pilot ranking.
    synthetic: bool
    raw_request: dict[str, Any] | None = None
    raw_result: dict[str, Any] | None = None
    artifact_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _only_a_completed_result_has_numbers(self) -> SourceResult:
        completed = self.state is AdapterState.COMPLETED
        if completed and self.performance is None:
            raise ValueError("a completed source result must carry its performance")
        if not completed and self.performance is not None:
            raise ValueError(f"a {self.state.value} source result must not carry performance")
        if not completed and not self.reason:
            raise ValueError(f"a {self.state.value} source result must give its reason")
        return self


# --- capabilities ---------------------------------------------------------------------------


class SourceCapabilities(Record):
    """What an engine supports. Anything not listed here is unsupported."""

    engine: NonEmptyStr
    engine_version: NonEmptyStr
    fidelity: NonEmptyStr
    synthetic: bool
    model_families: tuple[str, ...]
    well_configurations: tuple[str, ...]
    required_reservoir_parameters: dict[str, tuple[str, ...]]
    optional_reservoir_parameters: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    provides_economics: bool = False
    provides_wellhead_pressures: bool = False
    provides_long_term_decline: bool = False
    provides_scaling_assessment: bool = False
    # Capabilities that are planned but not available in this engine version.
    planned: tuple[str, ...] = ()

    def supports(self, model_family: str) -> bool:
        return model_family in self.model_families


class SourceModel(Protocol):
    def capabilities(self) -> SourceCapabilities: ...

    def evaluate(self, request: SourceRequest) -> SourceResult: ...


# --- building a request from a scenario -----------------------------------------------------


def build_source_request(
    *,
    study_id: str,
    well_scenario: WellScenario,
    operating_point: SourceOperatingPoint,
    numerical: SourceNumericalSettings,
    capabilities: SourceCapabilities,
    sample_id: str = "deterministic",
    seed: int = 0,
) -> SourceRequest:
    """Resolve the inputs of a well scenario into a request.

    Raises ``NotEstablished`` when a parameter that the engine requires, or a well depth, is
    unknown or in conflict. Optional parameters that are not known are left out.
    """
    base = f"well_scenarios[{well_scenario.source_scenario_id}]"
    reader = InputReader()
    required = set(capabilities.required_reservoir_parameters.get(well_scenario.model_family, ()))
    parameters: dict[str, float] = {}
    for name, evidence in well_scenario.reservoir_parameters.items():
        path = f"{base}.reservoir_parameters[{name}]"
        if name in required:
            value = reader.get(evidence, path)
        else:
            value = InputReader().get(evidence, path)
        if value is not None:
            parameters[name] = value

    wells: list[SourceWell] = []
    for well in well_scenario.wells:
        well_path = f"{base}.wells[{well.well_id}]"
        vertical = reader.get(well.vertical_depth, f"{well_path}.vertical_depth")
        measured = reader.get(well.measured_length, f"{well_path}.measured_length")
        if vertical is not None and measured is not None:
            wells.append(
                SourceWell(
                    well_id=well.well_id,
                    role=well.role,
                    vertical_depth_m=vertical.value_m,
                    measured_length_m=measured.value_m,
                )
            )
    reader.raise_if_missing()
    return SourceRequest(
        study_id=study_id,
        source_scenario_id=well_scenario.source_scenario_id,
        site_id=well_scenario.site_id,
        parameter_package_id=well_scenario.parameter_package_id,
        model_family=well_scenario.model_family,
        well_configuration_id=well_scenario.well_configuration_id,
        wells=tuple(wells),
        reservoir_parameters=parameters,
        operating_point=operating_point,
        numerical=numerical,
        sample_id=sample_id,
        seed=seed,
    )
