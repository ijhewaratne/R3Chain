"""Typed application service contracts, job records, and failure classification (spec 16.3)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field

from r3chain.domain.base import Identifier, NonEmptyStr, NonNegativeReal, Record, VersionedRecord
from r3chain.domain.records.runs import Result
from r3chain.domain.status import AdapterState, EvaluationStatus, TechnicalStatus
from r3chain.domain.timeseries import UtcDatetime


class FailureClass(StrEnum):
    """Five separate failure categories required by specification section 16.3."""

    INVALID_REQUEST = "invalid_request"
    DENIED_ACCESS = "denied_access"
    PHYSICAL_CHECK_FAILED = "physical_check_failed"
    NUMERICAL_NON_CONVERGENCE = "numerical_non_convergence"
    SERVICE_FAULT = "service_fault"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class JobRecord(VersionedRecord):
    """Persisted background job with stage progress, cancellation state, and access scope."""

    SCHEMA_NAME: ClassVar[str] = "job"
    schema_version: Literal["0.1.0"] = "0.1.0"

    job_id: Identifier
    study_id: Identifier
    project_id: Identifier
    scenario_revision: Identifier
    principal_id: NonEmptyStr
    access_scope: NonEmptyStr
    status: JobStatus
    cancel_requested: bool = False
    current_stage: str = ""
    completed_stages: int = 0
    total_stages: int = 0
    progress_fraction: NonNegativeReal = Field(default=0.0, le=1.0, json_schema_extra={"unit": "1"})
    run_id: Identifier | None = None
    result_ids: tuple[Identifier, ...] = ()
    created_at: UtcDatetime
    updated_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    error_message: str = ""
    failure_class: FailureClass | None = None
    diagnostics: tuple[str, ...] = ()


class ToolResult(VersionedRecord):
    """Unified tool and service result envelope (specification section 16.3)."""

    SCHEMA_NAME: ClassVar[str] = "tool_result"
    schema_version: Literal["0.1.0"] = "0.1.0"

    status: NonEmptyStr
    failure_class: FailureClass | None = None
    message: str = ""
    study_id: str | None = None
    scenario_revision: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    input_hash: str | None = None
    model_versions: dict[str, str] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    checks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    assumptions_used: tuple[dict[str, Any], ...] = ()
    missing_inputs: tuple[dict[str, Any], ...] = ()
    artifact_refs: tuple[str, ...] = ()
    synthetic: bool = False
    explanation: dict[str, Any] | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ContractSummary(Record):
    """Lightweight value+unit wrapper exported inside :attr:`ToolResult.values`."""

    value: Any
    unit: str | None = None
    boundary: str | None = None


def classify_adapter_failure(state: AdapterState) -> FailureClass | None:
    """Map an adapter state onto the five section 16.3 failure categories."""
    if state is AdapterState.INVALID_INPUT:
        return FailureClass.INVALID_REQUEST
    if state is AdapterState.PHYSICAL_LIMIT_EXCEEDED:
        return FailureClass.PHYSICAL_CHECK_FAILED
    if state is AdapterState.NON_CONVERGED:
        return FailureClass.NUMERICAL_NON_CONVERGENCE
    if state is AdapterState.TOOL_FAILURE:
        return FailureClass.SERVICE_FAULT
    return None


def classify_result_failure(result: Result) -> FailureClass | None:
    """Classify a study or candidate :class:`Result` under the five section 16.3 classes."""
    if result.evaluation_status is EvaluationStatus.INVALID_INPUT:
        return FailureClass.INVALID_REQUEST
    if result.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL:
        return FailureClass.NUMERICAL_NON_CONVERGENCE
    if result.evaluation_status is EvaluationStatus.TOOL_FAILURE:
        return FailureClass.SERVICE_FAULT
    if (
        result.evaluation_status is EvaluationStatus.VALID
        and result.technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
    ):
        return FailureClass.PHYSICAL_CHECK_FAILED
    return None
