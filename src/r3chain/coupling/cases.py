"""From a coupling case of a scenario to a coupling problem with every value resolved.

A coupling case names a source, a plant, a connection and an operating case. This module looks
them up, reads the engineering inputs they need and builds the request for the source and the
case for the network.

Inputs that are not established
    A required input that is unknown or in conflict raises ``NotEstablished`` with every such
    input listed. An optional limit that is not established is left out and carried along, so
    that its check is reported as not evaluated. It never becomes a passed check.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from r3chain.coupling.loop import (
    ConnectionLimitValues,
    CouplingProblem,
    CouplingResult,
    CouplingSettings,
    TransmissionModel,
    solve_coupled,
)
from r3chain.coupling.plant import PlantInputError, resolve_plant
from r3chain.domain.evidence import InputReader, Known, MissingInput, NotEstablished, missing_input
from r3chain.domain.records.network import (
    BoundaryKind,
    Circuit,
    Connection,
    Network,
    NetworkNode,
    NetworkPipe,
    Route,
)
from r3chain.domain.records.plant import CouplingCase, FlowControl
from r3chain.domain.records.runs import Policy
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState, StageStatus, stage_status_from_adapter
from r3chain.domain.units import Pressure
from r3chain.ports.network import (
    CalculationMode,
    FrictionModel,
    NetworkCapabilities,
    NetworkCase,
    NetworkModel,
    NetworkNumericalSettings,
    NetworkResult,
    SupplyPoint,
    network_input_hash,
)
from r3chain.ports.source import (
    SourceCapabilities,
    SourceModel,
    SourceNumericalSettings,
    SourceOperatingPoint,
    build_source_request,
)


class CouplingInputError(Exception):
    """A coupling case that cannot be evaluated, with the state that describes why."""

    def __init__(self, state: AdapterState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


def _invalid(reason: str) -> CouplingInputError:
    return CouplingInputError(AdapterState.INVALID_INPUT, reason)


def _policy_number(policy: Policy, name: str) -> float:
    value = policy.parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(f"policy {policy.policy_id} version {policy.version} lacks {name}")
    return float(value)


def _return_pressure(network: Network, return_node: str) -> Pressure | None:
    """The pressure the supply point holds at its return node.

    A pressure reference of the network at that node is held by the supply point. A reference
    at another node stays with the network, and the supply point then holds no pressure of its
    own.
    """
    references = [
        boundary
        for boundary in network.boundary_conditions
        if boundary.kind is BoundaryKind.PRESSURE_REFERENCE and boundary.pressure is not None
    ]
    if len(references) != 1:
        raise _invalid(
            f"network {network.network_id} needs exactly one pressure reference. It defines "
            f"{len(references)}"
        )
    return references[0].pressure if references[0].node_id == return_node else None


@dataclass(frozen=True)
class _Transmission:
    network: Network
    supply_node: str
    return_node: str
    pipe_ids: tuple[str, ...]
    node_ids: tuple[str, ...]


def _transmission_network(
    network: Network,
    connection: Connection,
    route: Route,
    *,
    inner_diameter_m: float,
    roughness_m: float,
    heat_transfer_coefficient_w_m2_k: float,
) -> _Transmission:
    """The network with the route added as a supply and a return pipe from the site.

    The pipes follow the elevation profile of the route. Their lengths are the supply and return
    pipe lengths of the route, shared out over the profile by chainage. The plant sits at the
    start of the route.
    """
    length = route.corridor_length_m
    heights = {node.node_id: node.elevation_m for node in network.nodes}
    points: list[tuple[float, float]] = []
    for point in route.elevation_profile:
        if not points or point.chainage_m > points[-1][0]:
            points.append((point.chainage_m, point.elevation_m))
    if not points:
        raise _invalid(
            f"route {route.route_id} has no elevation profile, so it cannot be modelled in the "
            "network"
        )
    if points[0][0] > 0.0:
        points.insert(0, (0.0, points[0][1]))
    if points[-1][0] < length:
        points.append((length, heights[connection.supply_node]))
    if len(points) < 2:
        raise _invalid(f"route {route.route_id} needs at least two profile points")

    count = len(points) - 1
    supply_ids = [f"{route.route_id}-s{index}" for index in range(count)]
    return_ids = [f"{route.route_id}-r{index}" for index in range(count)]
    clash = sorted(set(supply_ids + return_ids) & set(heights))
    if clash:
        raise _invalid(f"route node names already exist in the network: {', '.join(clash)}")
    supply_chain = [*supply_ids, connection.supply_node]
    return_chain = [*return_ids, connection.return_node]
    nodes = [
        NetworkNode(
            node_id=node_id,
            circuit=circuit,
            elevation_m=points[index][1],
            zone_id=connection.zone_id,
        )
        for index in range(count)
        for node_id, circuit in (
            (supply_ids[index], Circuit.SUPPLY),
            (return_ids[index], Circuit.RETURN),
        )
    ]
    pipes: list[NetworkPipe] = []
    for index in range(count):
        share = (points[index + 1][0] - points[index][0]) / length
        for name, start, end, total in (
            ("supply", supply_chain[index], supply_chain[index + 1], route.supply_pipe_length_m),
            ("return", return_chain[index + 1], return_chain[index], route.return_pipe_length_m),
        ):
            pipes.append(
                NetworkPipe(
                    pipe_id=f"{route.route_id}-{name}-{index}",
                    from_node=start,
                    to_node=end,
                    length_m=share * total,
                    inner_diameter_m=inner_diameter_m,
                    roughness_m=roughness_m,
                    heat_transfer_coefficient_w_m2_k=heat_transfer_coefficient_w_m2_k,
                )
            )
    extended = Network.model_validate(
        {
            **network.to_json_dict(),
            "nodes": [*network.to_json_dict()["nodes"], *(node.to_json_dict() for node in nodes)],
            "pipes": [*network.to_json_dict()["pipes"], *(pipe.to_json_dict() for pipe in pipes)],
        }
    )
    return _Transmission(
        network=extended,
        supply_node=supply_ids[0],
        return_node=return_ids[0],
        pipe_ids=tuple(pipe.pipe_id for pipe in pipes),
        node_ids=tuple(supply_ids + return_ids),
    )


def network_numerical_settings(policy: Policy) -> NetworkNumericalSettings:
    friction = policy.parameters.get("network_friction_model")
    if not isinstance(friction, str) or friction not in {item.value for item in FrictionModel}:
        raise _invalid(
            f"policy {policy.policy_id} version {policy.version} lacks a valid "
            "network_friction_model"
        )
    return NetworkNumericalSettings(
        max_iterations_count=int(_policy_number(policy, "network_max_iterations_count")),
        pipe_sections_count=int(_policy_number(policy, "network_pipe_sections_count")),
        friction_model=FrictionModel(friction),
        zero_flow_tolerance_kg_s=_policy_number(policy, "network_zero_flow_tolerance_kg_s"),
    )


def prepare_coupling(
    scenario: Scenario,
    case: CouplingCase,
    *,
    capabilities: SourceCapabilities,
    source_policy: Policy,
    numerical_policy: Policy,
    routes: Mapping[str, Route] | None = None,
) -> CouplingProblem:
    """Resolve one coupling case of a scenario into a coupling problem.

    ``routes`` adds routes that are not part of the scenario, for example generated ones.

    Raises ``NotEstablished`` when required inputs are unknown or in conflict, and
    ``CouplingInputError`` when the case does not fit the scenario or the engines.
    """
    base = f"coupling_cases[{case.coupling_case_id}]"

    def lookup[T](items: tuple[T, ...], attribute: str, identifier: str, what: str) -> T:
        for item in items:
            if getattr(item, attribute) == identifier:
                return item
        raise _invalid(f"{base}: {what} {identifier!r} is not defined in the scenario")

    well_scenario = lookup(
        scenario.well_scenarios, "source_scenario_id", case.source_scenario_id, "well scenario"
    )
    plant = lookup(scenario.plants, "plant_id", case.plant_id, "plant")
    connection = lookup(scenario.connections, "connection_id", case.connection_id, "connection")
    operating_case = lookup(
        scenario.operating_cases, "operating_case_id", case.operating_case_id, "operating case"
    )
    network = lookup(scenario.networks, "network_id", connection.network_id, "network")
    if operating_case.network_id != network.network_id:
        raise _invalid(
            f"{base}: operating case {operating_case.operating_case_id} is for network "
            f"{operating_case.network_id!r}, the connection is on {network.network_id!r}"
        )
    route: Route | None = None
    if case.route_id is not None:
        known_routes = (*scenario.routes, *(routes or {}).values())
        route = lookup(known_routes, "route_id", case.route_id, "route")
        if route.from_site_id != well_scenario.site_id or (
            route.to_connection_id != connection.connection_id
        ):
            raise _invalid(
                f"{base}: route {route.route_id} runs from site {route.from_site_id} to "
                f"connection {route.to_connection_id}. The case combines site "
                f"{well_scenario.site_id} with connection {connection.connection_id}"
            )
    if not capabilities.supports(well_scenario.model_family):
        raise CouplingInputError(
            AdapterState.UNSUPPORTED_MODEL,
            f"{capabilities.engine} does not support model family {well_scenario.model_family!r}",
        )

    missing: list[MissingInput] = []
    reader = InputReader()
    limits_path = f"connections[{connection.connection_id}].limits"
    case_path = f"operating_cases[{operating_case.operating_case_id}]"
    required_supply = reader.get(
        connection.limits.required_supply_temperature_k,
        f"{limits_path}.required_supply_temperature_k",
    )
    ground = reader.get(operating_case.ground_temperature_k, f"{case_path}.ground_temperature_k")
    loads = reader.get(operating_case.loads, f"{case_path}.loads")
    lift = reader.get(case.network_pressure_lift_pa, f"{base}.network_pressure_lift_pa")
    fixed_flow: float | None = None
    if case.flow_control is FlowControl.FIXED and case.fixed_geothermal_mass_flow_kg_s is not None:
        fixed_flow = reader.get(
            case.fixed_geothermal_mass_flow_kg_s, f"{base}.fixed_geothermal_mass_flow_kg_s"
        )
    backup_heat: float | None = None
    if case.backup is not None:
        backup_heat = reader.get(case.backup.max_heat_w, f"{base}.backup.max_heat_w")
    design: dict[str, float | None] = {}
    if case.transmission is not None:
        for name in ("inner_diameter_m", "roughness_m", "heat_transfer_coefficient_w_m2_k"):
            design[name] = reader.get(
                getattr(case.transmission, name), f"{base}.transmission.{name}"
            )
    missing.extend(reader.missing)

    try:
        spec, left_out = resolve_plant(plant)
    except NotEstablished as error:
        missing.extend(error.missing)
        spec, left_out = None, ()
    except PlantInputError as error:
        raise CouplingInputError(error.state, error.reason) from error

    low = _policy_number(source_policy, "min_mass_flow_kg_s")
    high = _policy_number(source_policy, "max_mass_flow_kg_s")
    try:
        request = build_source_request(
            study_id=scenario.study.study_id,
            well_scenario=well_scenario,
            operating_point=SourceOperatingPoint(
                requested_mass_flow_kg_s=low,
                injection_temperature_k=_policy_number(
                    source_policy, "min_injection_temperature_k"
                ),
            ),
            numerical=SourceNumericalSettings(
                max_iterations_count=int(_policy_number(source_policy, "max_iterations_count")),
                temperature_tolerance_delta_k=_policy_number(
                    source_policy, "temperature_tolerance_delta_k"
                ),
            ),
            capabilities=capabilities,
        )
    except NotEstablished as error:
        missing.extend(error.missing)
        request = None

    if (
        missing
        or spec is None
        or request is None
        or required_supply is None
        or ground is None
        or loads is None
        or lift is None
        or any(value is None for value in design.values())
    ):
        raise NotEstablished(tuple(missing))

    modelled = network
    supply_node, return_node = connection.supply_node, connection.return_node
    transmission: _Transmission | None = None
    if case.transmission is not None and route is not None:
        transmission = _transmission_network(
            network,
            connection,
            route,
            inner_diameter_m=float(design["inner_diameter_m"] or 0.0),
            roughness_m=float(design["roughness_m"] or 0.0),
            heat_transfer_coefficient_w_m2_k=float(
                design["heat_transfer_coefficient_w_m2_k"] or 0.0
            ),
        )
        modelled = transmission.network
        supply_node, return_node = transmission.supply_node, transmission.return_node

    # Limits of the connection that are not established stay visible as unevaluated checks.
    optional: list[MissingInput] = list(left_out)
    known: dict[str, float | Pressure | None] = {}
    for name in ("max_heat_w", "max_mass_flow_kg_s", "min_pressure", "max_pressure"):
        evidence = getattr(connection.limits, name)
        if isinstance(evidence, Known):
            known[name] = evidence.value
        else:
            known[name] = None
            optional.append(missing_input(evidence, f"{limits_path}.{name}"))

    def number(name: str) -> float | None:
        value = known[name]
        return value if isinstance(value, float | int) else None

    def pressure(name: str) -> Pressure | None:
        value = known[name]
        return value if isinstance(value, Pressure) else None

    try:
        settings = CouplingSettings.from_policy(numerical_policy)
    except ValueError as error:
        raise _invalid(str(error)) from error
    network_case = NetworkCase(
        # Named after the network inputs, so coupling cases that share them share one solve.
        case_id=(
            f"{operating_case.operating_case_id}-via-{route.route_id}"
            if transmission is not None and route is not None
            else f"{operating_case.operating_case_id}-at-{connection.connection_id}"
        ),
        network_id=network.network_id,
        calculation=CalculationMode.THERMAL,
        loads=loads,
        supply_points=(
            SupplyPoint(
                supply_point_id=connection.connection_id,
                supply_node=supply_node,
                return_node=return_node,
                supply_temperature_k=required_supply,
                return_pressure=_return_pressure(modelled, return_node),
                pressure_lift_pa=lift,
            ),
        ),
        ground_temperature_k=ground,
        numerical=network_numerical_settings(numerical_policy),
    )
    return CouplingProblem(
        coupling_case_id=case.coupling_case_id,
        source_request=request,
        min_geothermal_mass_flow_kg_s=low,
        max_geothermal_mass_flow_kg_s=high,
        flow_range_origin=f"policy {source_policy.policy_id} version {source_policy.version}",
        flow_control=case.flow_control,
        fixed_geothermal_mass_flow_kg_s=fixed_flow,
        plant=spec,
        network=modelled,
        network_case=network_case,
        limits=ConnectionLimitValues(
            connection_id=connection.connection_id,
            supply_node=connection.supply_node,
            return_node=connection.return_node,
            max_heat_w=number("max_heat_w"),
            max_mass_flow_kg_s=number("max_mass_flow_kg_s"),
            min_pressure=pressure("min_pressure"),
            max_pressure=pressure("max_pressure"),
        ),
        backup_id=case.backup.backup_id if case.backup is not None else None,
        backup_max_heat_w=backup_heat,
        settings=settings,
        settings_origin=(f"policy {numerical_policy.policy_id} version {numerical_policy.version}"),
        inputs_not_established=tuple(optional),
        transmission=(
            TransmissionModel(
                route_id=route.route_id,
                pipe_ids=transmission.pipe_ids,
                node_ids=transmission.node_ids,
            )
            if transmission is not None and route is not None
            else None
        ),
    )


# --- evaluating the cases of a scenario -----------------------------------------------------


class MemoNetworkModel:
    """A network model that solves each distinct case once.

    The network is solved at the supply temperature the connection requires, so its result does
    not depend on the source or the plant. Coupling cases that share an operating case, a
    connection and a pressure lift therefore share one network solution.
    """

    def __init__(self, model: NetworkModel) -> None:
        self._model = model
        self._solved: dict[str, NetworkResult] = {}

    def capabilities(self) -> NetworkCapabilities:
        return self._model.capabilities()

    def solve(self, network: Network, case: NetworkCase) -> NetworkResult:
        key = network_input_hash(network, case)
        if key not in self._solved:
            self._solved[key] = self._model.solve(network, case)
        return self._solved[key]


@dataclass(frozen=True)
class CaseOutcome:
    """What became of one coupling case."""

    coupling_case_id: str
    status: StageStatus
    reason: str = ""
    result: CouplingResult | None = None
    missing_inputs: tuple[MissingInput, ...] = ()


def evaluate_case(
    scenario: Scenario,
    case: CouplingCase,
    *,
    source_models: Sequence[SourceModel],
    network_model: NetworkModel,
    source_policy: Policy,
    numerical_policy: Policy,
    routes: Mapping[str, Route] | None = None,
) -> CaseOutcome:
    """Evaluate one coupling case and say what became of it.

    A case that cannot be evaluated keeps its own state: inputs that are not established, a
    model nobody supports and an invalid case are three different outcomes.
    """
    identifier = case.coupling_case_id
    well_scenario = next(
        (
            item
            for item in scenario.well_scenarios
            if item.source_scenario_id == case.source_scenario_id
        ),
        None,
    )
    if well_scenario is None:
        return CaseOutcome(
            identifier,
            StageStatus.INVALID_INPUT,
            f"well scenario {case.source_scenario_id!r} is not defined in the scenario",
        )
    model = next(
        (
            candidate
            for candidate in source_models
            if candidate.capabilities().supports(well_scenario.model_family)
        ),
        None,
    )
    if model is None:
        return CaseOutcome(
            identifier,
            StageStatus.UNSUPPORTED_MODEL,
            f"no engine supports model family {well_scenario.model_family!r}",
        )
    try:
        problem = prepare_coupling(
            scenario,
            case,
            capabilities=model.capabilities(),
            source_policy=source_policy,
            numerical_policy=numerical_policy,
            routes=routes,
        )
    except NotEstablished as error:
        return CaseOutcome(
            identifier,
            StageStatus.NOT_ESTABLISHED,
            "required inputs are not established",
            missing_inputs=error.missing,
        )
    except CouplingInputError as error:
        return CaseOutcome(identifier, stage_status_from_adapter(error.state), error.reason)
    result = solve_coupled(problem, model, network_model)
    return CaseOutcome(
        identifier, stage_status_from_adapter(result.state), result.reason, result=result
    )
