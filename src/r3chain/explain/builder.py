"""Build verified typed explanation records from stored run outputs (V05, V06)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from r3chain.domain.hashing import content_hash, short_hash
from r3chain.domain.records.runs import CheckOutcome, Result
from r3chain.domain.status import EvaluationStatus, SiteEvidenceStatus, TechnicalStatus
from r3chain.explain.checker import VerificationContext, build_verification_context, check_claim
from r3chain.explain.records import (
    CauseOfFailureClaim,
    ComparisonClaim,
    ComparisonEntry,
    ExplanationClaim,
    ExplanationRecord,
    OpenAssumptionClaim,
    StatusClaim,
    ValueClaim,
)
from r3chain.store.store import Store

_RESULT_KPIS_PRIORITY = (
    "indicative_route_cost_currency",
    "annual_geothermal_delivered_heat_j",
    "lcoh_geothermal_delivered_currency_per_j",
    "lcoh_system_delivered_currency_per_j",
    "conditional_service_probability_fraction",
    "expected_lcoh_pre_drilling_currency_per_j",
    "max_regret_currency",
    "first_rank_share_fraction",
)

_PROBABILITY_KPIS = frozenset(
    {
        "conditional_service_probability_fraction",
        "first_rank_share_fraction",
    }
)


def _claim_id(prefix: str, payload: Any) -> str:
    return f"clm-{prefix}-{short_hash(content_hash(payload), 10)}"


def _compare_sample_meta(ctx: VerificationContext) -> tuple[int, int] | None:
    comp_out = ctx.stage_outputs.get("compare")
    if comp_out is None or not comp_out.payload:
        return None
    total = int(comp_out.payload.get("sample_count", 0))
    cands = (comp_out.payload.get("comparison") or {}).get("candidates") or {}
    if isinstance(cands, Mapping) and cands:
        first = next(iter(cands.values()))
        acc = first.get("sample_account", {})
        return int(acc.get("total_samples", total)), int(acc.get("unresolved_numerical_samples", 0))
    return total, 0


def _claims_for_candidate_result(ctx: VerificationContext, res: Result) -> list[ExplanationClaim]:
    claims: list[ExplanationClaim] = []
    cid = res.subject
    run_id = ctx.run.run_id
    rev_id = ctx.run.scenario_revision
    sample_meta = _compare_sample_meta(ctx)

    claims.append(
        StatusClaim(
            claim_id=_claim_id("sts", [run_id, cid]),
            run_id=run_id,
            scenario_revision=rev_id,
            subject=cid,
            synthetic=res.synthetic,
            result_id=res.result_id,
            technical_status=res.technical_status,
            evaluation_status=res.evaluation_status,
            site_evidence_status=res.site_evidence_status,
            eligible=res.ranking_eligibility.eligible,
            conditional=res.ranking_eligibility.conditional,
            asserted_feasible=(
                res.evaluation_status is EvaluationStatus.VALID
                and res.technical_status is TechnicalStatus.PASSES_EVALUATED_CASES
                and not any(
                    ev.check.outcome is CheckOutcome.FAIL
                    for ev in ctx.checks_by_subject.get(cid, ())
                )
            ),
            asserted_infeasible=(
                res.evaluation_status is EvaluationStatus.VALID
                and res.technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
            ),
            all_checks_passed=False,
            source_refs=(res.result_id,),
        )
    )

    kpi_map = {k.definition.kpi_id: k for k in res.kpis}
    for kpi_id in _RESULT_KPIS_PRIORITY:
        kpi = kpi_map.get(kpi_id)
        if kpi is None or kpi.value is None:
            continue
        is_prob = kpi_id in _PROBABILITY_KPIS
        if is_prob and sample_meta is None:
            continue
        claims.append(
            ValueClaim(
                claim_id=_claim_id("val", [run_id, cid, kpi_id]),
                run_id=run_id,
                scenario_revision=rev_id,
                subject=cid,
                synthetic=res.synthetic,
                result_id=res.result_id,
                value_kind="kpi",
                kpi_id=kpi_id,
                boundary=kpi.definition.boundary,
                unit=kpi.definition.unit,
                value=float(kpi.value),
                rounding_tolerance=max(1e-9, abs(float(kpi.value)) * 1e-9),
                derived_from=kpi.derived_from,
                sample_count=sample_meta[0] if is_prob and sample_meta else None,
                unresolved_samples=sample_meta[1] if is_prob and sample_meta else None,
                source_refs=(res.result_id, *kpi.derived_from),
            )
        )

    for ev in ctx.checks_by_subject.get(cid, ()):
        if ev.check.outcome is not CheckOutcome.FAIL:
            continue
        if res.evaluation_status is EvaluationStatus.UNRESOLVED_NUMERICAL:
            continue
        claims.append(
            CauseOfFailureClaim(
                claim_id=_claim_id("cof", [run_id, cid, ev.check.check_id, ev.interval_or_sample]),
                run_id=run_id,
                scenario_revision=rev_id,
                subject=cid,
                synthetic=res.synthetic,
                result_id=res.result_id,
                stage=ev.stage,
                check_id=ev.check.check_id,
                description=ev.check.description,
                observed=ev.check.observed,
                limit=ev.check.limit,
                margin=ev.check.margin,
                unit=ev.check.unit,
                threshold_origin=ev.check.threshold_origin,
                interval_or_sample=ev.interval_or_sample,
                upstream_evidence=tuple(
                    part
                    for part in (
                        f"stage:{ev.stage}",
                        ev.interval_or_sample,
                        ev.check.threshold_origin or "",
                    )
                    if part
                ),
                source_refs=(res.result_id,),
            )
        )

    if (
        res.site_evidence_status is SiteEvidenceStatus.CONDITIONAL
        or res.ranking_eligibility.conditional
    ):
        claims.append(
            OpenAssumptionClaim(
                claim_id=_claim_id("oas-cond", [run_id, cid]),
                run_id=run_id,
                scenario_revision=rev_id,
                subject=cid,
                synthetic=res.synthetic,
                result_id=res.result_id,
                field_path=f"candidates[{cid}].site_evidence",
                state="conditional",
                reason="candidate retains conditional evidence or screening status",
                affects_subjects=(cid,),
                source_refs=(res.result_id,),
            )
        )
    return claims


def _open_assumption_claims(
    ctx: VerificationContext, subject: str, result_id: str | None
) -> list[ExplanationClaim]:
    claims: list[ExplanationClaim] = []
    seen: set[str] = set()
    for out in ctx.stage_outputs.values():
        for missing in out.missing_inputs:
            if missing.field_path in seen:
                continue
            seen.add(missing.field_path)
            if missing.state not in {"unknown", "conflict"}:
                continue
            row = ctx.assumptions_by_path.get(missing.field_path)
            claims.append(
                OpenAssumptionClaim(
                    claim_id=_claim_id("oas", [ctx.run.run_id, subject, missing.field_path]),
                    run_id=ctx.run.run_id,
                    scenario_revision=ctx.run.scenario_revision,
                    subject=subject,
                    synthetic=ctx.synthetic,
                    result_id=result_id,
                    assumption_id=row.assumption_id if row else None,
                    field_path=missing.field_path,
                    state="conflict" if missing.state == "conflict" else "unknown",
                    owner=missing.owner or (row.owner if row else None),
                    reason=missing.reason,
                    affects_subjects=(subject,),
                    source_refs=(out.stage,),
                )
            )
    return claims


def _finalize(
    *,
    scope: Literal["result", "rejection", "comparison", "study"],
    subject: str,
    result_id: str | None,
    synthetic: bool,
    claims: Sequence[ExplanationClaim],
    ctx: VerificationContext,
) -> ExplanationRecord:
    outcomes = tuple(check_claim(c, ctx) for c in claims)
    digest = short_hash(content_hash([ctx.run.run_id, scope, subject]), 10)
    return ExplanationRecord(
        explanation_id=f"exp-{scope}-{digest}",
        scope=scope,
        subject=subject,
        run_id=ctx.run.run_id,
        scenario_revision=ctx.run.scenario_revision,
        result_id=result_id,
        synthetic=synthetic,
        claims=tuple(claims),
        checks=outcomes,
        all_claims_accepted=all(o.accepted for o in outcomes),
    )


def explain_result(store: Store, run_id: str, subject: str) -> ExplanationRecord:
    """Build and verify a structured explanation for ``subject`` ('study' or a candidate_id)."""
    ctx = build_verification_context(store, run_id)
    if subject not in ctx.results_by_subject:
        raise KeyError(f"subject {subject!r} has no Result in run {run_id!r}")
    res = ctx.results_by_subject[subject]
    if subject == "study":
        claims: list[ExplanationClaim] = [
            StatusClaim(
                claim_id=_claim_id("sts", [run_id, "study"]),
                run_id=run_id,
                scenario_revision=ctx.run.scenario_revision,
                subject="study",
                synthetic=res.synthetic,
                result_id=res.result_id,
                technical_status=res.technical_status,
                evaluation_status=res.evaluation_status,
                site_evidence_status=res.site_evidence_status,
                eligible=res.ranking_eligibility.eligible,
                conditional=res.ranking_eligibility.conditional,
                asserted_feasible=False,
                asserted_infeasible=False,
                all_checks_passed=False,
                source_refs=(res.result_id,),
            ),
            *_open_assumption_claims(ctx, "study", res.result_id),
        ]
        if any(subj != "study" for subj in ctx.results_by_subject):
            comp_rec = explain_comparison(store, run_id)
            claims.extend(c for c in comp_rec.claims if not isinstance(c, OpenAssumptionClaim))
        return _finalize(
            scope="study",
            subject="study",
            result_id=res.result_id,
            synthetic=res.synthetic,
            claims=claims,
            ctx=ctx,
        )

    claims = _claims_for_candidate_result(ctx, res)
    return _finalize(
        scope="result",
        subject=subject,
        result_id=res.result_id,
        synthetic=res.synthetic,
        claims=claims,
        ctx=ctx,
    )


def explain_rejection(store: Store, run_id: str, target: str) -> ExplanationRecord:
    """Build and verify an explanation for a rejected site, option, or failing candidate."""
    ctx = build_verification_context(store, run_id)
    if target in ctx.results_by_subject:
        res = ctx.results_by_subject[target]
        claims = _claims_for_candidate_result(ctx, res)
        return _finalize(
            scope="rejection",
            subject=target,
            result_id=res.result_id,
            synthetic=res.synthetic,
            claims=claims,
            ctx=ctx,
        )

    if target in ctx.screen_criteria_by_site:
        claims = []
        for crit_id, crit in ctx.screen_criteria_by_site[target].items():
            if crit.get("outcome") != "fail":
                continue
            claims.append(
                CauseOfFailureClaim(
                    claim_id=_claim_id("cof-scr", [run_id, target, crit_id]),
                    run_id=run_id,
                    scenario_revision=ctx.run.scenario_revision,
                    subject=target,
                    synthetic=ctx.synthetic,
                    result_id=None,
                    stage="screen",
                    check_id=crit_id,
                    description=str(crit.get("description", "")),
                    observed=crit.get("observed"),
                    limit=crit.get("limit"),
                    unit=crit.get("unit"),
                    threshold_origin="screening policy",
                    interval_or_sample="deterministic",
                    upstream_evidence=tuple(str(e) for e in crit.get("evidence", ())),
                    source_refs=("stage:screen", f"site:{target}"),
                )
            )
        if claims:
            return _finalize(
                scope="rejection",
                subject=target,
                result_id=None,
                synthetic=ctx.synthetic,
                claims=claims,
                ctx=ctx,
            )

    for rej in ctx.rejected_options:
        opt = rej.get("option") or {}
        key = "|".join(f"{k}:{v}" for k, v in sorted(opt.items()))
        if target in {key, str(rej.get("reason", ""))}:
            stage_name = str(rej.get("stage", "candidates"))
            if stage_name == "screen" and opt.get("site_id") in ctx.screen_criteria_by_site:
                return explain_rejection(store, run_id, str(opt["site_id"]))
            claims = [
                CauseOfFailureClaim(
                    claim_id=_claim_id("cof-rej", [run_id, key, stage_name]),
                    run_id=run_id,
                    scenario_revision=ctx.run.scenario_revision,
                    subject=key,
                    synthetic=ctx.synthetic,
                    result_id=None,
                    stage=stage_name,
                    check_id=f"{stage_name}_rejected",
                    description=str(rej.get("reason", "")),
                    interval_or_sample="deterministic",
                    upstream_evidence=tuple(str(d) for d in rej.get("details", ())),
                    source_refs=(f"stage:{stage_name}",),
                )
            ]
            return _finalize(
                scope="rejection",
                subject=key,
                result_id=None,
                synthetic=ctx.synthetic,
                claims=claims,
                ctx=ctx,
            )
    raise KeyError(f"no failure or rejection record found for {target!r} in run {run_id!r}")


def explain_comparison(
    store: Store,
    run_id: str,
    candidate_ids: Sequence[str] | None = None,
) -> ExplanationRecord:
    """Build and verify a comparison explanation covering eligibility before metric comparison."""
    ctx = build_verification_context(store, run_id)
    cand_results = [res for subj, res in ctx.results_by_subject.items() if subj != "study"]
    if candidate_ids is not None:
        wanted = set(candidate_ids)
        cand_results = [res for res in cand_results if res.subject in wanted]
    if not cand_results:
        raise KeyError(f"no candidate results found in run {run_id!r}")

    assess_out = ctx.stage_outputs.get("assess")
    compare_out = ctx.stage_outputs.get("compare")
    assess_labels = {
        str(item["candidate_id"]): str(item["label"])
        for item in (assess_out.payload.get("assessments", ()) if assess_out else ())
        if isinstance(item, Mapping)
    }

    ranking_policy_id = str(
        (compare_out.payload.get("ranking_policy_id") if compare_out else None)
        or (assess_out.payload.get("policy_id") if assess_out else None)
        or "ranking"
    )
    sample_meta = _compare_sample_meta(ctx)

    # Choose delivered LCOH if evaluated by cost stage, else indicative route cost from assess.
    metric_id = "lcoh_geothermal_delivered_currency_per_j"
    metric_boundary = "geothermal_delivered"
    metric_unit = "currency/J"
    if not any(
        any(k.definition.kpi_id == metric_id and k.value is not None for k in res.kpis)
        for res in cand_results
        if res.ranking_eligibility.eligible
    ):
        metric_id = "indicative_route_cost_currency"
        metric_boundary = (
            "trench, supply and return pipes and crossings of the route from the pad to the "
            "connection, at the unit rates of the route policy. Not a cost estimate"
        )
        metric_unit = "currency"

    eligible_cands = [res for res in cand_results if res.ranking_eligibility.eligible]
    comp_block = (compare_out.payload.get("comparison") or {}) if compare_out else {}
    stability_map: dict[str, dict[str, Any]] = {
        str(r["candidate_id"]): dict(r)
        for r in comp_block.get("ranking_stability", ())
        if isinstance(r, Mapping)
    }

    entries: list[ComparisonEntry] = []
    for res in cand_results:
        kpi = next((k for k in res.kpis if k.definition.kpi_id == metric_id), None)
        if kpi is not None:
            metric_boundary = kpi.definition.boundary
            metric_unit = kpi.definition.unit
        stab = stability_map.get(res.subject, {})
        entries.append(
            ComparisonEntry(
                candidate_id=res.subject,
                result_id=res.result_id,
                label=assess_labels.get(res.subject, res.subject),
                eligible=res.ranking_eligibility.eligible,
                conditional=res.ranking_eligibility.conditional,
                value=float(kpi.value) if kpi is not None and kpi.value is not None else None,
                first_rank_count=(
                    int(stab["first_rank_count"]) if "first_rank_count" in stab else None
                ),
                denominator=int(stab["denominator"]) if "denominator" in stab else None,
            )
        )

    preference_outcome = str(
        comp_block.get("preference_outcome")
        or ("ranked_eligible" if eligible_cands else "no_eligible_candidate")
    )
    ranked_ids = tuple(
        e.candidate_id
        for e in sorted(
            (x for x in entries if x.eligible),
            key=lambda x: (x.value is None, x.value or 0.0, x.candidate_id),
        )
    )
    winner_id: str | None = None
    if preference_outcome == "clear_winner" and ranked_ids:
        ordered = comp_block.get("ordered_candidate_ids") or list(ranked_ids)
        first = str(ordered[0])
        if first in {res.subject for res in eligible_cands}:
            winner_id = first
    elif preference_outcome == "ranked_eligible" and len(ranked_ids) == 1:
        winner_id = ranked_ids[0]

    claims: list[ExplanationClaim] = [
        ComparisonClaim(
            claim_id=_claim_id("cmp", [run_id, metric_id, [res.subject for res in cand_results]]),
            run_id=run_id,
            scenario_revision=ctx.run.scenario_revision,
            subject="study",
            synthetic=ctx.synthetic or any(r.synthetic for r in cand_results),
            result_ids=tuple(res.result_id for res in cand_results),
            candidate_ids=tuple(res.subject for res in cand_results),
            metric_id=metric_id,
            boundary=metric_boundary,
            unit=metric_unit,
            ranking_policy_id=ranking_policy_id,
            preference_outcome=preference_outcome,
            winner_id=winner_id,
            ranked_candidate_ids=ranked_ids,
            entries=tuple(entries),
            sample_count=sample_meta[0] if sample_meta else None,
            unresolved_samples=sample_meta[1] if sample_meta else None,
            source_refs=("stage:assess", "stage:cost", "stage:compare"),
        ),
        *_open_assumption_claims(ctx, "study", ctx.study_result.result_id),
    ]
    return _finalize(
        scope="comparison",
        subject="study",
        result_id=ctx.study_result.result_id,
        synthetic=ctx.synthetic,
        claims=claims,
        ctx=ctx,
    )


def explain_study(store: Store, run_id: str) -> tuple[ExplanationRecord, ...]:
    """Build and verify explanation records across results, rejections, and comparisons."""
    ctx = build_verification_context(store, run_id)
    records: list[ExplanationRecord] = [explain_result(store, run_id, "study")]
    for subject in sorted(ctx.results_by_subject):
        if subject == "study":
            continue
        records.append(explain_result(store, run_id, subject))
    for site_id, criteria in sorted(ctx.screen_criteria_by_site.items()):
        if any(c.get("outcome") == "fail" for c in criteria.values()):
            records.append(explain_rejection(store, run_id, site_id))
    for rej in ctx.rejected_options:
        if rej.get("stage") == "screen":
            continue
        opt = rej.get("option") or {}
        key = "|".join(f"{k}:{v}" for k, v in sorted(opt.items()))
        if key:
            records.append(explain_rejection(store, run_id, key))
    return tuple(records)
