"""Operating, economic and uncertainty records (specification 7.2)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import ClassVar, Literal

import numpy as np
from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    JsonDict,
    NonEmptyStr,
    NonNegativeReal,
    PositiveReal,
    Real,
    Record,
    VersionedRecord,
    register_migration,
)
from r3chain.domain.evidence import Evidence, Provenance
from r3chain.domain.records.common import require_unique, value_or_none
from r3chain.domain.timeseries import (
    DEFAULT_PRESENTATION_TIMEZONE,
    IntervalSeries,
    UtcDatetime,
    timezone,
)
from r3chain.domain.units import CANONICAL_UNITS

# --- operation ------------------------------------------------------------------------------


class SeriesRef(Record):
    """A time series inside a registered dataset."""

    series_id: Identifier
    dataset_id: Identifier


class TimeBasis(Record):
    start_utc: UtcDatetime
    end_utc: UtcDatetime
    interval_s: PositiveReal
    presentation_timezone: str = DEFAULT_PRESENTATION_TIMEZONE

    @model_validator(mode="after")
    def _is_consistent(self) -> TimeBasis:
        if self.end_utc <= self.start_utc:
            raise ValueError("the time basis must end after it starts")
        timezone(self.presentation_timezone)
        return self


class SourceAvailability(Record):
    source_id: Identifier
    availability: SeriesRef


class StoredSeries(Record):
    """One time series resolved from a registered dataset and frozen with the scenario."""

    series_id: Identifier
    dataset_id: Identifier
    series: IntervalSeries

    @model_validator(mode="after")
    def _matches_series_id(self) -> StoredSeries:
        if self.series.series_id != self.series_id:
            raise ValueError(
                f"stored series {self.series_id!r} wraps series {self.series.series_id!r}"
            )
        return self


class ExistingSourceRole(StrEnum):
    BASE_LOAD = "base_load"
    PEAK_BACKUP = "peak_backup"


class ExistingHeatSource(Record):
    """An existing heat plant in the system (waste heat base load or peak/backup boiler)."""

    source_id: Identifier
    label: NonEmptyStr
    role: ExistingSourceRole
    priority: int
    max_heat_w: Evidence[Real]
    min_output_w: Evidence[Real] | None = None
    thermal_efficiency_fraction: Evidence[Real] | None = None
    availability: SeriesRef | None = None
    fuel_tariff: SeriesRef | None = None
    variable_om_currency_per_j: Evidence[Real] | None = None

    @model_validator(mode="after")
    def _bounds_are_consistent(self) -> ExistingHeatSource:
        high = value_or_none(self.max_heat_w)
        low = value_or_none(self.min_output_w) if self.min_output_w is not None else None
        eta = (
            value_or_none(self.thermal_efficiency_fraction)
            if self.thermal_efficiency_fraction is not None
            else None
        )
        if high is not None and high < 0.0:
            raise ValueError("max_heat_w cannot be negative")
        if low is not None and low < 0.0:
            raise ValueError("min_output_w cannot be negative")
        if high is not None and low is not None and low > high:
            raise ValueError("min_output_w cannot exceed max_heat_w")
        if eta is not None and eta <= 0.0:
            raise ValueError("thermal_efficiency_fraction must be positive")
        return self


class OperatingScenario(VersionedRecord):
    """Demand, source availability, temperature requirements, tariffs and dispatch policy.

    Demand, tariffs and availability must cover the same intervals. That is checked against the
    actual series by ``validate_scenario``.
    """

    SCHEMA_NAME: ClassVar[str] = "operating_scenario"
    schema_version: Literal["0.2.0"] = "0.2.0"

    operating_scenario_id: Identifier
    demand: SeriesRef
    source_availability: tuple[SourceAvailability, ...] = ()
    temperature_curves: tuple[SeriesRef, ...] = ()
    tariffs: tuple[SeriesRef, ...] = ()
    dispatch_policy_id: Identifier
    time_basis: TimeBasis
    # Milestone M4 additions for chronological operation and existing competing sources.
    existing_sources: tuple[ExistingHeatSource, ...] = ()
    electricity_tariff: SeriesRef | None = None
    heat_revenue_tariff: SeriesRef | None = None
    geothermal_availability: SeriesRef | None = None
    geothermal_min_output_fraction: Evidence[Real] | None = None
    distribution_loss_fraction: Evidence[Real] | None = None

    @model_validator(mode="after")
    def _is_consistent(self) -> OperatingScenario:
        require_unique((item.source_id for item in self.source_availability), "source_id")
        require_unique((item.source_id for item in self.existing_sources), "existing source_id")
        min_frac = (
            value_or_none(self.geothermal_min_output_fraction)
            if self.geothermal_min_output_fraction is not None
            else None
        )
        if min_frac is not None and not 0.0 <= min_frac <= 1.0:
            raise ValueError("geothermal_min_output_fraction must lie in [0, 1]")
        dist_loss = (
            value_or_none(self.distribution_loss_fraction)
            if self.distribution_loss_fraction is not None
            else None
        )
        if dist_loss is not None and not 0.0 <= dist_loss < 1.0:
            raise ValueError("distribution_loss_fraction must lie in [0, 1)")
        return self

    def series_refs(self) -> tuple[SeriesRef, ...]:
        refs: list[SeriesRef] = [
            self.demand,
            *(item.availability for item in self.source_availability),
            *self.temperature_curves,
            *self.tariffs,
        ]
        for ref in (
            self.electricity_tariff,
            self.heat_revenue_tariff,
            self.geothermal_availability,
        ):
            if ref is not None:
                refs.append(ref)
        for existing in self.existing_sources:
            if existing.availability is not None:
                refs.append(existing.availability)
            if existing.fuel_tariff is not None:
                refs.append(existing.fuel_tariff)
        # Preserve order without duplicate (series_id, dataset_id) checks if a series is shared.
        seen: set[tuple[str, str]] = set()
        unique: list[SeriesRef] = []
        for ref in refs:
            key = (ref.series_id, ref.dataset_id)
            if key not in seen:
                seen.add(key)
                unique.append(ref)
        return tuple(unique)


# --- economics ------------------------------------------------------------------------------


class PriceConvention(StrEnum):
    REAL = "real"
    NOMINAL = "nominal"


class CostCategory(StrEnum):
    CAPEX = "capex"
    FIXED_OPEX = "fixed_opex"
    VARIABLE_COST = "variable_cost"
    REPLACEMENT = "replacement"
    DECOMMISSIONING = "decommissioning"
    RESIDUAL_VALUE = "residual_value"


class AmountBasis(StrEnum):
    TOTAL = "total"
    PER_YEAR = "per_year"
    PER_JOULE_HEAT = "per_joule_heat"
    PER_JOULE_ELECTRICITY = "per_joule_electricity"


class CostScope(Record):
    """Which candidates, assets or baseline a cost item belongs to."""

    alternative_ids: tuple[Identifier, ...] = ()
    site_ids: tuple[Identifier, ...] = ()
    plant_ids: tuple[Identifier, ...] = ()
    route_ids: tuple[Identifier, ...] = ()
    connection_ids: tuple[Identifier, ...] = ()
    candidate_ids: tuple[Identifier, ...] = ()
    applies_to_baseline: bool = False


class CostItem(Record):
    """One line of the cost ledger.

    ``asset_id`` and ``cost_owner`` exist so that a well, pump or exchanger already costed by
    the source model is not added again downstream.
    """

    item_id: Identifier
    asset_id: Identifier
    category: CostCategory
    cost_owner: NonEmptyStr
    location: NonEmptyStr
    included: bool
    amount: Evidence[Real] = Field(json_schema_extra={"unit": "currency"})
    amount_basis: AmountBasis
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_year: int
    price_convention: PriceConvention
    lifetime_years: Evidence[Real] | None = None
    applies_to: CostScope | None = None


class FundingRule(Record):
    """A dated, sourced subsidy or grant rule kept separate from the unsubsidised case."""

    rule_id: Identifier
    title: NonEmptyStr
    effective_date: date
    source_id: Identifier
    reviewed: bool = False
    reviewer: NonEmptyStr | None = None
    grant_fraction: NonNegativeReal = Field(le=1.0)
    applies_to_replacements: bool
    locations: tuple[NonEmptyStr, ...] = ()
    categories: tuple[CostCategory, ...] = ()

    @model_validator(mode="after")
    def _reviewed_names_reviewer(self) -> FundingRule:
        if self.reviewed and self.reviewer is None:
            raise ValueError("a reviewed funding rule must name its reviewer")
        return self


class EconomicScenario(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "economic_scenario"
    schema_version: Literal["0.2.0"] = "0.2.0"

    economic_scenario_id: Identifier
    price_base_year: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_convention: PriceConvention
    vat_included: bool
    discount_rate_per_year: Evidence[Real]
    horizon_years: Evidence[Real]
    cost_items: tuple[CostItem, ...] = ()
    funding_rules: tuple[FundingRule, ...] = ()
    carbon_monetised: bool = False

    @model_validator(mode="after")
    def _one_price_convention_and_one_owner(self) -> EconomicScenario:
        require_unique((item.item_id for item in self.cost_items), "cost item_id")
        require_unique((rule.rule_id for rule in self.funding_rules), "funding rule_id")
        rate = value_or_none(self.discount_rate_per_year)
        if rate is not None and rate <= -1.0:
            raise ValueError("discount_rate_per_year must be above -1")
        horizon = value_or_none(self.horizon_years)
        if horizon is not None and horizon <= 0.0:
            raise ValueError("horizon_years must be positive")
        owners: dict[tuple[str, CostCategory], str] = {}
        for item in self.cost_items:
            if item.currency != self.currency:
                raise ValueError(
                    f"cost item {item.item_id} is in {item.currency}, the scenario in "
                    f"{self.currency}. Mixed currencies are rejected"
                )
            if item.price_convention is not self.price_convention:
                raise ValueError(
                    f"cost item {item.item_id} mixes {item.price_convention.value} prices into a "
                    f"{self.price_convention.value} scenario"
                )
            if item.price_year != self.price_base_year:
                raise ValueError(
                    f"cost item {item.item_id} has price year {item.price_year}, the scenario "
                    f"base year is {self.price_base_year}. Convert it and record the method"
                )
            if not item.included:
                continue
            key = (item.asset_id, item.category)
            if key in owners:
                raise ValueError(
                    f"duplicate cost ownership: {item.category.value} of asset {item.asset_id} "
                    f"is included by both {owners[key]} and {item.item_id}"
                )
            owners[key] = item.item_id
        return self


# --- uncertainty ----------------------------------------------------------------------------


class UncertaintyKind(StrEnum):
    DISTRIBUTION = "distribution"
    SCENARIO_SET = "scenario_set"


class DistributionSpec(Record):
    family: NonEmptyStr
    parameters: dict[str, Real]


class Support(Record):
    lower: Real = Field(json_schema_extra={"unit": "unit of the variable"})
    upper: Real = Field(json_schema_extra={"unit": "unit of the variable"})

    @model_validator(mode="after")
    def _is_ordered(self) -> Support:
        if self.lower >= self.upper:
            raise ValueError("support needs lower < upper")
        return self


class UncertainVariable(Record):
    """One uncertain input.

    A range alone does not imply a probability distribution. A variable is either a scenario set
    without probabilities or a named distribution with its own evidence.
    """

    variable_id: Identifier
    field_path: NonEmptyStr
    unit: str
    kind: UncertaintyKind
    distribution: DistributionSpec | None = None
    scenario_values: tuple[Real, ...] = Field(
        default=(), json_schema_extra={"unit": "unit of the variable"}
    )
    support: Support
    evidence: Provenance
    correlation_group: Identifier | None = None

    @model_validator(mode="after")
    def _is_one_kind(self) -> UncertainVariable:
        if self.unit not in CANONICAL_UNITS:
            raise ValueError(f"unit {self.unit!r} is not a canonical unit")
        if self.kind is UncertaintyKind.DISTRIBUTION:
            if self.distribution is None or self.scenario_values:
                raise ValueError(
                    "a distribution variable needs a named distribution and no scenario values"
                )
        else:
            if self.distribution is not None or not self.scenario_values:
                raise ValueError(
                    "a scenario_set variable needs explicit scenario values and no distribution. "
                    "A range alone does not define a distribution"
                )
            outside = [
                value
                for value in self.scenario_values
                if not self.support.lower <= value <= self.support.upper
            ]
            if outside:
                raise ValueError(f"scenario values outside the support: {outside}")
        return self


class CorrelationPair(Record):
    """Pairwise Spearman rank correlation between two uncertain variables."""

    variable_a: Identifier
    variable_b: Identifier
    rank_correlation: Real = Field(ge=-1.0, le=1.0, json_schema_extra={"unit": "dimensionless"})

    @model_validator(mode="after")
    def _distinct_variables(self) -> CorrelationPair:
        if self.variable_a == self.variable_b:
            raise ValueError(f"correlation pair cannot link variable {self.variable_a!r} to itself")
        return self


class CorrelationGroupSpec(Record):
    """Joint dependence specification across correlated uncertain variables (section 14.3)."""

    group_id: Identifier
    method: Literal["gaussian_copula", "rank_correlation"] = "rank_correlation"
    pairs: tuple[CorrelationPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _is_positive_semidefinite(self) -> CorrelationGroupSpec:
        seen: set[tuple[str, str]] = set()
        variables: list[str] = []
        idx_map: dict[str, int] = {}
        for pair in self.pairs:
            a, b = sorted((pair.variable_a, pair.variable_b))
            if (a, b) in seen:
                raise ValueError(
                    f"duplicate correlation pair ({a!r}, {b!r}) in group {self.group_id!r}"
                )
            seen.add((a, b))
            for vid in (pair.variable_a, pair.variable_b):
                if vid not in idx_map:
                    idx_map[vid] = len(variables)
                    variables.append(vid)
        n = len(variables)
        corr = np.eye(n, dtype=float)
        for pair in self.pairs:
            i, j = idx_map[pair.variable_a], idx_map[pair.variable_b]
            corr[i, j] = pair.rank_correlation
            corr[j, i] = pair.rank_correlation
        min_eig = float(np.min(np.linalg.eigvalsh(corr)))
        if min_eig < -1e-8:
            raise ValueError(
                f"correlation matrix for group {self.group_id!r} must be positive semi-definite "
                f"(minimum eigenvalue {min_eig:.4g})"
            )
        return self


class SuccessDefinition(Record):
    """The event that counts as geological success (specification 14.2)."""

    event: NonEmptyStr
    criteria: tuple[NonEmptyStr, ...] = Field(min_length=1)
    information_time: NonEmptyStr
    conditional_on: NonEmptyStr | None = None


class UncertaintyModel(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "uncertainty_model"
    schema_version: Literal["0.2.0"] = "0.2.0"

    uncertainty_model_id: Identifier
    variables: tuple[UncertainVariable, ...] = ()
    correlation_groups: tuple[CorrelationGroupSpec, ...] = ()
    success_definition: Evidence[SuccessDefinition]
    success_probability_fraction: Evidence[Real] | None = None
    failure_drilling_cost_fraction: Evidence[Real] | None = None
    abandonment_cost_currency: Evidence[Real] | None = None

    @model_validator(mode="after")
    def _is_consistent(self) -> UncertaintyModel:
        require_unique((variable.variable_id for variable in self.variables), "variable_id")
        require_unique(
            (group.group_id for group in self.correlation_groups), "correlation group_id"
        )
        var_map = {variable.variable_id: variable for variable in self.variables}
        for group in self.correlation_groups:
            for pair in group.pairs:
                for vid in (pair.variable_a, pair.variable_b):
                    if vid not in var_map:
                        raise ValueError(
                            f"correlation group {group.group_id!r} references unknown variable "
                            f"{vid!r}"
                        )
                    var_group = var_map[vid].correlation_group
                    if var_group is not None and var_group != group.group_id:
                        raise ValueError(
                            f"variable {vid!r} declares correlation_group {var_group!r}, not "
                            f"{group.group_id!r}"
                        )
        for label, ev in (
            ("success_probability_fraction", self.success_probability_fraction),
            ("failure_drilling_cost_fraction", self.failure_drilling_cost_fraction),
        ):
            val = value_or_none(ev) if ev is not None else None
            if val is not None and not 0.0 <= val <= 1.0:
                raise ValueError(f"{label} must lie in [0, 1]")
        aband = (
            value_or_none(self.abandonment_cost_currency)
            if self.abandonment_cost_currency is not None
            else None
        )
        if aband is not None and aband < 0.0:
            raise ValueError("abandonment_cost_currency cannot be negative")
        return self


@register_migration("operating_scenario", "0.1.0", "0.2.0")
def _operating_scenario_gains_sources_and_tariffs(data: JsonDict) -> JsonDict:
    """0.2.0 adds existing heat sources and explicit tariff and availability references."""
    return data


@register_migration("economic_scenario", "0.1.0", "0.2.0")
def _economic_scenario_gains_funding_and_scopes(data: JsonDict) -> JsonDict:
    """0.2.0 adds optional cost item scopes, funding rules and carbon_monetised flag."""
    return data


@register_migration("uncertainty_model", "0.1.0", "0.2.0")
def _uncertainty_model_gains_correlations_and_pre_drilling_params(data: JsonDict) -> JsonDict:
    """0.2.0 adds correlation groups and optional pre-drilling success/failure parameters."""
    return data
