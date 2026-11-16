"""Connection, route and network records (specification 7.2)."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

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
from r3chain.domain.evidence import Evidence
from r3chain.domain.records.common import Geometry, require_unique, value_or_none
from r3chain.domain.units import Pressure, UnitError, require_same_reference, require_unit_suffix


class HeatCarrier(StrEnum):
    HOT_WATER = "hot_water"
    STEAM = "steam"


class InterfaceType(StrEnum):
    # The plant circuit is separated from the network by a heat exchanger.
    SEPARATED_EXCHANGER = "separated_exchanger"
    # The plant feeds the network circuit directly.
    DIRECT_HYDRAULIC = "direct_hydraulic"


class ConnectionLimits(Record):
    max_heat_w: Evidence[Real]
    max_mass_flow_kg_s: Evidence[Real]
    required_supply_temperature_k: Evidence[Real]
    return_temperature_k: Evidence[Real]
    min_pressure: Evidence[Pressure]
    max_pressure: Evidence[Pressure]

    @model_validator(mode="after")
    def _limits_are_consistent(self) -> ConnectionLimits:
        for name in ("max_heat_w", "max_mass_flow_kg_s"):
            value = value_or_none(getattr(self, name))
            if value is not None and value <= 0.0:
                raise ValueError(f"{name} must be positive")
        supply = value_or_none(self.required_supply_temperature_k)
        back = value_or_none(self.return_temperature_k)
        for name, temperature in (
            ("required_supply_temperature_k", supply),
            ("return_temperature_k", back),
        ):
            if temperature is not None and temperature <= 0.0:
                raise ValueError(f"{name} must be an absolute temperature above 0 K")
        if supply is not None and back is not None and supply <= back:
            raise ValueError("required supply temperature must be above the return temperature")
        low = value_or_none(self.min_pressure)
        high = value_or_none(self.max_pressure)
        if low is not None and high is not None:
            try:
                require_same_reference(low, high)
            except UnitError as error:
                raise ValueError(str(error)) from error
            if low.value_pa >= high.value_pa:
                raise ValueError("min_pressure must be below max_pressure")
        return self


class Connection(VersionedRecord):
    """A supply and return node pair at which a plant can exchange heat with a network."""

    SCHEMA_NAME: ClassVar[str] = "connection"
    schema_version: Literal["0.1.0"] = "0.1.0"

    connection_id: Identifier
    network_id: Identifier
    zone_id: Identifier
    supply_node: Identifier
    return_node: Identifier
    interface_type: InterfaceType
    heat_carrier: HeatCarrier
    limits: ConnectionLimits

    @model_validator(mode="after")
    def _is_a_real_interface(self) -> Connection:
        if self.supply_node == self.return_node:
            raise ValueError("a connection needs distinct supply and return nodes")
        return self


class Crossing(Record):
    crossing_id: Identifier
    kind: NonEmptyStr
    chainage_m: NonNegativeReal


class ProfilePoint(Record):
    chainage_m: NonNegativeReal
    elevation_m: Real


class Route(VersionedRecord):
    """A corridor between a site and a connection.

    Trench length and pipe lengths are stored separately so that costs cannot be counted twice.
    """

    SCHEMA_NAME: ClassVar[str] = "route"
    schema_version: Literal["0.1.0"] = "0.1.0"

    route_id: Identifier
    from_site_id: Identifier
    to_connection_id: Identifier
    geometry: Geometry
    corridor_length_m: PositiveReal
    supply_pipe_length_m: PositiveReal
    return_pipe_length_m: PositiveReal
    crossings: tuple[Crossing, ...] = ()
    elevation_profile: tuple[ProfilePoint, ...] = ()
    vertical_datum: NonEmptyStr
    linked_customer_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> Route:
        require_unique((crossing.crossing_id for crossing in self.crossings), "crossing_id")
        require_unique(self.linked_customer_ids, "linked customer")
        chainages = [point.chainage_m for point in self.elevation_profile]
        if chainages != sorted(chainages):
            raise ValueError("elevation profile points must be ordered by chainage")
        for chainage in (*chainages, *(crossing.chainage_m for crossing in self.crossings)):
            if chainage > self.corridor_length_m:
                raise ValueError("a chainage lies beyond the corridor length")
        return self


class Circuit(StrEnum):
    SUPPLY = "supply"
    RETURN = "return"


class NetworkNode(Record):
    node_id: Identifier
    original_id: str | None = None
    circuit: Circuit
    elevation_m: Real
    zone_id: Identifier


_PIPE_NUMERIC_FIELDS = frozenset(
    {"length_m", "inner_diameter_m", "roughness_m", "heat_transfer_coefficient_w_m2_k"}
)


class NetworkPipe(Record):
    pipe_id: Identifier
    original_id: str | None = None
    from_node: Identifier
    to_node: Identifier
    length_m: PositiveReal
    inner_diameter_m: PositiveReal
    roughness_m: NonNegativeReal
    heat_transfer_coefficient_w_m2_k: NonNegativeReal
    # Names of the fields above whose values were estimated instead of taken from the source.
    estimated_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> NetworkPipe:
        if self.from_node == self.to_node:
            raise ValueError("a pipe must connect two different nodes")
        unknown = set(self.estimated_fields) - _PIPE_NUMERIC_FIELDS
        if unknown:
            raise ValueError(f"estimated_fields names unknown pipe fields: {sorted(unknown)}")
        return self


class Consumer(Record):
    consumer_id: Identifier
    supply_node: Identifier
    return_node: Identifier
    design_heat_w: PositiveReal


class Equipment(Record):
    equipment_id: Identifier
    kind: NonEmptyStr
    node_ids: tuple[Identifier, ...]
    parameters: dict[str, Real] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parameters_carry_units(self) -> Equipment:
        for name in self.parameters:
            require_unit_suffix(name)
        return self


class Control(Record):
    control_id: Identifier
    kind: NonEmptyStr
    target_id: Identifier
    parameters: dict[str, Real] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _parameters_carry_units(self) -> Control:
        for name in self.parameters:
            require_unit_suffix(name)
        return self


class BoundaryKind(StrEnum):
    PRESSURE_REFERENCE = "pressure_reference"
    FIXED_SUPPLY_TEMPERATURE = "fixed_supply_temperature"


class BoundaryCondition(Record):
    boundary_id: Identifier
    node_id: Identifier
    kind: BoundaryKind
    pressure: Pressure | None = None
    temperature_k: PositiveReal | None = None

    @model_validator(mode="after")
    def _has_its_value(self) -> BoundaryCondition:
        if self.kind is BoundaryKind.PRESSURE_REFERENCE and self.pressure is None:
            raise ValueError("a pressure_reference boundary needs a pressure")
        if self.kind is BoundaryKind.FIXED_SUPPLY_TEMPERATURE and self.temperature_k is None:
            raise ValueError("a fixed_supply_temperature boundary needs temperature_k")
        return self


class Fluid(Record):
    name: NonEmptyStr
    property_model: NonEmptyStr


class Network(VersionedRecord):
    """A district heating network with separate supply and return circuits."""

    SCHEMA_NAME: ClassVar[str] = "network"
    schema_version: Literal["0.1.0"] = "0.1.0"

    network_id: Identifier
    version: NonEmptyStr
    dataset_id: Identifier
    heat_carrier: HeatCarrier
    fluid: Fluid
    vertical_datum: NonEmptyStr
    nodes: tuple[NetworkNode, ...]
    pipes: tuple[NetworkPipe, ...]
    consumers: tuple[Consumer, ...] = ()
    equipment: tuple[Equipment, ...] = ()
    controls: tuple[Control, ...] = ()
    boundary_conditions: tuple[BoundaryCondition, ...] = ()

    @model_validator(mode="after")
    def _topology_is_consistent(self) -> Network:
        require_unique((node.node_id for node in self.nodes), "node_id")
        require_unique((pipe.pipe_id for pipe in self.pipes), "pipe_id")
        require_unique((consumer.consumer_id for consumer in self.consumers), "consumer_id")
        require_unique((item.equipment_id for item in self.equipment), "equipment_id")
        require_unique((item.control_id for item in self.controls), "control_id")
        require_unique((item.boundary_id for item in self.boundary_conditions), "boundary_id")

        circuits = {node.node_id: node.circuit for node in self.nodes}

        def circuit_of(node_id: str, where: str) -> Circuit:
            if node_id not in circuits:
                raise ValueError(f"{where} refers to unknown node {node_id!r}")
            return circuits[node_id]

        for pipe in self.pipes:
            start = circuit_of(pipe.from_node, f"pipe {pipe.pipe_id}")
            end = circuit_of(pipe.to_node, f"pipe {pipe.pipe_id}")
            if start is not end:
                raise ValueError(
                    f"pipe {pipe.pipe_id} joins the supply and return circuits. "
                    "Only consumers and equipment may link the two circuits"
                )
        for consumer in self.consumers:
            where = f"consumer {consumer.consumer_id}"
            if circuit_of(consumer.supply_node, where) is not Circuit.SUPPLY:
                raise ValueError(f"{where}: supply_node must lie in the supply circuit")
            if circuit_of(consumer.return_node, where) is not Circuit.RETURN:
                raise ValueError(f"{where}: return_node must lie in the return circuit")
        for item in self.equipment:
            for node_id in item.node_ids:
                circuit_of(node_id, f"equipment {item.equipment_id}")
        for boundary in self.boundary_conditions:
            circuit_of(boundary.node_id, f"boundary {boundary.boundary_id}")
        return self

    def node_ids(self) -> frozenset[str]:
        return frozenset(node.node_id for node in self.nodes)

    def pressures(self) -> tuple[Pressure, ...]:
        return tuple(
            boundary.pressure
            for boundary in self.boundary_conditions
            if boundary.pressure is not None
        )
