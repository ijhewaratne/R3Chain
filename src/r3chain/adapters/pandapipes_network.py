"""pandapipes adapter for hot water networks.

Written against and tested with pandapipes 0.17.0. Parameter names changed between pandapipes
releases, so this module is the only place that uses them. The engine is imported when a
calculation runs, so the rest of the system works without pandapipes installed.

Facts about the engine that this adapter relies on. Each was confirmed by reading the installed
source and is covered by a test:

Pressure
    pandapipes pressures are gauge pressures relative to the barometric pressure at the height of
    each node. The engine's own barometric formula converts between that and absolute pressure.
    The adapter returns node pressures in the reference used by the case.

Elevation
    The hydraulic equation contains the static term rho g dh for every pipe. Around a closed loop
    the static terms cancel, so elevation changes local pressure and leaves the flows unchanged.
    The engine uses g = 9.81 m/s2.

Zero flow
    For a branch without flow the engine returns a default temperature instead of an undefined
    one. The adapter marks such branches as stagnant and reports no temperature for them.

Pressure reference
    The pressure level is held at one node: the return node of the supply point, or the node of
    the network's pressure reference. The engine treats water as incompressible, so the pressure
    level changes no flow and no temperature. A reference elsewhere is therefore met by shifting
    every solved pressure by the same amount.

Not modelled here
    Steam, pumps and valves inside the network, more than one supply point, and transient heat
    transport. Each returns ``unsupported_model``.
"""

from __future__ import annotations

import logging
import math
import warnings
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import metadata
from typing import Any, NamedTuple

from r3chain.domain.records.network import (
    BoundaryKind,
    Circuit,
    HeatCarrier,
    Network,
)
from r3chain.domain.records.plant import ConsumerLoad
from r3chain.domain.records.runs import Check, CheckOutcome
from r3chain.domain.status import AdapterState
from r3chain.domain.topology import TopologySeverity, check_topology
from r3chain.domain.units import PASCALS_PER_BAR, Pressure, PressureReference
from r3chain.ports.network import (
    CalculationMode,
    ConsumerResult,
    EnergyBalance,
    FrictionModel,
    NetworkCapabilities,
    NetworkCase,
    NetworkResult,
    NodeResult,
    PipeResult,
    SupplyPointResult,
    network_input_hash,
)

ENGINE = "pandapipes"
TESTED_VERSIONS = ("0.17.0",)
# A pipe that flows against its definition is rebuilt along its flow. Two passes are enough for
# a network whose flow directions do not depend on the orientation. More than three means the
# directions are not settling.
MAX_ORIENTATION_PASSES = 3
# A pressure reference away from the supply point is first estimated with this density. The
# solved pressures are then shifted so that the reference node has its pressure exactly.
_WATER_DENSITY_ESTIMATE_KG_M3 = 1000.0
FLUID_MODEL = "pandapipes fluid library: water"


class _EngineRun(NamedTuple):
    net: Any
    junction: dict[str, int]
    pipe_index: dict[str, int]
    consumer_index: dict[str, int]
    messages: list[str]


def engine_version() -> str:
    return metadata.version("pandapipes")


@contextmanager
def _captured_messages() -> Iterator[list[str]]:
    """Collect the warnings and log messages the engine emits during one calculation."""
    messages: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                messages.append(record.getMessage())

    handler = _Handler()
    logger = logging.getLogger("pandapipes")
    previous = logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            yield messages
            messages.extend(str(item.message) for item in caught)
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous


def ambient_pressure_pa(height_m: float) -> float:
    """Barometric pressure at a height, by the engine's own formula."""
    from pandapipes.component_models.component_toolbox import p_correction_height_air

    return float(p_correction_height_air(height_m)) * PASCALS_PER_BAR


def _absolute_pa(pressure: Pressure, height_m: float) -> float:
    if pressure.reference is PressureReference.ABSOLUTE:
        return pressure.value_pa
    return pressure.value_pa + ambient_pressure_pa(height_m)


