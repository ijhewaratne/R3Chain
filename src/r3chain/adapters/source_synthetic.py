"""A deterministic test model of a geothermal doublet.

THIS IS NOT PYDOUBLET AND IT IS NOT VALIDATED PHYSICS. It is a small set of invented relations
that lets the rest of the system be built and tested before the real engine is available
(specification 20.13, open decision O03). Every result it returns is labelled synthetic and can
never enter a pilot ranking.

It is useful as a test engine because its behaviour is fully known:

* The wellhead temperature has a closed form solution, so results can be checked exactly.
* The same temperature is found by a fixed point iteration that genuinely fails to converge at
  very low flow or with too few iterations. That gives a real numerical failure which is clearly
  different from a physical limit.
* It has explicit operating limits, so physical failures can be produced on purpose.

Relations
---------
Productivity (radial Darcy flow), in kg/s per Pa::

    PI = 2 pi k h rho / (mu ln(r_e / r_w))      with h = thickness * net to gross

Drawdown at the production well and pressure rise at the injection well::

    dp = m / PI

Wellhead temperature, from a heat loss along the production well that is proportional to the
difference between the mean fluid temperature and the mean ground temperature::

    T_wh = T_res - G ((T_res + T_wh) / 2 - T_ground) / (m cp)      with G = loss per metre * length

Extracted heat and electrical pump power::

    Q = m cp (T_wh - T_inj)          P = m (dp_production + dp_injection) / (rho eta)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from r3chain.domain.records.runs import Check, CheckOutcome
from r3chain.domain.records.site import WellRole
from r3chain.domain.status import AdapterState
from r3chain.ports.source import (
    OutputSemantics,
    SourceCapabilities,
    SourcePerformance,
    SourceRequest,
    SourceResult,
)

ENGINE = "r3chain-synthetic-source"
ENGINE_VERSION = "1.0.0"
MODEL_FAMILY = "synthetic-test-model"
WELL_CONFIGURATION = "doublet"
FIDELITY = (
    "Invented analytic relations for software testing. Not PyDoublet, not a reservoir "
    "simulator and not validated physics."
)

REQUIRED_PARAMETERS = (
    "reservoir_temperature_k",
    "reservoir_thickness_m",
    "net_to_gross_fraction",
    "permeability_m2",
)
OPTIONAL_PARAMETERS = (
    "max_mass_flow_kg_s",
    "min_injection_temperature_k",
    "max_drawdown_pa",
)
NOT_CALCULATED = (
    "production_wellhead_pressure",
    "injection_wellhead_pressure",
    "long_term_decline",
    "economics",
)


@dataclass(frozen=True)
class SyntheticConstants:
    """The constants of the test model. Changing one of them is a new engine version."""

    heat_capacity_j_kg_k: float = 4180.0
    density_kg_m3: float = 1000.0
    dynamic_viscosity_pa_s: float = 3.0e-4
    well_radius_m: float = 0.1
    drainage_radius_m: float = 1000.0
    wellbore_loss_w_m_k: float = 2.0
    surface_temperature_k: float = 283.15
    pump_efficiency_fraction: float = 0.6


CONSTANTS = SyntheticConstants()


def productivity_index_kg_s_pa(
    permeability_m2: float, net_thickness_m: float, constants: SyntheticConstants = CONSTANTS
) -> float:
    return (
        2.0
        * math.pi
        * permeability_m2
        * net_thickness_m
        * constants.density_kg_m3
        / (
            constants.dynamic_viscosity_pa_s
            * math.log(constants.drainage_radius_m / constants.well_radius_m)
        )
    )


class SyntheticSourceModel:
    """The test engine behind the source port."""

    def __init__(self, constants: SyntheticConstants = CONSTANTS) -> None:
        self._constants = constants

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            engine=ENGINE,
            engine_version=ENGINE_VERSION,
            fidelity=FIDELITY,
            synthetic=True,
            model_families=(MODEL_FAMILY,),
            well_configurations=(WELL_CONFIGURATION,),
            required_reservoir_parameters={MODEL_FAMILY: REQUIRED_PARAMETERS},
            optional_reservoir_parameters={MODEL_FAMILY: OPTIONAL_PARAMETERS},
            planned=("multiple injection wells", "fractured reservoir relations"),
        )

    # --- result builders --------------------------------------------------------------------

    def _result(
        self, request: SourceRequest, state: AdapterState, **fields: object
    ) -> SourceResult:
        return SourceResult.model_validate(
            {
                "engine": ENGINE,
                "engine_version": ENGINE_VERSION,
                "fidelity": FIDELITY,
                "model_family": request.model_family,
                "source_scenario_id": request.source_scenario_id,
                "input_hash": request.input_hash(),
                "sample_id": request.sample_id,
                "state": state,
                "not_calculated": NOT_CALCULATED,
                "economics_calculated": False,
                "synthetic": True,
                "raw_request": request.to_json_dict(),
                **fields,
            }
        )

    # --- evaluation -------------------------------------------------------------------------

    def evaluate(self, request: SourceRequest) -> SourceResult:
        constants = self._constants

        if request.model_family != MODEL_FAMILY:
            return self._result(
                request,
                AdapterState.UNSUPPORTED_MODEL,
                reason=f"model family {request.model_family!r} is not supported by {ENGINE}",
            )
        roles = sorted(well.role.value for well in request.wells)
        if request.well_configuration_id != WELL_CONFIGURATION or roles != [
            WellRole.INJECTION.value,
            WellRole.PRODUCTION.value,
        ]:
            return self._result(
                request,
                AdapterState.UNSUPPORTED_MODEL,
                reason=(
                    "only a doublet of one production and one injection well is supported, "
                    f"not configuration {request.well_configuration_id!r} with wells {roles}"
                ),
            )

        parameters = request.reservoir_parameters
        problems = [f"missing {name}" for name in REQUIRED_PARAMETERS if name not in parameters]
        problems += [
            f"{name} must be positive"
            for name in (*REQUIRED_PARAMETERS, *OPTIONAL_PARAMETERS)
            if name in parameters and parameters[name] <= 0.0
        ]
        if parameters.get("net_to_gross_fraction", 0.5) > 1.0:
            problems.append("net_to_gross_fraction cannot exceed 1")
        mass_flow = request.operating_point.requested_mass_flow_kg_s
        injection_temperature = request.operating_point.injection_temperature_k
        if mass_flow <= 0.0:
            problems.append(
                "requested mass flow must be positive. The wellhead temperature is not defined "
                "without flow"
            )
        if injection_temperature <= 0.0:
            problems.append("injection temperature must be an absolute temperature above 0 K")
        production = next(well for well in request.wells if well.role is WellRole.PRODUCTION)
        if production.measured_length_m <= 0.0:
            problems.append("the production well needs a positive measured length")
        if problems:
            return self._result(request, AdapterState.INVALID_INPUT, reason="; ".join(problems))

        reservoir_temperature = parameters["reservoir_temperature_k"]
        net_thickness = parameters["reservoir_thickness_m"] * parameters["net_to_gross_fraction"]
        productivity = productivity_index_kg_s_pa(
            parameters["permeability_m2"], net_thickness, constants
        )
        drawdown = mass_flow / productivity

        # Operating limits. Each one is reported with its observed value and margin.
        origin = f"reservoir_parameters of {request.source_scenario_id}"
        checks: list[Check] = []

        def limit_check(
            name: str, description: str, observed: float, upper: bool, unit: str
        ) -> None:
            if name not in parameters:
                return
            limit = parameters[name]
            margin = limit - observed if upper else observed - limit
            checks.append(
                Check(
                    check_id=name,
                    description=description,
                    outcome=CheckOutcome.PASS if margin >= 0.0 else CheckOutcome.FAIL,
                    observed=observed,
                    limit=limit,
                    margin=margin,
                    unit=unit,
                    threshold_origin=f"{name} in {origin}",
                )
            )

        limit_check(
            "max_mass_flow_kg_s", "mass flow within the well limit", mass_flow, True, "kg/s"
        )
        limit_check(
            "min_injection_temperature_k",
            "injection temperature above its minimum",
            injection_temperature,
            False,
            "K",
        )
        limit_check("max_drawdown_pa", "drawdown within the pressure limit", drawdown, True, "Pa")
        failed = tuple(check.check_id for check in checks if check.outcome is CheckOutcome.FAIL)
        if failed:
            return self._result(
                request,
                AdapterState.PHYSICAL_LIMIT_EXCEEDED,
                reason=f"operating limit exceeded: {', '.join(failed)}",
                checks=tuple(checks),
                active_limits=failed,
            )

        # Wellhead temperature by fixed point iteration.
        conductance = constants.wellbore_loss_w_m_k * production.measured_length_m
        ground_temperature = (constants.surface_temperature_k + reservoir_temperature) / 2.0
        capacity_rate = mass_flow * constants.heat_capacity_j_kg_k
        tolerance = request.numerical.temperature_tolerance_delta_k
        wellhead = reservoir_temperature
        change = math.inf
        iterations = 0
        converged = False
        while iterations < request.numerical.max_iterations_count:
            iterations += 1
            mean_fluid = (reservoir_temperature + wellhead) / 2.0
            updated = reservoir_temperature - conductance * (mean_fluid - ground_temperature) / (
                capacity_rate
            )
            change = abs(updated - wellhead)
            wellhead = updated
            if not math.isfinite(wellhead) or abs(wellhead) > 1.0e9:
                break
            if change <= tolerance:
                converged = True
                break
        if not converged:
            return self._result(
                request,
                AdapterState.NON_CONVERGED,
                reason=(
                    f"the wellhead temperature iteration did not converge in {iterations} "
                    f"iterations. The last change was {change:.3g} K"
                ),
                checks=tuple(checks),
                iterations_count=iterations,
            )

        heat_check = Check(
            check_id="heat_extraction",
            description="wellhead temperature above injection temperature",
            outcome=CheckOutcome.PASS if wellhead > injection_temperature else CheckOutcome.FAIL,
            observed=wellhead,
            limit=injection_temperature,
            margin=wellhead - injection_temperature,
            unit="K",
            threshold_origin="requested injection temperature",
        )
        checks.append(heat_check)
        if heat_check.outcome is CheckOutcome.FAIL:
            return self._result(
                request,
                AdapterState.PHYSICAL_LIMIT_EXCEEDED,
                reason="the injection temperature is not below the wellhead temperature",
                checks=tuple(checks),
                active_limits=("heat_extraction",),
                iterations_count=iterations,
            )

        heat = capacity_rate * (wellhead - injection_temperature)
        pump_power = (
            mass_flow
            * 2.0
            * drawdown
            / (constants.density_kg_m3 * constants.pump_efficiency_fraction)
        )
        performance = SourcePerformance(
            mass_flow_kg_s=mass_flow,
            mass_flow_semantics=OutputSemantics.REQUESTED_VALUE_ACHIEVED,
            wellhead_production_temperature_k=wellhead,
            injection_temperature_k=injection_temperature,
            geothermal_extracted_heat_w=heat,
            heat_semantics=OutputSemantics.AVAILABLE_CAPACITY,
            electrical_pump_power_w=pump_power,
            production_drawdown_pa=drawdown,
            injection_pressure_rise_pa=drawdown,
        )
        return self._result(
            request,
            AdapterState.COMPLETED,
            performance=performance,
            checks=tuple(checks),
            iterations_count=iterations,
            raw_result={
                "productivity_index_kg_s_pa": productivity,
                "wellbore_conductance_w_k": conductance,
                "ground_temperature_k": ground_temperature,
                "constants": asdict(constants),
            },
        )
