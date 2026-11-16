"""The coupling of source, plant and network (specification 10.4).

One coupled operating point must satisfy three models at once: the source gives a wellhead
temperature for a flow and an injection temperature, the plant turns that into heat for the
network and returns the geothermal stream at some temperature, and the network says what it
needs at the connection.

What is fixed and what is found
    The network is solved with the supply temperature the connection requires. Its return
    temperature, its mass flow and the heat it takes at the connection are the demand. The
    geothermal flow and the injection temperature are found by iteration.

The iteration, once per pass
    1. Evaluate the source at the trial flow and the trial injection temperature.
    2. Solve the plant with the wellhead temperature the source returned. In ``follow_demand``
       control the plant states the geothermal flow it needs.
    3. Dispatch only the heat the plant can deliver. A shortfall goes to a declared backup
       source, and what is left is a deficit.
    4. Compare. The temperature residual is the plant's geothermal outlet against the trial
       injection temperature. The flow residual is the plant's flow against the trial flow. The
       heat residual is the heat the source reports as extracted against the heat the plant
       takes from the geothermal stream.
    5. Stop when all three residuals are within the numerical policy. Otherwise move the trial
       values towards the plant's values, damped by the policy, and repeat.

Outcomes that are never merged
    ``completed``                the demand is covered and every check passes.
    ``physical_limit_exceeded``  a valid evaluation that fails: a deficit or a failed check.
    ``non_converged``            the residuals did not come within tolerance. Nothing is
                                 concluded about feasibility.
    A failure of the source or the network engine is passed on with its own state and origin.

No hidden heat
    The network model needs an ideal boundary at the connection. The heat it feeds in is exactly
    the demand. It is assigned to the plant and to a declared backup. Whatever is left is
    reported as unassigned and fails the evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.coupling.fluids import mean_heat_capacity_of_stream
from r3chain.coupling.ledger import (
    CONSUMERS,
    ELECTRICITY_GRID,
    GEOTHERMAL_CIRCUIT,
    GROUND,
    NETWORK,
    NETWORK_ACCOUNTING,
    PLANT,
    RESERVOIR,
    SURROUNDINGS,
    UNASSIGNED_SLACK,
    EnergyLedger,
    LedgerEntry,
    LedgerQuantity,
    SlackExchange,
    build_ledger,
)
from r3chain.coupling.plant import (
    GeothermalInlet,
    NetworkDemand,
    PlantPoint,
    PlantSpec,
    coldest_geothermal_outlet_k,
    evaluate_plant,
    performance_basis,
    required_geothermal_flow,
)
from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeReal,
    PositiveReal,
    Real,
    Record,
    VersionedRecord,
)
from r3chain.domain.evidence import MissingInput
from r3chain.domain.hashing import content_hash
from r3chain.domain.records.network import Network
from r3chain.domain.records.plant import FlowControl
from r3chain.domain.records.runs import Check, CheckOutcome, Policy
from r3chain.domain.status import AdapterState
from r3chain.domain.units import Pressure
from r3chain.ports.network import NetworkCase, NetworkModel, NetworkResult, network_input_hash
from r3chain.ports.source import SourceCapabilities, SourceModel, SourceRequest, SourceResult

# --- settings -------------------------------------------------------------------------------


class CouplingSettings(Record):
    """Iteration limit, tolerances and damping. They come from a numerical policy."""

    max_iterations_count: int = Field(ge=1)
    temperature_change_tolerance_delta_k: PositiveReal
    relative_heat_mismatch_tolerance_fraction: PositiveReal
    relative_flow_mismatch_tolerance_fraction: PositiveReal
    # Used instead of the relative tolerances near zero heat and zero flow.
    absolute_heat_mismatch_tolerance_w: NonNegativeReal
    absolute_flow_mismatch_tolerance_kg_s: NonNegativeReal
    # Share of each update that is applied. 1 applies it in full.
    damping_fraction: PositiveReal = Field(le=1.0)

    @classmethod
    def from_policy(cls, policy: Policy) -> CouplingSettings:
        values = policy.parameters
        missing = [name for name in cls.model_fields if name not in values]
        if missing:
            raise ValueError(
                f"policy {policy.policy_id} version {policy.version} lacks: {', '.join(missing)}"
            )
        return cls.model_validate(
            {
                name: int(values[name]) if name.endswith("_count") else float(values[name])
                for name in cls.model_fields
            }
        )

    def heat_tolerance_w(self, scale_w: float) -> float:
        return max(
            self.absolute_heat_mismatch_tolerance_w,
            self.relative_heat_mismatch_tolerance_fraction * abs(scale_w),
        )

    def flow_tolerance_kg_s(self, scale_kg_s: float) -> float:
        return max(
            self.absolute_flow_mismatch_tolerance_kg_s,
            self.relative_flow_mismatch_tolerance_fraction * abs(scale_kg_s),
        )


# --- problem --------------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionLimitValues:
    """The limits of a connection that are known. ``None`` means not established."""

    connection_id: str
    supply_node: str
    return_node: str
    max_heat_w: float | None = None
    max_mass_flow_kg_s: float | None = None
    min_pressure: Pressure | None = None
    max_pressure: Pressure | None = None

    def origin(self, name: str) -> str:
        return f"connections[{self.connection_id}].limits.{name}"


@dataclass(frozen=True)
class CouplingProblem:
    """Everything one coupled evaluation depends on, with every value resolved."""

    coupling_case_id: str
    # The operating point of this request is replaced on every pass.
    source_request: SourceRequest
    # The flow range the source is operated in. The coupling never leaves it.
    min_geothermal_mass_flow_kg_s: float
    max_geothermal_mass_flow_kg_s: float
    flow_range_origin: str
    flow_control: FlowControl
    plant: PlantSpec
    network: Network
    # One supply point at the connection, at the supply temperature the connection requires.
    network_case: NetworkCase
    limits: ConnectionLimitValues
    settings: CouplingSettings
    settings_origin: str
    fixed_geothermal_mass_flow_kg_s: float | None = None
    backup_id: str | None = None
    backup_max_heat_w: float | None = None
    # Optional inputs that are unknown or in conflict. Their checks stay unevaluated.
    inputs_not_established: tuple[MissingInput, ...] = ()
    not_modelled: tuple[str, ...] = ()

    def fingerprint(self) -> dict[str, Any]:
        return {
            "coupling_case_id": self.coupling_case_id,
            "source_request": self.source_request.to_json_dict(),
            "flow_range_kg_s": [
                self.min_geothermal_mass_flow_kg_s,
                self.max_geothermal_mass_flow_kg_s,
            ],
            "flow_control": self.flow_control.value,
            "fixed_geothermal_mass_flow_kg_s": self.fixed_geothermal_mass_flow_kg_s,
            "plant": self.plant.fingerprint(),
            "network": network_input_hash(self.network, self.network_case),
            "limits": {
                "connection_id": self.limits.connection_id,
                "max_heat_w": self.limits.max_heat_w,
                "max_mass_flow_kg_s": self.limits.max_mass_flow_kg_s,
                "min_pressure": (
                    self.limits.min_pressure.to_json_dict() if self.limits.min_pressure else None
                ),
                "max_pressure": (
                    self.limits.max_pressure.to_json_dict() if self.limits.max_pressure else None
                ),
            },
            "backup": [self.backup_id, self.backup_max_heat_w],
            "settings": self.settings.to_json_dict(),
        }

    def input_hash(self) -> str:
        return content_hash(self.fingerprint())


# --- result ---------------------------------------------------------------------------------


class FailureOrigin(StrEnum):
    SOURCE = "source"
    PLANT = "plant"
    NETWORK = "network"
    COUPLING = "coupling"


class ShortfallCause(StrEnum):
    # The source is not hot enough for the required supply temperature.
    INSUFFICIENT_TEMPERATURE = "insufficient_temperature"
    # The source, the exchanger or the heat pump cannot deliver enough heat.
    INSUFFICIENT_POWER = "insufficient_power"
    # The heat pump would have to operate outside its performance map.
    OUTSIDE_PERFORMANCE_MAP = "outside_performance_map"


class Residuals(Record):
    temperature_delta_k: NonNegativeReal
    heat_w: NonNegativeReal
    heat_fraction: NonNegativeReal
    flow_kg_s: NonNegativeReal
    flow_fraction: NonNegativeReal


class IterationRecord(Record):
    """One pass of the coupling iteration."""

    iteration: int
    # The trial values at which the source was evaluated.
    geothermal_mass_flow_kg_s: Real
    injection_temperature_k: Real
    wellhead_temperature_k: Real
    # What the plant made of them.
    plant_mass_flow_kg_s: Real
    plant_geothermal_outlet_temperature_k: Real
    plant_heat_to_network_w: Real
    residuals: Residuals
    within_tolerance: bool
    source_evaluations_count: int
    note: str = ""


class OperatingPoint(Record):
    """The coupled operating point. Heat and power in W, at one moment."""

    # False when the plant can take no heat from the source, so the wells are not run.
    source_operated: bool
    geothermal_mass_flow_kg_s: NonNegativeReal
    wellhead_temperature_k: PositiveReal | None = None
    injection_temperature_k: PositiveReal | None = None
    geothermal_extracted_heat_w: NonNegativeReal
    source_pump_electricity_w: NonNegativeReal
    network_mass_flow_kg_s: NonNegativeReal
    network_return_temperature_k: PositiveReal | None = None
    required_supply_temperature_k: PositiveReal
    plant_outlet_temperature_k: PositiveReal | None = None
    # Heat the network takes at the connection.
    demand_heat_w: NonNegativeReal
    plant_heat_w: NonNegativeReal
    backup_heat_w: NonNegativeReal
    # Heat that neither the plant nor a declared backup delivers.
    deficit_heat_w: NonNegativeReal
    compressor_electricity_w: NonNegativeReal
    network_pump_hydraulic_power_w: Real | None = None


_WITH_OPERATING_POINT = frozenset({AdapterState.COMPLETED, AdapterState.PHYSICAL_LIMIT_EXCEEDED})


class CouplingResult(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "coupling_result"
    schema_version: Literal["0.1.0"] = "0.1.0"

    coupling_case_id: Identifier
    input_hash: str
    state: AdapterState
    reason: str = ""
    failure_origin: FailureOrigin | None = None
    arrangement: NonEmptyStr
    # equipment_rating, performance_map or screening_model.
    performance_basis: NonEmptyStr
    # True when the source engine is a test model. Such a result never enters a pilot ranking.
    synthetic: bool
    engines: dict[str, str]
    # True when the network takes no heat at the connection, so nothing is operated.
    idle: bool = False
    operating_point: OperatingPoint | None = None
    plant: PlantPoint | None = None
    # Why the plant cannot cover the demand on its own. Empty when it can.
    shortfall_causes: tuple[ShortfallCause, ...] = ()
    # What stops the plant from covering the demand on its own, most specific first.
    limiting_conditions: tuple[str, ...] = ()
    backup_id: Identifier | None = None
    checks: tuple[Check, ...] = ()
    converged: bool = False
    iterations: tuple[IterationRecord, ...] = ()
    residuals: Residuals | None = None
    settings: CouplingSettings
    settings_origin: NonEmptyStr
    source_evaluations_count: int = 0
    ledger: EnergyLedger | None = None
    source_result: SourceResult | None = None
    network_result: NetworkResult | None = None
    inputs_not_established: tuple[MissingInput, ...] = ()
    not_modelled: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _numbers_match_the_state(self) -> CouplingResult:
        if self.state is AdapterState.COMPLETED and self.operating_point is None:
            raise ValueError("a completed coupling result must carry its operating point")
        if self.state not in _WITH_OPERATING_POINT and self.operating_point is not None:
            raise ValueError(
                f"a {self.state.value} coupling result must not carry an operating point"
            )
        if self.state is not AdapterState.COMPLETED and not self.reason:
            raise ValueError(f"a {self.state.value} coupling result must give its reason")
        if self.state is AdapterState.COMPLETED:
            failed = [check.check_id for check in self.checks if check.outcome is CheckOutcome.FAIL]
            if failed:
                raise ValueError(f"a completed coupling result has failed checks: {failed}")
        return self

    def check(self, check_id: str) -> Check:
        return next(item for item in self.checks if item.check_id == check_id)


# --- helpers --------------------------------------------------------------------------------

_NOT_MODELLED = (
    "heat loss and pressure loss of the route between the site and the connection",
    "electricity of the network circulation pump: only its hydraulic power is reported",
    "part load behaviour of pumps and of the heat pump",
)


def _prefixed(checks: tuple[Check, ...], prefix: str) -> tuple[Check, ...]:
    return tuple(
        check.model_copy(update={"check_id": f"{prefix}_{check.check_id}"}) for check in checks
    )


def _limit_check(
    check_id: str,
    description: str,
    observed: float,
    limit: float | None,
    *,
    upper: bool,
    unit: str,
    origin: str,
) -> Check:
    """A check against a limit that may not be established."""
    if limit is None:
        return Check(
            check_id=check_id,
            description=f"{description} was not evaluated: {origin} is not established",
            outcome=CheckOutcome.NOT_EVALUATED,
        )
    margin = limit - observed if upper else observed - limit
    return Check(
        check_id=check_id,
        description=description,
        outcome=CheckOutcome.PASS if margin >= -1e-9 * max(abs(limit), 1.0) else CheckOutcome.FAIL,
        observed=observed,
        limit=limit,
        margin=margin,
        unit=unit,
        threshold_origin=origin,
    )


def _connection_checks(
    problem: CouplingProblem, network: NetworkResult, heat_w: float, mass_flow_kg_s: float
) -> list[Check]:
    limits = problem.limits
    checks = [
        _limit_check(
            "connection_max_heat",
            "heat taken at the connection",
            heat_w,
            limits.max_heat_w,
            upper=True,
            unit="W",
            origin=limits.origin("max_heat_w"),
        ),
        _limit_check(
            "connection_max_mass_flow",
            "mass flow at the connection",
            mass_flow_kg_s,
            limits.max_mass_flow_kg_s,
            upper=True,
            unit="kg/s",
            origin=limits.origin("max_mass_flow_kg_s"),
        ),
    ]
    pressures = [
        network.node(node_id).pressure for node_id in (limits.supply_node, limits.return_node)
    ]
    for name, bound, observed, upper in (
        ("min_pressure", limits.min_pressure, min(p.value_pa for p in pressures), False),
        ("max_pressure", limits.max_pressure, max(p.value_pa for p in pressures), True),
    ):
        if bound is not None and bound.reference is not pressures[0].reference:
            checks.append(
                Check(
                    check_id=f"connection_{name}",
                    description=(
                        f"pressure at the connection was not evaluated: {limits.origin(name)} is "
                        f"{bound.reference.value}, the network result is "
                        f"{pressures[0].reference.value}"
                    ),
                    outcome=CheckOutcome.NOT_EVALUATED,
                )
            )
            continue
        checks.append(
            _limit_check(
                f"connection_{name}",
                f"{'highest' if upper else 'lowest'} pressure at the connection",
                observed,
                bound.value_pa if bound is not None else None,
                upper=upper,
                unit=f"Pa ({pressures[0].reference.value})",
                origin=limits.origin(name),
            )
        )
    return checks


def _shortfall_causes(limiting: list[str]) -> tuple[ShortfallCause, ...]:
    """Name the kinds of cause behind a list of limiting conditions."""
    causes: list[ShortfallCause] = []
    for name in limiting:
        if name == "exchanger_hot_end_approach":
            cause = ShortfallCause.INSUFFICIENT_TEMPERATURE
        elif name == "heat_pump_map_domain":
            cause = ShortfallCause.OUTSIDE_PERFORMANCE_MAP
        else:
            cause = ShortfallCause.INSUFFICIENT_POWER
        if cause not in causes:
            causes.append(cause)
    return tuple(causes)


@dataclass(frozen=True)
class _Dispatch:
    point: PlantPoint
    backup_heat_w: float
    deficit_heat_w: float
    # The checks that fail when the plant is asked for the whole demand. Empty if none does.
    shortfall_limits: tuple[str, ...]


def _dispatch(
    spec: PlantSpec, inlet: GeothermalInlet, demand: NetworkDemand, backup_max_heat_w: float | None
) -> _Dispatch:
    """Give the network only what the plant can deliver, then call on the declared backup.

    The backup stands in series after the plant, so with a backup the plant heats the network
    stream only as far as its own share. The plant is then evaluated for that share.
    """
    full = evaluate_plant(spec, inlet, demand)
    if full.meets_demand:
        return _Dispatch(full, 0.0, 0.0, ())
    failed = tuple(
        check.check_id for check in full.checks if check.outcome is CheckOutcome.FAIL
    ) or (full.binding_limit,)
    required = max(demand.heat_w, 0.0)
    shortfall = max(required - full.heat_to_network_w, 0.0)
    if backup_max_heat_w is None:
        return _Dispatch(full, 0.0, shortfall, failed)
    backup = min(shortfall, backup_max_heat_w)
    share = evaluate_plant(spec, inlet, demand.with_heat_w(required - backup))
    deficit = max(required - backup - share.heat_to_network_w, 0.0)
    return _Dispatch(share, backup, deficit, failed)


class _Builder:
    """Fills the fields every result of one problem shares."""

    def __init__(
        self, problem: CouplingProblem, capabilities: SourceCapabilities, network_engine: str
    ) -> None:
        self._problem = problem
        self._shared: dict[str, Any] = {
            "coupling_case_id": problem.coupling_case_id,
            "input_hash": problem.input_hash(),
            "arrangement": problem.plant.arrangement,
            "performance_basis": performance_basis(problem.plant),
            "synthetic": capabilities.synthetic,
            "engines": {
                "source": f"{capabilities.engine} {capabilities.engine_version}",
                "network": network_engine,
            },
            "backup_id": problem.backup_id,
            "settings": problem.settings,
            "settings_origin": problem.settings_origin,
            "inputs_not_established": problem.inputs_not_established,
            "not_modelled": (*_NOT_MODELLED, *problem.not_modelled),
        }

    def build(self, **fields: Any) -> CouplingResult:
        return CouplingResult.model_validate({**self._shared, **fields})


# --- the coupled solution -------------------------------------------------------------------


def solve_coupled(
    problem: CouplingProblem, source_model: SourceModel, network_model: NetworkModel
) -> CouplingResult:
    """Find the coupled operating point of one source, one plant and one network case."""
    settings = problem.settings
    spec = problem.plant
    capabilities = source_model.capabilities()
    network_capabilities = network_model.capabilities()
    builder = _Builder(
        problem,
        capabilities,
        f"{network_capabilities.engine} {network_capabilities.engine_version}",
    )

    # --- the network states the demand ------------------------------------------------------
    network = network_model.solve(problem.network, problem.network_case)
    if network.state is not AdapterState.COMPLETED:
        return builder.build(
            state=network.state,
            failure_origin=FailureOrigin.NETWORK,
            reason=f"the network case fails: {network.reason}",
            checks=_prefixed(network.checks, "network"),
            limiting_conditions=tuple(f"network:{name}" for name in network.active_limits),
            network_result=network,
            warnings=network.warnings,
        )
    supply = network.supply_points[0]
    required_supply = supply.supply_temperature_k
    demand_heat = supply.heat_supplied_w
    return_temperature = supply.return_temperature_k
    if (
        demand_heat is None
        or return_temperature is None
        or demand_heat <= settings.absolute_heat_mismatch_tolerance_w
        or supply.mass_flow_kg_s <= problem.network_case.numerical.zero_flow_tolerance_kg_s
    ):
        return builder.build(
            state=AdapterState.COMPLETED,
            idle=True,
            converged=True,
            operating_point=OperatingPoint(
                source_operated=False,
                geothermal_mass_flow_kg_s=0.0,
                geothermal_extracted_heat_w=0.0,
                source_pump_electricity_w=0.0,
                network_mass_flow_kg_s=max(supply.mass_flow_kg_s, 0.0),
                required_supply_temperature_k=required_supply,
                demand_heat_w=0.0,
                plant_heat_w=0.0,
                backup_heat_w=0.0,
                deficit_heat_w=0.0,
                compressor_electricity_w=0.0,
            ),
            network_result=network,
            warnings=(
                "the network takes no heat at the connection. The plant and the source are not "
                "operated",
                *network.warnings,
            ),
        )
    demand = NetworkDemand(
        mass_flow_kg_s=supply.mass_flow_kg_s,
        return_temperature_k=return_temperature,
        supply_temperature_k=required_supply,
        fluid=mean_heat_capacity_of_stream(
            heat_w=demand_heat,
            mass_flow_kg_s=supply.mass_flow_kg_s,
            hot_temperature_k=required_supply,
            cold_temperature_k=return_temperature,
            reported_by=f"{network.engine} {network.engine_version}",
        ),
    )

    # --- the iteration ----------------------------------------------------------------------
    follow = problem.flow_control is FlowControl.FOLLOW_DEMAND
    lowest = problem.min_geothermal_mass_flow_kg_s
    evaluations = 0

    def evaluate_source(flow_kg_s: float, injection_k: float) -> SourceResult:
        nonlocal evaluations
        evaluations += 1
        return source_model.evaluate(problem.source_request.at(flow_kg_s, injection_k))

    if follow:
        flow = lowest
    elif problem.fixed_geothermal_mass_flow_kg_s is not None:
        flow = problem.fixed_geothermal_mass_flow_kg_s
    else:
        raise ValueError("fixed flow control needs a geothermal mass flow")
    injection = coldest_geothermal_outlet_k(spec, demand)
    # Once the source has rejected a flow, the largest flow it accepted and the limits it named.
    cap: float | None = None
    cap_limits: tuple[str, ...] = ()
    history: list[IterationRecord] = []
    source: SourceResult | None = None
    dispatch: _Dispatch | None = None
    residuals: Residuals | None = None
    converged = False
    idle_source = False

    for iteration in range(1, settings.max_iterations_count + 1):
        note = ""
        source = evaluate_source(flow, injection)
        if source.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED and follow and flow > lowest:
            # Bisect between the lowest flow of the range and the rejected one.
            rejected, requested = source, flow
            low, high = lowest, flow
            accepted = evaluate_source(low, injection)
            while (
                accepted.state is AdapterState.COMPLETED
                and high - low > settings.flow_tolerance_kg_s(low)
            ):
                middle = 0.5 * (low + high)
                trial = evaluate_source(middle, injection)
                if trial.state is AdapterState.COMPLETED:
                    low, accepted = middle, trial
                elif trial.state is AdapterState.PHYSICAL_LIMIT_EXCEEDED:
                    high, rejected = middle, trial
                else:
                    accepted = trial
            source, flow = accepted, low
            if source.state is AdapterState.COMPLETED:
                cap, cap_limits = low, rejected.active_limits
                note = (
                    f"the source rejected {requested:.4g} kg/s ({', '.join(cap_limits)}). The "
                    f"largest flow it accepts is {cap:.4g} kg/s"
                )
        if source.state is not AdapterState.COMPLETED or source.performance is None:
            return builder.build(
                state=source.state,
                failure_origin=FailureOrigin.SOURCE,
                reason=(
                    f"the source model ended as {source.state.value} at {flow:.4g} kg/s and an "
                    f"injection temperature of {injection:.2f} K: {source.reason}"
                ),
                checks=_prefixed(source.checks, "source"),
                limiting_conditions=tuple(f"source:{name}" for name in source.active_limits),
                iterations=tuple(history),
                source_evaluations_count=evaluations,
                source_result=source,
                network_result=network,
            )
        performance = source.performance
        wellhead = performance.wellhead_production_temperature_k
        geothermal_fluid = mean_heat_capacity_of_stream(
            heat_w=performance.geothermal_extracted_heat_w,
            mass_flow_kg_s=performance.mass_flow_kg_s,
            hot_temperature_k=wellhead,
            cold_temperature_k=performance.injection_temperature_k,
            reported_by=f"{source.engine} {source.engine_version}",
        )

        if follow:
            choice = required_geothermal_flow(
                spec,
                inlet_temperature_k=wellhead,
                fluid=geothermal_fluid,
                demand=demand,
                min_flow_kg_s=lowest,
                max_flow_kg_s=cap if cap is not None else problem.max_geothermal_mass_flow_kg_s,
            )
            plant_flow = choice.flow_kg_s
            dispatch = _dispatch(
                spec,
                GeothermalInlet(plant_flow, wellhead, geothermal_fluid),
                demand,
                problem.backup_max_heat_w,
            )
        else:
            plant_flow = flow
            dispatch = _dispatch(
                spec,
                GeothermalInlet(plant_flow, wellhead, geothermal_fluid),
                demand,
                problem.backup_max_heat_w,
            )
        point = dispatch.point

        if point.heat_from_geothermal_w <= settings.absolute_heat_mismatch_tolerance_w:
            # The plant can take no heat from this source, so the wells are not run.
            idle_source = True
            residuals = Residuals(
                temperature_delta_k=0.0,
                heat_w=0.0,
                heat_fraction=0.0,
                flow_kg_s=0.0,
                flow_fraction=0.0,
            )
            history.append(
                IterationRecord(
                    iteration=iteration,
                    geothermal_mass_flow_kg_s=flow,
                    injection_temperature_k=injection,
                    wellhead_temperature_k=wellhead,
                    plant_mass_flow_kg_s=0.0,
                    plant_geothermal_outlet_temperature_k=wellhead,
                    plant_heat_to_network_w=0.0,
                    residuals=residuals,
                    within_tolerance=True,
                    source_evaluations_count=evaluations,
                    note=(
                        "the plant can take no heat from the geothermal stream at this wellhead "
                        "temperature. The source is not operated"
                    ),
                )
            )
            converged = True
            break

        heat_scale = max(performance.geothermal_extracted_heat_w, point.heat_from_geothermal_w)
        flow_scale = max(flow, plant_flow)
        heat_gap = abs(performance.geothermal_extracted_heat_w - point.heat_from_geothermal_w)
        flow_gap = abs(plant_flow - flow)
        residuals = Residuals(
            temperature_delta_k=abs(point.geothermal_outlet_temperature_k - injection),
            heat_w=heat_gap,
            heat_fraction=heat_gap / heat_scale,
            flow_kg_s=flow_gap,
            flow_fraction=flow_gap / flow_scale,
        )
        within = (
            residuals.temperature_delta_k <= settings.temperature_change_tolerance_delta_k
            and heat_gap <= settings.heat_tolerance_w(heat_scale)
            and flow_gap <= settings.flow_tolerance_kg_s(flow_scale)
        )
        history.append(
            IterationRecord(
                iteration=iteration,
                geothermal_mass_flow_kg_s=flow,
                injection_temperature_k=injection,
                wellhead_temperature_k=wellhead,
                plant_mass_flow_kg_s=plant_flow,
                plant_geothermal_outlet_temperature_k=point.geothermal_outlet_temperature_k,
                plant_heat_to_network_w=point.heat_to_network_w,
                residuals=residuals,
                within_tolerance=within,
                source_evaluations_count=evaluations,
                note=note,
            )
        )
        if within:
            converged = True
            break
        damping = settings.damping_fraction
        flow += damping * (plant_flow - flow)
        injection += damping * (point.geothermal_outlet_temperature_k - injection)

    if not converged or source is None or dispatch is None or residuals is None:
        last = history[-1].residuals if history else None
        detail = (
            f" The last residuals were {last.temperature_delta_k:.3g} K, "
            f"{last.heat_fraction:.3g} of the heat and {last.flow_fraction:.3g} of the flow."
            if last is not None
            else ""
        )
        return builder.build(
            state=AdapterState.NON_CONVERGED,
            failure_origin=FailureOrigin.COUPLING,
            reason=(
                f"the coupling did not converge within {settings.max_iterations_count} "
                f"iterations.{detail} Nothing is concluded about feasibility"
            ),
            iterations=tuple(history),
            residuals=last,
            source_evaluations_count=evaluations,
            source_result=source,
            network_result=network,
        )

    # --- the converged operating point ------------------------------------------------------
    final = source.performance
    if final is None:
        raise RuntimeError("a completed source result carries its performance")
    point = dispatch.point
    deficit_tolerance = settings.heat_tolerance_w(demand_heat)
    unmet = dispatch.deficit_heat_w > deficit_tolerance
    short = unmet or dispatch.backup_heat_w > 0.0
    extracted = 0.0 if idle_source else final.geothermal_extracted_heat_w
    pump_electricity = 0.0 if idle_source else final.electrical_pump_power_w
    geothermal_flow = 0.0 if idle_source else final.mass_flow_kg_s

    limiting: list[str] = []
    if short:
        limiting.extend(dispatch.shortfall_limits)
        at_cap = cap is not None and point.geothermal_mass_flow_kg_s >= cap * (1.0 - 1e-9)
        if at_cap and not idle_source:
            limiting.extend(f"source:{name}" for name in cap_limits)
        elif (
            follow
            and not idle_source
            and point.geothermal_mass_flow_kg_s
            >= problem.max_geothermal_mass_flow_kg_s * (1.0 - 1e-9)
        ):
            limiting.append("geothermal_flow_range")

    checks: list[Check] = list(point.checks)
    checks.append(
        Check(
            check_id="unassigned_slack_heat",
            description=(
                "heat the ideal network boundary feeds in that neither the plant nor a declared "
                "backup delivers"
            ),
            outcome=CheckOutcome.FAIL if unmet else CheckOutcome.PASS,
            observed=dispatch.deficit_heat_w,
            limit=deficit_tolerance,
            margin=deficit_tolerance - dispatch.deficit_heat_w,
            unit="W",
            threshold_origin=problem.settings_origin,
        )
    )
    checks.extend(_connection_checks(problem, network, demand_heat, supply.mass_flow_kg_s))
    electricity = point.compressor_electricity_w + pump_electricity
    if "max_electrical_import_w" in spec.parameters:
        checks.append(
            _limit_check(
                "electrical_import",
                "electricity for the compressor and the well pumps",
                electricity,
                spec.parameters["max_electrical_import_w"],
                upper=True,
                unit="W",
                origin=spec.origin("max_electrical_import_w"),
            )
        )
    elif point.compressor_electricity_w > 0.0:
        checks.append(
            _limit_check(
                "electrical_import",
                "electricity for the compressor and the well pumps",
                electricity,
                None,
                upper=True,
                unit="W",
                origin=spec.origin("max_electrical_import_w"),
            )
        )
    if not idle_source:
        checks.extend(_prefixed(source.checks, "source"))
    checks.extend(_prefixed(network.checks, "network"))

    # --- ledger -----------------------------------------------------------------------------
    source_basis = f"{source.engine} {source.engine_version}"
    network_basis = f"{network.engine} {network.engine_version}"
    plant_basis = f"plant {spec.plant_id}, arrangement {spec.arrangement}"
    direct_heat = point.heat_from_geothermal_w - (
        point.heat_pump.evaporator_heat_w if point.heat_pump else 0.0
    )
    balance = network.energy_balance
    entries = [
        LedgerEntry(
            entry_id="geothermal_extraction",
            description="heat extracted between the production wellhead and reinjection",
            quantity=LedgerQuantity.HEAT,
            from_boundary=RESERVOIR,
            to_boundary=GEOTHERMAL_CIRCUIT,
            power_w=extracted,
            basis=source_basis,
        ),
        LedgerEntry(
            entry_id="exchanger_transfer",
            description="heat the geothermal stream gives to the plant through an exchanger",
            quantity=LedgerQuantity.HEAT,
            from_boundary=GEOTHERMAL_CIRCUIT,
            to_boundary=PLANT,
            power_w=direct_heat,
            basis=plant_basis,
        ),
    ]
    if point.heat_pump is not None:
        entries += [
            LedgerEntry(
                entry_id="heat_pump_evaporator",
                description="heat the geothermal stream gives to the heat pump evaporator",
                quantity=LedgerQuantity.HEAT,
                from_boundary=GEOTHERMAL_CIRCUIT,
                to_boundary=PLANT,
                power_w=point.heat_pump.evaporator_heat_w,
                basis=point.heat_pump.description,
            ),
            LedgerEntry(
                entry_id="compressor_electricity",
                description="electricity of the heat pump compressor",
                quantity=LedgerQuantity.ELECTRICITY,
                from_boundary=ELECTRICITY_GRID,
                to_boundary=PLANT,
                power_w=point.compressor_electricity_w,
                basis=point.heat_pump.description,
            ),
            LedgerEntry(
                entry_id="compressor_heat_loss",
                description="compressor electricity lost to the surroundings",
                quantity=LedgerQuantity.HEAT,
                from_boundary=PLANT,
                to_boundary=SURROUNDINGS,
                power_w=point.heat_loss_to_surroundings_w,
                basis=plant_basis,
            ),
        ]
    entries.append(
        LedgerEntry(
            entry_id="plant_delivery",
            description="heat the plant delivers to the network at the connection",
            quantity=LedgerQuantity.HEAT,
            from_boundary=PLANT,
            to_boundary=NETWORK,
            power_w=point.heat_to_network_w,
            basis=plant_basis,
        )
    )
    if problem.backup_id is not None:
        entries.append(
            LedgerEntry(
                entry_id="backup_delivery",
                description=f"heat the declared backup source {problem.backup_id} delivers",
                quantity=LedgerQuantity.HEAT,
                from_boundary=f"backup:{problem.backup_id}",
                to_boundary=NETWORK,
                power_w=dispatch.backup_heat_w,
                basis="dispatch of the shortfall to the declared backup",
            )
        )
    entries += [
        LedgerEntry(
            entry_id="unassigned_slack_heat",
            description="heat of the ideal network boundary that no finite source delivers",
            quantity=LedgerQuantity.HEAT,
            from_boundary=UNASSIGNED_SLACK,
            to_boundary=NETWORK,
            power_w=dispatch.deficit_heat_w,
            basis="slack audit",
        ),
        LedgerEntry(
            entry_id="consumer_delivery",
            description="heat delivered to the consumers",
            quantity=LedgerQuantity.HEAT,
            from_boundary=NETWORK,
            to_boundary=CONSUMERS,
            power_w=balance.heat_delivered_w if balance else 0.0,
            basis=network_basis,
        ),
        LedgerEntry(
            entry_id="pipe_heat_loss",
            description="heat lost from the network pipes to the ground",
            quantity=LedgerQuantity.HEAT,
            from_boundary=NETWORK,
            to_boundary=GROUND,
            power_w=balance.pipe_heat_loss_w if balance else 0.0,
            basis=network_basis,
        ),
        LedgerEntry(
            entry_id="network_accounting_residual",
            description="heat the network engine does not account for in its own balance",
            quantity=LedgerQuantity.HEAT,
            from_boundary=NETWORK,
            to_boundary=NETWORK_ACCOUNTING,
            power_w=balance.residual_w if balance else 0.0,
            basis=network_basis,
        ),
        LedgerEntry(
            entry_id="well_pump_electricity",
            description="electricity of the well pumps, as reported by the source model",
            quantity=LedgerQuantity.ELECTRICITY,
            from_boundary=ELECTRICITY_GRID,
            to_boundary="well_pumps",
            power_w=pump_electricity,
            basis=source_basis,
        ),
        LedgerEntry(
            entry_id="network_circulation",
            description="hydraulic power the circulation pump gives to the network water",
            quantity=LedgerQuantity.HYDRAULIC_POWER,
            from_boundary="network_pump",
            to_boundary=NETWORK,
            power_w=supply.hydraulic_power_w,
            basis=network_basis,
        ),
    ]
    assigned = {f"plant:{spec.plant_id}": point.heat_to_network_w}
    if problem.backup_id is not None:
        assigned[f"backup:{problem.backup_id}"] = dispatch.backup_heat_w
    slack = SlackExchange(
        boundary_id=supply.supply_point_id,
        description=(
            "ideal boundary of the network model at the connection: it holds the pressure and "
            "feeds in the heat the network takes"
        ),
        heat_supplied_w=demand_heat,
        assigned_heat_w=assigned,
        unassigned_heat_w=dispatch.deficit_heat_w,
        net_mass_flow_kg_s=0.0,
        intended_dispatch=(
            "the plant covers the demand"
            if problem.backup_id is None
            else f"the plant covers the demand, backup {problem.backup_id} covers a shortfall"
        ),
        tolerance_w=deficit_tolerance,
        within_intended_dispatch=not unmet,
    )
    ledger = build_ledger(
        entries, tolerance_w=settings.heat_tolerance_w(demand_heat), slack_exchanges=(slack,)
    )
    checks.append(
        Check(
            check_id="energy_ledger_closed",
            description="largest imbalance of the geothermal circuit, the plant and the network",
            outcome=CheckOutcome.PASS if ledger.closed else CheckOutcome.FAIL,
            observed=max(abs(item.residual_w) for item in ledger.balances),
            limit=settings.heat_tolerance_w(demand_heat),
            margin=settings.heat_tolerance_w(demand_heat)
            - max(abs(item.residual_w) for item in ledger.balances),
            unit="W",
            threshold_origin=problem.settings_origin,
        )
    )

    failed = [check.check_id for check in checks if check.outcome is CheckOutcome.FAIL]
    causes = _shortfall_causes(limiting)
    reason = ""
    if unmet:
        reason = (
            f"{dispatch.deficit_heat_w:.0f} W of the {demand_heat:.0f} W taken at connection "
            f"{problem.limits.connection_id} are not covered: "
            f"{' and '.join(cause.value for cause in causes) or 'shortfall'}, limited by "
            f"{', '.join(limiting) or 'the plant'}"
        )
    elif failed:
        reason = f"limit exceeded: {', '.join(failed)}"
    warnings = [*point.warnings, *network.warnings]
    if point.performance_basis == "screening_model":
        warnings.append(
            "the heat pump numbers come from a screening model and are not equipment performance"
        )
    warnings.extend(
        f"{item.field_path} is {item.state}: {item.reason}"
        for item in problem.inputs_not_established
    )
    return builder.build(
        state=AdapterState.PHYSICAL_LIMIT_EXCEEDED if failed else AdapterState.COMPLETED,
        failure_origin=(FailureOrigin.PLANT if unmet else FailureOrigin.COUPLING)
        if failed
        else None,
        reason=reason,
        operating_point=OperatingPoint(
            source_operated=not idle_source,
            geothermal_mass_flow_kg_s=geothermal_flow,
            wellhead_temperature_k=final.wellhead_production_temperature_k,
            injection_temperature_k=None if idle_source else final.injection_temperature_k,
            geothermal_extracted_heat_w=extracted,
            source_pump_electricity_w=pump_electricity,
            network_mass_flow_kg_s=supply.mass_flow_kg_s,
            network_return_temperature_k=return_temperature,
            required_supply_temperature_k=required_supply,
            plant_outlet_temperature_k=point.network_outlet_temperature_k,
            demand_heat_w=demand_heat,
            plant_heat_w=point.heat_to_network_w,
            backup_heat_w=dispatch.backup_heat_w,
            deficit_heat_w=dispatch.deficit_heat_w,
            compressor_electricity_w=point.compressor_electricity_w,
            network_pump_hydraulic_power_w=supply.hydraulic_power_w,
        ),
        plant=point,
        shortfall_causes=causes,
        limiting_conditions=tuple(limiting),
        checks=tuple(checks),
        converged=True,
        iterations=tuple(history),
        residuals=residuals,
        source_evaluations_count=evaluations,
        ledger=ledger,
        source_result=source,
        network_result=network,
        warnings=tuple(dict.fromkeys(warnings)),
    )
