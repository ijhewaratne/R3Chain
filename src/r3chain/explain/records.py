"""Explanation records and typed claims (Plan v2 V05, specification section 15.4).

Every explanation is structured data before it becomes prose: typed claims pointing at stored
results, checks, policies, sample accounting, or open assumptions.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal

from pydantic import Field

from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeReal,
    Real,
    Record,
    VersionedRecord,
)
from r3chain.domain.status import EvaluationStatus, SiteEvidenceStatus, TechnicalStatus


class ClaimType(StrEnum):
    VALUE = "value"
    STATUS = "status"
    CAUSE_OF_FAILURE = "cause_of_failure"
    COMPARISON = "comparison"
    OPEN_ASSUMPTION = "open_assumption"


class ValueClaim(Record):
    """A numerical claim tied to one stored KPI, check value, or scenario input."""

    claim_id: Identifier
    claim_type: Literal["value"] = "value"
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr
    synthetic: bool
    result_id: Identifier
    value_kind: Literal["kpi", "check_observed", "check_limit", "input"]
    kpi_id: Identifier | None = None
    check_id: Identifier | None = None
    field_path: str | None = None
    boundary: str | None = None
    unit: NonEmptyStr
    value: Real = Field(json_schema_extra={"unit": "see claim.unit"})
    rounding_tolerance: NonNegativeReal = Field(
        default=1e-9, json_schema_extra={"unit": "see claim.unit"}
    )
    derived_from: tuple[str, ...] = ()
    sample_count: int | None = None
    unresolved_samples: int | None = None
    source_refs: tuple[str, ...] = ()


class StatusClaim(Record):
    """A claim about technical, evaluation, site evidence, or eligibility status."""

    claim_id: Identifier
    claim_type: Literal["status"] = "status"
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr
    synthetic: bool
    result_id: Identifier
    technical_status: TechnicalStatus
    evaluation_status: EvaluationStatus
    site_evidence_status: SiteEvidenceStatus | None = None
    eligible: bool
    conditional: bool = False
    asserted_feasible: bool = False
    asserted_infeasible: bool = False
    all_checks_passed: bool = False
    source_refs: tuple[str, ...] = ()


class CauseOfFailureClaim(Record):
    """A claim explaining why an option or candidate failed or was rejected (spec 15.4)."""

    claim_id: Identifier
    claim_type: Literal["cause_of_failure"] = "cause_of_failure"
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr
    synthetic: bool
    result_id: Identifier | None = None
    stage: NonEmptyStr
    check_id: Identifier
    description: str = ""
    observed: Real | str | None = Field(default=None, json_schema_extra={"unit": "see claim.unit"})
    limit: Real | str | None = Field(default=None, json_schema_extra={"unit": "see claim.unit"})
    margin: Real | None = Field(default=None, json_schema_extra={"unit": "see claim.unit"})
    unit: str | None = None
    threshold_origin: str | None = None
    interval_or_sample: NonEmptyStr = "deterministic"
    upstream_evidence: tuple[str, ...] = ()
    rounding_tolerance: NonNegativeReal = Field(
        default=1e-9, json_schema_extra={"unit": "see claim.unit"}
    )
    source_refs: tuple[str, ...] = ()


class ComparisonEntry(Record):
    """One candidate row inside a comparison claim."""

    candidate_id: Identifier
    result_id: Identifier
    label: str = ""
    eligible: bool
    conditional: bool = False
    value: Real | None = Field(default=None, json_schema_extra={"unit": "see claim.unit"})
    first_rank_count: int | None = None
    denominator: int | None = None


class ComparisonClaim(Record):
    """A cross-candidate ranking or uncertainty preference claim."""

    claim_id: Identifier
    claim_type: Literal["comparison"] = "comparison"
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr = "study"
    synthetic: bool
    result_ids: tuple[Identifier, ...] = Field(min_length=1)
    candidate_ids: tuple[Identifier, ...] = Field(min_length=1)
    metric_id: Identifier
    boundary: NonEmptyStr
    unit: NonEmptyStr
    ranking_policy_id: Identifier
    preference_outcome: NonEmptyStr
    winner_id: Identifier | None = None
    ranked_candidate_ids: tuple[Identifier, ...] = ()
    entries: tuple[ComparisonEntry, ...] = ()
    sample_count: int | None = None
    unresolved_samples: int | None = None
    rounding_tolerance: NonNegativeReal = Field(
        default=1e-9, json_schema_extra={"unit": "see claim.unit"}
    )
    source_refs: tuple[str, ...] = ()


class OpenAssumptionClaim(Record):
    """A claim exposing an unknown, conflicting, or conditional assumption (W24, P3)."""

    claim_id: Identifier
    claim_type: Literal["open_assumption"] = "open_assumption"
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr
    synthetic: bool
    result_id: Identifier | None = None
    assumption_id: str | None = None
    field_path: NonEmptyStr
    state: Literal["unknown", "conflict", "conditional"]
    owner: str | None = None
    reason: str = ""
    affects_subjects: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()


ExplanationClaim = Annotated[
    ValueClaim | StatusClaim | CauseOfFailureClaim | ComparisonClaim | OpenAssumptionClaim,
    Field(discriminator="claim_type"),
]


class ClaimCheckOutcome(Record):
    """Deterministic verification outcome for one typed claim (V06)."""

    claim_id: Identifier
    claim_type: ClaimType
    accepted: bool
    reason: str = ""
    error_codes: tuple[str, ...] = ()


class ExplanationRecord(VersionedRecord):
    """Structured explanation for a result, rejection, or comparison (V05)."""

    SCHEMA_NAME: ClassVar[str] = "explanation_record"
    schema_version: Literal["0.1.0"] = "0.1.0"

    explanation_id: Identifier
    scope: Literal["result", "rejection", "comparison", "study"]
    subject: NonEmptyStr
    run_id: Identifier
    scenario_revision: Identifier
    result_id: Identifier | None = None
    synthetic: bool
    claims: tuple[ExplanationClaim, ...]
    checks: tuple[ClaimCheckOutcome, ...] = ()
    all_claims_accepted: bool = True
