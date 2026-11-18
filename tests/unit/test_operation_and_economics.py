"""Unit and pipeline tests for chronological operation, reduction and economics (M4)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from tests.conftest import (
    POLICY_DIRECTORY,
    SYNTHETIC_SCENARIO,
    FakeClock,
    changed,
    repository_policies,
)

from r3chain.cli import main
from r3chain.coupling.cases import MemoNetworkModel
from r3chain.coupling.loop import CouplingResult, CouplingSettings, OperatingPoint, ShortfallCause
from r3chain.domain.evidence import Known, NotEstablished, Provenance, ProvenanceType, Unknown
from r3chain.domain.hashing import file_checksum
from r3chain.domain.records.runs import Result
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.scenarios import (
    AmountBasis,
    CostCategory,
    CostItem,
    EconomicScenario,
    FundingRule,
    PriceConvention,
)
from r3chain.domain.status import AdapterState, EvaluationStatus, StageStatus
from r3chain.domain.units import JOULES_PER_MWH
from r3chain.domain.validation import load_scenario_file, read_data_file
from r3chain.economics.cashflow import compare_to_baseline, evaluate_boundary_cashflows
from r3chain.economics.ledger import CostBoundary, LedgerRow, reconcile_ledger
from r3chain.operation.dispatch import RuleBasedDispatchStrategy, geothermal_spec_from_coupling
from r3chain.operation.reduction import measure_reduction_error
from r3chain.operation.series import resolve_series_sources
from r3chain.pipeline.runner import PipelineRunner
from r3chain.pipeline.stages import default_stages
from r3chain.ports.dispatch import (
    DispatchGeothermalSpec,
    DispatchRequest,
)
from r3chain.store.store import Store, load_policies


def _stage(result: Result, name: str) -> Any:
    return next(item for item in result.stage_outputs if item.stage == name)


@pytest.mark.requirement("W08")
@pytest.mark.requirement("W16")
def test_geothermal_capacity_uses_max_coupled_heat_and_marks_limit_proven(
    scenario_data: dict[str, Any],
) -> None:
    case_ids = {item["operating_case_id"] for item in scenario_data["operating_cases"]}
    assert {"winter-design", "shoulder-season", "summer-minimum", "source-outage"} <= case_ids

    settings = CouplingSettings.from_policy(repository_policies()["numerical-coupling-default"])

    # Demand-following coupled points: plant output never exceeds demand; deficit > 0 proves limit.
    def point(
        demand_w: float,
        plant_w: float,
        loss_w: float,
        *,
        deficit_w: float = 0.0,
        fail_temp: bool = False,
        pump_w: float = 10_000.0,
        comp_w: float = 0.0,
    ) -> CouplingResult:
        failed = fail_temp or deficit_w > 0.0
        causes: tuple[ShortfallCause, ...] = (
            (ShortfallCause.INSUFFICIENT_TEMPERATURE,)
            if fail_temp
            else ((ShortfallCause.INSUFFICIENT_POWER,) if deficit_w > 0.0 else ())
        )
        return CouplingResult(
            coupling_case_id=f"c-{int(demand_w)}",
            input_hash="sha256:" + "0" * 64,
            state=AdapterState.PHYSICAL_LIMIT_EXCEEDED if failed else AdapterState.COMPLETED,
            reason="limit reached" if failed else "",
            arrangement="direct_exchanger",
            performance_basis="equipment_rating",
            synthetic=True,
            engines={"source": "s", "network": "n"},
            settings=settings,
            settings_origin="numerical-coupling-default@3",
            shortfall_causes=causes,
            operating_point=OperatingPoint(
                source_operated=not fail_temp,
                geothermal_mass_flow_kg_s=10.0,
                geothermal_extracted_heat_w=plant_w,
                source_pump_electricity_w=pump_w,
                network_mass_flow_kg_s=10.0,
                required_supply_temperature_k=353.15,
                demand_heat_w=demand_w,
                plant_heat_w=plant_w,
                backup_heat_w=0.0,
                deficit_heat_w=deficit_w,
                compressor_electricity_w=comp_w,
                transmission_heat_loss_w=loss_w,
            ),
        )

    # Shoulder covered completely + winter hitting physical limit (deficit > 0) -> limit proven.
    proven = geothermal_spec_from_coupling(
        "cand-limit",
        [
            point(1_300_000.0, 1_300_000.0, 30_000.0, pump_w=40_000.0, comp_w=100_000.0),
            point(
                3_100_000.0,
                2_580_000.0,
                30_000.0,
                deficit_w=520_000.0,
                pump_w=160_000.0,
                comp_w=240_000.0,
            ),
        ],
    )
    assert proven.max_delivered_heat_w == pytest.approx(2_550_000.0)
    assert proven.max_plant_handover_heat_w == pytest.approx(2_580_000.0)
    assert proven.capacity_basis == "limit_proven"
    assert proven.capacity_series_w == ()
    assert [
        (p.plant_heat_w, p.source_pump_electricity_w, p.compressor_electricity_w)
        for p in proven.electricity_curve
    ] == [
        (1_300_000.0, 40_000.0, 100_000.0),
        (2_580_000.0, 160_000.0, 240_000.0),
    ]

    # Every coupled case covered with zero deficit -> capacity is only a lower bound.
    lower = geothermal_spec_from_coupling(
        "cand-lower",
        [point(1_300_000.0, 1_300_000.0, 30_000.0), point(3_100_000.0, 3_100_000.0, 30_000.0)],
    )
    assert lower.max_delivered_heat_w == pytest.approx(3_070_000.0)
    assert lower.capacity_basis == "lower_bound"

    # Temperature failure yields 0 compliant geothermal output.
    failed_spec = geothermal_spec_from_coupling(
        "cand-fail-temp",
        [point(1_200_000.0, 500_000.0, 10_000.0, fail_temp=True)],
    )
    assert failed_spec.max_delivered_heat_w == 0.0
    assert failed_spec.capacity_basis == "limit_proven"

    # Dispatch consumes electricity without tariff -> NotEstablished.
    with pytest.raises(NotEstablished):
        RuleBasedDispatchStrategy().dispatch(
            DispatchRequest(
                request_id="no-tariff",
                operating_scenario_id="op",
                starts_utc=(datetime(2026, 1, 1, tzinfo=UTC),),
                durations_s=(3600.0,),
                demand_heat_w=(1_000_000.0,),
                geothermal=DispatchGeothermalSpec(
                    unit_id="g",
                    max_delivered_heat_w=1_000_000.0,
                    max_plant_handover_heat_w=1_000_000.0,
                    source_electricity_per_plant_heat_ratio=0.05,
                ),
            )
        )


@pytest.mark.requirement("W08")
def test_interval_reduction_measures_error_and_rejects_excessive_approximation() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    count = 24
    # Sharp spike every 6th interval missed by stride 3 -> large relative error -> rejected.
    peaky_demand = [
        1_500_000.0 if h in {1, 4, 7, 10, 13, 16, 19, 22} else 300_000.0 for h in range(count)
    ]
    req = DispatchRequest(
        request_id="red-test",
        operating_scenario_id="op",
        starts_utc=tuple(start + timedelta(hours=h) for h in range(count)),
        durations_s=tuple(3600.0 for _ in range(count)),
        demand_heat_w=tuple(peaky_demand),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo", max_delivered_heat_w=1_000_000.0, max_plant_handover_heat_w=1_000_000.0
        ),
    )
    strategy = RuleBasedDispatchStrategy()
    chosen, assessment = measure_reduction_error(req, strategy, stride=3, max_error_fraction=0.01)
    assert not assessment.accepted
    assert assessment.max_relative_error_fraction > 0.01
    assert "Exceeds" in assessment.reason or "exceeds" in assessment.reason
    # Rejected reduction falls back to the full chronological result.
    assert chosen.to_json_dict() == strategy.dispatch(req).to_json_dict()

    # Smooth constant series is exact and accepted under 1% tolerance.
    smooth = req.model_copy(update={"demand_heat_w": tuple(800_000.0 for _ in range(count))})
    _, ok = measure_reduction_error(smooth, strategy, stride=3, max_error_fraction=0.01)
    assert ok.accepted and ok.max_relative_error_fraction == pytest.approx(0.0)


@pytest.mark.requirement("W28")
def test_series_sources_verify_origin_and_load_json_and_parquet(tmp_path: Path) -> None:
    study = tmp_path / "study"
    shutil.copytree(SYNTHETIC_SCENARIO.parent, study)
    raw = read_data_file(study / "scenario.yaml")

    # Missing allowed_use prevents loading the time series file.
    bad_datasets = []
    for ds in raw["datasets"]:
        if ds["dataset_id"] == "syn-series-2026":
            stripped = {**ds}
            stripped.pop("allowed_use")
            bad_datasets.append(stripped)
        else:
            bad_datasets.append(ds)
    data, problems = resolve_series_sources({**raw, "datasets": bad_datasets}, study)
    assert problems and "allowed_use" in problems[0][1]
    assert data["time_series"] == []

    # Corrupted series checksum is reported cleanly when loading a scenario file.
    series_file = study / "series" / "annual_2026.json"
    series_file.write_text(series_file.read_text(encoding="utf-8") + " ", encoding="utf-8")
    _, report = load_scenario_file(study / "scenario.yaml")
    assert not report.executable
    assert any(issue.code == "series_import" for issue in report.errors)

    # Parquet time series with canonical unit & kind metadata loads identically to JSON.
    pq_path = tmp_path / "demand.parquet"
    starts = [datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=h) for h in range(4)]
    frame = pd.DataFrame(
        {
            "start_utc": [t.isoformat() for t in starts],
            "duration_s": [3600.0] * 4,
            "value": [1_000_000.0, 1_100_000.0, 900_000.0, 800_000.0],
        }
    )
    frame.attrs = {"unit": "W", "kind": "interval_mean"}
    frame.to_parquet(pq_path)
    pq_raw = {
        "datasets": [
            {
                "dataset_id": "ds-pq",
                "version": "1",
                "kind": "timeseries",
                "title": "Parquet demand",
                "source_uri": "demand.parquet",
                "checksum": file_checksum(pq_path),
                "owner": "test",
                "acquired_at": "2026-01-01T00:00:00Z",
                "allowed_use": "public",
                "access_group": "public",
                "quality": "unreviewed",
                "synthetic": True,
            }
        ],
        "series_sources": [{"series_id": "pq-demand", "dataset_id": "ds-pq"}],
    }
    resolved_pq, pq_problems = resolve_series_sources(pq_raw, tmp_path)
    assert pq_problems == []
    assert resolved_pq["time_series"][0]["series"]["values"] == [
        1_000_000.0,
        1_100_000.0,
        900_000.0,
        800_000.0,
    ]


@pytest.mark.requirement("W15")
@pytest.mark.requirement("W35")
def test_plant_and_delivered_boundaries_stay_separate_and_funded_is_labelled() -> None:
    provenance = Provenance(provenance_type=ProvenanceType.SYNTHETIC, source_id="syn-definitions")

    def known(value: float) -> Known[float]:
        return Known(value=value, provenance=provenance)

    econ = EconomicScenario(
        economic_scenario_id="econ-b",
        price_base_year=2026,
        currency="EUR",
        price_convention=PriceConvention.REAL,
        vat_included=False,
        carbon_monetised=False,
        discount_rate_per_year=known(0.04),
        horizon_years=known(20.0),
        funding_rules=(
            FundingRule(
                rule_id="geo-grant",
                title="20% drilling grant",
                effective_date=datetime(2026, 1, 1, tzinfo=UTC).date(),
                source_id="syn-definitions",
                reviewed=False,
                grant_fraction=0.2,
                locations=("source",),
                categories=(CostCategory.CAPEX,),
                applies_to_replacements=False,
            ),
        ),
    )
    rows = (
        LedgerRow(
            item_id="wells",
            asset_id="wells",
            category=CostCategory.CAPEX,
            cost_owner="system",
            location="source",
            included=True,
            amount_currency=10_000_000.0,
            amount_basis=AmountBasis.TOTAL,
            lifetime_years=30.0,
            origin="f",
        ),
        LedgerRow(
            item_id="route",
            asset_id="route",
            category=CostCategory.CAPEX,
            cost_owner="system",
            location="route",
            included=True,
            amount_currency=2_000_000.0,
            amount_basis=AmountBasis.TOTAL,
            lifetime_years=40.0,
            origin="f",
        ),
        LedgerRow(
            item_id="backup-opex",
            asset_id="backup",
            category=CostCategory.VARIABLE_COST,
            cost_owner="operation",
            location="existing",
            included=True,
            amount_currency=100_000.0,
            amount_basis=AmountBasis.PER_YEAR,
            origin="f",
        ),
    )
    ledger = reconcile_ledger("cand", rows)
    plant_b = evaluate_boundary_cashflows(
        econ, ledger, boundary=CostBoundary.PLANT, annual_heat_j=3.0e13
    )
    geo_b = evaluate_boundary_cashflows(
        econ, ledger, boundary=CostBoundary.GEOTHERMAL_DELIVERED, annual_heat_j=2.8e13
    )
    sys_b = evaluate_boundary_cashflows(
        econ,
        ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=3.5e13,
        annual_revenue_currency=500_000.0,
    )
    funded_sys = evaluate_boundary_cashflows(
        econ,
        ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=3.5e13,
        annual_revenue_currency=500_000.0,
        funding_rules=econ.funding_rules,
        case_label="funded",
    )
    assert plant_b.lcoh_currency_per_j is not None and geo_b.lcoh_currency_per_j is not None
    assert plant_b.lcoh_currency_per_j < geo_b.lcoh_currency_per_j
    assert sys_b.case_label == "unsubsidised" and funded_sys.case_label == "funded"
    assert sys_b.capex_currency == pytest.approx(12_000_000.0)
    assert funded_sys.capex_currency == pytest.approx(10_000_000.0)

    # Baseline comparison keeps unsubsidised separate and avoids double-counting avoided fuel.
    base_ledger = reconcile_ledger(
        "baseline",
        (
            LedgerRow(
                item_id="base-fuel",
                asset_id="boiler",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="existing",
                included=True,
                amount_currency=900_000.0,
                amount_basis=AmountBasis.PER_YEAR,
                origin="f",
            ),
        ),
    )
    base_sys = evaluate_boundary_cashflows(
        econ,
        base_ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=3.5e13,
        annual_revenue_currency=500_000.0,
    )
    comp = compare_to_baseline(
        baseline_id="baseline-existing",
        baseline=base_sys,
        candidate=sys_b,
        geothermal_displaced_heat_j=2.8e13,
        baseline_annual_heat_j=3.5e13,
        candidate_annual_heat_j=3.5e13,
    )
    assert comp.incremental_discounted_cost_currency == pytest.approx(
        sys_b.pv_cost_currency - base_sys.pv_cost_currency
    )
    # Identical heat revenue on both sides means NPV improvement equals minus incremental cost.
    assert comp.npv_vs_baseline_currency == pytest.approx(
        -comp.incremental_discounted_cost_currency
    )


@pytest.fixture(scope="module")
def synthetic_m4_store(
    tmp_path_factory: pytest.TempPathFactory,
    network_model: MemoNetworkModel,
    _synthetic_data: dict[str, Any],
) -> Iterator[tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result]]:
    """Share one warmed store and full pipeline run across M4 integration and CLI tests."""
    store_dir = tmp_path_factory.mktemp("m4") / "store"
    store = Store(store_dir, clock=FakeClock())
    for policy in load_policies(POLICY_DIRECTORY):
        store.register_policy(policy)
    scenario = Scenario.model_validate(_synthetic_data)
    revision_id = store.add_revision(scenario, author="test", rationale="m4").revision_id
    runner = PipelineRunner(
        store, default_stages(network_model=network_model), clock=FakeClock(), code_version_text="t"
    )
    run, result = runner.run(revision_id, raise_on_error=True)
    yield store_dir, store, runner, _synthetic_data, run, result
    store.close()


@pytest.mark.requirement("W08")
@pytest.mark.requirement("W15")
@pytest.mark.requirement("W33")
@pytest.mark.requirement("A10")
@pytest.mark.requirement("A17")
def test_pipeline_operate_and_cost_stages_merge_kpis_and_replay_identically(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
) -> None:
    _, store, runner, _, run, result = synthetic_m4_store

    operate = _stage(result, "operate")
    cost = _stage(result, "cost")
    assert operate.status is StageStatus.COMPLETED
    assert cost.status is StageStatus.COMPLETED
    assert _stage(result, "compare").status is StageStatus.COMPLETED
    assert any("thermal decline" in line for line in cost.payload["not_modelled"])
    assert any("piecewise-linearly" in line for line in operate.payload["not_modelled"])
    assert any("scaled proportionally" in line for line in operate.payload["not_modelled"])

    # Replay reproduces the identical deterministic hash across all M4 stages.
    outcome = runner.replay(run.run_id)
    assert outcome.identical and outcome.original_hash == outcome.replay_hash

    by_subject = {res.subject: res for res in store.get_results(run.run_id)}
    assessments = {
        item["candidate_id"]: item for item in _stage(result, "assess").payload["assessments"]
    }
    by_name = {item["label"]: cid for cid, item in assessments.items()}

    # Alpha-direct proves ~2.55 MW capacity in winter-design; alpha-hp covers winter cleanly.
    alpha_direct_id = by_name["alpha to conn-west by route 1, plant-direct"]
    alpha_hp_id = by_name["alpha to conn-west by route 1, plant-heat-pump"]
    assert operate.payload["candidates"][alpha_direct_id]["capacity_basis"] == "limit_proven"
    assert operate.payload["candidates"][alpha_direct_id]["totals"]["reference_capacity_w"] > 2.5e6
    assert (
        operate.payload["candidates"][alpha_direct_id]["totals"]["source_pump_electricity_j"]
        < 1_130.0 * JOULES_PER_MWH
    )
    assert operate.payload["candidates"][alpha_hp_id]["capacity_basis"] == "lower_bound"
    assert operate.payload["candidates"][alpha_hp_id]["totals"]["reference_capacity_w"] > 3.0e6

    # Beta-direct fails temperature -> 0 heat & undefined LCOH; beta-hp runs up to ~1.48 MW.
    beta_direct_id = by_name["beta to conn-west by route 1, plant-direct"]
    beta_hp_id = by_name["beta to conn-west by route 1, plant-heat-pump"]
    beta_direct_kpis = {k.definition.kpi_id: k for k in by_subject[beta_direct_id].kpis}
    assert beta_direct_kpis["annual_geothermal_delivered_heat_j"].value == 0.0
    assert beta_direct_kpis["lcoh_geothermal_delivered_currency_per_j"].value is None
    assert "indicative_route_cost_currency" in beta_direct_kpis

    beta_hp_kpis = {k.definition.kpi_id: k for k in by_subject[beta_hp_id].kpis}
    assert (beta_hp_kpis["annual_geothermal_delivered_heat_j"].value or 0.0) > 0.0
    assert beta_hp_kpis["lcoh_geothermal_delivered_currency_per_j"].value is not None

    # W33: gamma route 2 compares linked demand customers with vs without extra demand.
    comparisons = cost.payload["route_demand_comparisons"]
    assert len(comparisons) == 6
    first = comparisons[0]
    assert first["linked_customer_count"] == 3
    assert first["with_demand_geothermal_delivered_j"] > first["base_geothermal_delivered_j"]
    assert (
        first["with_demand_lcoh_geothermal_delivered_currency_per_j"]
        < first["base_lcoh_geothermal_delivered_currency_per_j"]
    )


@pytest.mark.requirement("A15")
def test_changing_economic_discount_rate_recomputes_only_cost_stage(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
) -> None:
    _, store, runner, raw, _, _ = synthetic_m4_store
    higher_rate = changed(
        raw,
        "economic_scenarios[econ-base].discount_rate_per_year",
        {
            "state": "known",
            "value": 0.06,
            "provenance": {"provenance_type": "synthetic", "source_id": "syn-definitions"},
        },
    )
    rev2 = store.add_revision(
        Scenario.model_validate(higher_rate), author="test", rationale="6 percent"
    ).revision_id
    run2, _ = runner.run(rev2)
    hits = {rec.stage: rec.cache_hit for rec in run2.stages}
    assert hits["operate"] is True
    assert hits["assess"] is True
    assert hits["cost"] is False


@pytest.mark.requirement("A12")
def test_unknown_economic_item_leaves_candidate_cost_not_established(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
) -> None:
    _, store, runner, raw, _, base_result = synthetic_m4_store
    path = "economic_scenarios[econ-base].cost_items[wells-alpha-capex].amount"
    modified = changed(
        raw,
        path,
        {"state": "unknown", "reason": "drilling tender pending", "owner": "economics lead"},
    )
    rev = store.add_revision(
        Scenario.model_validate(modified), author="test", rationale="unknown alpha capex"
    ).revision_id
    run, result = runner.run(rev)
    cost = _stage(result, "cost")
    assert cost.status is StageStatus.COMPLETED
    assert any(item.field_path == path for item in cost.missing_inputs)

    assessments = {
        item["candidate_id"]: item for item in _stage(base_result, "assess").payload["assessments"]
    }
    alpha_hp_id = next(
        cid
        for cid, item in assessments.items()
        if item["label"] == "alpha to conn-west by route 1, plant-heat-pump"
    )
    assert cost.payload["candidates"][alpha_hp_id]["status"] == "not_established"
    by_subject = {res.subject: res for res in store.get_results(run.run_id)}
    alpha_hp_result = by_subject[alpha_hp_id]
    assert alpha_hp_result.evaluation_status is EvaluationStatus.NOT_EVALUATED
    assert not alpha_hp_result.ranking_eligibility.eligible
    assert "economic evaluation is not established" in alpha_hp_result.ranking_eligibility.reasons


def test_invalid_cashflow_inputs_return_invalid_input_status_not_tool_failure(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
) -> None:
    from r3chain.pipeline.operation_cost import CostStage
    from r3chain.pipeline.runner import ScenarioNotExecutableError, StageContext

    _, store, runner, raw, _, base_result = synthetic_m4_store
    assessments = {
        item["candidate_id"]: item for item in _stage(base_result, "assess").payload["assessments"]
    }
    alpha_hp_id = next(
        cid
        for cid, item in assessments.items()
        if item["label"] == "alpha to conn-west by route 1, plant-heat-pump"
    )
    upstream = {out.stage: out for out in base_result.stage_outputs}
    # Validation blocks fractional CAPEX lifetime before execution, while CostStage also maps any
    # cash-flow ValueError per-candidate to invalid_input (never tool_failure).
    prov = {"provenance_type": "synthetic", "source_id": "syn-definitions"}
    bad_cand = changed(
        raw,
        "economic_scenarios[econ-base].cost_items[wells-alpha-capex].lifetime_years",
        {"state": "known", "value": 12.5, "provenance": prov},
    )
    bad_cand_scenario = Scenario.model_validate(bad_cand)
    rev_cand = store.add_revision(
        bad_cand_scenario, author="test", rationale="fractional alpha life"
    ).revision_id
    with pytest.raises(ScenarioNotExecutableError):
        runner.run(rev_cand)

    cost_c = CostStage().run(
        StageContext(
            scenario=bad_cand_scenario,
            revision_id=rev_cand,
            policies=repository_policies(),
            seed=0,
            upstream=upstream,
        )
    )
    assert cost_c.status is StageStatus.COMPLETED
    assert cost_c.payload["candidates"][alpha_hp_id]["status"] == "invalid_input"
    cand_subj = next(s for s in cost_c.payload["subject_results"] if s["subject"] == alpha_hp_id)
    assert cand_subj["evaluation_status"] == EvaluationStatus.INVALID_INPUT.value
    assert "lifetime_years" in cand_subj["ranking_ineligibility_reason"]
    merged = {
        r.subject: r for r in runner._subject_results({**upstream, "cost": cost_c}, base_result)
    }
    assert merged[alpha_hp_id].evaluation_status is EvaluationStatus.INVALID_INPUT
    assert not merged[alpha_hp_id].ranking_eligibility.eligible

    # Baseline fractional horizon -> CostStage finishes with StageStatus.INVALID_INPUT.
    bad_horizon = changed(
        raw,
        "economic_scenarios[econ-base].horizon_years",
        {"state": "known", "value": 15.5, "provenance": prov},
    )
    cost_h = CostStage().run(
        StageContext(
            scenario=Scenario.model_validate(bad_horizon),
            revision_id="rev-horizon",
            policies=repository_policies(),
            seed=0,
            upstream=upstream,
        )
    )
    assert cost_h.status is StageStatus.INVALID_INPUT
    assert "horizon_years" in cost_h.reason


@pytest.mark.requirement("W33")
@pytest.mark.requirement("W35")
def test_economics_cli_prints_boundaries_funded_and_demand_comparisons(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
    capsys: pytest.CaptureFixture[str],
) -> None:
    store_dir, _, _, _, run, _ = synthetic_m4_store
    store_flag = ["--store", str(store_dir)]

    code = main([*store_flag, "economics", run.run_id])
    econ_out = capsys.readouterr().out
    assert code == 0
    assert "Baseline  baseline-existing" in econ_out
    assert (
        "LCOH plant:" in econ_out
        and "geo delivered:" in econ_out
        and "system delivered:" in econ_out
    )
    assert "funded case:" in econ_out
    assert "limit proven" in econ_out and "lower bound" in econ_out
    assert "Not modelled:" in econ_out
    assert "Routes with linked additional customers (6)" in econ_out

    code = main([*store_flag, "economics", run.run_id, "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert code == 0 and "operate" in doc and "cost" in doc

    assert repository_policies()["dispatch-synthetic"].kind.value == "dispatch"
    assert isinstance(
        CostItem.model_validate(
            {
                "item_id": "x",
                "asset_id": "x",
                "category": "capex",
                "cost_owner": "system",
                "location": "source",
                "included": True,
                "amount": Unknown(reason="tbd", owner="owner").to_json_dict(),
                "amount_basis": "total",
                "currency": "EUR",
                "price_year": 2026,
                "price_convention": "real",
            }
        ).amount,
        Unknown,
    )


def test_capacity_cases_drive_capacity_and_diagnostics_without_disqualifying_east_connection(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
    capsys: pytest.CaptureFixture[str],
) -> None:
    store_dir, store, _, _, run, result = synthetic_m4_store
    assessments = _stage(result, "assess").payload["assessments"]
    ranking = _stage(result, "assess").payload["ranking"]
    # Moving winter-design to capacity_case_ids restores the 11 ranked candidates from M3.
    assert ranking["eligible_count"] == 11

    by_name = {item["label"]: item for item in assessments}
    alpha_west_direct = by_name["alpha to conn-west by route 1, plant-direct"]
    assert alpha_west_direct["technical_status"] == "passes_evaluated_cases"
    assert alpha_west_direct["ranking_eligibility"]["eligible"] is True
    assert alpha_west_direct["capacity_cases"][0]["outcome"] == "limit_found"

    # At conn-east, winter-design ends with network:positive_absolute_pressure (no operating point).
    # Capacity remains lower_bound and a diagnostic records the case and reason on the candidate.
    alpha_east_direct = by_name["alpha to conn-east by route 1, plant-direct"]
    assert alpha_east_direct["capacity_cases"][0]["outcome"] == "not_established"
    assert "positive_absolute_pressure" in alpha_east_direct["capacity_cases"][0]["reason"]
    by_subject = {res.subject: res for res in store.get_results(run.run_id)}
    east_res = by_subject[alpha_east_direct["candidate_id"]]
    assert any(
        "winter-design" in line and "positive_absolute_pressure" in line
        for line in east_res.diagnostics
    )
    operate = _stage(result, "operate").payload
    assert (
        operate["candidates"][alpha_east_direct["candidate_id"]]["capacity_basis"] == "lower_bound"
    )

    # Both candidates and economics CLI print the capacity case outcomes.
    code = main(["--store", str(store_dir), "candidates", run.run_id])
    cand_out = capsys.readouterr().out
    assert code == 0 and "limit found" in cand_out and "not established" in cand_out

    code = main(["--store", str(store_dir), "economics", run.run_id])
    econ_out = capsys.readouterr().out
    assert code == 0 and "limit found" in econ_out and "not established" in econ_out


@pytest.mark.requirement("A15")
def test_silent_choices_scenario_selection_cache_and_reconciliation_are_explicit(
    synthetic_m4_store: tuple[Path, Store, PipelineRunner, dict[str, Any], Any, Result],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from r3chain.domain import policies as policy_lib
    from r3chain.economics.policies import EconomicSettings
    from r3chain.pipeline.operation_cost import CostStage, OperateStage
    from r3chain.pipeline.runner import StageContext

    _, store, runner, raw, _, base_result = synthetic_m4_store
    base_pols = repository_policies()

    # 1. Fractional route_lifetime_years is rejected by EconomicSettings.from_policy.
    bad_econ_pol = base_pols["economic-synthetic"].model_copy(
        update={
            "parameters": {
                **base_pols["economic-synthetic"].parameters,
                "route_lifetime_years": 39.5,
            }
        }
    )
    with pytest.raises(policy_lib.PolicyError, match="route_lifetime_years"):
        EconomicSettings.from_policy(bad_econ_pol)

    # 2. Multiple operating / economic scenarios without selector -> INVALID_INPUT naming choices;
    # explicit selector -> selects cleanly; unknown selector -> INVALID_INPUT.
    upstream = {out.stage: out for out in base_result.stage_outputs}
    base_scen = Scenario.model_validate(raw)
    op2 = base_scen.operating_scenarios[0].model_copy(update={"operating_scenario_id": "op-alt"})
    scen_multi_op = base_scen.model_copy(
        update={"operating_scenarios": (base_scen.operating_scenarios[0], op2)}
    )
    out_ambig_op = OperateStage().run(
        StageContext(
            scenario=scen_multi_op,
            revision_id="rev-multi-op",
            policies=base_pols,
            seed=0,
            upstream=upstream,
        )
    )
    assert out_ambig_op.status is StageStatus.INVALID_INPUT
    assert "operation-base" in out_ambig_op.reason and "op-alt" in out_ambig_op.reason

    disp_selected = base_pols["dispatch-synthetic"].model_copy(
        update={
            "parameters": {
                **base_pols["dispatch-synthetic"].parameters,
                "operating_scenario_id": "op-alt",
            }
        }
    )
    out_sel_op = OperateStage().run(
        StageContext(
            scenario=scen_multi_op,
            revision_id="rev-sel-op",
            policies={**base_pols, "dispatch-synthetic": disp_selected},
            seed=0,
            upstream=upstream,
        )
    )
    assert out_sel_op.status is StageStatus.COMPLETED
    assert out_sel_op.payload["operating_scenario_id"] == "op-alt"

    econ2 = base_scen.economic_scenarios[0].model_copy(update={"economic_scenario_id": "econ-alt"})
    scen_multi_ec = base_scen.model_copy(
        update={"economic_scenarios": (base_scen.economic_scenarios[0], econ2)}
    )
    out_ambig_ec = CostStage().run(
        StageContext(
            scenario=scen_multi_ec,
            revision_id="rev-multi-ec",
            policies=base_pols,
            seed=0,
            upstream=upstream,
        )
    )
    assert out_ambig_ec.status is StageStatus.INVALID_INPUT
    assert "econ-base" in out_ambig_ec.reason and "econ-alt" in out_ambig_ec.reason

    econ_selected = base_pols["economic-synthetic"].model_copy(
        update={
            "parameters": {
                **base_pols["economic-synthetic"].parameters,
                "economic_scenario_id": "econ-alt",
            }
        }
    )
    out_sel_ec = CostStage().run(
        StageContext(
            scenario=scen_multi_ec,
            revision_id="rev-sel-ec",
            policies={**base_pols, "economic-synthetic": econ_selected},
            seed=0,
            upstream=upstream,
        )
    )
    assert out_sel_ec.status is StageStatus.COMPLETED
    assert out_sel_ec.payload["economic_scenario_id"] == "econ-alt"

    # 3. Two candidates where one has TWO duplicate ownership errors still completes CostStage
    # (2 errors == 2 candidates total, so counting error messages instead of candidates failed).
    from r3chain.economics.ledger import ReconciliationTable
    from r3chain.pipeline import operation_cost as op_cost_mod

    two_cands = [item for item in upstream["candidates"].payload["candidates"][:2]]
    alpha_cand_id = str(two_cands[0]["candidate_id"])
    upstream_two = {
        **upstream,
        "candidates": upstream["candidates"].model_copy(
            update={"payload": {**upstream["candidates"].payload, "candidates": two_cands}}
        ),
    }
    orig_assemble = op_cost_mod.assemble_candidate_ledger

    def _two_dup_for_first(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("subject_id") == alpha_cand_id:
            return ReconciliationTable(
                subject_id=alpha_cand_id,
                rows=(),
                duplicate_ownership_errors=("dup error 1", "dup error 2"),
            )
        return orig_assemble(*args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(op_cost_mod, "assemble_candidate_ledger", _two_dup_for_first)
        out_dup = CostStage().run(
            StageContext(
                scenario=base_scen,
                revision_id="rev-dup",
                policies=base_pols,
                seed=0,
                upstream=upstream_two,
            )
        )
    assert out_dup.status is StageStatus.COMPLETED
    assert out_dup.payload["candidates"][alpha_cand_id]["status"] == "invalid_input"
    assert any(v["status"] == "completed" for v in out_dup.payload["candidates"].values())

    # 4. Changing ranking policy leaves operate and cost cached (OperateStage drops assess dep).
    assert "assess" not in OperateStage.requires
    new_ranking = base_pols["ranking-synthetic"].model_copy(
        update={
            "version": "99",
            "parameters": {
                **base_pols["ranking-synthetic"].parameters,
                "tie_tolerance_fraction": 0.05,
            },
        }
    )
    ref = store.register_policy(new_ranking)
    new_pol_refs = [
        ref.to_json_dict() if p["policy_id"] == "ranking-synthetic" else p for p in raw["policies"]
    ]
    rev_rank = store.add_revision(
        Scenario.model_validate({**raw, "policies": new_pol_refs}),
        author="test",
        rationale="change ranking only",
    ).revision_id
    run_rank, _ = runner.run(rev_rank)
    hits = {rec.stage: rec.cache_hit for rec in run_rank.stages}
    assert hits["assess"] is False
    assert hits["operate"] is True
    assert hits["cost"] is True

    # 5. _subject_results merges identically even when operate precedes assess in outputs.
    reordered = {
        "operate": upstream["operate"],
        "assess": upstream["assess"],
        "cost": upstream["cost"],
    }
    forward = {r.subject: r for r in runner._subject_results(upstream, base_result)}
    backward = {r.subject: r for r in runner._subject_results(reordered, base_result)}
    for subj, f_res in forward.items():
        b_res = backward[subj]
        assert f_res.evaluation_status is b_res.evaluation_status
        assert f_res.technical_status is b_res.technical_status
        assert f_res.ranking_eligibility.eligible is b_res.ranking_eligibility.eligible
        assert set(f_res.ranking_eligibility.reasons) == set(b_res.ranking_eligibility.reasons)
