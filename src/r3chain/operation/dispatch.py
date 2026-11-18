"""Rule-based chronological heat dispatch and scenario builders (specification 12.1-12.3)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Literal

from r3chain.coupling.loop import CouplingResult, ShortfallCause
from r3chain.domain import policies
from r3chain.domain.base import NonNegativeReal, PositiveReal, Record
from r3chain.domain.evidence import InputReader, MissingInput, NotEstablished
from r3chain.domain.records.runs import Policy
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.scenarios import ExistingSourceRole, OperatingScenario
from r3chain.domain.status import AdapterState
from r3chain.domain.timeseries import IntervalSeries, SeriesKind
from r3chain.ports.dispatch import (
    DispatchCapabilities,
    DispatchElectricityPoint,
    DispatchGeothermalSpec,
    DispatchRequest,
    DispatchResult,
    DispatchStrategy,
    DispatchTotals,
    DispatchUnit,
    ExistingUnitRole,
)

# --- policy settings ------------------------------------------------------------------------


class DispatchSettings(Record):
    geothermal_priority: int
    allow_curtailment: bool
    interval_mode: str
    max_reduction_error_fraction: NonNegativeReal
    reduced_stride_count: int
    operating_scenario_id: str | None = None
    max_unserved_heat_j: NonNegativeReal | None = None

    @classmethod
    def from_policy(cls, policy: Policy) -> DispatchSettings:
        mode = policies.text(policy, "interval_mode")
        if mode not in {"full", "reduced"}:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: interval_mode must be 'full' or 'reduced'"
            )
        max_err = policies.number(policy, "max_reduction_error_fraction")
        if not 0.0 <= max_err <= 1.0:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: max_reduction_error_fraction must lie in [0, 1]"
            )
        stride = policies.count(policy, "reduced_stride_count")
        if stride < 1:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: reduced_stride_count must be at least 1"
            )
        return cls(
            geothermal_priority=policies.count(policy, "geothermal_priority_count"),
            allow_curtailment=policies.flag(policy, "allow_curtailment"),
            interval_mode=mode,
            max_reduction_error_fraction=max_err,
            reduced_stride_count=stride,
            operating_scenario_id=policies.optional_text(policy, "operating_scenario_id"),
            max_unserved_heat_j=policies.optional_non_negative_number(
                policy, "max_unserved_heat_j"
            ),
        )


# --- rule-based dispatch strategy (extension point X8) --------------------------------------


class RuleBasedDispatchStrategy:
    """Priority-ordered chronological dispatch with explicit curtailment and backup tracking.

    Merit order:

    1. Existing units with ``priority < geothermal_priority`` (existing base load such as waste
       incineration).
    2. Geothermal plant (if present), subject to availability/maintenance and minimum output
       turn-down. Unused geothermal availability is logged as ``curtailed_w`` and receives no
       energy or economic credit.
    3. Existing units with ``priority >= geothermal_priority`` (peak and backup boilers).
    4. Any remaining demand stays explicit as ``unserved_w``.
    """

    def capabilities(self) -> DispatchCapabilities:
        return DispatchCapabilities(
            strategy="r3chain-rule-based-dispatch",
            strategy_version="1.0.0",
            supports_existing_base_load=True,
            supports_backup_sources=True,
            supports_min_output_and_maintenance=True,
            supports_storage=False,
            synthetic=False,
        )

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        caps = self.capabilities()
        if request.requires_storage and not caps.supports_storage:
            return DispatchResult(
                strategy=caps.strategy,
                strategy_version=caps.strategy_version,
                state=AdapterState.UNSUPPORTED_MODEL,
                reason=(
                    "thermal storage is required by the request but not supported by this strategy"
                ),
            )
        geo = request.geothermal
        before_geo = sorted(
            (u for u in request.existing_units if u.priority < request.geothermal_priority),
            key=lambda u: (u.priority, u.unit_id),
        )
        after_geo = sorted(
            (u for u in request.existing_units if u.priority >= request.geothermal_priority),
            key=lambda u: (u.priority, u.unit_id),
        )
        by_unit_w: dict[str, list[float]] = {u.unit_id: [] for u in request.existing_units}
        by_unit_j: dict[str, float] = {u.unit_id: 0.0 for u in request.existing_units}

        geo_w: list[float] = []
        geo_plant_w: list[float] = []
        curtailed_w: list[float] = []
        unserved_w: list[float] = []

        demand_j = 0.0
        delivered_j = 0.0
        geo_avail_j = 0.0
        geo_delivered_j = 0.0
        geo_plant_j = 0.0
        geo_curtailed_j = 0.0
        route_loss_j = 0.0
        base_j = 0.0
        backup_j = 0.0
        unserved_j = 0.0
        pump_elec_j = 0.0
        comp_elec_j = 0.0
        operating_s = 0.0
        geo_elec_cost = 0.0
        backup_cost = 0.0
        base_cost = 0.0
        peak_backup_w = 0.0
        elec_prices = request.electricity_price_currency_per_j

        for index, (dt, dem_w) in enumerate(
            zip(request.durations_s, request.demand_heat_w, strict=True)
        ):
            demand_j += dem_w * dt
            remaining = dem_w

            # 1. Existing base load (higher priority than geothermal).
            for unit in before_geo:
                out_w = _allocate_existing_unit(unit, index, remaining)
                by_unit_w[unit.unit_id].append(out_w)
                by_unit_j[unit.unit_id] += out_w * dt
                base_j += out_w * dt
                base_cost += _unit_interval_cost(unit, index, out_w, dt)
                remaining -= out_w

            # 2. Geothermal plant.
            if geo is not None:
                avail_frac = geo.availability_fraction[index] if geo.availability_fraction else 1.0
                raw_cap_w = (
                    geo.capacity_series_w[index]
                    if geo.capacity_series_w
                    else geo.max_delivered_heat_w
                )
                avail_w = raw_cap_w * avail_frac
                full_loss_w = (
                    geo.plant_loss_series_w[index]
                    if geo.plant_loss_series_w
                    else geo.route_loss_when_active_w
                )
                min_w = avail_w * geo.min_output_fraction
                geo_avail_j += avail_w * dt
                if avail_w > 0.0 and remaining > 0.0 and remaining >= min_w:
                    dispatched_w = min(remaining, avail_w)
                    curtail_w = max(avail_w - dispatched_w, 0.0)
                    # Transmission pipe loss depends on operating temperature, not load factor.
                    loss_w = full_loss_w
                    plant_out_w = dispatched_w + loss_w
                    operating_s += dt
                else:
                    dispatched_w = 0.0
                    plant_out_w = 0.0
                    loss_w = 0.0
                    curtail_w = avail_w
                remaining -= dispatched_w
                geo_w.append(dispatched_w)
                geo_plant_w.append(plant_out_w)
                curtailed_w.append(curtail_w)
                geo_delivered_j += dispatched_w * dt
                geo_plant_j += plant_out_w * dt
                geo_curtailed_j += curtail_w * dt
                route_loss_j += loss_w * dt
                p_w, c_w = _interpolate_electricity_w(geo, plant_out_w)
                p_elec_j = p_w * dt
                c_elec_j = c_w * dt
                pump_elec_j += p_elec_j
                comp_elec_j += c_elec_j
                if p_elec_j + c_elec_j > 0.0:
                    if not elec_prices:
                        raise NotEstablished(
                            (
                                MissingInput(
                                    field_path=(
                                        f"operating_scenarios[{request.operating_scenario_id}]"
                                        ".electricity_tariff"
                                    ),
                                    state="unknown",
                                    reason=(
                                        "electricity tariff is required when geothermal pumps or "
                                        "compressors consume electricity"
                                    ),
                                    owner="economics specialist",
                                ),
                            )
                        )
                    geo_elec_cost += (p_elec_j + c_elec_j) * elec_prices[index]
            else:
                geo_w.append(0.0)
                geo_plant_w.append(0.0)
                curtailed_w.append(0.0)

            # 3. Backup / peaking units (lower priority than geothermal).
            interval_backup_w = 0.0
            for unit in after_geo:
                out_w = _allocate_existing_unit(unit, index, remaining)
                by_unit_w[unit.unit_id].append(out_w)
                by_unit_j[unit.unit_id] += out_w * dt
                backup_j += out_w * dt
                interval_backup_w += out_w
                backup_cost += _unit_interval_cost(unit, index, out_w, dt)
                remaining -= out_w
            peak_backup_w = max(peak_backup_w, interval_backup_w)

            unserved_here = max(remaining, 0.0)
            unserved_w.append(unserved_here)
            unserved_j += unserved_here * dt
            delivered_j += (dem_w - unserved_here) * dt

        ref_cap_w = geo.max_delivered_heat_w if geo is not None else 0.0
        efls = geo_delivered_j / ref_cap_w if ref_cap_w > 0.0 else 0.0
        utilisation = geo_delivered_j / geo_avail_j if geo_avail_j > 0.0 else 0.0
        diagnostics: list[str] = []
        not_modelled: list[str] = [
            "multi-year reservoir thermal decline is not modelled in single-year dispatch"
        ]
        if _has_electricity_consumption(geo):
            assert geo is not None
            if len(geo.electricity_curve) >= 2:
                not_modelled.append(
                    "well pump and compressor electricity are interpolated piecewise-linearly "
                    "across coupled operating points rather than nonlinear part-load curves"
                )
            else:
                not_modelled.append(
                    "well pump and compressor electricity are scaled proportionally from one "
                    "coupled operating point rather than nonlinear part-load curves"
                )
        if geo is not None and not request.allow_curtailment and geo_curtailed_j > 0.0:
            diagnostics.append(
                f"{geo_curtailed_j:.3g} J of geothermal capacity could not be absorbed because "
                "no thermal storage is modelled"
            )
        totals = DispatchTotals(
            total_duration_s=math.fsum(request.durations_s),
            demand_heat_j=demand_j,
            delivered_heat_j=delivered_j,
            geothermal_available_heat_j=geo_avail_j,
            geothermal_delivered_heat_j=geo_delivered_j,
            geothermal_plant_handover_heat_j=geo_plant_j,
            geothermal_curtailed_heat_j=geo_curtailed_j,
            route_heat_loss_j=route_loss_j,
            base_load_heat_j=base_j,
            backup_heat_j=backup_j,
            unserved_heat_j=unserved_j,
            source_pump_electricity_j=pump_elec_j,
            compressor_electricity_j=comp_elec_j,
            operating_time_s=operating_s,
            equivalent_full_load_s=efls,
            reference_capacity_w=ref_cap_w,
            utilisation_fraction=utilisation,
            geothermal_electricity_cost_currency=geo_elec_cost,
            backup_fuel_and_om_cost_currency=backup_cost,
            base_load_fuel_and_om_cost_currency=base_cost,
            energy_cost_currency=geo_elec_cost + backup_cost + base_cost,
            peak_backup_heat_w=peak_backup_w,
            capacity_basis=geo.capacity_basis if geo is not None else "lower_bound",
            by_unit_heat_j=by_unit_j,
        )
        return DispatchResult(
            strategy=caps.strategy,
            strategy_version=caps.strategy_version,
            state=AdapterState.COMPLETED,
            synthetic=caps.synthetic,
            totals=totals,
            geothermal_w=tuple(geo_w),
            geothermal_plant_w=tuple(geo_plant_w),
            curtailed_w=tuple(curtailed_w),
            unserved_w=tuple(unserved_w),
            by_unit_w={key: tuple(values) for key, values in by_unit_w.items()},
            diagnostics=tuple(diagnostics),
            not_modelled=tuple(not_modelled),
        )


def _allocate_existing_unit(unit: DispatchUnit, index: int, remaining_w: float) -> float:
    if remaining_w <= 0.0 or unit.max_heat_w <= 0.0:
        return 0.0
    avail_frac = unit.availability_fraction[index] if unit.availability_fraction else 1.0
    available_w = unit.max_heat_w * avail_frac
    if available_w < unit.min_output_w or available_w <= 0.0:
        return 0.0
    if remaining_w < unit.min_output_w:
        return 0.0
    return min(remaining_w, available_w)


def _unit_interval_cost(unit: DispatchUnit, index: int, output_w: float, dt: float) -> float:
    if output_w <= 0.0:
        return 0.0
    heat_j = output_w * dt
    fuel_price = unit.fuel_price_currency_per_j[index] if unit.fuel_price_currency_per_j else 0.0
    fuel_cost = (heat_j / unit.thermal_efficiency_fraction) * fuel_price
    om_cost = heat_j * unit.variable_om_currency_per_j
    return fuel_cost + om_cost


# --- default registry -----------------------------------------------------------------------


def default_dispatch_strategy() -> DispatchStrategy:
    return RuleBasedDispatchStrategy()


# --- builders from scenario and coupling results --------------------------------------------


def _power_series_w(series: IntervalSeries) -> tuple[NonNegativeReal, ...]:
    if series.kind is SeriesKind.INTERVAL_MEAN and series.unit == "W":
        divide_by_dt = False
    elif series.kind is SeriesKind.INTERVAL_TOTAL and series.unit == "J":
        divide_by_dt = True
    else:
        raise ValueError(
            f"demand series {series.series_id!r} must be mean power in W or interval energy in J"
        )
    powers: list[float] = []
    for idx, (value, start, dt) in enumerate(
        zip(series.values, series.starts_utc, series.durations_s, strict=True)
    ):
        val = float(value)
        if val < 0.0:
            raise ValueError(
                f"demand series {series.series_id!r} cannot be negative "
                f"(interval {idx} at {start.isoformat()} is {val})"
            )
        powers.append(val / dt if divide_by_dt else val)
    return tuple(powers)


def _has_electricity_consumption(geo: DispatchGeothermalSpec | None) -> bool:
    if geo is None:
        return False
    return (
        geo.source_electricity_per_plant_heat_ratio > 0.0
        or geo.compressor_electricity_per_plant_heat_ratio > 0.0
        or any(
            pt.source_pump_electricity_w > 0.0 or pt.compressor_electricity_w > 0.0
            for pt in geo.electricity_curve
        )
    )


def _interpolate_electricity_w(
    geo: DispatchGeothermalSpec,
    plant_out_w: float,
) -> tuple[float, float]:
    """Interpolate well-pump and compressor electrical power at plant handover load."""
    if plant_out_w <= 0.0:
        return 0.0, 0.0
    curve = geo.electricity_curve
    if not curve:
        return (
            plant_out_w * geo.source_electricity_per_plant_heat_ratio,
            plant_out_w * geo.compressor_electricity_per_plant_heat_ratio,
        )
    first = curve[0]
    if first.plant_heat_w > 0.0 and plant_out_w <= first.plant_heat_w:
        frac = plant_out_w / first.plant_heat_w
        return frac * first.source_pump_electricity_w, frac * first.compressor_electricity_w
    last = curve[-1]
    if plant_out_w >= last.plant_heat_w:
        if last.plant_heat_w <= 0.0:
            return 0.0, 0.0
        scale = plant_out_w / last.plant_heat_w
        return scale * last.source_pump_electricity_w, scale * last.compressor_electricity_w
    for idx in range(len(curve) - 1):
        left, right = curve[idx], curve[idx + 1]
        if left.plant_heat_w <= plant_out_w <= right.plant_heat_w:
            frac = (plant_out_w - left.plant_heat_w) / (right.plant_heat_w - left.plant_heat_w)
            pump_w = left.source_pump_electricity_w + frac * (
                right.source_pump_electricity_w - left.source_pump_electricity_w
            )
            comp_w = left.compressor_electricity_w + frac * (
                right.compressor_electricity_w - left.compressor_electricity_w
            )
            return pump_w, comp_w
    return 0.0, 0.0


def geothermal_spec_from_coupling(
    candidate_id: str,
    coupling_results: Sequence[CouplingResult],
    *,
    availability: tuple[float, ...] = (),
    min_output_fraction: float = 0.0,
) -> DispatchGeothermalSpec:
    """Turn coupled operating point(s) into a geothermal capacity for annual dispatch.

    Coupling solves demand-following operating points rather than sweeping full capacity curves.
    The largest compliant plant output across all coupled cases therefore defines the available
    capacity, marked ``limit_proven`` when at least one evaluated case hit a shortfall/deficit and
    ``lower_bound`` when every coupled case covered demand with zero deficit.
    """
    # Tuple fields: (delivered_w, plant_w, loss_w, pump_ratio, comp_ratio)
    compliant_points: list[tuple[float, float, float, float, float]] = []
    curve_by_heat_w: dict[float, tuple[float, float]] = {}
    limit_proven = False
    for result in coupling_results:
        point = result.operating_point
        if point is None:
            continue
        if point.deficit_heat_w > 1.0 or bool(result.shortfall_causes):
            limit_proven = True
        if ShortfallCause.INSUFFICIENT_TEMPERATURE in result.shortfall_causes:
            # Cannot reach the required network supply temperature on its own: yields no usable
            # compliant geothermal heat without an additional temperature-lift stage.
            continue
        loss_w = point.transmission_heat_loss_w or 0.0
        delivered_w = max(point.plant_heat_w - loss_w, 0.0)
        pump_ratio = (
            point.source_pump_electricity_w / point.plant_heat_w
            if point.plant_heat_w > 0.0
            else 0.0
        )
        comp_ratio = (
            point.compressor_electricity_w / point.plant_heat_w if point.plant_heat_w > 0.0 else 0.0
        )
        compliant_points.append((delivered_w, point.plant_heat_w, loss_w, pump_ratio, comp_ratio))
        if point.plant_heat_w > 0.0:
            prev = curve_by_heat_w.get(point.plant_heat_w)
            curve_by_heat_w[point.plant_heat_w] = (
                max(prev[0], point.source_pump_electricity_w)
                if prev
                else point.source_pump_electricity_w,
                max(prev[1], point.compressor_electricity_w)
                if prev
                else point.compressor_electricity_w,
            )

    basis: Literal["limit_proven", "lower_bound"] = (
        "limit_proven" if limit_proven else "lower_bound"
    )
    if not compliant_points:
        return DispatchGeothermalSpec(
            unit_id=f"{candidate_id}-geo",
            max_delivered_heat_w=0.0,
            max_plant_handover_heat_w=0.0,
            capacity_basis=basis,
            min_output_fraction=min_output_fraction,
            availability_fraction=availability,
        )

    best = max(compliant_points, key=lambda item: (item[0], item[1]))
    electricity_curve = tuple(
        DispatchElectricityPoint(
            plant_heat_w=heat_w,
            source_pump_electricity_w=pump_w,
            compressor_electricity_w=comp_w,
        )
        for heat_w, (pump_w, comp_w) in sorted(curve_by_heat_w.items())
    )
    return DispatchGeothermalSpec(
        unit_id=f"{candidate_id}-geo",
        max_delivered_heat_w=best[0],
        max_plant_handover_heat_w=best[1],
        capacity_basis=basis,
        min_output_fraction=min_output_fraction,
        availability_fraction=availability,
        route_loss_when_active_w=best[2],
        source_electricity_per_plant_heat_ratio=best[3],
        compressor_electricity_per_plant_heat_ratio=best[4],
        electricity_curve=electricity_curve,
    )


def build_dispatch_request(
    scenario: Scenario,
    operating: OperatingScenario,
    *,
    request_id: str,
    settings: DispatchSettings,
    geothermal: DispatchGeothermalSpec | None = None,
    additional_peak_heat_w: float = 0.0,
    series_override: Mapping[str, IntervalSeries] | None = None,
) -> DispatchRequest:
    """Assemble a ``DispatchRequest`` from a frozen scenario, time series and dispatch policy."""
    series_map = {item.series_id: item.series for item in scenario.time_series}
    if series_override:
        series_map.update(series_override)

    base = f"operating_scenarios[{operating.operating_scenario_id}]"
    missing: list[MissingInput] = []
    reader = InputReader()

    if operating.demand.series_id not in series_map:
        missing.append(
            MissingInput(
                field_path=f"{base}.demand",
                state="unknown",
                reason=f"time series {operating.demand.series_id!r} is not loaded",
                owner="operation specialist",
            )
        )
        raise NotEstablished(tuple(missing))
    demand_series = series_map[operating.demand.series_id]
    base_demand_w = _power_series_w(demand_series)
    peak_base_w = max(base_demand_w) if base_demand_w else 0.0
    if additional_peak_heat_w > 0.0 and peak_base_w > 0.0:
        scale = 1.0 + additional_peak_heat_w / peak_base_w
        demand_w: tuple[PositiveReal | float, ...] = tuple(v * scale for v in base_demand_w)
    else:
        demand_w = base_demand_w

    elec_prices: tuple[float, ...] = ()
    if operating.electricity_tariff is not None:
        ref = operating.electricity_tariff
        if ref.series_id not in series_map:
            missing.append(
                MissingInput(
                    field_path=f"{base}.electricity_tariff",
                    state="unknown",
                    reason=f"tariff series {ref.series_id!r} is not loaded",
                    owner="economics specialist",
                )
            )
        else:
            elec_series = series_map[ref.series_id]
            if elec_series.unit != "currency/J":
                raise ValueError(
                    f"electricity tariff series {ref.series_id!r} must use unit 'currency/J'"
                )
            elec_prices = tuple(float(v) for v in elec_series.values)
    elif _has_electricity_consumption(geothermal):
        missing.append(
            MissingInput(
                field_path=f"{base}.electricity_tariff",
                state="unknown",
                reason=(
                    "electricity tariff is required when pumps or compressors consume electricity"
                ),
                owner="economics specialist",
            )
        )

    units: list[DispatchUnit] = []
    for existing in operating.existing_sources:
        u_base = f"{base}.existing_sources[{existing.source_id}]"
        max_w = reader.get(existing.max_heat_w, f"{u_base}.max_heat_w")
        min_w = (
            reader.get(existing.min_output_w, f"{u_base}.min_output_w")
            if existing.min_output_w is not None
            else 0.0
        )
        eta = (
            reader.get(
                existing.thermal_efficiency_fraction, f"{u_base}.thermal_efficiency_fraction"
            )
            if existing.thermal_efficiency_fraction is not None
            else 1.0
        )
        var_om = (
            reader.get(existing.variable_om_currency_per_j, f"{u_base}.variable_om_currency_per_j")
            if existing.variable_om_currency_per_j is not None
            else 0.0
        )
        avail: tuple[float, ...] = ()
        if existing.availability is not None:
            if existing.availability.series_id not in series_map:
                missing.append(
                    MissingInput(
                        field_path=f"{u_base}.availability",
                        state="unknown",
                        reason=(
                            f"availability series {existing.availability.series_id!r} is not loaded"
                        ),
                        owner="operation specialist",
                    )
                )
            else:
                avail = tuple(float(v) for v in series_map[existing.availability.series_id].values)
        fuel_prices: tuple[float, ...] = ()
        if existing.fuel_tariff is not None:
            if existing.fuel_tariff.series_id not in series_map:
                missing.append(
                    MissingInput(
                        field_path=f"{u_base}.fuel_tariff",
                        state="unknown",
                        reason=(
                            f"fuel tariff series {existing.fuel_tariff.series_id!r} is not loaded"
                        ),
                        owner="economics specialist",
                    )
                )
            else:
                fuel_s = series_map[existing.fuel_tariff.series_id]
                if fuel_s.unit != "currency/J":
                    raise ValueError(
                        f"fuel tariff series {fuel_s.series_id!r} must use unit 'currency/J'"
                    )
                fuel_prices = tuple(float(v) for v in fuel_s.values)
        if max_w is not None and min_w is not None and eta is not None and var_om is not None:
            units.append(
                DispatchUnit(
                    unit_id=existing.source_id,
                    label=existing.label,
                    role=(
                        ExistingUnitRole.BASE_LOAD
                        if existing.role is ExistingSourceRole.BASE_LOAD
                        else ExistingUnitRole.PEAK_BACKUP
                    ),
                    priority=existing.priority,
                    max_heat_w=max_w,
                    min_output_w=min_w,
                    availability_fraction=avail,
                    thermal_efficiency_fraction=eta,
                    fuel_price_currency_per_j=fuel_prices,
                    variable_om_currency_per_j=var_om,
                )
            )

    missing.extend(reader.missing)
    if missing:
        raise NotEstablished(tuple(missing))

    return DispatchRequest(
        request_id=request_id,
        operating_scenario_id=operating.operating_scenario_id,
        starts_utc=demand_series.starts_utc,
        durations_s=demand_series.durations_s,
        demand_heat_w=demand_w,
        existing_units=tuple(units),
        geothermal=geothermal,
        geothermal_priority=settings.geothermal_priority,
        electricity_price_currency_per_j=elec_prices,
        allow_curtailment=settings.allow_curtailment,
    )
