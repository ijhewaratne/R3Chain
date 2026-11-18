"""Unit tests for typed explanation records, claim checks, and fixed DE/EN wording (V05-V07)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import POLICY_DIRECTORY, FakeClock

from r3chain.domain.records.runs import CheckOutcome, Result
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import EvaluationStatus, StageStatus, TechnicalStatus
from r3chain.explain import (
    CauseOfFailureClaim,
    ComparisonClaim,
    ComparisonEntry,
    OpenAssumptionClaim,
    StatusClaim,
    ValueClaim,
    build_verification_context,
    check_claim,
    check_explanation,
    explain_comparison,
    explain_rejection,
    explain_result,
    explain_study,
    render_claim,
    render_explanation,
)
from r3chain.pipeline.runner import PipelineRunner
from r3chain.pipeline.stages import default_stages
from r3chain.store.store import Store, load_policies


@pytest.fixture(scope="module")
def synthetic_explain_store(
    tmp_path_factory: pytest.TempPathFactory,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> tuple[Store, Any, Result]:
    root = tmp_path_factory.mktemp("m6_explain")
    store = Store(root / "store", clock=FakeClock())
    for policy in load_policies(POLICY_DIRECTORY):
        store.register_policy(policy)
    raw = json.loads(json.dumps(_synthetic_data))
    revision = store.add_revision(
        Scenario.model_validate(raw), author="m6-explain", rationale="explain suite baseline"
    ).revision_id
    runner = PipelineRunner(
        store,
        default_stages(network_model=network_model),
        clock=FakeClock(),
        code_version_text="m6-explain",
    )
    run, result = runner.run(revision, seed=7)
    return store, run, result


@pytest.mark.requirement("V05")
@pytest.mark.requirement("V06")
@pytest.mark.requirement("V07")
def test_every_synthetic_explanation_passes_claim_check_and_renders_de_en(
    synthetic_explain_store: tuple[Store, Any, Result],
) -> None:
    store, run, _ = synthetic_explain_store
    explanations = explain_study(store, run.run_id)
    assert len(explanations) >= 10

    seen_types: set[str] = set()
    for rec in explanations:
        checked = check_explanation(rec, store)
        assert checked.all_claims_accepted, (
            f"{rec.explanation_id} rejected: {[c for c in checked.checks if not c.accepted]}"
        )
        en = render_explanation(checked, lang="en")
        de = render_explanation(checked, lang="de")
        assert "[SYNTHETIC]" in en
        assert "[SYNTHETISCH]" in de
        assert en != de
        for claim in checked.claims:
            seen_types.add(claim.claim_type)
            en_c = render_claim(claim, lang="en")
            de_c = render_claim(claim, lang="de")
            assert en_c != de_c
            assert "[SYNTHETIC]" in en_c and "[SYNTHETISCH]" in de_c

    assert seen_types == {
        "value",
        "status",
        "cause_of_failure",
        "comparison",
        "open_assumption",
    }

    # Screening exclusion on site delta names the failed criterion with observed vs limit.
    delta_exp = explain_rejection(store, run.run_id, "delta")
    assert delta_exp.all_claims_accepted
    assert any(
        isinstance(c, CauseOfFailureClaim) and c.stage == "screen" and c.observed is not None
        for c in delta_exp.claims
    )


@pytest.mark.requirement("V06")
def test_wrong_claim_of_every_type_is_deterministically_rejected(
    synthetic_explain_store: tuple[Store, Any, Result],
) -> None:
    store, run, _ = synthetic_explain_store
    ctx = build_verification_context(store, run.run_id)
    results = store.get_results(run.run_id)

    passing_res = next(
        r
        for r in results
        if r.subject != "study"
        and r.ranking_eligibility.eligible
        and any(
            k.definition.kpi_id == "lcoh_geothermal_delivered_currency_per_j"
            and k.value is not None
            for k in r.kpis
        )
    )
    failing_cand_id = next(
        cid
        for cid, evs in ctx.checks_by_subject.items()
        if cid != "study"
        and any(ev.check.outcome is CheckOutcome.FAIL for ev in evs)
        and ctx.results_by_subject[cid].technical_status is TechnicalStatus.FAILS_EVALUATED_CASE
    )
    failing_res = ctx.results_by_subject[failing_cand_id]
    real_fail = next(
        ev for ev in ctx.checks_by_subject[failing_cand_id] if ev.check.outcome is CheckOutcome.FAIL
    )

    # 1. Wrong ValueClaim variants: wrong number, wrong boundary, wrong unit, stale rev, non-syn.
    p_exp = explain_result(store, run.run_id, passing_res.subject)
    real_val = next(
        c
        for c in p_exp.claims
        if isinstance(c, ValueClaim) and c.kpi_id == "lcoh_geothermal_delivered_currency_per_j"
    )
    bad_num = real_val.model_copy(update={"value": real_val.value * 2.5 + 1.0})
    out_num = check_claim(bad_num, ctx)
    assert not out_num.accepted and "value_mismatch" in out_num.error_codes

    bad_boundary = real_val.model_copy(update={"boundary": "geothermal_plant"})
    out_bound = check_claim(bad_boundary, ctx)
    assert not out_bound.accepted and "boundary_mismatch" in out_bound.error_codes

    bad_unit = real_val.model_copy(update={"unit": "EUR/MWh"})
    out_unit = check_claim(bad_unit, ctx)
    assert not out_unit.accepted and "unit_mismatch" in out_unit.error_codes

    bad_rev = real_val.model_copy(update={"scenario_revision": "rev-stale"})
    out_rev = check_claim(bad_rev, ctx)
    assert not out_rev.accepted and "stale_revision" in out_rev.error_codes

    bad_syn = real_val.model_copy(update={"synthetic": False})
    out_syn = check_claim(bad_syn, ctx)
    assert not out_syn.accepted and "synthetic_label_dropped" in out_syn.error_codes

    # 2. Wrong StatusClaim: false feasibility on failed candidate, wrong status, missing checks.
    false_feasible = StatusClaim(
        claim_id="clm-bad-sts-1",
        run_id=run.run_id,
        scenario_revision=run.scenario_revision,
        subject=failing_res.subject,
        synthetic=failing_res.synthetic,
        result_id=failing_res.result_id,
        technical_status=TechnicalStatus.PASSES_EVALUATED_CASES,
        evaluation_status=EvaluationStatus.VALID,
        site_evidence_status=failing_res.site_evidence_status,
        eligible=True,
        asserted_feasible=True,
    )
    out_sts = check_claim(false_feasible, ctx)
    assert not out_sts.accepted and "technical_status_mismatch" in out_sts.error_codes

    false_flag_only = StatusClaim(
        claim_id="clm-bad-sts-2",
        run_id=run.run_id,
        scenario_revision=run.scenario_revision,
        subject=failing_res.subject,
        synthetic=failing_res.synthetic,
        result_id=failing_res.result_id,
        technical_status=failing_res.technical_status,
        evaluation_status=failing_res.evaluation_status,
        site_evidence_status=failing_res.site_evidence_status,
        eligible=failing_res.ranking_eligibility.eligible,
        asserted_feasible=True,
    )
    out_false_feas = check_claim(false_flag_only, ctx)
    assert not out_false_feas.accepted and "false_feasibility" in out_false_feas.error_codes

    # 3. Wrong CauseOfFailureClaim: nonexistent / passing check, or tampered observed limit.
    wrong_check = CauseOfFailureClaim(
        claim_id="clm-bad-cof-1",
        run_id=run.run_id,
        scenario_revision=run.scenario_revision,
        subject=failing_res.subject,
        synthetic=failing_res.synthetic,
        result_id=failing_res.result_id,
        stage="couple",
        check_id="cost_reconciliation",  # passed check, never caused failure
        interval_or_sample="deterministic",
    )
    out_cof1 = check_claim(wrong_check, ctx)
    assert not out_cof1.accepted and "unknown_or_non_failing_check" in out_cof1.error_codes

    if isinstance(real_fail.check.observed, int | float):
        tampered_obs = CauseOfFailureClaim(
            claim_id="clm-bad-cof-2",
            run_id=run.run_id,
            scenario_revision=run.scenario_revision,
            subject=failing_res.subject,
            synthetic=failing_res.synthetic,
            result_id=failing_res.result_id,
            stage=real_fail.stage,
            check_id=real_fail.check.check_id,
            observed=float(real_fail.check.observed) + 12345.0,
            limit=real_fail.check.limit,
            unit=real_fail.check.unit,
            interval_or_sample=real_fail.interval_or_sample,
        )
        out_cof2 = check_claim(tampered_obs, ctx)
        assert not out_cof2.accepted and "check_value_mismatch" in out_cof2.error_codes

    # 4. Wrong ComparisonClaim: ranking an ineligible candidate, wrong boundary, bad denominator.
    comp_rec = explain_comparison(store, run.run_id)
    real_cmp = next(c for c in comp_rec.claims if isinstance(c, ComparisonClaim))

    bad_winner = real_cmp.model_copy(update={"winner_id": failing_res.subject})
    out_cmp1 = check_claim(bad_winner, ctx)
    assert not out_cmp1.accepted and "ineligible_winner" in out_cmp1.error_codes

    bad_ranked = real_cmp.model_copy(
        update={"ranked_candidate_ids": (*real_cmp.ranked_candidate_ids, failing_res.subject)}
    )
    out_cmp2 = check_claim(bad_ranked, ctx)
    assert not out_cmp2.accepted and "ineligible_candidate_ranked" in out_cmp2.error_codes

    bad_denom = real_cmp.model_copy(update={"sample_count": 9999})
    out_cmp3 = check_claim(bad_denom, ctx)
    assert not out_cmp3.accepted and "denominator_mismatch" in out_cmp3.error_codes

    # 5. Wrong OpenAssumptionClaim: claiming a known field is unknown, or wrong owner.
    active_row = next(a for a in ctx.assumptions_by_path.values() if a.status.value == "active")
    bad_open = OpenAssumptionClaim(
        claim_id="clm-bad-oas-1",
        run_id=run.run_id,
        scenario_revision=run.scenario_revision,
        subject="study",
        synthetic=ctx.synthetic,
        field_path=active_row.field_path,
        state="unknown",
    )
    out_oas = check_claim(bad_open, ctx)
    assert not out_oas.accepted and "not_open_assumption" in out_oas.error_codes

    real_unknown = next(
        (c for c in comp_rec.claims if isinstance(c, OpenAssumptionClaim) and c.owner),
        None,
    )
    assert real_unknown is not None
    bad_owner = real_unknown.model_copy(update={"owner": "nobody-owns-this"})
    out_owner = check_claim(bad_owner, ctx)
    assert not out_owner.accepted and "owner_mismatch" in out_owner.error_codes


@pytest.mark.requirement("V06")
def test_unresolved_numerical_status_cannot_claim_physical_infeasibility(tmp_path: Path) -> None:
    """P1: StatusClaim asserting physical infeasibility on unresolved_numerical fails check."""
    from datetime import UTC, datetime

    from tests.conftest import SYNTHETIC_SCENARIO

    from r3chain.domain.records.runs import RankingEligibility, Run, RunStatus, StageOutput
    from r3chain.domain.validation import load_scenario_file

    scenario, report = load_scenario_file(SYNTHETIC_SCENARIO)
    assert scenario is not None and report.executable
    store = Store(tmp_path / "store", clock=FakeClock())
    rev = store.add_revision(scenario, author="t", rationale="p1 test").revision_id
    now = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
    run_id = "run-num-fail-1"
    store.create_run(
        Run(
            run_id=run_id,
            scenario_revision=rev,
            input_hashes={},
            code_version="t",
            engine_versions={},
            seed=0,
            status=RunStatus.RUNNING,
            created_at=now,
        )
    )
    study_res = Result(
        result_id="res-num-fail-study",
        run_id=run_id,
        scenario_revision=rev,
        subject="study",
        synthetic=True,
        technical_status=TechnicalStatus.NOT_ESTABLISHED,
        evaluation_status=EvaluationStatus.UNRESOLVED_NUMERICAL,
        ranking_eligibility=RankingEligibility(eligible=False, reasons=("unresolved",)),
        stage_outputs=(
            StageOutput(stage="couple", stage_version="2.0.0", status=StageStatus.NON_CONVERGED),
        ),
    )
    cand_res = Result(
        result_id="res-num-fail-cand1",
        run_id=run_id,
        scenario_revision=rev,
        subject="cand-1",
        synthetic=True,
        technical_status=TechnicalStatus.NOT_ESTABLISHED,
        evaluation_status=EvaluationStatus.UNRESOLVED_NUMERICAL,
        ranking_eligibility=RankingEligibility(eligible=False, reasons=("non_converged",)),
    )
    store.add_result(study_res)
    store.add_result(cand_res)
    store.finalize_run(
        store.get_run(run_id).model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "finished_at": now,
                "result_hash": "sha256:" + "0" * 64,
            }
        )
    )

    ctx = build_verification_context(store, run_id)
    bad_p1 = StatusClaim(
        claim_id="clm-bad-p1",
        run_id=run_id,
        scenario_revision=rev,
        subject="cand-1",
        synthetic=True,
        result_id="res-num-fail-cand1",
        technical_status=TechnicalStatus.NOT_ESTABLISHED,
        evaluation_status=EvaluationStatus.UNRESOLVED_NUMERICAL,
        eligible=False,
        asserted_infeasible=True,
    )
    outcome = check_claim(bad_p1, ctx)
    assert not outcome.accepted and "numerical_reported_as_physical" in outcome.error_codes

    # ComparisonEntry with missing denominator fails check_claim too (P4).
    fake_cmp = ComparisonClaim(
        claim_id="clm-bad-p4",
        run_id=run_id,
        scenario_revision=rev,
        subject="study",
        synthetic=True,
        result_ids=("res-num-fail-cand1",),
        candidate_ids=("cand-1",),
        metric_id="indicative_route_cost_currency",
        boundary="route",
        unit="currency",
        ranking_policy_id="ranking-synthetic",
        preference_outcome="no_eligible_candidate",
        entries=(
            ComparisonEntry(
                candidate_id="cand-1",
                result_id="res-num-fail-cand1",
                eligible=False,
                denominator=10,
            ),
        ),
    )
    assert not check_claim(fake_cmp, ctx).accepted
    store.close()
