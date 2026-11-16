"""Plant, operating case and coupling case records.

Specification section 10 needs three things that the thirteen core records of section 7.2 do not
hold: how geothermal heat reaches the network (the plant), what the network asks for at one
moment (the operating case), and which source, plant, connection and operating case are to be
evaluated together (the coupling case).

Plant
    One integration concept. Its ``arrangement`` names an entry of the plant registry in
    ``r3chain.coupling.plant``. The equipment values are engineering inputs with provenance.
    Which values an arrangement requires is declared by the registry, in the same way a source
    engine declares its reservoir parameters.

OperatingCase
    One steady state of a network: the heat every consumer takes and the ground temperature.

CouplingCase
    One coupled operating point to evaluate. From milestone M3 these are generated from the
    candidates. Until then they are written out explicitly.
"""

from __future__ import annotations

from enum import StrEnum
from itertools import pairwise
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import Identifier, NonEmptyStr, Real, Record, VersionedRecord
from r3chain.domain.evidence import Evidence
from r3chain.domain.records.common import require_unique, value_or_none
from r3chain.domain.units import require_unit_suffix

# --- plant ----------------------------------------------------------------------------------


class HeatPumpMap(Record):
    """Tabulated heat pump performance from a manufacturer or another reviewed source.

    Rows follow ``source_inlet_temperatures_k`` and columns follow
    ``sink_outlet_temperatures_k``. The coefficient of performance is the condenser heat divided
    by the electrical input of the compressor. An operating point outside the table is outside
    the map. It is never extrapolated.
    """

    map_id: Identifier
    description: NonEmptyStr
    source_inlet_temperatures_k: tuple[Real, ...] = Field(min_length=2)
    sink_outlet_temperatures_k: tuple[Real, ...] = Field(min_length=2)
    heating_cop_ratio: tuple[tuple[Real, ...], ...]
    max_condenser_heat_w: tuple[tuple[Real, ...], ...]

    @model_validator(mode="after")
    def _is_a_complete_table(self) -> HeatPumpMap:
        for name in ("source_inlet_temperatures_k", "sink_outlet_temperatures_k"):
            axis = getattr(self, name)
            if axis[0] <= 0.0:
                raise ValueError(f"{name} must hold absolute temperatures above 0 K")
            if any(second <= first for first, second in pairwise(axis)):
                raise ValueError(f"{name} must increase strictly")
        rows = len(self.source_inlet_temperatures_k)
        columns = len(self.sink_outlet_temperatures_k)
        for name, least, why in (
            ("heating_cop_ratio", 1.0, "a heating coefficient of performance is above 1"),
            ("max_condenser_heat_w", 0.0, "a capacity is positive"),
        ):
            table = getattr(self, name)
            if len(table) != rows or any(len(row) != columns for row in table):
                raise ValueError(f"{name} must have {rows} rows of {columns} values")
            if any(value <= least for row in table for value in row):
                raise ValueError(f"{name}: {why}")
        return self


class Plant(VersionedRecord):
    """One integration concept between a geothermal source and a network connection."""

    SCHEMA_NAME: ClassVar[str] = "plant"
    schema_version: Literal["0.1.0"] = "0.1.0"

    plant_id: Identifier
    arrangement: NonEmptyStr
    description: str = ""
    # The required keys depend on the arrangement. Every key carries its unit as a suffix.
    parameters: dict[str, Evidence[Real]]
    # Needed by an arrangement with a heat pump, unless it uses the labelled screening model.
    heat_pump_map: Evidence[HeatPumpMap] | None = None

    @model_validator(mode="after")
    def _parameters_carry_units(self) -> Plant:
        for name in self.parameters:
            require_unit_suffix(name)
        return self


# --- operating case -------------------------------------------------------------------------


