"""Status vocabularies and the rules that keep different kinds of failure apart.

Specification sections 7.4 and 7.5. A numerical failure is never a physical infeasibility, a
missing input is never a failed check, and an unsupported capability is never a zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from r3chain.domain.base import Record


class AdapterState(StrEnum):
    """Outcome of one engine call (specification 7.4)."""

    COMPLETED = "completed"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MODEL = "unsupported_model"
    NON_CONVERGED = "non_converged"
    PHYSICAL_LIMIT_EXCEEDED = "physical_limit_exceeded"
    CANCELLED = "cancelled"
    TOOL_FAILURE = "tool_failure"


class StageStatus(StrEnum):
    """Outcome of one pipeline stage: the adapter states plus two pipeline outcomes."""

    COMPLETED = "completed"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED_MODEL = "unsupported_model"
    NON_CONVERGED = "non_converged"
    PHYSICAL_LIMIT_EXCEEDED = "physical_limit_exceeded"
    CANCELLED = "cancelled"
    TOOL_FAILURE = "tool_failure"
    # A needed input is unknown or in conflict, or the calculation does not exist yet.
    NOT_ESTABLISHED = "not_established"
    # Not executed because an upstream stage produced no usable output.
    NOT_RUN = "not_run"


class TechnicalStatus(StrEnum):
    PASSES_EVALUATED_CASES = "passes_evaluated_cases"
    FAILS_EVALUATED_CASE = "fails_evaluated_case"
    NOT_ESTABLISHED = "not_established"


class SiteEvidenceStatus(StrEnum):
    DOCUMENTED = "documented"
    CONDITIONAL = "conditional"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


class EvaluationStatus(StrEnum):
    VALID = "valid"
    UNRESOLVED_NUMERICAL = "unresolved_numerical"
    INVALID_INPUT = "invalid_input"
    UNSUPPORTED = "unsupported"
    NOT_EVALUATED = "not_evaluated"
    CANCELLED = "cancelled"
    TOOL_FAILURE = "tool_failure"


class RankingEligibility(Record):
    eligible: bool
    conditional: bool = False
    reasons: tuple[str, ...] = ()


def stage_status_from_adapter(state: AdapterState) -> StageStatus:
    return StageStatus(state.value)


# Evaluation problems in order of precedence. The first one present decides the status.
_EVALUATION_PRECEDENCE: tuple[tuple[StageStatus, EvaluationStatus], ...] = (
    (StageStatus.TOOL_FAILURE, EvaluationStatus.TOOL_FAILURE),
    (StageStatus.CANCELLED, EvaluationStatus.CANCELLED),
    (StageStatus.INVALID_INPUT, EvaluationStatus.INVALID_INPUT),
    (StageStatus.NON_CONVERGED, EvaluationStatus.UNRESOLVED_NUMERICAL),
    (StageStatus.UNSUPPORTED_MODEL, EvaluationStatus.UNSUPPORTED),
    (StageStatus.NOT_ESTABLISHED, EvaluationStatus.NOT_EVALUATED),
)


def summarise_stage_statuses(
    statuses: Iterable[StageStatus],
) -> tuple[TechnicalStatus, EvaluationStatus]:
    """Derive the technical and evaluation status of a chain of stages.

    * A physical limit that was exceeded is a valid evaluation with a failed technical case.
    * Non convergence is an unresolved numerical evaluation. It never becomes a technical
      failure.
    * A technical pass needs every stage that ran to complete.
    """
    executed = [status for status in statuses if status is not StageStatus.NOT_RUN]
    if not executed:
        return TechnicalStatus.NOT_ESTABLISHED, EvaluationStatus.NOT_EVALUATED

    present = set(executed)
    evaluation = EvaluationStatus.VALID
    for stage_status, evaluation_status in _EVALUATION_PRECEDENCE:
        if stage_status in present:
            evaluation = evaluation_status
            break

    if StageStatus.PHYSICAL_LIMIT_EXCEEDED in present:
        technical = TechnicalStatus.FAILS_EVALUATED_CASE
    elif present == {StageStatus.COMPLETED}:
        technical = TechnicalStatus.PASSES_EVALUATED_CASES
    else:
        technical = TechnicalStatus.NOT_ESTABLISHED
    return technical, evaluation


def ranking_eligibility(
    technical: TechnicalStatus,
    site_evidence: SiteEvidenceStatus,
    evaluation: EvaluationStatus,
    *,
    comparison_fields_complete: bool,
    rankable_site_statuses: frozenset[SiteEvidenceStatus] = frozenset(
        {SiteEvidenceStatus.DOCUMENTED}
    ),
) -> RankingEligibility:
    """Decide whether a candidate may be ranked (specification 7.5).

    A candidate with conditional site evidence can be ranked only when the decision policy lists
    that status as rankable, and it stays marked as conditional.
    """
    reasons: list[str] = []
    if evaluation is not EvaluationStatus.VALID:
        reasons.append(f"evaluation is {evaluation.value}")
    if technical is not TechnicalStatus.PASSES_EVALUATED_CASES:
        reasons.append(f"technical status is {technical.value}")
    if site_evidence not in rankable_site_statuses:
        reasons.append(f"site evidence is {site_evidence.value}")
    if not comparison_fields_complete:
        reasons.append("comparison fields are incomplete")
    conditional = site_evidence is not SiteEvidenceStatus.DOCUMENTED
    return RankingEligibility(eligible=not reasons, conditional=conditional, reasons=tuple(reasons))
