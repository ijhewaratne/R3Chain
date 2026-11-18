"""Port interface for chronological annual heat dispatch (extension point X8).

Specification section 12.2: for each interval, useful heat is allocated across existing base
load sources, geothermal production (including heat pump contribution when present), backup
sources, and any declared storage, recording curtailed geothermal output and unserved demand.

    sum_g(Q_g) + Q_discharge - Q_charge - Q_loss = Q_demand - Q_unserved

Notice what ``DispatchResult`` reports:

* ``geothermal_delivered_heat_j`` is only the useful geothermal heat actually dispatched and
  delivered, never available capacity multiplied by hours.
* ``geothermal_plant_handover_heat_j`` is the geothermal heat at the plant boundary before route
  transmission losses, so plant-boundary and delivered-boundary costs use their matching heat.
"""

from __future__ import annotations

import math
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
from r3chain.domain.records.common import require_unique
from r3chain.domain.status import AdapterState
from r3chain.domain.timeseries import UtcDatetime


class DispatchCapabilities(Record):
    strategy: NonEmptyStr
    strategy_version: NonEmptyStr
    supports_existing_base_load: bool = True
    supports_backup_sources: bool = True
    supports_min_output_and_maintenance: bool = True
    supports_storage: bool = False
    synthetic: bool = False


class ExistingUnitRole(StrEnum):
    BASE_LOAD = "base_load"
    PEAK_BACKUP = "peak_backup"


class DispatchUnit(Record):
    """One existing heat unit in the merit order (waste heat base load or peak/backup boiler)."""

    unit_id: Identifier
    label: NonEmptyStr
    role: ExistingUnitRole
    priority: int
    max_heat_w: NonNegativeReal
    min_output_w: NonNegativeReal = 0.0
    availability_fraction: tuple[Real, ...] = ()
    thermal_efficiency_fraction: PositiveReal = 1.0
    fuel_price_currency_per_j: tuple[Real, ...] = ()
    variable_om_currency_per_j: NonNegativeReal = 0.0

    @model_validator(mode="after")
    def _is_consistent(self) -> DispatchUnit:
        if self.min_output_w > self.max_heat_w:
            raise ValueError(f"unit {self.unit_id}: min_output_w cannot exceed max_heat_w")
        for fraction in self.availability_fraction:
            if not 0.0 <= fraction <= 1.0:
                raise ValueError(f"unit {self.unit_id}: availability_fraction must lie in [0, 1]")
        return self


class DispatchElectricityPoint(Record):
    """Measured coupled plant handover heat and auxiliary electricity demand."""

    plant_heat_w: NonNegativeReal
    source_pump_electricity_w: NonNegativeReal = 0.0
    compressor_electricity_w: NonNegativeReal = 0.0