def _is_open(load: ConsumerLoad, tolerance_kg_s: float) -> bool:
    if load.mass_flow_kg_s is not None:
        return load.mass_flow_kg_s > tolerance_kg_s
    return load.heat_w > 0.0


class PandapipesNetworkModel:
    """The pandapipes engine behind the network port. It keeps no state between calls."""

    def capabilities(self) -> NetworkCapabilities:
        return NetworkCapabilities(
            engine=ENGINE,
            engine_version=engine_version(),
            fluid_model=FLUID_MODEL,
            heat_carriers=(HeatCarrier.HOT_WATER,),
            calculations=(CalculationMode.HYDRAULIC, CalculationMode.THERMAL),
            friction_models=tuple(FrictionModel),
            max_supply_points=1,
            supports_transient=False,
            supports_equipment=False,
            planned=(
                "more than one supply point",
                "pumps and valves inside the network",
                "transient heat transport",
            ),
        )

    # --- result builders --------------------------------------------------------------------

    def _failure(
        self,
        network: Network,
        case: NetworkCase,
        state: AdapterState,
        reason: str,
        *,
        checks: tuple[Check, ...] = (),
        active_limits: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> NetworkResult:
        return NetworkResult(
            engine=ENGINE,
            engine_version=engine_version(),
            fluid_model=FLUID_MODEL,
            network_id=network.network_id,
            case_id=case.case_id,
            input_hash=network_input_hash(network, case),
            calculation=case.calculation,
            state=state,
            reason=reason[:1000],
            checks=checks,
            active_limits=active_limits,
            warnings=warnings,
        )

    # --- checks before the engine runs ------------------------------------------------------

    def _unsupported(self, network: Network, case: NetworkCase) -> str | None:
        if network.heat_carrier is not HeatCarrier.HOT_WATER:
            return (
                f"heat carrier {network.heat_carrier.value} is not supported. "
                "A steam network needs a separately validated model"
            )
        kinds = sorted(
            {item.kind for item in network.equipment} | {c.kind for c in network.controls}
        )
        if kinds:
            return f"network equipment and controls are not modelled yet: {', '.join(kinds)}"
        if len(case.supply_points) != 1:
            return (
                f"exactly one supply point is supported, the case has {len(case.supply_points)}. "
                "Several supply points arrive with plant coupling"
            )
        for boundary in network.boundary_conditions:
            if boundary.kind is not BoundaryKind.PRESSURE_REFERENCE:
                return f"boundary condition {boundary.kind.value} is not modelled yet"
        return None

    def _pressure_reference(
        self, network: Network, case: NetworkCase
    ) -> tuple[str, Pressure] | str:
        """The one node whose pressure is held, with its pressure. A text when there is not one.

        The supply point may hold the pressure at its return node, or the network's pressure
        reference may hold it at any node. Two references, or none, are an invalid case.
        """
        point = case.supply_points[0]
        held = [
            (boundary.node_id, boundary.pressure)
            for boundary in network.boundary_conditions
            if boundary.pressure is not None
        ]
        if point.return_pressure is not None:
            for node_id, pressure in held:
                if node_id != point.return_node:
                    return (
                        f"two pressure references: the case holds the pressure at "
                        f"{point.return_node} and the network at {node_id}. Give one"
                    )
                if (
                    pressure.reference is not point.return_pressure.reference
                    or abs(pressure.value_pa - point.return_pressure.value_pa) > 1.0
                ):
                    return (
                        "the case return pressure contradicts the network pressure reference at "
                        f"node {node_id}"
                    )
            return point.return_node, point.return_pressure
        if len(held) != 1:
            return (
                "exactly one pressure reference is needed: the supply point gives none and the "
                f"network gives {len(held)}"
            )
        return held[0]

    def _invalid(self, network: Network, case: NetworkCase) -> list[str]:
        problems: list[str] = []
        if case.network_id != network.network_id:
            problems.append(f"the case is for network {case.network_id!r}")
        nodes = {node.node_id: node for node in network.nodes}
        point = case.supply_points[0]
        for node_id, expected in (
            (point.supply_node, Circuit.SUPPLY),
            (point.return_node, Circuit.RETURN),
        ):
            if node_id not in nodes:
                problems.append(f"supply point node {node_id!r} is not in the network")
            elif nodes[node_id].circuit is not expected:
                problems.append(
                    f"supply point node {node_id!r} is not in the {expected.value} circuit"
                )
        if point.supply_temperature_k <= 0.0 or case.ground_temperature_k <= 0.0:
            problems.append("temperatures must be absolute temperatures above 0 K")
        if point.pressure_lift_pa <= 0.0:
            problems.append("the pressure lift of the supply point must be positive")
        reference = self._pressure_reference(network, case)
        if isinstance(reference, str):
            problems.append(reference)
        else:
            for limit in (case.min_node_pressure, case.max_node_pressure):
                if limit is not None and limit.reference is not reference[1].reference:
                    problems.append(
                        "inconsistent pressure references: the node pressure limits and the "
                        "pressure reference must share one reference"
                    )

        consumers = {consumer.consumer_id for consumer in network.consumers}
        loads = {load.consumer_id: load for load in case.loads}
        for unknown in sorted(set(loads) - consumers):
            problems.append(f"load for unknown consumer {unknown!r}")
        for absent in sorted(consumers - set(loads)):
            problems.append(f"no load given for consumer {absent!r}. State zero explicitly")
        for load in case.loads:
            name = f"consumer {load.consumer_id}"
            if load.heat_w < 0.0:
                problems.append(
                    f"{name}: negative heat means feeding heat in through a consumer, which is "
                    "not supported. Model a heat source as a supply point"
                )
            if load.mass_flow_kg_s is not None:
                if load.mass_flow_kg_s < 0.0:
                    problems.append(f"{name}: reversed flow through a consumer is not supported")
                if load.heat_w > 0.0 and load.mass_flow_kg_s == 0.0:
                    problems.append(f"{name}: heat cannot be delivered without flow")
            else:
                if case.calculation is CalculationMode.HYDRAULIC:
                    problems.append(
                        f"{name}: a hydraulic calculation needs a mass flow. Return temperature "
                        "control needs a thermal calculation"
                    )
                temperature = load.return_temperature_k or 0.0
                if not 0.0 < temperature < point.supply_temperature_k:
                    problems.append(
                        f"{name}: the return temperature must lie between 0 K and the supply "
                        "temperature"
                    )
        if not problems:
            pairs = [(point.supply_node, point.return_node)]
            problems.extend(
                issue.message
                for issue in check_topology(network, pairs)
                if issue.severity is TopologySeverity.ERROR
            )
        return problems

    # --- calculation ------------------------------------------------------------------------

    def solve(self, network: Network, case: NetworkCase) -> NetworkResult:
        unsupported = self._unsupported(network, case)
        if unsupported is not None:
            return self._failure(network, case, AdapterState.UNSUPPORTED_MODEL, unsupported)
        problems = self._invalid(network, case)
        if problems:
            return self._failure(network, case, AdapterState.INVALID_INPUT, "; ".join(problems))
        found = self._pressure_reference(network, case)
        if isinstance(found, str):
            return self._failure(network, case, AdapterState.INVALID_INPUT, found)
        reference_node, reference_pressure = found

        import pandapipes as pp
        from pandapipes.constants import GRAVITATION_CONSTANT
        from pandapipes.pf.pipeflow_setup import PipeflowNotConverged

        point = case.supply_points[0]
        thermal = case.calculation is CalculationMode.THERMAL
        tolerance = case.numerical.zero_flow_tolerance_kg_s
        heights = {node.node_id: node.elevation_m for node in network.nodes}
        loads = {load.consumer_id: load for load in case.loads}

        reference_absolute = _absolute_pa(reference_pressure, heights[reference_node])
        # The supply point holds its return node. When the pressure reference lies elsewhere, the
        # return pressure is estimated from the height difference, and the solved pressures are
        # shifted afterwards so that the reference node has its pressure.
        return_absolute = reference_absolute - _WATER_DENSITY_ESTIMATE_KG_M3 * 9.81 * (
            heights[point.return_node] - heights[reference_node]
        )
        return_guess = min(
            (load.return_temperature_k for load in case.loads if load.return_temperature_k),
            default=point.supply_temperature_k - 30.0,
        )
        friction_class = {
            FrictionModel.SWAMEE_JAIN: pp.SwameeJain,
            FrictionModel.NIKURADSE: pp.Nikuradse,
            FrictionModel.COLEBROOK: pp.Colebrook,
        }[case.numerical.friction_model]
        limit = case.numerical.max_iterations_count

        def run_engine(
            flipped: frozenset[str], return_bar: float, flow_bar: float
        ) -> _EngineRun | NetworkResult:
            """Build the engine network and solve it. Nothing of it survives this call.

            A pipe named in ``flipped`` is built against its definition, so that the engine
            sees it along its flow.
            """
            net = pp.create_empty_network(fluid="water")
            junction: dict[str, int] = {}
            for node in network.nodes:
                supply_side = node.circuit is Circuit.SUPPLY
                initial = point.supply_temperature_k if supply_side or not thermal else return_guess
                junction[node.node_id] = pp.create_junction(
                    net,
                    pn_bar=flow_bar if supply_side else return_bar,
                    tfluid_k=initial,
                    height_m=node.elevation_m,
                    name=node.node_id,
                )
            pipe_index: dict[str, int] = {}
            for pipe in network.pipes:
                first, second = pipe.from_node, pipe.to_node
                if pipe.pipe_id in flipped:
                    first, second = second, first
                pipe_index[pipe.pipe_id] = pp.create_pipe_from_parameters(
                    net,
                    junction[first],
                    junction[second],
                    length_km=pipe.length_m / 1000.0,
                    inner_diameter_mm=pipe.inner_diameter_m * 1000.0,
                    k_mm=pipe.roughness_m * 1000.0,
                    u_w_per_m2k=pipe.heat_transfer_coefficient_w_m2_k,
                    text_k=case.ground_temperature_k,
                    sections=case.numerical.pipe_sections_count,
                    name=pipe.pipe_id,
                )
            consumer_index: dict[str, int] = {}
            for consumer in network.consumers:
                load = loads[consumer.consumer_id]
                if not _is_open(load, tolerance):
                    continue
                control: dict[str, float] = (
                    {"controlled_mdot_kg_per_s": load.mass_flow_kg_s}
                    if load.mass_flow_kg_s is not None
                    else {"treturn_k": load.return_temperature_k or 0.0}
                )
                consumer_index[consumer.consumer_id] = pp.create_heat_consumer(
                    net,
                    junction[consumer.supply_node],
                    junction[consumer.return_node],
                    qext_w=load.heat_w,
                    name=consumer.consumer_id,
                    **control,
                )
            pp.create_circ_pump_const_pressure(
                net,
                return_junction=junction[point.return_node],
                flow_junction=junction[point.supply_node],
                p_flow_bar=flow_bar,
                plift_bar=flow_bar - return_bar,
                t_flow_k=point.supply_temperature_k,
                name=point.supply_point_id,
            )
            options: dict[str, Any] = {"friction_model": friction_class(), "use_numba": False}
            if thermal:
                options |= {
                    "mode": "bidirectional",
                    "max_iter_hyd": limit,
                    "max_iter_therm": limit,
                    "max_iter_bidirect": limit,
                }
            else:
                options |= {"mode": "hydraulics", "max_iter_hyd": limit}
            with _captured_messages() as messages:
                try:
                    pp.pipeflow(net, **options)
                except PipeflowNotConverged as error:
                    return self._failure(
                        network,
                        case,
                        AdapterState.NON_CONVERGED,
                        f"the engine did not converge within {limit} iterations: {error}",
                    )
                except Exception as error:
                    return self._failure(
                        network,
                        case,
                        AdapterState.TOOL_FAILURE,
                        f"{type(error).__name__}: {error}",
                    )
            if not bool(net.converged):
                return self._failure(
                    network, case, AdapterState.NON_CONVERGED, "the engine reported no convergence"
                )
            return _EngineRun(net, junction, pipe_index, consumer_index, messages)

        def oriented(return_pa: float) -> tuple[_EngineRun, frozenset[str]] | NetworkResult:
            """Solve with the return node held at an absolute pressure.

            The engine reports the outlet temperature of a pipe at the end it was defined to. A
            pipe that flows against its definition is therefore rebuilt along its flow and the
            case is solved again, before any temperature is read.
            """
            return_bar = (return_pa - ambient_pressure_pa(heights[point.return_node])) / (
                PASCALS_PER_BAR
            )
            flow_bar = (
                return_pa + point.pressure_lift_pa - ambient_pressure_pa(heights[point.supply_node])
            ) / PASCALS_PER_BAR
            flipped: frozenset[str] = frozenset()
            for _ in range(MAX_ORIENTATION_PASSES):
                outcome = run_engine(flipped, return_bar, flow_bar)
                if isinstance(outcome, NetworkResult):
                    return outcome
                against = frozenset(
                    pipe.pipe_id
                    for pipe in network.pipes
                    if float(
                        outcome.net.res_pipe.loc[outcome.pipe_index[pipe.pipe_id]][
                            "mdot_from_kg_per_s"
                        ]
                    )
                    < -tolerance
                )
                if not thermal or not against:
                    return outcome, flipped
                flipped = flipped ^ against
            return self._failure(
                network,
                case,
                AdapterState.NON_CONVERGED,
                "the flow directions did not settle while the pipes were oriented along their flow",
            )

        solved = oriented(return_absolute)
        if isinstance(solved, NetworkResult):
            return solved
        outcome, flipped = solved
        row = outcome.net.res_junction.loc[outcome.junction[reference_node]]
        held = float(row["p_bar"]) * PASCALS_PER_BAR + ambient_pressure_pa(heights[reference_node])
        # The engine treats water as incompressible, so the pressure level changes no flow and
        # no temperature. Shifting every pressure by the same amount is then the solution with
        # the reference node held exactly. A test compares it with a second engine run.
        shift = reference_absolute - held if math.isfinite(held) else 0.0
        net, junction = outcome.net, outcome.junction
        pipe_index, consumer_index, messages = (
            outcome.pipe_index,
            outcome.consumer_index,
            outcome.messages,
        )

        # --- read the engine results --------------------------------------------------------
        fluid = net.fluid
        reference = reference_pressure.reference
        junction_results = net.res_junction
        pipe_results = net.res_pipe
        used: list[float] = [float(value) for value in junction_results["p_bar"]]
        used += [float(value) for value in pipe_results["mdot_from_kg_per_s"]]
        if thermal:
            used += [float(value) for value in junction_results["t_k"]]
        if not all(math.isfinite(value) for value in used):
            return self._failure(
                network,
                case,
                AdapterState.NON_CONVERGED,
                "the engine returned values that are not finite",
            )

        absolute: dict[str, float] = {}
        node_temperature: dict[str, float] = {}
        for node in network.nodes:
            row = junction_results.loc[junction[node.node_id]]
            absolute[node.node_id] = (
                float(row["p_bar"]) * PASCALS_PER_BAR
                + ambient_pressure_pa(node.elevation_m)
                + shift
            )
            node_temperature[node.node_id] = float(row["t_k"])

        weakest = min(absolute, key=lambda node_id: absolute[node_id])
        if absolute[weakest] <= 0.0:
            impossible = Check(
                check_id="positive_absolute_pressure",
                description=f"lowest absolute pressure, at node {weakest}",
                outcome=CheckOutcome.FAIL,
                observed=absolute[weakest],
                limit=0.0,
                margin=absolute[weakest],
                unit="Pa (absolute)",
                threshold_origin="an absolute pressure cannot be zero or negative",
            )
            return self._failure(
                network,
                case,
                AdapterState.PHYSICAL_LIMIT_EXCEEDED,
                f"the static pressure is too low: the engine solution has an absolute pressure "
                f"of {absolute[weakest]:.0f} Pa at node {weakest}, which cannot exist. No node "
                "results are reported",
                checks=(impossible,),
                active_limits=("positive_absolute_pressure",),
                warnings=tuple(messages),
            )

        def reported(node_id: str) -> Pressure:
            value = absolute[node_id]
            if reference is PressureReference.GAUGE:
                value -= ambient_pressure_pa(heights[node_id])
            return Pressure(value_pa=value, reference=reference)

        balance: dict[str, float] = defaultdict(float)
        flowing: set[str] = set()

        pipes: list[PipeResult] = []
        pipe_loss_total = 0.0
        for pipe in network.pipes:
            row = pipe_results.loc[pipe_index[pipe.pipe_id]]
            direction = -1.0 if pipe.pipe_id in flipped else 1.0
            flow = direction * float(row["mdot_from_kg_per_s"])
            balance[pipe.from_node] -= flow
            balance[pipe.to_node] += flow
            stagnant = abs(flow) <= tolerance
            if not stagnant:
                flowing |= {pipe.from_node, pipe.to_node}
            drop = absolute[pipe.from_node] - absolute[pipe.to_node]
            density = (
                float(fluid.get_density(node_temperature[pipe.from_node]))
                + float(fluid.get_density(node_temperature[pipe.to_node]))
            ) / 2.0
            static = (
                density * GRAVITATION_CONSTANT * (heights[pipe.to_node] - heights[pipe.from_node])
            )
            inlet = outlet = loss = None
            if thermal and not stagnant:
                upstream = pipe.from_node if flow > 0.0 else pipe.to_node
                inlet = node_temperature[upstream]
                outlet = float(row["t_outlet_k"])
                capacity = float(fluid.get_heat_capacity((inlet + outlet) / 2.0))
                loss = abs(flow) * capacity * (inlet - outlet)
                pipe_loss_total += loss
            pipes.append(
                PipeResult(
                    pipe_id=pipe.pipe_id,
                    mass_flow_kg_s=flow,
                    velocity_m_s=direction * float(row["v_mean_m_per_s"]),
                    pressure_drop_pa=drop,
                    friction_pressure_drop_pa=drop - static,
                    stagnant=stagnant,
                    inlet_temperature_k=inlet,
                    outlet_temperature_k=outlet,
                    heat_loss_w=loss,
                )
            )

        consumers: list[ConsumerResult] = []
        delivered_total = 0.0
        for consumer in network.consumers:
            difference = absolute[consumer.supply_node] - absolute[consumer.return_node]
            if consumer.consumer_id not in consumer_index:
                consumers.append(
                    ConsumerResult(
                        consumer_id=consumer.consumer_id,
                        closed=True,
                        delivered_heat_w=0.0,
                        mass_flow_kg_s=0.0,
                        differential_pressure_pa=difference,
                    )
                )
                continue
            row = net.res_heat_consumer.loc[consumer_index[consumer.consumer_id]]
            flow = float(row["mdot_from_kg_per_s"])
            balance[consumer.supply_node] -= flow
            balance[consumer.return_node] += flow
            flowing |= {consumer.supply_node, consumer.return_node}
            heat = float(row["qext_w"]) if thermal else 0.0
            delivered_total += heat
            consumers.append(
                ConsumerResult(
                    consumer_id=consumer.consumer_id,
                    closed=False,
                    delivered_heat_w=heat,
                    mass_flow_kg_s=flow,
                    differential_pressure_pa=difference,
                    supply_temperature_k=float(row["t_from_k"]) if thermal else None,
                    return_temperature_k=float(row["t_outlet_k"]) if thermal else None,
                )
            )

        pump = net.res_circ_pump_pressure.iloc[0]
        pump_flow = float(pump["mdot_from_kg_per_s"])
        balance[point.return_node] -= pump_flow
        balance[point.supply_node] += pump_flow
        circulating = abs(pump_flow) > tolerance
        if circulating:
            flowing |= {point.supply_node, point.return_node}
        return_temperature = (
            node_temperature[point.return_node] if thermal and circulating else None
        )
        supplied = float(pump["qext_w"]) if thermal and circulating else None
        density_at_pump = float(fluid.get_density(return_temperature or point.supply_temperature_k))
        supply_points = (
            SupplyPointResult(
                supply_point_id=point.supply_point_id,
                mass_flow_kg_s=pump_flow,
                pressure_lift_pa=absolute[point.supply_node] - absolute[point.return_node],
                hydraulic_power_w=pump_flow * point.pressure_lift_pa / density_at_pump,
                supply_temperature_k=point.supply_temperature_k,
                return_temperature_k=return_temperature,
                heat_supplied_w=supplied,
            ),
        )

        nodes = tuple(
            NodeResult(
                node_id=node.node_id,
                pressure=reported(node.node_id),
                temperature_k=(
                    node_temperature[node.node_id] if thermal and node.node_id in flowing else None
                ),
            )
            for node in network.nodes
        )
        energy = None
        if thermal:
            total = supplied or 0.0
            energy = EnergyBalance(
                heat_supplied_w=total,
                heat_delivered_w=delivered_total,
                pipe_heat_loss_w=pipe_loss_total,
                residual_w=total - delivered_total - pipe_loss_total,
            )

        notes = list(messages)
        version = engine_version()
        if version not in TESTED_VERSIONS:
            notes.append(f"pandapipes {version} has not been tested with this adapter")
        stagnant_pipes = [item.pipe_id for item in pipes if item.stagnant]
        if thermal and stagnant_pipes:
            notes.append(
                "no temperature is reported for stagnant pipes: " + ", ".join(stagnant_pipes)
            )

        # --- pressure checks ----------------------------------------------------------------
        checks: list[Check] = []
        lowest = min(nodes, key=lambda item: item.pressure.value_pa)
        highest = max(nodes, key=lambda item: item.pressure.value_pa)
        unit = f"Pa ({reference.value})"
        for name, bound, observed, lower in (
            ("min_node_pressure", case.min_node_pressure, lowest, True),
            ("max_node_pressure", case.max_node_pressure, highest, False),
        ):
            if bound is None:
                continue
            value = observed.pressure.value_pa
            margin = value - bound.value_pa if lower else bound.value_pa - value
            checks.append(
                Check(
                    check_id=name,
                    description=f"{'lowest' if lower else 'highest'} node pressure, at node "
                    f"{observed.node_id}",
                    outcome=CheckOutcome.PASS if margin >= 0.0 else CheckOutcome.FAIL,
                    observed=value,
                    limit=bound.value_pa,
                    margin=margin,
                    unit=unit,
                    threshold_origin=case.pressure_limit_origin,
                )
            )
        checks.append(
            Check(
                check_id="positive_absolute_pressure",
                description=f"lowest absolute pressure, at node {weakest}",
                outcome=CheckOutcome.PASS if absolute[weakest] > 0.0 else CheckOutcome.FAIL,
                observed=absolute[weakest],
                limit=0.0,
                margin=absolute[weakest],
                unit="Pa (absolute)",
                threshold_origin="an absolute pressure cannot be zero or negative",
            )
        )
        failed = tuple(check.check_id for check in checks if check.outcome is CheckOutcome.FAIL)

        return NetworkResult(
            engine=ENGINE,
            engine_version=version,
            fluid_model=FLUID_MODEL,
            network_id=network.network_id,
            case_id=case.case_id,
            input_hash=network_input_hash(network, case),
            calculation=case.calculation,
            state=AdapterState.PHYSICAL_LIMIT_EXCEEDED if failed else AdapterState.COMPLETED,
            reason=f"pressure limit exceeded: {', '.join(failed)}" if failed else "",
            pressure_reference=reference,
            nodes=nodes,
            pipes=tuple(pipes),
            consumers=tuple(consumers),
            supply_points=supply_points,
            energy_balance=energy,
            mass_balance_residual_kg_s=max((abs(value) for value in balance.values()), default=0.0),
            checks=tuple(checks),
            active_limits=failed,
            warnings=tuple(notes),
        )
