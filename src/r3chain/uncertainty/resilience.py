"""Deterministic resilience stress cases reported without probabilities (plan v2 V02)."""

from __future__ import annotations

import math
from collections.abc import Sequence

from r3chain.domain.base import NonEmptyStr, NonNegativeReal, Record
from r3chain.domain.status import AdapterState
from r3chain.ports.dispatch import (
    DispatchRequest,
    DispatchStrategy,
    DispatchUnit,
    ExistingUnitRole,
)


class ResilienceOutcome(Record):
    """Unserved heat, hours with deficit, and worst interval under one scenario outage."""

    case_id: NonEmptyStr
    title: NonEmptyStr
    affected: bool
    unserved_heat_j: NonNegativeReal
    unserved_hours: NonNegativeReal
    largest_deficit_w: NonNegativeReal
    note: str = ""


_CASE_TITLES = {
    "source_outage": "Loss of geothermal source",
    "backup_loss": "Loss of peak and backup boilers",
    "base_load_loss": "Loss of existing base load units",
    "heat_pump_electricity_loss": "Loss of electricity supply to heat pump plant",
}


def _zero_availability_unit(unit: DispatchUnit, n: int) -> DispatchUnit:
    return unit.model_copy(update={"availability_fraction": (0.0,) * n})


def _metrics_from_unserved(
    durations_s: tuple[float, ...],
    unserved_w: tuple[float, ...],
) -> tuple[float, float, float]:
    unserved_j = math.fsum(u * dt for u, dt in zip(unserved_w, durations_s, strict=True))
    unserved_hours = math.fsum(
        dt / 3600.0 for u, dt in zip(unserved_w, durations_s, strict=True) if u > 1.0
    )
    largest_w = max(unserved_w, default=0.0)
    return unserved_j, unserved_hours, largest_w


def evaluate_resilience_cases(
    request: DispatchRequest,
    *,
    strategy: DispatchStrategy,
    cases: Sequence[str],
    has_heat_pump: bool,
) -> tuple[ResilienceOutcome, ...]:
    """Evaluate outage scenario cases via the dispatch strategy (never as probabilities)."""
    n = len(request.durations_s)
    zero_geo = (
        request.geothermal.model_copy(update={"availability_fraction": (0.0,) * n})
        if request.geothermal is not None
        else None
    )
    outcomes: list[ResilienceOutcome] = []
    for case_id in cases:
        if case_id == "source_outage":
            stressed = request.model_copy(
                update={
                    "request_id": f"{request.request_id}-{case_id}",
                    "geothermal": zero_geo,
                }
            )
            affected = request.geothermal is not None
            note = "geothermal source unavailable across full evaluation period"
        elif case_id == "backup_loss":
            stressed_units = tuple(
                _zero_availability_unit(u, n) if u.role is ExistingUnitRole.PEAK_BACKUP else u
                for u in request.existing_units
            )
            stressed = request.model_copy(
                update={
                    "request_id": f"{request.request_id}-{case_id}",
                    "existing_units": stressed_units,
                }
            )
            affected = any(u.role is ExistingUnitRole.PEAK_BACKUP for u in request.existing_units)
            note = "all backup/peaking units offline"
        elif case_id == "base_load_loss":
            stressed_units = tuple(
                _zero_availability_unit(u, n)
                if (
                    u.role is ExistingUnitRole.BASE_LOAD
                    and u.priority < request.geothermal_priority
                )
                else u
                for u in request.existing_units
            )
            stressed = request.model_copy(
                update={
                    "request_id": f"{request.request_id}-{case_id}",
                    "existing_units": stressed_units,
                }
            )
            affected = any(
                u.role is ExistingUnitRole.BASE_LOAD and u.priority < request.geothermal_priority
                for u in request.existing_units
            )
            note = "existing base load units offline"
        elif case_id == "heat_pump_electricity_loss":
            if has_heat_pump:
                stressed = request.model_copy(
                    update={
                        "request_id": f"{request.request_id}-{case_id}",
                        "geothermal": zero_geo,
                    }
                )
                affected = True
                note = "heat pump electricity supply lost; heat pump offline"
            else:
                stressed = request.model_copy(
                    update={"request_id": f"{request.request_id}-{case_id}"}
                )
                affected = False
                note = "candidate has no heat pump compressor (unaffected by HP electricity outage)"
        else:
            raise ValueError(f"unsupported resilience case {case_id!r}")

        res = strategy.dispatch(stressed)
        if res.state is not AdapterState.COMPLETED:
            raise ValueError(f"resilience dispatch {case_id!r} failed: {res.reason}")
        unserved_j, hours, peak_deficit_w = _metrics_from_unserved(
            request.durations_s, res.unserved_w
        )
        outcomes.append(
            ResilienceOutcome(
                case_id=case_id,
                title=_CASE_TITLES.get(case_id, case_id),
                affected=affected,
                unserved_heat_j=unserved_j,
                unserved_hours=hours,
                largest_deficit_w=peak_deficit_w,
                note=note,
            )
        )
    return tuple(outcomes)
