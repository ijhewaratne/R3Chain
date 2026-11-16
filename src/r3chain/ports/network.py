"""The network port: what every district heating network model must accept and return.

Specification section 11. The core builds a ``NetworkCase`` for a canonical ``Network`` and
reads a ``NetworkResult``. pandapipes sits behind this interface, and a steam model or another
engine can be added beside it (extension point X5).

Rules that every adapter must keep:

* The canonical records stay in SI units. Every conversion to engine units happens inside the
  adapter.
* A heat carrier or component the adapter does not model returns ``unsupported_model``. It is
  never silently ignored.
* The heat a supply point feeds in is reported explicitly. A supply point in this milestone is
  an ideal source, so its heat is the amount a later coupling step must cover with a finite
  source. It is never hidden.
* A branch without flow has no defined steady state temperature. Its temperature is reported as
  absent. An engine default is never passed on as a result.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal, Protocol

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
from r3chain.domain.hashing import content_hash
from r3chain.domain.records.common import require_unique
from r3chain.domain.records.network import HeatCarrier, Network
from r3chain.domain.records.runs import Check
from r3chain.domain.status import AdapterState
from r3chain.domain.units import Pressure, PressureReference

# --- case -----------------------------------------------------------------------------------


class CalculationMode(StrEnum):
    # Pressures and flows only, at one uniform fluid temperature.
    HYDRAULIC = "hydraulic"
    # Pressures, flows and temperatures solved together.
    THERMAL = "thermal"


class FrictionModel(StrEnum):
    SWAMEE_JAIN = "swamee_jain"
    NIKURADSE = "nikuradse"
    COLEBROOK = "colebrook"


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


class SupplyPoint(Record):
    """A circulating heat source at a connection.

    It holds the return side at ``return_pressure``, raises the pressure by ``pressure_lift_pa``
    and feeds the supply side at ``supply_temperature_k``. The mass flow and the heat it has to
    provide follow from the network.
    """

    supply_point_id: Identifier
    supply_node: Identifier
    return_node: Identifier
    supply_temperature_k: Real
    return_pressure: Pressure
    pressure_lift_pa: Real


class NetworkNumericalSettings(Record):
    max_iterations_count: int = Field(ge=1)
    pipe_sections_count: int = Field(ge=1)
    friction_model: FrictionModel
    # A branch that carries less than this is treated as stagnant.
    zero_flow_tolerance_kg_s: NonNegativeReal


class NetworkCase(VersionedRecord):
    """One steady operating case of one network."""

    SCHEMA_NAME: ClassVar[str] = "network_case"
    schema_version: Literal["0.1.0"] = "0.1.0"

    case_id: Identifier
    network_id: Identifier
    calculation: CalculationMode
    loads: tuple[ConsumerLoad, ...]
    supply_points: tuple[SupplyPoint, ...]
    ground_temperature_k: Real
    # Optional limits on node pressure, with the place they come from.
    min_node_pressure: Pressure | None = None
    max_node_pressure: Pressure | None = None
    pressure_limit_origin: NonEmptyStr | None = None
    numerical: NetworkNumericalSettings

    @model_validator(mode="after")
    def _is_consistent(self) -> NetworkCase:
        require_unique((load.consumer_id for load in self.loads), "consumer load")
        require_unique((point.supply_point_id for point in self.supply_points), "supply_point_id")
        has_limit = self.min_node_pressure is not None or self.max_node_pressure is not None
        if has_limit and self.pressure_limit_origin is None:
            raise ValueError("a node pressure limit must state its origin")
        return self


def network_input_hash(network: Network, case: NetworkCase) -> str:
    return content_hash({"network": network.to_json_dict(), "case": case.to_json_dict()})


# --- result ---------------------------------------------------------------------------------


class NodeResult(Record):
    node_id: Identifier
    pressure: Pressure
    temperature_k: PositiveReal | None = None


class PipeResult(Record):
    """Signed quantities are positive in the direction from ``from_node`` to ``to_node``."""

    pipe_id: Identifier
    mass_flow_kg_s: Real
    velocity_m_s: Real
    # Absolute pressure at the from node minus absolute pressure at the to node.
    pressure_drop_pa: Real
    # The part of the pressure drop caused by friction. The rest is the static height change.
    friction_pressure_drop_pa: Real
    stagnant: bool
    inlet_temperature_k: PositiveReal | None = None
    outlet_temperature_k: PositiveReal | None = None
    heat_loss_w: Real | None = None


class ConsumerResult(Record):
    consumer_id: Identifier
    # True when the consumer takes no flow in this case.
    closed: bool
    delivered_heat_w: NonNegativeReal
    mass_flow_kg_s: NonNegativeReal
    differential_pressure_pa: Real
    supply_temperature_k: PositiveReal | None = None
    return_temperature_k: PositiveReal | None = None


class SupplyPointResult(Record):
    supply_point_id: Identifier
    mass_flow_kg_s: Real
    pressure_lift_pa: Real
    hydraulic_power_w: Real
    supply_temperature_k: PositiveReal
    return_temperature_k: PositiveReal | None = None
    # Heat fed into the network by this ideal source. Absent in a hydraulic calculation.
    heat_supplied_w: Real | None = None


class EnergyBalance(Record):
    heat_supplied_w: Real
    heat_delivered_w: Real
    pipe_heat_loss_w: Real
    residual_w: Real


# A physical limit that was exceeded is a valid evaluation and normally keeps its numbers. It
# comes without node results only when the solution itself is impossible, for example an absolute
# pressure below zero, which a pressure value cannot hold.
_WITH_RESULTS = frozenset({AdapterState.COMPLETED, AdapterState.PHYSICAL_LIMIT_EXCEEDED})


class NetworkResult(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "network_result"
    schema_version: Literal["0.1.0"] = "0.1.0"

    engine: NonEmptyStr
    engine_version: NonEmptyStr
    fluid_model: NonEmptyStr
    network_id: Identifier
    case_id: Identifier
    input_hash: str
    calculation: CalculationMode
    state: AdapterState
    reason: str = ""
    # The reference of every node pressure in this result.
    pressure_reference: PressureReference | None = None
    nodes: tuple[NodeResult, ...] = ()
    pipes: tuple[PipeResult, ...] = ()
    consumers: tuple[ConsumerResult, ...] = ()
    supply_points: tuple[SupplyPointResult, ...] = ()
    energy_balance: EnergyBalance | None = None
    # Largest imbalance of mass flow at any node.
    mass_balance_residual_kg_s: NonNegativeReal | None = None
    checks: tuple[Check, ...] = ()
    active_limits: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _results_match_the_state(self) -> NetworkResult:
        if self.state is AdapterState.COMPLETED and (
            not self.nodes or self.pressure_reference is None
        ):
            raise ValueError("a completed network result must carry node results")
        if self.state not in _WITH_RESULTS and (
            self.nodes or self.pipes or self.consumers or self.supply_points
        ):
            raise ValueError(f"a {self.state.value} network result must not carry results")
        if self.state is not AdapterState.COMPLETED and not self.reason:
            raise ValueError(f"a {self.state.value} network result must give its reason")
        return self

    def node(self, node_id: str) -> NodeResult:
        return next(item for item in self.nodes if item.node_id == node_id)

    def pipe(self, pipe_id: str) -> PipeResult:
        return next(item for item in self.pipes if item.pipe_id == pipe_id)

    def consumer(self, consumer_id: str) -> ConsumerResult:
        return next(item for item in self.consumers if item.consumer_id == consumer_id)


# --- capabilities ---------------------------------------------------------------------------


class NetworkCapabilities(Record):
    engine: NonEmptyStr
    engine_version: NonEmptyStr
    fluid_model: NonEmptyStr
    heat_carriers: tuple[HeatCarrier, ...]
    calculations: tuple[CalculationMode, ...]
    friction_models: tuple[FrictionModel, ...]
    max_supply_points: int
    supports_transient: bool = False
    supports_equipment: bool = False
    planned: tuple[str, ...] = ()


class NetworkModel(Protocol):
    def capabilities(self) -> NetworkCapabilities: ...

    def solve(self, network: Network, case: NetworkCase) -> NetworkResult: ...
