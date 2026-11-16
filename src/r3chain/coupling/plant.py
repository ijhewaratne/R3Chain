"""Plant arrangements and their registry (specification 10.2, 10.3 and figure 2).

A plant arrangement states how geothermal heat reaches the network. Each arrangement is one
registered entry with its own parameters and its own evaluation, so a new integration concept is
added here without touching anything else (extension point X6).

Registered arrangements

direct_exchanger
    One counterflow exchanger between the geothermal circuit and the network circuit.

heat_pump_assisted
    The network stream is first heated by a direct exchanger from the geothermal stream. A heat
    pump then lifts it to the required supply temperature. The evaporator takes its heat from
    the geothermal stream after the direct exchanger. The condenser heats the network stream
    after the direct exchanger. The two heat paths are reported separately.

pressure_separation_exchanger
    Two exchangers in series through an intermediate water loop: geothermal circuit to loop, and
    loop to network. The second exchanger separates the pressure zone of the network from the
    plant. The loop circulates with the same heat capacity rate as the network stream.

What an evaluation returns
    The operating point the plant reaches for a given geothermal inlet and a given network
    demand, the limit that binds, and its checks. The checks are evaluated at the required duty,
    so a plant that cannot meet the demand names the terminal or the limit that stops it.

The minimum approach, UA, fouling allowance and pressure losses are equipment or study inputs.
Nothing in this module supplies a default for any of them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from r3chain.coupling.exchanger import (
    ExchangerLimit,
    ExchangerPoint,
    Stream,
    passes,
    pressure_loss_pa,
    rate_counterflow,
    terminal_checks,
)
from r3chain.coupling.fluids import EnthalpyModel
from r3chain.coupling.heat_pump import (
    CarnotScreeningHeatPump,
    HeatPumpLimit,
    HeatPumpModel,
    HeatPumpPoint,
    MapHeatPump,
    OutsideMapDomain,
    operate_heat_pump,
)
from r3chain.domain.base import NonNegativeReal, PositiveReal, Real, Record
from r3chain.domain.evidence import InputReader, Known, MissingInput, missing_input
from r3chain.domain.records.plant import Plant
from r3chain.domain.records.runs import Check, CheckOutcome
from r3chain.domain.status import AdapterState

DEMAND_MET = "demand"
EQUIPMENT_RATING = "equipment_rating"

# A check passes when its margin is not below this. It absorbs floating point rounding only.
_ROUNDING = 1e-9


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlantSpec:
    """A plant with every value resolved, ready to evaluate."""

    plant_id: str
    arrangement: str
    parameters: Mapping[str, float]
    heat_pump: HeatPumpModel | None = None

    def origin(self, name: str) -> str:
        return f"plants[{self.plant_id}].parameters[{name}]"

    def fingerprint(self) -> dict[str, Any]:
        """Everything that defines this plant, as JSON compatible data for hashing."""
        pump: dict[str, Any] | None = None
        if isinstance(self.heat_pump, MapHeatPump):
            pump = {"basis": self.heat_pump.basis, "map": self.heat_pump.map.to_json_dict()}
        elif isinstance(self.heat_pump, CarnotScreeningHeatPump):
            pump = {"basis": self.heat_pump.basis, "parameters": asdict(self.heat_pump)}
        elif self.heat_pump is not None:
            pump = {"basis": self.heat_pump.basis, "description": self.heat_pump.description}
        return {
            "plant_id": self.plant_id,
            "arrangement": self.arrangement,
            "parameters": dict(sorted(self.parameters.items())),
            "heat_pump": pump,
        }


@dataclass(frozen=True)
class GeothermalInlet:
    mass_flow_kg_s: float
    temperature_k: float
    fluid: EnthalpyModel


@dataclass(frozen=True)
class NetworkDemand:
    """What the network asks for at the connection: a stream to be heated to a temperature."""

    mass_flow_kg_s: float
    return_temperature_k: float
    supply_temperature_k: float
    fluid: EnthalpyModel

    @property
    def heat_w(self) -> float:
        return self.mass_flow_kg_s * (
            self.fluid.enthalpy_j_kg(self.supply_temperature_k)
            - self.fluid.enthalpy_j_kg(self.return_temperature_k)
        )

    def with_heat_w(self, heat_w: float) -> NetworkDemand:
        """The same stream, to be heated by a given heat instead of to the supply temperature."""
        target = self.fluid.temperature_k(
            self.fluid.enthalpy_j_kg(self.return_temperature_k) + heat_w / self.mass_flow_kg_s
        )
        return replace(self, supply_temperature_k=target)


# --- result ---------------------------------------------------------------------------------


class PlantPoint(Record):
    """The operating point of a plant for one geothermal inlet and one network demand."""

    plant_id: str
    arrangement: str
    # equipment_rating, performance_map or screening_model.
    performance_basis: str
    # 'demand' when the plant meets the demand. Otherwise the limit that stops it.
    binding_limit: str
    geothermal_mass_flow_kg_s: PositiveReal
    geothermal_inlet_temperature_k: Real
    geothermal_outlet_temperature_k: Real
    network_mass_flow_kg_s: PositiveReal
    network_inlet_temperature_k: Real
    network_outlet_temperature_k: Real
    required_outlet_temperature_k: Real
    required_heat_w: NonNegativeReal
    heat_to_network_w: NonNegativeReal
    heat_from_geothermal_w: NonNegativeReal
    compressor_electricity_w: NonNegativeReal = 0.0
    heat_loss_to_surroundings_w: NonNegativeReal = 0.0
    exchangers: dict[str, ExchangerPoint]
    heat_pump: HeatPumpPoint | None = None
    # Absent when the rated pressure loss of that side was not given.
    geothermal_side_pressure_loss_pa: NonNegativeReal | None = None
    network_side_pressure_loss_pa: NonNegativeReal | None = None
    checks: tuple[Check, ...] = ()
    # Heat from the geothermal stream plus compressor electricity, minus heat to the network
    # and the stated loss.
    balance_residual_w: Real
    warnings: tuple[str, ...] = ()

    @property
    def meets_demand(self) -> bool:
        return self.binding_limit == DEMAND_MET


Evaluation = Callable[[PlantSpec, GeothermalInlet, NetworkDemand], PlantPoint]
# The lowest temperature at which a plant can return the geothermal stream for a demand.
ColdestOutlet = Callable[[PlantSpec, NetworkDemand], float]


@dataclass(frozen=True)
class Arrangement:
    name: str
    description: str
    required_parameters: tuple[str, ...]
    optional_parameters: tuple[str, ...]
    uses_heat_pump: bool
    evaluate: Evaluation
    coldest_outlet_k: ColdestOutlet


_REGISTRY: dict[str, Arrangement] = {}


def register_arrangement(arrangement: Arrangement) -> Arrangement:
    if arrangement.name in _REGISTRY:
        raise ValueError(f"arrangement {arrangement.name!r} is already registered")
    _REGISTRY[arrangement.name] = arrangement
    return arrangement


def arrangements() -> tuple[Arrangement, ...]:
    return tuple(_REGISTRY[name] for name in sorted(_REGISTRY))


def arrangement_named(name: str) -> Arrangement | None:
    return _REGISTRY.get(name)


# --- shared parts ---------------------------------------------------------------------------

_PRESSURE_PARAMETERS = (
    "geothermal_side_rated_pressure_loss_pa",
    "geothermal_side_rated_mass_flow_kg_s",
    "max_geothermal_side_pressure_loss_pa",
    "network_side_rated_pressure_loss_pa",
    "network_side_rated_mass_flow_kg_s",
    "max_network_side_pressure_loss_pa",
)
_COMMON_OPTIONAL = (
    "exchanger_fouling_allowance_fraction",
    "max_electrical_import_w",
    *_PRESSURE_PARAMETERS,
)
_SCREENING_PARAMETERS = (
    "carnot_efficiency_fraction",
    "evaporator_temperature_difference_delta_k",
    "condenser_temperature_difference_delta_k",
)


def _effective_ua_w_k(spec: PlantSpec, name: str, notes: list[str]) -> float:
    """Conductance in service: the stated UA reduced by the fouling allowance, if one is given."""
    conductance = spec.parameters[name]
    fouling = spec.parameters.get("exchanger_fouling_allowance_fraction")
    if fouling is None:
        notes.append(f"no fouling allowance was given, so {name} is used as stated")
        return conductance
    return conductance * (1.0 - fouling)


def _pressure_losses(
    spec: PlantSpec, geothermal_flow_kg_s: float, network_flow_kg_s: float
) -> tuple[float | None, float | None, list[Check]]:
    checks: list[Check] = []
    losses: list[float | None] = []
    for side, flow in (("geothermal", geothermal_flow_kg_s), ("network", network_flow_kg_s)):
        rated = spec.parameters.get(f"{side}_side_rated_pressure_loss_pa")
        rated_flow = spec.parameters.get(f"{side}_side_rated_mass_flow_kg_s")
        check_id = f"exchanger_{side}_side_pressure_loss"
        if rated is None or rated_flow is None:
            losses.append(None)
            checks.append(
                Check(
                    check_id=check_id,
                    description=(
                        f"pressure loss on the {side} side was not evaluated: the rated pressure "
                        "loss and its rated mass flow were not given"
                    ),
                    outcome=CheckOutcome.NOT_EVALUATED,
                )
            )
            continue
        loss = pressure_loss_pa(rated, rated_flow, flow)
        losses.append(loss)
        limit_name = f"max_{side}_side_pressure_loss_pa"
        limit = spec.parameters.get(limit_name)
        if limit is None:
            checks.append(
                Check(
                    check_id=check_id,
                    description=(
                        f"pressure loss on the {side} side is {loss:.0f} Pa. It was not compared "
                        f"with a limit: {limit_name} was not given"
                    ),
                    outcome=CheckOutcome.NOT_EVALUATED,
                )
            )
            continue
        checks.append(
            Check(
                check_id=check_id,
                description=f"pressure loss on the {side} side of the exchanger",
                outcome=CheckOutcome.PASS if passes(limit - loss, limit) else CheckOutcome.FAIL,
                observed=loss,
                limit=limit,
                margin=limit - loss,
                unit="Pa (difference)",
                threshold_origin=spec.origin(limit_name),
            )
        )
    return losses[0], losses[1], checks


def _min_outlet_check(spec: PlantSpec, outlet_k: float, at: str) -> Check | None:
    name = "min_geothermal_outlet_temperature_k"
    limit = spec.parameters.get(name)
    if limit is None:
        return None
    margin = outlet_k - limit
    return Check(
        check_id="min_geothermal_outlet_temperature",
        description=f"geothermal outlet temperature at {at}",
        outcome=CheckOutcome.PASS if passes(margin, limit) else CheckOutcome.FAIL,
        observed=outlet_k,
        limit=limit,
        margin=margin,
        unit="K",
        threshold_origin=spec.origin(name),
    )


def _exchanger_checks(
    spec: PlantSpec,
    hot: Stream,
    cold: Stream,
    *,
    required_cold_outlet_k: float,
    rating_heat_w: float,
) -> list[Check]:
    """Checks of the exchanger between the geothermal stream and the stream it heats.

    The limits are checked in a fixed order: hot end, cold end, geothermal outlet temperature,
    surface rating. Each one is checked for the duty that the earlier ones permit. A shortfall
    is therefore reported once, at the limit that causes it.
    """
    approach = spec.parameters["exchanger_min_approach_delta_k"]
    hot_end, cold_end, permitted = terminal_checks(
        hot,
        cold,
        required_cold_outlet_k=required_cold_outlet_k,
        min_approach_delta_k=approach,
        threshold_origin=spec.origin("exchanger_min_approach_delta_k"),
    )
    checks = [hot_end, cold_end]
    outlet_check = _min_outlet_check(
        spec, hot.temperature_after_k(-permitted), "the duty the terminals permit"
    )
    if outlet_check is not None and outlet_check.limit is not None:
        checks.append(outlet_check)
        permitted = min(permitted, max(-hot.heat_to_reach_w(outlet_check.limit), 0.0))
    margin = rating_heat_w - permitted
    checks.append(
        Check(
            check_id="exchanger_rating",
            description=(
                "heat the exchanger surface can transfer, compared with the duty the other "
                "limits permit"
            ),
            outcome=CheckOutcome.PASS if passes(margin, permitted) else CheckOutcome.FAIL,
            observed=rating_heat_w,
            limit=permitted,
            margin=margin,
            unit="W",
            threshold_origin=f"rated with {spec.origin('exchanger_ua_w_k')}",
        )
    )
    return checks


def _exchanger_binding(limit: ExchangerLimit) -> str:
    if limit is ExchangerLimit.DEMAND:
        return DEMAND_MET
    if limit is ExchangerLimit.MIN_HOT_OUTLET:
        return "min_geothermal_outlet_temperature"
    return f"exchanger_{limit.value}"


# --- direct exchanger -----------------------------------------------------------------------


def _evaluate_direct(spec: PlantSpec, inlet: GeothermalInlet, demand: NetworkDemand) -> PlantPoint:
    notes: list[str] = []
    hot = Stream(inlet.mass_flow_kg_s, inlet.temperature_k, inlet.fluid)
    cold = Stream(demand.mass_flow_kg_s, demand.return_temperature_k, demand.fluid)
    approach = spec.parameters["exchanger_min_approach_delta_k"]
    point = rate_counterflow(
        hot,
        cold,
        ua_w_k=_effective_ua_w_k(spec, "exchanger_ua_w_k", notes),
        min_approach_delta_k=approach,
        cold_outlet_target_k=demand.supply_temperature_k,
        min_hot_outlet_temperature_k=spec.parameters.get("min_geothermal_outlet_temperature_k"),
    )
    required = demand.heat_w
    geothermal_loss, network_loss, pressure_checks = _pressure_losses(
        spec, inlet.mass_flow_kg_s, demand.mass_flow_kg_s
    )
    checks = _exchanger_checks(
        spec,
        hot,
        cold,
        required_cold_outlet_k=demand.supply_temperature_k,
        rating_heat_w=point.rating_heat_w,
    )
    checks.extend(pressure_checks)
    return PlantPoint(
        plant_id=spec.plant_id,
        arrangement=spec.arrangement,
        performance_basis=EQUIPMENT_RATING,
        binding_limit=_exchanger_binding(point.binding_limit),
        geothermal_mass_flow_kg_s=inlet.mass_flow_kg_s,
        geothermal_inlet_temperature_k=inlet.temperature_k,
        geothermal_outlet_temperature_k=point.hot_outlet_temperature_k,
        network_mass_flow_kg_s=demand.mass_flow_kg_s,
        network_inlet_temperature_k=demand.return_temperature_k,
        network_outlet_temperature_k=point.cold_outlet_temperature_k,
        required_outlet_temperature_k=demand.supply_temperature_k,
        required_heat_w=max(required, 0.0),
        heat_to_network_w=point.heat_w,
        heat_from_geothermal_w=point.heat_w,
        exchangers={"exchanger": point},
        geothermal_side_pressure_loss_pa=geothermal_loss,
        network_side_pressure_loss_pa=network_loss,
        checks=tuple(checks),
        balance_residual_w=point.balance_residual_w,
        warnings=tuple(notes),
    )


# --- heat pump assisted ---------------------------------------------------------------------


def _evaluate_heat_pump_assisted(
    spec: PlantSpec, inlet: GeothermalInlet, demand: NetworkDemand
) -> PlantPoint:
    if spec.heat_pump is None:
        raise ValueError("the heat pump assisted arrangement needs a heat pump model")
    notes: list[str] = []
    model = spec.heat_pump
    hot = Stream(inlet.mass_flow_kg_s, inlet.temperature_k, inlet.fluid)
    cold = Stream(demand.mass_flow_kg_s, demand.return_temperature_k, demand.fluid)
    approach = spec.parameters["exchanger_min_approach_delta_k"]
    min_outlet = spec.parameters["min_geothermal_outlet_temperature_k"]
    loss_fraction = spec.parameters["compressor_heat_loss_fraction"]
    required = max(demand.heat_w, 0.0)

    # Direct exchanger branch: the geothermal stream heats the network return as far as it can.
    preheat = rate_counterflow(
        hot,
        cold,
        ua_w_k=_effective_ua_w_k(spec, "exchanger_ua_w_k", notes),
        min_approach_delta_k=approach,
        cold_outlet_target_k=demand.supply_temperature_k,
        min_hot_outlet_temperature_k=min_outlet,
    )
    # Heat pump: evaporator on the geothermal stream after the exchanger, condenser on the
    # network stream after the exchanger.
    after_exchanger = Stream(inlet.mass_flow_kg_s, preheat.hot_outlet_temperature_k, inlet.fluid)
    required_condenser = max(required - preheat.heat_w, 0.0)
    available_evaporator = max(-after_exchanger.heat_to_reach_w(min_outlet), 0.0)

    checks: list[Check] = []
    pump: HeatPumpPoint | None = None
    binding = DEMAND_MET
    if required_condenser > _ROUNDING * max(required, 1.0):
        try:
            pump = operate_heat_pump(
                model,
                source_inlet_temperature_k=after_exchanger.inlet_temperature_k,
                sink_outlet_temperature_k=demand.supply_temperature_k,
                required_condenser_heat_w=required_condenser,
                max_evaporator_heat_w=available_evaporator,
                compressor_heat_loss_fraction=loss_fraction,
            )
        except OutsideMapDomain as outside:
            binding = "heat_pump_map_domain"
            notes.append(str(outside))
            checks.append(
                Check(
                    check_id="heat_pump_map_domain",
                    description=f"{outside.quantity} within the range of the performance model",
                    outcome=CheckOutcome.FAIL,
                    observed=outside.value,
                    limit=outside.bound,
                    margin=-abs(outside.value - outside.bound),
                    unit=outside.unit,
                    threshold_origin=model.description,
                )
            )
        else:
            if pump.binding_limit is not HeatPumpLimit.DEMAND:
                binding = pump.binding_limit.value
            checks.extend(_heat_pump_checks(spec, pump, after_exchanger, min_outlet))

    condenser = pump.condenser_heat_w if pump else 0.0
    evaporator = pump.evaporator_heat_w if pump else 0.0
    electricity = pump.compressor_electricity_w if pump else 0.0
    loss = pump.compressor_heat_loss_w if pump else 0.0
    to_network = preheat.heat_w + condenser
    from_geothermal = preheat.heat_w + evaporator
    geothermal_loss, network_loss, pressure_checks = _pressure_losses(
        spec, inlet.mass_flow_kg_s, demand.mass_flow_kg_s
    )
    checks.extend(pressure_checks)
    if model.basis != "performance_map":
        notes.append(
            "heat pump performance comes from a screening model. It is not achieved equipment "
            "performance"
        )
    return PlantPoint(
        plant_id=spec.plant_id,
        arrangement=spec.arrangement,
        performance_basis=model.basis,
        binding_limit=binding,
        geothermal_mass_flow_kg_s=inlet.mass_flow_kg_s,
        geothermal_inlet_temperature_k=inlet.temperature_k,
        geothermal_outlet_temperature_k=after_exchanger.temperature_after_k(-evaporator),
        network_mass_flow_kg_s=demand.mass_flow_kg_s,
        network_inlet_temperature_k=demand.return_temperature_k,
        network_outlet_temperature_k=cold.temperature_after_k(to_network),
        required_outlet_temperature_k=demand.supply_temperature_k,
        required_heat_w=required,
        heat_to_network_w=to_network,
        heat_from_geothermal_w=from_geothermal,
        compressor_electricity_w=electricity,
        heat_loss_to_surroundings_w=loss,
        exchangers={"direct_exchanger": preheat},
        heat_pump=pump,
        geothermal_side_pressure_loss_pa=geothermal_loss,
        network_side_pressure_loss_pa=network_loss,
        checks=tuple(checks),
        balance_residual_w=from_geothermal + electricity - to_network - loss,
        warnings=tuple(notes),
    )


def _heat_pump_checks(
    spec: PlantSpec, pump: HeatPumpPoint, source: Stream, min_outlet_k: float
) -> list[Check]:
    checks: list[Check] = []
    tolerance = 1e-9 * max(pump.condenser_heat_w, 1.0)
    checks.append(
        Check(
            check_id="heat_pump_balance",
            description=(
                "condenser heat equals evaporator heat plus compressor electricity, less the "
                f"stated loss of {pump.compressor_heat_loss_fraction:g} of that electricity"
            ),
            outcome=(
                CheckOutcome.PASS
                if abs(pump.balance_residual_w) <= tolerance
                else CheckOutcome.FAIL
            ),
            observed=abs(pump.balance_residual_w),
            limit=tolerance,
            margin=tolerance - abs(pump.balance_residual_w),
            unit="W",
            threshold_origin="energy conservation, within floating point rounding",
        )
    )
    if pump.max_condenser_heat_w is None:
        checks.append(
            Check(
                check_id="heat_pump_capacity",
                description=(
                    "heat pump capacity was not evaluated: the screening model states no capacity"
                ),
                outcome=CheckOutcome.NOT_EVALUATED,
            )
        )
    else:
        margin = pump.max_condenser_heat_w - pump.required_condenser_heat_w
        checks.append(
            Check(
                check_id="heat_pump_capacity",
                description="required condenser heat within the capacity of the heat pump",
                outcome=(
                    CheckOutcome.PASS
                    if passes(margin, pump.max_condenser_heat_w)
                    else CheckOutcome.FAIL
                ),
                observed=pump.required_condenser_heat_w,
                limit=pump.max_condenser_heat_w,
                margin=margin,
                unit="W",
                threshold_origin=pump.description,
            )
        )
    # Evaporator heat that the condenser heat would take from the geothermal stream. It is
    # checked for the duty the capacity permits, so a capacity shortfall is reported once.
    share = 1.0 - (1.0 - pump.compressor_heat_loss_fraction) / pump.heating_cop_ratio
    permitted = pump.required_condenser_heat_w
    if pump.max_condenser_heat_w is not None:
        permitted = min(permitted, pump.max_condenser_heat_w)
    outlet = source.temperature_after_k(-permitted * share)
    outlet_check = _min_outlet_check(spec, outlet, "the heat pump duty the capacity permits")
    if outlet_check is not None:
        checks.append(outlet_check)
    return checks


# --- pressure separation exchanger ----------------------------------------------------------


def _evaluate_pressure_separation(
    spec: PlantSpec, inlet: GeothermalInlet, demand: NetworkDemand
) -> PlantPoint:
    notes: list[str] = [
        "the intermediate loop circulates with the heat capacity rate of the network stream",
        "pumping of the intermediate loop is not modelled",
    ]
    hot = Stream(inlet.mass_flow_kg_s, inlet.temperature_k, inlet.fluid)
    network = Stream(demand.mass_flow_kg_s, demand.return_temperature_k, demand.fluid)
    approach = spec.parameters["exchanger_min_approach_delta_k"]
    separation_ua = spec.parameters["separation_exchanger_ua_w_k"]
    separation_approach = spec.parameters["separation_exchanger_min_approach_delta_k"]
    primary_ua = _effective_ua_w_k(spec, "exchanger_ua_w_k", notes)
    min_outlet = spec.parameters.get("min_geothermal_outlet_temperature_k")
    required = max(demand.heat_w, 0.0)

    def loop_for(heat_w: float) -> tuple[Stream, float]:
        """The loop stream entering the primary exchanger, and the temperature it must reach.

        With equal heat capacity rates the two streams of the separation exchanger keep a
        constant temperature difference. It is the difference the surface needs for this heat,
        and never less than the minimum approach.
        """
        difference = max(heat_w / separation_ua, separation_approach)
        loop_hot = network.temperature_after_k(heat_w) + difference
        loop_cold = demand.fluid.temperature_k(
            demand.fluid.enthalpy_j_kg(loop_hot) - heat_w / demand.mass_flow_kg_s
        )
        return Stream(demand.mass_flow_kg_s, loop_cold, demand.fluid), loop_hot

    def primary_for(heat_w: float) -> ExchangerPoint:
        loop, loop_hot = loop_for(heat_w)
        return rate_counterflow(
            hot,
            loop,
            ua_w_k=primary_ua,
            min_approach_delta_k=approach,
            cold_outlet_target_k=loop_hot,
            min_hot_outlet_temperature_k=min_outlet,
        )

    at_required = primary_for(required)
    heat = required
    if at_required.binding_limit is not ExchangerLimit.DEMAND:
        # The largest heat the primary exchanger can pass on. A larger heat needs a hotter loop,
        # so the set of heats it can deliver is one interval starting at zero.
        low, high = 0.0, required
        for _ in range(200):
            middle = 0.5 * (low + high)
            if primary_for(middle).binding_limit is ExchangerLimit.DEMAND:
                low = middle
            else:
                high = middle
            if high - low <= 1e-12 * max(required, 1.0):
                break
        heat = low
    primary = primary_for(heat)
    _, loop_hot = loop_for(heat)
    separation = rate_counterflow(
        Stream(demand.mass_flow_kg_s, loop_hot, demand.fluid),
        network,
        ua_w_k=separation_ua,
        min_approach_delta_k=0.0,
        cold_outlet_target_k=network.temperature_after_k(heat),
    )

    required_loop, required_loop_hot = loop_for(required)
    geothermal_loss, network_loss, pressure_checks = _pressure_losses(
        spec, inlet.mass_flow_kg_s, demand.mass_flow_kg_s
    )
    checks = _exchanger_checks(
        spec,
        hot,
        required_loop,
        required_cold_outlet_k=required_loop_hot,
        rating_heat_w=at_required.rating_heat_w,
    )
    checks.extend(pressure_checks)
    return PlantPoint(
        plant_id=spec.plant_id,
        arrangement=spec.arrangement,
        performance_basis=EQUIPMENT_RATING,
        binding_limit=_exchanger_binding(at_required.binding_limit),
        geothermal_mass_flow_kg_s=inlet.mass_flow_kg_s,
        geothermal_inlet_temperature_k=inlet.temperature_k,
        geothermal_outlet_temperature_k=hot.temperature_after_k(-heat),
        network_mass_flow_kg_s=demand.mass_flow_kg_s,
        network_inlet_temperature_k=demand.return_temperature_k,
        network_outlet_temperature_k=network.temperature_after_k(heat),
        required_outlet_temperature_k=demand.supply_temperature_k,
        required_heat_w=required,
        heat_to_network_w=heat,
        heat_from_geothermal_w=heat,
        exchangers={"exchanger": primary, "separation_exchanger": separation},
        geothermal_side_pressure_loss_pa=geothermal_loss,
        network_side_pressure_loss_pa=network_loss,
        checks=tuple(checks),
        balance_residual_w=primary.balance_residual_w,
        warnings=tuple(notes),
    )


def _coldest_outlet_of_exchanger(spec: PlantSpec, demand: NetworkDemand) -> float:
    approach = spec.parameters["exchanger_min_approach_delta_k"]
    return max(
        demand.return_temperature_k + approach,
        spec.parameters.get("min_geothermal_outlet_temperature_k", 0.0),
    )


def _coldest_outlet_of_separation(spec: PlantSpec, demand: NetworkDemand) -> float:
    return (
        _coldest_outlet_of_exchanger(spec, demand)
        + spec.parameters["separation_exchanger_min_approach_delta_k"]
    )


def _coldest_outlet_of_heat_pump(spec: PlantSpec, demand: NetworkDemand) -> float:
    return spec.parameters["min_geothermal_outlet_temperature_k"]


# --- registry entries -----------------------------------------------------------------------

DIRECT_EXCHANGER = register_arrangement(
    Arrangement(
        name="direct_exchanger",
        description=(
            "One counterflow exchanger between the geothermal circuit and the network circuit."
        ),
        required_parameters=("exchanger_ua_w_k", "exchanger_min_approach_delta_k"),
        optional_parameters=("min_geothermal_outlet_temperature_k", *_COMMON_OPTIONAL),
        uses_heat_pump=False,
        evaluate=_evaluate_direct,
        coldest_outlet_k=_coldest_outlet_of_exchanger,
    )
)

HEAT_PUMP_ASSISTED = register_arrangement(
    Arrangement(
        name="heat_pump_assisted",
        description=(
            "A direct exchanger heats the network return from the geothermal stream. A heat "
            "pump lifts it to the supply temperature. Its evaporator is on the geothermal "
            "stream after the exchanger, its condenser on the network stream after the "
            "exchanger."
        ),
        required_parameters=(
            "exchanger_ua_w_k",
            "exchanger_min_approach_delta_k",
            "min_geothermal_outlet_temperature_k",
            "compressor_heat_loss_fraction",
        ),
        optional_parameters=(
            *_COMMON_OPTIONAL,
            *_SCREENING_PARAMETERS,
            "heat_pump_max_condenser_heat_w",
        ),
        uses_heat_pump=True,
        evaluate=_evaluate_heat_pump_assisted,
        coldest_outlet_k=_coldest_outlet_of_heat_pump,
    )
)

PRESSURE_SEPARATION_EXCHANGER = register_arrangement(
    Arrangement(
        name="pressure_separation_exchanger",
        description=(
            "Two exchangers in series through an intermediate water loop: geothermal circuit to "
            "loop, and loop to network. The second exchanger separates the pressure zone of the "
            "network from the plant."
        ),
        required_parameters=(
            "exchanger_ua_w_k",
            "exchanger_min_approach_delta_k",
            "separation_exchanger_ua_w_k",
            "separation_exchanger_min_approach_delta_k",
        ),
        optional_parameters=("min_geothermal_outlet_temperature_k", *_COMMON_OPTIONAL),
        uses_heat_pump=False,
        evaluate=_evaluate_pressure_separation,
        coldest_outlet_k=_coldest_outlet_of_separation,
    )
)


# --- from a plant record to a plant that can be evaluated -----------------------------------


class PlantInputError(Exception):
    """A plant record that cannot be evaluated, with the state that describes why."""

    def __init__(self, state: AdapterState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason


def _value_problem(name: str, value: float) -> str | None:
    if name.endswith("_fraction"):
        return (
            None
            if 0.0 <= value < 1.0 or name == "carnot_efficiency_fraction"
            else (f"{name} must lie in 0 to below 1")
        )
    if name.endswith("_delta_k"):
        return None if value >= 0.0 else f"{name} must not be negative"
    return None if value > 0.0 else f"{name} must be positive"


def resolve_plant(plant: Plant) -> tuple[PlantSpec, tuple[MissingInput, ...]]:
    """Resolve a plant record into a plant that can be evaluated.

    Raises ``NotEstablished`` when a required value is unknown or in conflict, and
    ``PlantInputError`` when the arrangement is not registered or the record does not fit it.
    Optional values that are not established are left out and returned, so that they stay
    visible.
    """
    arrangement = arrangement_named(plant.arrangement)
    if arrangement is None:
        known = ", ".join(item.name for item in arrangements())
        raise PlantInputError(
            AdapterState.UNSUPPORTED_MODEL,
            f"plant arrangement {plant.arrangement!r} is not registered. Registered: {known}",
        )
    base = f"plants[{plant.plant_id}]"
    allowed = {*arrangement.required_parameters, *arrangement.optional_parameters}
    problems = [
        f"parameter {name} is not used by arrangement {arrangement.name}"
        for name in sorted(set(plant.parameters) - allowed)
    ]
    problems += [
        f"parameter {name} is required by arrangement {arrangement.name}"
        for name in arrangement.required_parameters
        if name not in plant.parameters
    ]
    if plant.heat_pump_map is not None and not arrangement.uses_heat_pump:
        problems.append(f"arrangement {arrangement.name} has no heat pump, but a map is given")
    if problems:
        raise PlantInputError(AdapterState.INVALID_INPUT, "; ".join(problems))

    reader = InputReader()
    values: dict[str, float] = {}
    left_out: list[MissingInput] = []
    for name, evidence in plant.parameters.items():
        path = f"{base}.parameters[{name}]"
        if name in arrangement.required_parameters:
            value = reader.get(evidence, path)
        elif isinstance(evidence, Known):
            value = evidence.value
        else:
            left_out.append(missing_input(evidence, path))
            value = None
        if value is not None:
            values[name] = value

    model: HeatPumpModel | None = None
    if arrangement.uses_heat_pump:
        if plant.heat_pump_map is not None:
            table = reader.get(plant.heat_pump_map, f"{base}.heat_pump_map")
            model = MapHeatPump(table) if table is not None else None
        elif all(name in plant.parameters for name in _SCREENING_PARAMETERS):
            missing_screening = [name for name in _SCREENING_PARAMETERS if name not in values]
            if missing_screening:
                for name in missing_screening:
                    reader.get(plant.parameters[name], f"{base}.parameters[{name}]")
        else:
            raise PlantInputError(
                AdapterState.INVALID_INPUT,
                f"arrangement {arrangement.name} needs a heat pump performance map, or the "
                f"parameters of the screening model: {', '.join(_SCREENING_PARAMETERS)}",
            )
    reader.raise_if_missing()

    problems = [
        problem
        for name, value in sorted(values.items())
        if (problem := _value_problem(name, value)) is not None
    ]
    if problems:
        raise PlantInputError(AdapterState.INVALID_INPUT, "; ".join(problems))

    if arrangement.uses_heat_pump and model is None:
        try:
            model = CarnotScreeningHeatPump(
                carnot_efficiency_fraction=values["carnot_efficiency_fraction"],
                evaporator_temperature_difference_delta_k=values[
                    "evaporator_temperature_difference_delta_k"
                ],
                condenser_temperature_difference_delta_k=values[
                    "condenser_temperature_difference_delta_k"
                ],
                max_condenser_heat_w=values.get("heat_pump_max_condenser_heat_w"),
            )
        except ValueError as error:
            raise PlantInputError(AdapterState.INVALID_INPUT, str(error)) from error
    spec = PlantSpec(
        plant_id=plant.plant_id,
        arrangement=arrangement.name,
        parameters=values,
        heat_pump=model,
    )
    return spec, tuple(left_out)


@dataclass(frozen=True)
class FlowChoice:
    """The geothermal flow a plant asks for, and the limit that no flow in the range lifts."""

    flow_kg_s: float
    meets_demand: bool
    # 'demand' when the demand can be met. Otherwise the limit that binds at the largest flow.
    limit_at_largest_flow: str


def required_geothermal_flow(
    spec: PlantSpec,
    *,
    inlet_temperature_k: float,
    fluid: EnthalpyModel,
    demand: NetworkDemand,
    min_flow_kg_s: float,
    max_flow_kg_s: float,
) -> FlowChoice:
    """The smallest geothermal flow in a range that serves the demand as well as possible.

    More geothermal flow never lets a plant deliver less heat. If the demand can be met, the
    result is the smallest flow that meets it. If it cannot, the result is the smallest flow
    that delivers as much heat as the largest flow does. A plant that is short of temperature
    therefore does not drive the source to its flow limit for nothing.
    """
    arrangement = _REGISTRY[spec.arrangement]

    def point_at(flow_kg_s: float) -> PlantPoint:
        inlet = GeothermalInlet(flow_kg_s, inlet_temperature_k, fluid)
        return arrangement.evaluate(spec, inlet, demand)

    largest = point_at(max_flow_kg_s)
    most = largest.heat_to_network_w

    def serves(flow_kg_s: float) -> bool:
        point = point_at(flow_kg_s)
        if largest.meets_demand:
            return point.meets_demand
        return point.heat_to_network_w >= most * (1.0 - 1e-12)

    low, high = min_flow_kg_s, max_flow_kg_s
    if serves(low):
        high = low
    else:
        for _ in range(200):
            middle = 0.5 * (low + high)
            if serves(middle):
                high = middle
            else:
                low = middle
            if high - low <= 1e-12 * high:
                break
    return FlowChoice(
        flow_kg_s=high,
        meets_demand=largest.meets_demand,
        limit_at_largest_flow=largest.binding_limit,
    )


def evaluate_plant(spec: PlantSpec, inlet: GeothermalInlet, demand: NetworkDemand) -> PlantPoint:
    return _REGISTRY[spec.arrangement].evaluate(spec, inlet, demand)


def coldest_geothermal_outlet_k(spec: PlantSpec, demand: NetworkDemand) -> float:
    return _REGISTRY[spec.arrangement].coldest_outlet_k(spec, demand)


def performance_basis(spec: PlantSpec) -> str:
    return spec.heat_pump.basis if spec.heat_pump is not None else EQUIPMENT_RATING
