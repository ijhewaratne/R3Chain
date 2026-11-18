"""Interval reduction with mandatory error verification against the full annual run.

Implementation plan M4: any interval reduction is accepted only after its error is measured
against the full chronological run.
"""

from __future__ import annotations

from r3chain.domain.base import NonNegativeReal, Record
from r3chain.domain.status import AdapterState
from r3chain.ports.dispatch import (
    DispatchGeothermalSpec,
    DispatchRequest,
    DispatchResult,
    DispatchStrategy,
    DispatchTotals,
    DispatchUnit,
)


class ReductionAssessment(Record):
    accepted: bool
    method: str
    full_intervals_count: int
    reduced_intervals_count: int
    max_relative_error_fraction: NonNegativeReal
    tolerance_fraction: NonNegativeReal
    metric_errors_fraction: dict[str, NonNegativeReal]
    reason: str = ""


def _slice_series(series: tuple[float, ...], indices: list[int]) -> tuple[float, ...]:
    if not series:
        return ()
    return tuple(series[idx] for idx in indices)


def build_reduced_request(request: DispatchRequest, stride: int) -> DispatchRequest:
    """Select every ``stride``-th interval and scale durations to conserve total time."""
    if stride <= 1:
        return request
    count = len(request.starts_utc)
    indices = list(range(0, count, stride))
    selected_s = sum(request.durations_s[idx] for idx in indices)
    total_s = sum(request.durations_s)
    scale = total_s / selected_s if selected_s > 0.0 else 1.0

    units = tuple(
        DispatchUnit(
            unit_id=u.unit_id,
            label=u.label,
            role=u.role,
            priority=u.priority,
            max_heat_w=u.max_heat_w,
            min_output_w=u.min_output_w,
            availability_fraction=_slice_series(u.availability_fraction, indices),
            thermal_efficiency_fraction=u.thermal_efficiency_fraction,
            fuel_price_currency_per_j=_slice_series(u.fuel_price_currency_per_j, indices),
            variable_om_currency_per_j=u.variable_om_currency_per_j,
        )
        for u in request.existing_units
    )
    geo = request.geothermal
    reduced_geo = (
        DispatchGeothermalSpec(
            unit_id=geo.unit_id,
            max_delivered_heat_w=geo.max_delivered_heat_w,
            max_plant_handover_heat_w=geo.max_plant_handover_heat_w,
            capacity_basis=geo.capacity_basis,
            min_output_fraction=geo.min_output_fraction,
            availability_fraction=_slice_series(geo.availability_fraction, indices),
            route_loss_when_active_w=geo.route_loss_when_active_w,
            source_electricity_per_plant_heat_ratio=geo.source_electricity_per_plant_heat_ratio,
            compressor_electricity_per_plant_heat_ratio=(
                geo.compressor_electricity_per_plant_heat_ratio
            ),
            electricity_curve=geo.electricity_curve,
            capacity_series_w=_slice_series(geo.capacity_series_w, indices),
            plant_loss_series_w=_slice_series(geo.plant_loss_series_w, indices),
        )
        if geo is not None
        else None
    )
    return DispatchRequest(
        request_id=f"{request.request_id}-reduced-{stride}",
        operating_scenario_id=request.operating_scenario_id,
        starts_utc=tuple(request.starts_utc[idx] for idx in indices),
        durations_s=tuple(request.durations_s[idx] * scale for idx in indices),
        demand_heat_w=tuple(request.demand_heat_w[idx] for idx in indices),
        existing_units=units,
        geothermal=reduced_geo,
        geothermal_priority=request.geothermal_priority,
        electricity_price_currency_per_j=_slice_series(
            request.electricity_price_currency_per_j, indices
        ),
        allow_curtailment=request.allow_curtailment,
        requires_storage=request.requires_storage,
    )


def _metric_rel_error(full: DispatchTotals, reduced: DispatchTotals, attr: str) -> float:
    ref = float(getattr(full, attr))
    approx = float(getattr(reduced, attr))
    denom = max(abs(ref), abs(approx), 1.0)
    if ref == 0.0 and approx == 0.0:
        return 0.0
    if ref == 0.0:
        return abs(approx - ref) / max(full.demand_heat_j, 1.0)
    return abs(approx - ref) / denom


def measure_reduction_error(
    request: DispatchRequest,
    strategy: DispatchStrategy,
    *,
    stride: int,
    max_error_fraction: float,
    return_full: bool = False,
) -> tuple[DispatchResult, ReductionAssessment]:
    """Compare reduced chronological intervals against the full run before accepting."""
    full_result = strategy.dispatch(request)
    if full_result.state is not AdapterState.COMPLETED or full_result.totals is None:
        assessment = ReductionAssessment(
            accepted=False,
            method=f"uniform_stride_{stride}",
            full_intervals_count=len(request.starts_utc),
            reduced_intervals_count=len(request.starts_utc),
            max_relative_error_fraction=0.0,
            tolerance_fraction=max_error_fraction,
            metric_errors_fraction={},
            reason=f"full run ended as {full_result.state.value}: {full_result.reason}",
        )
        return full_result, assessment

    reduced_request = build_reduced_request(request, stride)
    reduced_result = strategy.dispatch(reduced_request)
    if reduced_result.state is not AdapterState.COMPLETED or reduced_result.totals is None:
        assessment = ReductionAssessment(
            accepted=False,
            method=f"uniform_stride_{stride}",
            full_intervals_count=len(request.starts_utc),
            reduced_intervals_count=len(reduced_request.starts_utc),
            max_relative_error_fraction=1.0,
            tolerance_fraction=max_error_fraction,
            metric_errors_fraction={},
            reason=f"reduced run ended as {reduced_result.state.value}",
        )
        return full_result, assessment

    metrics = (
        "geothermal_delivered_heat_j",
        "geothermal_curtailed_heat_j",
        "backup_heat_j",
        "base_load_heat_j",
        "unserved_heat_j",
        "energy_cost_currency",
    )
    errors = {
        name: _metric_rel_error(full_result.totals, reduced_result.totals, name) for name in metrics
    }
    worst = max(errors.values(), default=0.0)
    accepted = worst <= max_error_fraction
    worst_name = max(errors, key=lambda name: errors[name], default="")
    reason = (
        ""
        if accepted
        else (
            f"reduction error {worst:.3%} on {worst_name} exceeds policy limit "
            f"{max_error_fraction:.3%}. Falling back to the full chronological run"
        )
    )
    assessment = ReductionAssessment(
        accepted=accepted,
        method=f"uniform_stride_{stride}",
        full_intervals_count=len(request.starts_utc),
        reduced_intervals_count=len(reduced_request.starts_utc),
        max_relative_error_fraction=worst,
        tolerance_fraction=max_error_fraction,
        metric_errors_fraction=errors,
        reason=reason,
    )
    chosen = full_result if return_full or not accepted else reduced_result
    return chosen, assessment
