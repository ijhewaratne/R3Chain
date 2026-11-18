"""Unit, pipeline, reporting, and CLI tests for milestone M5 (uncertainty and decision rules)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import (
    POLICY_DIRECTORY,
    FakeClock,
    changed,
    policy_variant,
    repository_policies,
)

from r3chain.cli import main
from r3chain.domain import policies as policy_lib
from r3chain.domain.records.runs import Result, RunStatus
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.scenarios import (
    UncertaintyModel,
)
from r3chain.domain.status import StageStatus
from r3chain.domain.validation import parse_scenario
from r3chain.pipeline.runner import PipelineRunner
from r3chain.pipeline.stages import default_stages
from r3chain.reporting import build_evidence_report, render_markdown
from r3chain.store.store import Store, load_policies, policy_ref
from r3chain.uncertainty.policies import DecisionRuleSettings, UncertaintySettings


@pytest.fixture(scope="module")
def synthetic_m5_store(
    tmp_path_factory: pytest.TempPathFactory,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result]:
    """Share one warmed pipeline run across M5 integration, reporting, and CLI assertions."""
    root = tmp_path_factory.mktemp("m5")
    store_dir = root / "store"
    store = Store(store_dir, clock=FakeClock())
    for policy in load_policies(POLICY_DIRECTORY):
        store.register_policy(policy)
    raw = json.loads(json.dumps(_synthetic_data))
    revision = store.add_revision(
        Scenario.model_validate(raw), author="m5-test", rationale="m5 baseline"
    ).revision_id
    runner = PipelineRunner(
        store,
        default_stages(network_model=network_model),
        clock=FakeClock(),
        code_version_text="m5",
    )
    run, result = runner.run(revision, seed=7)
    return store_dir, store, runner, raw, run, result


def _stage(result: Result, name: str) -> Any:
    return next(item for item in result.stage_outputs if item.stage == name)


@pytest.mark.requirement("W13")
@pytest.mark.requirement("W18")
@pytest.mark.requirement("V01")
@pytest.mark.requirement("V02")
def test_synthetic_compare_runs_scenario_sweep_resilience_and_reports(
    synthetic_m5_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    store_dir, store, runner, _, run, result = synthetic_m5_store
    assert run.status is RunStatus.COMPLETED
    compare = _stage(result, "compare")
    assert compare.status is StageStatus.COMPLETED
    # uncertainty-base uses scenario_set + unknown O05 success_definition -> no fake probabilities.
    assert compare.payload["has_probabilities"] is False
    assert compare.payload["success_event_established"] is False
    assert compare.payload["sample_count"] == 9
    assert any("success_definition" in m.field_path for m in compare.missing_inputs)

    comp = compare.payload["comparison"]
    assert comp["total_paired_samples"] == 9
    assert len(comp["ordered_candidate_ids"]) == 11
    assert len(comp["decision_drivers"]) == 2
    assert {r["candidate_id"] for r in comp["ranking_stability"]} == set(comp["candidates"])
    # Every candidate's resilience cases (V02) report J/h/W, never probabilities.
    assert len(compare.payload["resilience"]) >= 11
    sample_cid = comp["ordered_candidate_ids"][0]
    res_ids = {r["case_id"] for r in compare.payload["resilience"][sample_cid]}
    assert res_ids == {
        "source_outage",
        "backup_loss",
        "base_load_loss",
        "heat_pump_electricity_loss",
    }

    # Candidate Result carries M5 KPIs and accounting checks; replay is identical.
    by_subject = {res.subject: res for res in store.get_results(run.run_id)}
    cand_kpis = {k.definition.kpi_id: k.value for k in by_subject[sample_cid].kpis}
    assert cand_kpis["conditional_service_probability_fraction"] is None
    assert cand_kpis["resilience_source_outage_unserved_heat_j"] == 0.0
    assert any(c.check_id == "uncertainty_sample_accounting" for c in by_subject[sample_cid].checks)
    outcome = runner.replay(run.run_id)
    assert outcome.identical and outcome.original_hash == outcome.replay_hash

    # Evidence report renders Markdown and JSON without invoking any model.
    report = build_evidence_report(store, run.run_id)
    md = render_markdown(report)
    assert "## Uncertainty, Regret & Decision Analysis" in md
    assert "### Deterministic Resilience Stress Cases (V02)" in md
    assert "### Connection Adoption Stages & Thresholds (W18)" in md
    assert len(compare.payload["adoption_thresholds"]) == 11

    # CLI compare & report commands work cleanly.
    store_flag = ["--store", str(store_dir)]
    assert main([*store_flag, "compare", run.run_id]) == 0
    cmp_out = capsys.readouterr().out
    assert "Uncertainty compare" in cmp_out and "Decision drivers" in cmp_out

    assert main([*store_flag, "compare", run.run_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sample_count"] == 9

    out_md = tmp_path / "evidence.md"
    assert main([*store_flag, "report", run.run_id, "--output", str(out_md)]) == 0
    assert "wrote " in capsys.readouterr().out
    assert "## Candidate Eligibility & Technical Separation" in out_md.read_text(encoding="utf-8")
    assert main([*store_flag, "report", run.run_id, "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == run.run_id


@pytest.mark.requirement("A14")
@pytest.mark.requirement("W02")
@pytest.mark.requirement("V03")
@pytest.mark.requirement("V04")
def test_probabilistic_correlated_point_estimate_and_progressive_policies_on_same_revision(
    synthetic_m5_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
) -> None:
    _, store, runner, raw, _, _ = synthetic_m5_store
    prov = {"provenance_type": "synthetic", "source_id": "syn-definitions", "locator": "M5 test"}
    pols = repository_policies()

    # Define probabilistic correlated uncertainty model + reviewed success & pre-drilling inputs.
    prob_model = {
        "schema_version": "0.2.0",
        "uncertainty_model_id": "uncertainty-base",
        "variables": [
            {
                "variable_id": "alpha-reservoir-temperature",
                "field_path": (
                    "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]"
                ),
                "unit": "K",
                "kind": "distribution",
                "distribution": {"family": "normal", "parameters": {"mean": 378.15, "std": 10.0}},
                "support": {"lower": 355.15, "upper": 398.15},
                "evidence": prov,
                "correlation_group": "subsurface",
            },
            {
                "variable_id": "discount-rate",
                "field_path": "economic_scenarios[econ-base].discount_rate_per_year",
                "unit": "1/a",
                "kind": "distribution",
                "distribution": {
                    "family": "triangular",
                    "parameters": {"low": 0.03, "mode": 0.04, "high": 0.07},
                },
                "support": {"lower": 0.02, "upper": 0.08},
                "evidence": prov,
                "correlation_group": "subsurface",
            },
        ],
        "correlation_groups": [
            {
                "group_id": "subsurface",
                "method": "rank_correlation",
                "pairs": [
                    {
                        "variable_a": "alpha-reservoir-temperature",
                        "variable_b": "discount-rate",
                        "rank_correlation": -0.5,
                    }
                ],
            }
        ],
        "success_definition": {
            "state": "known",
            "value": {
                "event": "Commercial doublet delivers >= 355 K within drawdown limit",
                "criteria": ["source_operated", "min_temperature_k"],
                "information_time": "post_drilling_test",
                "conditional_on": "exploration_drilling",
            },
            "provenance": prov,
        },
        "success_probability_fraction": {"state": "known", "value": 0.7, "provenance": prov},
        "failure_drilling_cost_fraction": {"state": "known", "value": 0.45, "provenance": prov},
        "abandonment_cost_currency": {"state": "known", "value": 600_000.0, "provenance": prov},
    }
    prob_raw = changed(raw, "uncertainty_models[uncertainty-base]", prob_model)
    # Keep sample counts compact (3) for fast pipeline execution while testing V03/V04/A14.
    fast_u = policy_variant(pols["uncertainty-synthetic"], sample_count=3)
    fast_u = fast_u.model_copy(update={"version": "test-fast"})
    fast_ref = store.register_policy(fast_u).to_json_dict()
    prob_raw = {
        **prob_raw,
        "policies": [
            fast_ref if p["policy_id"] == "uncertainty-synthetic" else p
            for p in prob_raw["policies"]
        ],
    }
    rev_prob = store.add_revision(
        Scenario.model_validate(prob_raw), author="test", rationale="probabilistic model"
    ).revision_id

    _, res_s1 = runner.run(rev_prob, seed=11)
    _, res_s2 = runner.run(rev_prob, seed=11)
    assert res_s1.deterministic_hash() == res_s2.deterministic_hash()
    cmp1 = _stage(res_s1, "compare").payload
    assert cmp1["has_probabilities"] is True
    assert cmp1["success_event_established"] is True
    first_cid = cmp1["comparison"]["ordered_candidate_ids"][0]
    first_risk = cmp1["comparison"]["candidates"][first_cid]
    assert first_risk["sample_account"]["conditional_service_probability"] == pytest.approx(1.0)
    assert first_risk["pre_drilling"] is not None
    assert (
        first_risk["pre_drilling"]["expected_pv_cost_currency"]
        > first_risk["pre_drilling"]["p_success"] * first_risk["pv_cost_quantiles"]["mean"]
    )

    # Point estimate mode (V03) runs on the SAME probabilistic uncertainty_model cleanly.
    pe_ref = policy_ref(pols["uncertainty-point-estimate"]).to_json_dict()
    pe_raw = {
        **prob_raw,
        "policies": [
            pe_ref if p["policy_id"] == "uncertainty-synthetic" else p for p in prob_raw["policies"]
        ],
    }
    rev_pe = store.add_revision(
        Scenario.model_validate(pe_raw), author="test", rationale="point estimate policy"
    ).revision_id
    _, res_pe = runner.run(rev_pe, seed=11)
    cmp_pe = _stage(res_pe, "compare").payload
    assert cmp_pe["mode"] == "point_estimate" and cmp_pe["sample_count"] == 1

    # Progressive allocation policy (V04) verifies prefix error vs full sample batch.
    prog_pol = policy_variant(
        pols["uncertainty-progressive"],
        progressive_initial_count=2,
        progressive_step_count=1,
        progressive_max_count=4,
    )
    prog_pol = prog_pol.model_copy(update={"version": "test-prog"})
    prog_ref = store.register_policy(prog_pol).to_json_dict()
    prog_raw = {
        **prob_raw,
        "policies": [
            prog_ref if p["policy_id"] == "uncertainty-synthetic" else p
            for p in prob_raw["policies"]
        ],
    }
    rev_prog = store.add_revision(
        Scenario.model_validate(prog_raw), author="test", rationale="progressive policy"
    ).revision_id
    _, res_prog = runner.run(rev_prog, seed=11)
    cmp_prog = _stage(res_prog, "compare").payload
    assert cmp_prog["mode"] == "progressive"
    assert cmp_prog["progressive"] is not None
    assert "relative_error_vs_full" in cmp_prog["progressive"]


def test_uncertainty_policies_migrations_and_validation_reject_bad_inputs(
    scenario_data: dict[str, Any],
) -> None:
    pols = repository_policies()
    assert UncertaintySettings.from_policy(pols["uncertainty-synthetic"]).mode == "uncertainty"
    assert (
        UncertaintySettings.from_policy(pols["uncertainty-point-estimate"]).mode == "point_estimate"
    )
    assert UncertaintySettings.from_policy(pols["uncertainty-progressive"]).mode == "progressive"
    assert (
        DecisionRuleSettings.from_policy(pols["ranking-synthetic"]).decision_metric
        == "lcoh_geothermal_delivered_currency_per_j"
    )

    bad_u = pols["uncertainty-synthetic"].model_copy(
        update={"parameters": {**pols["uncertainty-synthetic"].parameters, "mode": "bogus"}}
    )
    with pytest.raises(policy_lib.PolicyError, match="mode"):
        UncertaintySettings.from_policy(bad_u)

    # Migration 0.1.0 -> 0.2.0 works cleanly on UncertaintyModel.
    migrated_u = UncertaintyModel.model_validate(
        {**scenario_data["uncertainty_models"][0], "schema_version": "0.1.0"}
    )
    assert migrated_u.schema_version == "0.2.0"

    # Bad distribution family is rejected at whole-scenario validation time.
    bad_var = {
        **scenario_data["uncertainty_models"][0]["variables"][0],
        "kind": "distribution",
        "distribution": {"family": "cauchy", "parameters": {"scale": 1.0}},
        "scenario_values": [],
    }
    bad_scen = changed(
        scenario_data,
        "uncertainty_models[uncertainty-base].variables[alpha-reservoir-temperature]",
        bad_var,
    )
    _, rep = parse_scenario(bad_scen)
    assert any(i.code == "unsupported_distribution_family" for i in rep.errors)