class ConsumerLoad(Record):
    """The demand of one consumer in one operating case.

    Exactly one control is given: a fixed mass flow, or a return temperature that the consumer
    reaches by adjusting its flow.
    """

    consumer_id: Identifier
    heat_w: Real
    mass_flow_kg_s: Real | None = None
    return_temperature_k: Real | None = None

    @model_validator(mode="after")
    def _has_exactly_one_control(self) -> ConsumerLoad:
        if (self.mass_flow_kg_s is None) == (self.return_temperature_k is None):
            raise ValueError("give exactly one of mass_flow_kg_s and return_temperature_k")
        return self


class OperatingCase(VersionedRecord):
    """One steady state of a network.

    Every consumer of the network needs a load. A consumer that takes nothing is stated with a
    heat of zero, so that an omission can never pass for a closed consumer.
    """

    SCHEMA_NAME: ClassVar[str] = "operating_case"
    schema_version: Literal["0.1.0"] = "0.1.0"

    operating_case_id: Identifier
    network_id: Identifier
    description: str = ""
    ground_temperature_k: Evidence[Real]
    loads: Evidence[tuple[ConsumerLoad, ...]]

    @model_validator(mode="after")
    def _is_consistent(self) -> OperatingCase:
        ground = value_or_none(self.ground_temperature_k)
        if ground is not None and ground <= 0.0:
            raise ValueError("ground_temperature_k must be an absolute temperature above 0 K")
        loads = value_or_none(self.loads)
        if loads is not None:
            require_unique((load.consumer_id for load in loads), "consumer load")
        return self


# --- coupling case --------------------------------------------------------------------------


class FlowControl(StrEnum):
    # The geothermal flow is the smallest one that covers the demand, up to the source limits.
    FOLLOW_DEMAND = "follow_demand"
    # The geothermal flow is given. The plant takes from it only what the network asks for.
    FIXED = "fixed"


class BackupSource(Record):
    """An explicit heat source that tops up what the plant cannot deliver.

    It stands in series after the plant at the same connection. Its heat is always reported as
    its own ledger entry. Without a backup, a shortfall is reported as a deficit.
    """

    backup_id: Identifier
    description: NonEmptyStr
    max_heat_w: Evidence[Real]

    @model_validator(mode="after")
    def _capacity_is_positive(self) -> BackupSource:
        capacity = value_or_none(self.max_heat_w)
        if capacity is not None and capacity <= 0.0:
            raise ValueError("max_heat_w must be positive")
        return self


class CouplingCase(VersionedRecord):
    """One coupled operating point: a source, a plant, a connection and an operating case."""

    SCHEMA_NAME: ClassVar[str] = "coupling_case"
    schema_version: Literal["0.1.0"] = "0.1.0"

    coupling_case_id: Identifier
    description: str = ""
    source_scenario_id: Identifier
    plant_id: Identifier
    connection_id: Identifier
    route_id: Identifier | None = None
    operating_case_id: Identifier
    flow_control: FlowControl
    fixed_geothermal_mass_flow_kg_s: Evidence[Real] | None = None
    # Pressure difference that the circulation pump holds between the supply and the return
    # node of the connection.
    network_pressure_lift_pa: Evidence[Real]
    backup: BackupSource | None = None

    @model_validator(mode="after")
    def _control_is_complete(self) -> CouplingCase:
        fixed = self.flow_control is FlowControl.FIXED
        if fixed and self.fixed_geothermal_mass_flow_kg_s is None:
            raise ValueError("flow_control 'fixed' needs fixed_geothermal_mass_flow_kg_s")
        if not fixed and self.fixed_geothermal_mass_flow_kg_s is not None:
            raise ValueError(
                "fixed_geothermal_mass_flow_kg_s is only allowed with flow_control 'fixed'"
            )
        for name in ("fixed_geothermal_mass_flow_kg_s", "network_pressure_lift_pa"):
            evidence = getattr(self, name)
            value = value_or_none(evidence) if evidence is not None else None
            if value is not None and value <= 0.0:
                raise ValueError(f"{name} must be positive")
        return self
