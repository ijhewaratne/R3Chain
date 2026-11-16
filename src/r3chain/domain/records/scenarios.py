"""Operating, economic and uncertainty records (specification 7.2)."""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    PositiveReal,
    Real,
    Record,
    VersionedRecord,
)
from r3chain.domain.evidence import Evidence, Provenance
from r3chain.domain.records.common import require_unique, value_or_none
from r3chain.domain.timeseries import DEFAULT_PRESENTATION_TIMEZONE, UtcDatetime, timezone
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


class OperatingScenario(VersionedRecord):
    """Demand, source availability, temperature requirements, tariffs and dispatch policy.

    Demand, tariffs and availability must cover the same intervals. That is checked against the
    actual series by ``validate_scenario``.
    """

    SCHEMA_NAME: ClassVar[str] = "operating_scenario"
    schema_version: Literal["0.1.0"] = "0.1.0"

    operating_scenario_id: Identifier
    demand: SeriesRef
    source_availability: tuple[SourceAvailability, ...] = ()
    temperature_curves: tuple[SeriesRef, ...] = ()
    tariffs: tuple[SeriesRef, ...] = ()
    dispatch_policy_id: Identifier
    time_basis: TimeBasis

    @model_validator(mode="after")
    def _is_consistent(self) -> OperatingScenario:
        require_unique((item.source_id for item in self.source_availability), "source_id")
        return self

    def series_refs(self) -> tuple[SeriesRef, ...]:
        return (
            self.demand,
            *(item.availability for item in self.source_availability),
            *self.temperature_curves,
            *self.tariffs,
        )


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


class EconomicScenario(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "economic_scenario"
    schema_version: Literal["0.1.0"] = "0.1.0"

    economic_scenario_id: Identifier
    price_base_year: int
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_convention: PriceConvention
    vat_included: bool
    discount_rate_per_year: Evidence[Real]
    horizon_years: Evidence[Real]
    cost_items: tuple[CostItem, ...] = ()

    @model_validator(mode="after")
    def _one_price_convention_and_one_owner(self) -> EconomicScenario:
        require_unique((item.item_id for item in self.cost_items), "cost item_id")
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


class SuccessDefinition(Record):
    """The event that counts as geological success (specification 14.2)."""

    event: NonEmptyStr
    criteria: tuple[NonEmptyStr, ...] = Field(min_length=1)
    information_time: NonEmptyStr
    conditional_on: NonEmptyStr | None = None


class UncertaintyModel(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "uncertainty_model"
    schema_version: Literal["0.1.0"] = "0.1.0"

    uncertainty_model_id: Identifier
    variables: tuple[UncertainVariable, ...] = ()
    success_definition: Evidence[SuccessDefinition]

    @model_validator(mode="after")
    def _is_consistent(self) -> UncertaintyModel:
        require_unique((variable.variable_id for variable in self.variables), "variable_id")
        return self
