"""Assumption, policy, run and result records (specification 7.2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    JsonDict,
    NonEmptyStr,
    NonNegativeReal,
    Real,
    Record,
    VersionedRecord,
    register_migration,
)
from r3chain.domain.evidence import MissingInput, ProvenanceType
from r3chain.domain.hashing import content_hash
from r3chain.domain.status import (
    EvaluationStatus,
    RankingEligibility,
    SiteEvidenceStatus,
    StageStatus,
    TechnicalStatus,
)
from r3chain.domain.timeseries import UtcDatetime
from r3chain.domain.units import require_unit_suffix

# --- assumptions ----------------------------------------------------------------------------


class AssumptionStatus(StrEnum):
    ACTIVE = "active"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class Assumption(VersionedRecord):
    """One row of the assumption register.

    The register is derived from the evidence stored on the scenario fields. It is a view of the
    scenario, so a value and its provenance cannot drift apart.
    """

    SCHEMA_NAME: ClassVar[str] = "assumption"
    schema_version: Literal["0.1.0"] = "0.1.0"

    assumption_id: str
    field_path: str
    status: AssumptionStatus
    value: Any = None
    unit: str | None = None
    provenance_type: ProvenanceType | None = None
    source_id: str | None = None
    locator: str | None = None
    author: str | None = None
    reviewer: str | None = None
    method: str | None = None
    note: str = ""
    owner: str | None = None


# --- policies -------------------------------------------------------------------------------


class PolicyKind(StrEnum):
    SCREENING = "screening"
    NUMERICAL = "numerical"
    DISPATCH = "dispatch"
    RANKING = "ranking"
    METRIC = "metric"
    UNCERTAINTY = "uncertainty"
    CANDIDATE = "candidate"
    SOURCE = "source"
    ROUTE = "route"


class ReviewStatus(StrEnum):
    UNREVIEWED = "unreviewed"
    REVIEWED = "reviewed"


class Policy(VersionedRecord):
    """A named, versioned set of thresholds or rules.

    Thresholds live here and never in code, so a new limit is a new policy version.
    """

    SCHEMA_NAME: ClassVar[str] = "policy"
    schema_version: Literal["0.3.0"] = "0.3.0"

    policy_id: Identifier
    kind: PolicyKind
    version: NonEmptyStr
    title: NonEmptyStr
    origin: NonEmptyStr
    status: ReviewStatus
    reviewer: NonEmptyStr | None = None
    parameters: dict[str, Real | bool | str]

    @model_validator(mode="after")
    def _is_consistent(self) -> Policy:
        if self.status is ReviewStatus.REVIEWED and self.reviewer is None:
            raise ValueError("a reviewed policy must name its reviewer")
        for name, value in self.parameters.items():
            if isinstance(value, float) and not isinstance(value, bool):
                require_unit_suffix(name)
        return self


class PolicyRef(Record):
    policy_id: Identifier
    version: NonEmptyStr
    policy_hash: str


# --- runs -----------------------------------------------------------------------------------


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class StageOutput(Record):
    """The structured output of one pipeline stage."""

    stage: Identifier
    stage_version: NonEmptyStr
    status: StageStatus
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    missing_inputs: tuple[MissingInput, ...] = ()
    diagnostics: tuple[str, ...] = ()
    # True when the output was produced by a test engine or from synthetic values.
    synthetic: bool = False


class StageRecord(Record):
    """How one stage was executed in one run: computed, or validly reused from a cache."""

    stage: Identifier
    stage_version: NonEmptyStr
    status: StageStatus
    cache_key: str
    cache_hit: bool
    reused_from_run_id: Identifier | None = None
    output_hash: str
    duration_s: NonNegativeReal


class Run(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "run"
    schema_version: Literal["0.1.0"] = "0.1.0"

    run_id: Identifier
    scenario_revision: Identifier
    input_hashes: dict[str, str]
    code_version: NonEmptyStr
    engine_versions: dict[str, str]
    seed: int
    status: RunStatus
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    parent_run_ids: tuple[Identifier, ...] = ()
    stages: tuple[StageRecord, ...] = ()
    result_hash: str | None = None


# --- results --------------------------------------------------------------------------------


class KpiDefinition(Record):
    kpi_id: Identifier
    name: NonEmptyStr
    unit: NonEmptyStr
    boundary: NonEmptyStr
    description: str = ""


class KpiValue(Record):
    """A KPI is invalid without its boundary, its unit and what it was derived from."""

    definition: KpiDefinition
    value: Real | None = Field(json_schema_extra={"unit": "see definition.unit"})
    derived_from: tuple[NonEmptyStr, ...] = Field(min_length=1)


class CheckOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


class Check(Record):
    """One engineering check with its threshold, origin, observed value and margin."""

    check_id: Identifier
    description: NonEmptyStr
    outcome: CheckOutcome
    observed: Real | None = Field(default=None, json_schema_extra={"unit": "see unit"})
    limit: Real | None = Field(default=None, json_schema_extra={"unit": "see unit"})
    margin: Real | None = Field(default=None, json_schema_extra={"unit": "see unit"})
    unit: str | None = None
    threshold_origin: str | None = None

    @model_validator(mode="after")
    def _evaluated_checks_are_complete(self) -> Check:
        if self.outcome is CheckOutcome.NOT_EVALUATED:
            return self
        required = {
            "observed": self.observed,
            "limit": self.limit,
            "margin": self.margin,
            "unit": self.unit,
            "threshold_origin": self.threshold_origin,
        }
        absent = [name for name, value in required.items() if value is None]
        if absent:
            raise ValueError(f"an evaluated check must state: {', '.join(absent)}")
        return self


class Result(VersionedRecord):
    """The outcome of evaluating one subject: a whole study or, later, one candidate.

    The four statuses are stored separately (specification 7.5).
    """

    SCHEMA_NAME: ClassVar[str] = "result"
    schema_version: Literal["0.2.0"] = "0.2.0"

    result_id: Identifier
    run_id: Identifier
    scenario_revision: Identifier
    subject: NonEmptyStr
    sample_id: NonEmptyStr = "deterministic"
    synthetic: bool
    technical_status: TechnicalStatus
    site_evidence_status: SiteEvidenceStatus | None = None
    evaluation_status: EvaluationStatus
    ranking_eligibility: RankingEligibility
    kpis: tuple[KpiValue, ...] = ()
    checks: tuple[Check, ...] = ()
    stage_outputs: tuple[StageOutput, ...] = ()
    diagnostics: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()

    def deterministic_hash(self) -> str:
        """Hash of everything that must be identical when the same frozen inputs are replayed."""
        content = self.to_json_dict()
        for name in ("result_id", "run_id"):
            content.pop(name)
        return content_hash(content)


# --- schema migrations ----------------------------------------------------------------------


@register_migration("policy", "0.1.0", "0.2.0")
def _policy_gains_the_source_kind(data: JsonDict) -> JsonDict:
    """0.2.0 adds the policy kind 'source'. Existing policies are unchanged."""
    return data


@register_migration("policy", "0.2.0", "0.3.0")
def _policy_gains_the_route_kind(data: JsonDict) -> JsonDict:
    """0.3.0 adds the policy kind 'route'. Existing policies are unchanged."""
    return data


@register_migration("result", "0.1.0", "0.2.0")
def _stage_outputs_gain_the_synthetic_flag(data: JsonDict) -> JsonDict:
    """0.2.0 adds 'synthetic' to every stage output. Older outputs default to False."""
    return data
