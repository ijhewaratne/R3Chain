"""Deterministic claim verification against stored records (Plan v2 V06).

Every typed claim is checked solely against frozen scenario, assumption, stage, and result
records in the store. A language model may phrase text, never validate or alter a claim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from r3chain.domain.assumptions import assumption_register
from r3chain.domain.evidence import MissingInput
from r3chain.domain.records.runs import (
    Assumption,
    Check,
    CheckOutcome,
    KpiValue,
    Result,
    Run,
    StageOutput,
)
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, SiteEvidenceStatus, TechnicalStatus
from r3chain.explain.records import (
    CauseOfFailureClaim,
    ClaimCheckOutcome,
    ComparisonClaim,
    ExplanationClaim,
    ExplanationRecord,
    OpenAssumptionClaim,
    StatusClaim,
    ValueClaim,
)
from r3chain.store.store import Store

_PROBABILITY_KPIS = frozenset(
    {
        "conditional_service_probability_fraction",
        "first_rank_share_fraction",
    }
)


@dataclass(frozen=True)
class StoredCheckEvidence:
    """One stored failing or evaluated check plus the stage case/interval that produced it."""

    check: Check
    stage: str
    interval_or_sample: str


@dataclass(frozen=True)
class VerificationContext:
    """Indexed read-only snapshot of one stored run for claim checking."""

    run: Run
    scenario: Scenario
    study_result: Result
    results_by_id: Mapping[str, Result]
    results_by_subject: Mapping[str, Result]
    stage_outputs: Mapping[str, StageOutput]
    assumptions_by_path: Mapping[str, Assumption]
    missing_by_path: Mapping[str, tuple[MissingInput, ...]]
    checks_by_subject: Mapping[str, tuple[StoredCheckEvidence, ...]] = field(default_factory=dict)
    screen_criteria_by_site: Mapping[str, Mapping[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    rejected_options: tuple[dict[str, Any], ...] = ()
    synthetic: bool = False


def build_verification_context(store: Store, run_id: str) -> VerificationContext:
    """Load run, scenario, results, assumptions, and stage outputs once from the store."""
    run = store.get_run(run_id)
    scenario = store.get_scenario(run.scenario_revision)
    results = store.get_results(run_id)
    study_res = next(res for res in results if res.subject == "study")
    stage_outputs = {out.stage: out for out in study_res.stage_outputs}
    assumptions = assumption_register(scenario)
    assumptions_by_path = {row.field_path: row for row in assumptions}

    missing_by_path: dict[str, list[MissingInput]] = {}
    for out in study_res.stage_outputs:
        for item in out.missing_inputs:
            missing_by_path.setdefault(item.field_path, []).append(item)

    results_by_id = {res.result_id: res for res in results}
    results_by_subject = {res.subject: res for res in results}

    checks_by_subject: dict[str, list[StoredCheckEvidence]] = {}
    for res in results:
        bucket = checks_by_subject.setdefault(res.subject, [])
        for chk in res.checks:
            bucket.append(
                StoredCheckEvidence(check=chk, stage="result", interval_or_sample="deterministic")
            )

    couple_out = stage_outputs.get("couple")
    if couple_out is not None:
        for cid, cases in (couple_out.payload.get("candidates") or {}).items():
            if not isinstance(cases, Mapping):
                continue
            bucket = checks_by_subject.setdefault(str(cid), [])
            for case_id, entry in cases.items():
                if not isinstance(entry, Mapping):
                    continue
                res_dict = entry.get("result")
                if not isinstance(res_dict, Mapping):
                    continue
                for raw_chk in res_dict.get("checks", ()):
                    chk = Check.model_validate(raw_chk)
                    bucket.append(
                        StoredCheckEvidence(
                            check=chk,
                            stage="couple",
                            interval_or_sample=f"case:{case_id}",
                        )
                    )

    screen_criteria_by_site: dict[str, dict[str, dict[str, Any]]] = {}
    screen_out = stage_outputs.get("screen")
    if screen_out is not None:
        for site_id, sdata in (screen_out.payload.get("sites") or {}).items():
            if isinstance(sdata, Mapping):
                screen_criteria_by_site[str(site_id)] = {
                    str(c["criterion_id"]): dict(c)
                    for c in sdata.get("criteria", ())
                    if isinstance(c, Mapping) and "criterion_id" in c
                }

    candidates_out = stage_outputs.get("candidates")
    rejected_options = tuple(
        dict(item)
        for item in (candidates_out.payload.get("rejected", ()) if candidates_out else ())
        if isinstance(item, Mapping)
    )

    return VerificationContext(
        run=run,
        scenario=scenario,
        study_result=study_res,
        results_by_id=results_by_id,
        results_by_subject=results_by_subject,
        stage_outputs=stage_outputs,
        assumptions_by_path=assumptions_by_path,
        missing_by_path={k: tuple(v) for k, v in missing_by_path.items()},
        checks_by_subject={k: tuple(v) for k, v in checks_by_subject.items()},
        screen_criteria_by_site=screen_criteria_by_site,
        rejected_options=rejected_options,
        synthetic=study_res.synthetic,
    )


def _reject(claim: ExplanationClaim, reason: str, *codes: str) -> ClaimCheckOutcome:
    return ClaimCheckOutcome(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type,
        accepted=False,
        reason=reason,
        error_codes=tuple(codes),
    )


def _accept(claim: ExplanationClaim) -> ClaimCheckOutcome:
    return ClaimCheckOutcome(
        claim_id=claim.claim_id,
        claim_type=claim.claim_type,
        accepted=True,
        reason="verified against stored run records",
        error_codes=(),
    )


def _check_common(
    claim: ExplanationClaim,
    ctx: VerificationContext,
    *,
    result_id: str | None = None,
    allow_synthetic_in_pilot: bool = False,
) -> tuple[str, ...] | None:
    if claim.run_id != ctx.run.run_id:
        return ("run_id_mismatch",)
    if claim.scenario_revision != ctx.run.scenario_revision:
        return ("stale_revision",)
    if result_id is not None:
        res = ctx.results_by_id.get(result_id)
        if res is None:
            return ("unknown_result_id",)
        if res.subject != claim.subject:
            return ("subject_mismatch",)
        if res.synthetic and not claim.synthetic:
            return ("synthetic_label_dropped",)
    if ctx.synthetic and not claim.synthetic and not allow_synthetic_in_pilot:
        return ("synthetic_label_dropped",)
    return None


def _compare_sample_counts(ctx: VerificationContext) -> tuple[int, int] | None:
    comp_out = ctx.stage_outputs.get("compare")
    if comp_out is None or not comp_out.payload:
        return None
    total = int(comp_out.payload.get("sample_count", 0))
    comp = comp_out.payload.get("comparison", {})
    cands = comp.get("candidates", {})
    if isinstance(cands, Mapping) and cands:
        any_row = next(iter(cands.values()))
        acc = any_row.get("sample_account", {})
        unresolved = int(acc.get("unresolved_numerical_samples", 0))
        return int(acc.get("total_samples", total)), unresolved
    return total, 0


def _find_kpi(result: Result, kpi_id: str) -> KpiValue | None:
    return next((item for item in result.kpis if item.definition.kpi_id == kpi_id), None)


def _close(a: float, b: float, abs_tol: float) -> bool:
    return abs(a - b) <= abs_tol


def _check_value_claim(claim: ValueClaim, ctx: VerificationContext) -> ClaimCheckOutcome:
    err = _check_common(claim, ctx, result_id=claim.result_id)
    if err is not None:
        return _reject(claim, f"claim metadata mismatch: {', '.join(err)}", *err)
    result = ctx.results_by_id[claim.result_id]
    if claim.value_kind == "kpi":
        if not claim.kpi_id:
            return _reject(claim, "value claim of kind 'kpi' requires kpi_id", "missing_kpi_id")
        kpi = _find_kpi(result, claim.kpi_id)
        if kpi is None:
            return _reject(
                claim,
                f"KPI {claim.kpi_id!r} not found on result {claim.result_id}",
                "unknown_kpi",
            )
        if kpi.value is None:
            return _reject(
                claim,
                f"KPI {claim.kpi_id!r} has undefined (None) value in {claim.result_id}",
                "null_kpi_value",
            )
        if kpi.definition.unit != claim.unit:
            return _reject(
                claim,
                f"unit mismatch for {claim.kpi_id}: expected {kpi.definition.unit!r}, "
                f"got {claim.unit!r}",
                "unit_mismatch",
            )
        if claim.boundary is not None and kpi.definition.boundary != claim.boundary:
            return _reject(
                claim,
                f"boundary mismatch for {claim.kpi_id}: expected {kpi.definition.boundary!r}, "
                f"got {claim.boundary!r}",
                "boundary_mismatch",
            )
        if not _close(float(claim.value), float(kpi.value), float(claim.rounding_tolerance)):
            return _reject(
                claim,
                f"value {claim.value} for {claim.kpi_id} differs from stored {kpi.value} "
                f"beyond tolerance {claim.rounding_tolerance}",
                "value_mismatch",
            )
        if claim.kpi_id in _PROBABILITY_KPIS:
            counts = _compare_sample_counts(ctx)
            if counts is None or claim.sample_count is None:
                return _reject(
                    claim,
                    f"probability/share KPI {claim.kpi_id} requires explicit sample denominator",
                    "missing_denominator",
                )
            total_samples, unresolved = counts
            if claim.sample_count != total_samples or (
                claim.unresolved_samples is not None and claim.unresolved_samples != unresolved
            ):
                return _reject(
                    claim,
                    f"denominator mismatch for {claim.kpi_id}: expected "
                    f"samples={total_samples}, unresolved={unresolved}",
                    "denominator_mismatch",
                )
        return _accept(claim)

    if claim.value_kind in {"check_observed", "check_limit"}:
        if not claim.check_id:
            return _reject(claim, "check value claim requires check_id", "missing_check_id")
        entries = [
            e
            for e in ctx.checks_by_subject.get(claim.subject, ())
            if e.check.check_id == claim.check_id
        ]
        if not entries:
            return _reject(
                claim,
                f"check {claim.check_id!r} not found for subject {claim.subject!r}",
                "unknown_check",
            )
        matched = False
        for ev in entries:
            if ev.check.outcome is CheckOutcome.NOT_EVALUATED:
                continue
            target = ev.check.observed if claim.value_kind == "check_observed" else ev.check.limit
            if target is None or ev.check.unit != claim.unit:
                continue
            if _close(float(claim.value), float(target), float(claim.rounding_tolerance)):
                matched = True
                break
        if not matched:
            return _reject(
                claim,
                f"check {claim.check_id!r} {claim.value_kind} does not match stored check record",
                "check_value_mismatch",
            )
        return _accept(claim)

    # input value claim
    if not claim.field_path:
        return _reject(claim, "input value claim requires field_path", "missing_field_path")
    row = ctx.assumptions_by_path.get(claim.field_path)
    if row is None or row.status.value != "active" or not isinstance(row.value, int | float):
        return _reject(
            claim,
            f"active numeric assumption {claim.field_path!r} not found",
            "unknown_or_inactive_input",
        )
    if not _close(float(claim.value), float(row.value), float(claim.rounding_tolerance)):
        return _reject(
            claim,
            f"input {claim.field_path!r} value {claim.value} mismatches stored {row.value}",
            "value_mismatch",
        )
    return _accept(claim)


def _check_status_claim(claim: StatusClaim, ctx: VerificationContext) -> ClaimCheckOutcome:
    err = _check_common(claim, ctx, result_id=claim.result_id)
    if err is not None:
        return _reject(claim, f"claim metadata mismatch: {', '.join(err)}", *err)
    res = ctx.results_by_id[claim.result_id]
    if claim.technical_status is not res.technical_status:
        return _reject(
            claim,
            f"technical status {claim.technical_status.value!r} != stored "
            f"{res.technical_status.value!r}",
            "technical_status_mismatch",
        )
    if claim.evaluation_status is not res.evaluation_status:
        return _reject(
            claim,
            f"evaluation status {claim.evaluation_status.value!r} != stored "
            f"{res.evaluation_status.value!r}",
            "evaluation_status_mismatch",
        )
    if (
        claim.site_evidence_status is not None
        and claim.site_evidence_status is not res.site_evidence_status
    ):
        return _reject(
            claim,
            f"site evidence status mismatches stored {res.site_evidence_status}",
            "site_evidence_mismatch",
        )
    if claim.eligible != res.ranking_eligibility.eligible:
        return _reject(
            claim,
            f"ranking eligibility {claim.eligible} != stored {res.ranking_eligibility.eligible}",
            "eligibility_mismatch",
        )
    if claim.conditional != res.ranking_eligibility.conditional:
        return _reject(
            claim,
            f"conditional flag {claim.conditional} != stored {res.ranking_eligibility.conditional}",
            "conditional_mismatch",
        )
    checks = ctx.checks_by_subject.get(claim.subject, ())
    if claim.asserted_feasible and (
        res.evaluation_status is not EvaluationStatus.VALID
        or res.technical_status is not TechnicalStatus.PASSES_EVALUATED_CASES
        or any(ev.check.outcome is CheckOutcome.FAIL for ev in checks)
    ):
        return _reject(
            claim,
            "feasibility asserted on a non-passing or invalid candidate result (S1)",
            "false_feasibility",
        )
    if claim.asserted_infeasible and (
        res.evaluation_status is not EvaluationStatus.VALID
        or res.technical_status is not TechnicalStatus.FAILS_EVALUATED_CASE
    ):
        return _reject(
            claim,
            "physical infeasibility asserted without valid/failed physical case (P1)",
            "numerical_reported_as_physical",
        )
    if claim.all_checks_passed and (
        res.evaluation_status is not EvaluationStatus.VALID
        or any(ev.check.outcome is not CheckOutcome.PASS for ev in checks)
    ):
        return _reject(
            claim,
            "all_checks_passed asserted when failing or un-evaluated check exists (S6)",
            "missing_or_failed_check_claimed_passed",
        )
    return _accept(claim)


def _values_match(
    claimed: float | str | None,
    stored: float | str | None,
    tol: float,
) -> bool:
    if claimed is None and stored is None:
        return True
    if isinstance(claimed, int | float) and isinstance(stored, int | float):
        return _close(float(claimed), float(stored), tol)
    return claimed == stored


def _check_cause_claim(claim: CauseOfFailureClaim, ctx: VerificationContext) -> ClaimCheckOutcome:
    err = _check_common(claim, ctx, result_id=claim.result_id)
    if err is not None:
        return _reject(claim, f"claim metadata mismatch: {', '.join(err)}", *err)

    if claim.stage == "screen":
        criteria = ctx.screen_criteria_by_site.get(claim.subject, {})
        crit = criteria.get(claim.check_id)
        if crit is None or crit.get("outcome") != "fail":
            return _reject(
                claim,
                f"screening criterion {claim.check_id!r} on {claim.subject!r} did not fail",
                "unknown_or_non_failing_check",
            )
        if not _values_match(
            claim.observed, crit.get("observed"), float(claim.rounding_tolerance)
        ) or not _values_match(claim.limit, crit.get("limit"), float(claim.rounding_tolerance)):
            return _reject(
                claim,
                f"screening criterion {claim.check_id!r} observed/limit mismatch",
                "check_value_mismatch",
            )
        if claim.unit is not None and crit.get("unit") != claim.unit:
            return _reject(claim, "screening criterion unit mismatch", "unit_mismatch")
        return _accept(claim)

    if claim.stage in {"routes", "candidates"}:
        matched_rej = [
            rej
            for rej in ctx.rejected_options
            if rej.get("stage") == claim.stage
            and (
                f"candidate:{claim.subject}" == rej.get("reason")
                or claim.subject
                in " ".join(f"{k}={v}" for k, v in (rej.get("option") or {}).items())
                or claim.subject
                == "|".join(f"{k}:{v}" for k, v in sorted((rej.get("option") or {}).items()))
            )
        ]
        if not matched_rej:
            return _reject(
                claim,
                f"pre-evaluation rejection for {claim.subject!r} in {claim.stage!r} not found",
                "unknown_rejection",
            )
        return _accept(claim)

    res = ctx.results_by_subject.get(claim.subject)
    if res is not None and res.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL:
        return _reject(
            claim,
            "cannot assert physical failure check on an unresolved numerical result (P1)",
            "numerical_reported_as_physical",
        )

    candidates = [
        ev
        for ev in ctx.checks_by_subject.get(claim.subject, ())
        if ev.check.check_id == claim.check_id
        and ev.check.outcome is CheckOutcome.FAIL
        and (
            claim.interval_or_sample == "deterministic"
            or ev.interval_or_sample == claim.interval_or_sample
            or ev.interval_or_sample == "deterministic"
        )
    ]
    if not candidates:
        return _reject(
            claim,
            f"failing check {claim.check_id!r} not found for {claim.subject!r} "
            f"at {claim.interval_or_sample!r} (P2)",
            "unknown_or_non_failing_check",
        )
    for ev in candidates:
        obs_ok = _values_match(claim.observed, ev.check.observed, float(claim.rounding_tolerance))
        lim_ok = _values_match(claim.limit, ev.check.limit, float(claim.rounding_tolerance))
        unit_ok = claim.unit is None or claim.unit == ev.check.unit
        if obs_ok and lim_ok and unit_ok:
            return _accept(claim)
    return _reject(
        claim,
        f"observed/limit/unit for failed check {claim.check_id!r} do not match stored Check record",
        "check_value_mismatch",
    )


def _check_comparison_claim(claim: ComparisonClaim, ctx: VerificationContext) -> ClaimCheckOutcome:
    err = _check_common(claim, ctx)
    if err is not None:
        return _reject(claim, f"claim metadata mismatch: {', '.join(err)}", *err)

    for rid in claim.result_ids:
        if rid not in ctx.results_by_id:
            return _reject(claim, f"unknown result_id {rid!r} in comparison", "unknown_result_id")

    for cid in claim.candidate_ids:
        if cid not in ctx.results_by_subject:
            return _reject(
                claim, f"candidate {cid!r} missing from stored run results", "unknown_candidate"
            )

    for ranked_cid in claim.ranked_candidate_ids:
        res = ctx.results_by_subject.get(ranked_cid)
        if res is None or not res.ranking_eligibility.eligible:
            return _reject(
                claim,
                f"ranked candidate {ranked_cid!r} is not eligible for ranking (S2)",
                "ineligible_candidate_ranked",
            )

    if claim.winner_id is not None:
        win_res = ctx.results_by_subject.get(claim.winner_id)
        if (
            win_res is None
            or not win_res.ranking_eligibility.eligible
            or win_res.technical_status is not TechnicalStatus.PASSES_EVALUATED_CASES
            or win_res.evaluation_status is not EvaluationStatus.VALID
        ):
            return _reject(
                claim,
                f"winner {claim.winner_id!r} is ineligible or failed checks (S2)",
                "ineligible_winner",
            )
        comp_out = ctx.stage_outputs.get("compare")
        if comp_out is not None and comp_out.payload:
            stored_pref = (comp_out.payload.get("comparison") or {}).get("preference_outcome")
            if stored_pref in {"no_defensible_preference", "insufficient_evidence"}:
                return _reject(
                    claim,
                    f"winner {claim.winner_id!r} claimed despite stored outcome {stored_pref!r}",
                    "forced_winner",
                )

    for entry in claim.entries:
        res = ctx.results_by_subject.get(entry.candidate_id)
        if res is None or res.result_id != entry.result_id:
            return _reject(
                claim,
                f"entry {entry.candidate_id!r} cites mismatched result_id {entry.result_id!r}",
                "unknown_result_id",
            )
        if entry.eligible != res.ranking_eligibility.eligible:
            return _reject(
                claim,
                f"entry {entry.candidate_id!r} eligibility disagrees with stored result",
                "eligibility_mismatch",
            )
        if entry.value is not None:
            kpi = _find_kpi(res, claim.metric_id)
            if kpi is None or kpi.value is None:
                return _reject(
                    claim,
                    f"metric {claim.metric_id!r} missing for candidate {entry.candidate_id!r}",
                    "unknown_kpi",
                )
            if kpi.definition.unit != claim.unit:
                return _reject(claim, "comparison metric unit mismatch", "unit_mismatch")
            if kpi.definition.boundary != claim.boundary:
                return _reject(
                    claim,
                    f"comparison boundary mismatch: stored {kpi.definition.boundary!r} vs "
                    f"{claim.boundary!r} (S3)",
                    "boundary_mismatch",
                )
            if not _close(float(entry.value), float(kpi.value), float(claim.rounding_tolerance)):
                return _reject(
                    claim,
                    f"comparison value {entry.value} for {entry.candidate_id!r} != {kpi.value}",
                    "value_mismatch",
                )

    if claim.sample_count is not None or any(e.denominator is not None for e in claim.entries):
        counts = _compare_sample_counts(ctx)
        if counts is None:
            return _reject(
                claim,
                "comparison cites sample counts without compare stage records (P4)",
                "missing_denominator",
            )
        total_samples, unresolved = counts
        if claim.sample_count is not None and claim.sample_count != total_samples:
            return _reject(
                claim,
                f"sample_count {claim.sample_count} != stored {total_samples} (P4)",
                "denominator_mismatch",
            )
        if claim.unresolved_samples is not None and claim.unresolved_samples != unresolved:
            return _reject(
                claim,
                f"unresolved_samples {claim.unresolved_samples} != stored {unresolved} (P4)",
                "denominator_mismatch",
            )
        for entry in claim.entries:
            if entry.denominator is not None and entry.denominator != total_samples:
                return _reject(
                    claim,
                    f"entry denominator {entry.denominator} != stored {total_samples} (P4)",
                    "denominator_mismatch",
                )
    return _accept(claim)


def _check_open_assumption_claim(
    claim: OpenAssumptionClaim, ctx: VerificationContext
) -> ClaimCheckOutcome:
    err = _check_common(claim, ctx, result_id=claim.result_id)
    if err is not None:
        return _reject(claim, f"claim metadata mismatch: {', '.join(err)}", *err)

    if claim.state in {"unknown", "conflict"}:
        row = ctx.assumptions_by_path.get(claim.field_path)
        stage_items = ctx.missing_by_path.get(claim.field_path, ())
        matched_state = (row is not None and row.status.value == claim.state) or any(
            item.state == claim.state for item in stage_items
        )
        if not matched_state:
            return _reject(
                claim,
                f"assumption {claim.field_path!r} is not recorded as {claim.state!r}",
                "not_open_assumption",
            )
        if claim.owner is not None:
            owners = {
                o
                for o in (
                    row.owner if row else None,
                    row.reviewer if row else None,
                    *(item.owner for item in stage_items),
                )
                if o
            }
            if owners and claim.owner not in owners:
                return _reject(
                    claim,
                    f"owner {claim.owner!r} does not match recorded {sorted(owners)}",
                    "owner_mismatch",
                )
        return _accept(claim)

    # conditional state
    targets = claim.affects_subjects or (claim.subject,)
    conditional_found = any(
        (res := ctx.results_by_subject.get(subj)) is not None
        and (
            res.site_evidence_status is SiteEvidenceStatus.CONDITIONAL
            or res.ranking_eligibility.conditional
        )
        for subj in targets
    ) or any(
        crit.get("outcome") == "unknown"
        for criteria in ctx.screen_criteria_by_site.values()
        for crit in criteria.values()
    )
    if not conditional_found:
        return _reject(
            claim,
            f"no conditional site evidence or screening status found for {targets}",
            "not_conditional",
        )
    return _accept(claim)


def check_claim(claim: ExplanationClaim, ctx: VerificationContext) -> ClaimCheckOutcome:
    """Deterministically accept or reject one typed claim against ``ctx``."""
    if isinstance(claim, ValueClaim):
        return _check_value_claim(claim, ctx)
    if isinstance(claim, StatusClaim):
        return _check_status_claim(claim, ctx)
    if isinstance(claim, CauseOfFailureClaim):
        return _check_cause_claim(claim, ctx)
    if isinstance(claim, ComparisonClaim):
        return _check_comparison_claim(claim, ctx)
    if isinstance(claim, OpenAssumptionClaim):
        return _check_open_assumption_claim(claim, ctx)
    raise TypeError(f"unsupported claim type: {type(claim)!r}")


def check_explanation(record: ExplanationRecord, store: Store) -> ExplanationRecord:
    """Verify every claim in ``record`` against stored run data and attach outcomes."""
    ctx = build_verification_context(store, record.run_id)
    outcomes = tuple(check_claim(claim, ctx) for claim in record.claims)
    return record.model_copy(
        update={
            "checks": outcomes,
            "all_claims_accepted": all(outcome.accepted for outcome in outcomes),
        }
    )


def verify_claims_with_context(
    claims: Sequence[ExplanationClaim], ctx: VerificationContext
) -> tuple[ClaimCheckOutcome, ...]:
    """Verify a batch of claims against a shared :class:`VerificationContext`."""
    return tuple(check_claim(claim, ctx) for claim in claims)