class DispatchGeothermalSpec(Record):
    """Geothermal plant and route supply envelope exposed to chronological dispatch."""

    unit_id: Identifier
    max_delivered_heat_w: NonNegativeReal
    max_plant_handover_heat_w: NonNegativeReal
    capacity_basis: Literal["limit_proven", "lower_bound"] = "lower_bound"
    min_output_fraction: NonNegativeReal = Field(default=0.0, le=1.0)
    availability_fraction: tuple[Real, ...] = ()
    route_loss_when_active_w: NonNegativeReal = 0.0
    source_electricity_per_plant_heat_ratio: NonNegativeReal = 0.0
    compressor_electricity_per_plant_heat_ratio: NonNegativeReal = 0.0
    electricity_curve: tuple[DispatchElectricityPoint, ...] = ()
    capacity_series_w: tuple[NonNegativeReal, ...] = ()
    plant_loss_series_w: tuple[NonNegativeReal, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> DispatchGeothermalSpec:
        for fraction in self.availability_fraction:
            if not 0.0 <= fraction <= 1.0:
                raise ValueError("geothermal availability_fraction must lie in [0, 1]")
        if self.plant_loss_series_w and len(self.plant_loss_series_w) != len(
            self.capacity_series_w
        ):
            raise ValueError("plant_loss_series_w must have the same length as capacity_series_w")
        for idx in range(1, len(self.electricity_curve)):
            prev_w = self.electricity_curve[idx - 1].plant_heat_w
            if self.electricity_curve[idx].plant_heat_w <= prev_w:
                raise ValueError(
                    "electricity_curve points must have strictly increasing plant_heat_w"
                )
        return self


class DispatchRequest(VersionedRecord):
    """Interval-resolved demand, sources and tariffs for one dispatch calculation."""

    SCHEMA_NAME: ClassVar[str] = "dispatch_request"
    schema_version: Literal["0.1.0"] = "0.1.0"

    request_id: Identifier
    operating_scenario_id: Identifier
    starts_utc: tuple[UtcDatetime, ...] = Field(min_length=1)
    durations_s: tuple[PositiveReal, ...] = Field(min_length=1)
    demand_heat_w: tuple[NonNegativeReal, ...] = Field(min_length=1)
    existing_units: tuple[DispatchUnit, ...] = ()
    geothermal: DispatchGeothermalSpec | None = None
    geothermal_priority: int = 50
    electricity_price_currency_per_j: tuple[Real, ...] = ()
    allow_curtailment: bool = True
    requires_storage: bool = False

    @model_validator(mode="after")
    def _series_align(self) -> DispatchRequest:
        count = len(self.starts_utc)
        if len(self.durations_s) != count or len(self.demand_heat_w) != count:
            raise ValueError("starts_utc, durations_s and demand_heat_w must have equal length")
        require_unique((unit.unit_id for unit in self.existing_units), "existing unit_id")
        if self.electricity_price_currency_per_j and (
            len(self.electricity_price_currency_per_j) != count
        ):
            raise ValueError("electricity_price_currency_per_j must match interval count")
        for unit in self.existing_units:
            for attr in ("availability_fraction", "fuel_price_currency_per_j"):
                series = getattr(unit, attr)
                if series and len(series) != count:
                    raise ValueError(f"unit {unit.unit_id}.{attr} must match interval count")
        if self.geothermal is not None:
            for attr in ("availability_fraction", "capacity_series_w", "plant_loss_series_w"):
                series = getattr(self.geothermal, attr)
                if series and len(series) != count:
                    raise ValueError(f"geothermal.{attr} must match interval count")
        return self


class DispatchTotals(Record):
    """Annual integrated quantities of one completed dispatch calculation."""

    total_duration_s: PositiveReal
    demand_heat_j: NonNegativeReal
    delivered_heat_j: NonNegativeReal
    geothermal_available_heat_j: NonNegativeReal
    geothermal_delivered_heat_j: NonNegativeReal
    geothermal_plant_handover_heat_j: NonNegativeReal
    geothermal_curtailed_heat_j: NonNegativeReal
    route_heat_loss_j: NonNegativeReal
    base_load_heat_j: NonNegativeReal
    backup_heat_j: NonNegativeReal
    unserved_heat_j: NonNegativeReal
    source_pump_electricity_j: NonNegativeReal
    compressor_electricity_j: NonNegativeReal
    operating_time_s: NonNegativeReal
    equivalent_full_load_s: NonNegativeReal
    reference_capacity_w: NonNegativeReal
    utilisation_fraction: NonNegativeReal
    geothermal_electricity_cost_currency: Real
    backup_fuel_and_om_cost_currency: Real
    base_load_fuel_and_om_cost_currency: Real
    energy_cost_currency: Real
    peak_backup_heat_w: NonNegativeReal
    capacity_basis: Literal["limit_proven", "lower_bound"] = "lower_bound"
    by_unit_heat_j: dict[str, NonNegativeReal] = Field(default_factory=dict)


class DispatchResult(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "dispatch_result"
    schema_version: Literal["0.1.0"] = "0.1.0"

    strategy: NonEmptyStr
    strategy_version: NonEmptyStr
    state: AdapterState
    reason: str = ""
    synthetic: bool = False
    totals: DispatchTotals | None = None
    geothermal_w: tuple[NonNegativeReal, ...] = ()
    geothermal_plant_w: tuple[NonNegativeReal, ...] = ()
    curtailed_w: tuple[NonNegativeReal, ...] = ()
    unserved_w: tuple[NonNegativeReal, ...] = ()
    by_unit_w: dict[str, tuple[NonNegativeReal, ...]] = Field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    not_modelled: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _completed_carries_balanced_totals(self) -> DispatchResult:
        if self.state is AdapterState.COMPLETED and self.totals is None:
            raise ValueError("a completed dispatch result must include totals")
        if self.totals is not None:
            supplied = (
                self.totals.geothermal_delivered_heat_j
                + self.totals.base_load_heat_j
                + self.totals.backup_heat_j
            )
            needed = self.totals.demand_heat_j - self.totals.unserved_heat_j
            scale = max(self.totals.demand_heat_j, 1.0)
            if not math.isclose(supplied, needed, rel_tol=1e-6, abs_tol=1e-3 * scale):
                raise ValueError(
                    f"dispatch balance failed: supplied {supplied} J vs served demand {needed} J"
                )
        return self


class DispatchStrategy(Protocol):
    def capabilities(self) -> DispatchCapabilities: ...

    def dispatch(self, request: DispatchRequest) -> DispatchResult: ...
