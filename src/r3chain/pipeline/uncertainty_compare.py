"""Pipeline stage for M5: joint uncertainty sampling, resilience cases, and decision rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from r3chain.coupling.cases import MemoNetworkModel, evaluate_case
from r3chain.coupling.loop import CouplingResult, ShortfallCause
from r3chain.domain import policies
from r3chain.domain.assumptions import missing_inputs
from r3chain.domain.evidence import MissingInput, NotEstablished
from r3chain.domain.paths import PatchOperation, apply_patch, get_at
from r3chain.domain.records.common import value_or_none
from r3chain.domain.records.network import Route
from r3chain.domain.records.plant import CouplingCase
from r3chain.domain.records.runs import (
    Check,
    CheckOutcome,
    KpiValue,
    Policy,
    PolicyKind,
    StageOutput,
)
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.scenarios import (
    EconomicScenario,
    OperatingScenario,
    UncertaintyModel,
)
from r3chain.domain.status import AdapterState, StageStatus
from r3chain.economics.cashflow import evaluate_boundary_cashflows
from r3chain.economics.kpis import make_kpi
from r3chain.economics.ledger import (
    CostBoundary,
    assemble_baseline_ledger,
    assemble_candidate_ledger,
)
from r3chain.economics.policies import EconomicSettings
from r3chain.geospatial.routes import GeneratedRoute
from r3chain.operation.dispatch import (
    DispatchSettings,
    RuleBasedDispatchStrategy,
    build_dispatch_request,
    geothermal_spec_from_coupling,
)
from r3chain.pipeline.evaluation import single_policy
from r3chain.pipeline.operation_cost import (
    _annual_revenue,
    _evaluate_three_boundaries,
    _generated_routes_by_id,
    _geothermal_operating_params,
    _select_scenario_item,
)
from r3chain.pipeline.runner import StageContext
from r3chain.ports.dispatch import DispatchGeothermalSpec
from r3chain.ports.network import NetworkModel
from r3chain.ports.source import SourceModel
from r3chain.search.candidates import Candidate
from r3chain.uncertainty.policies import DecisionRuleSettings, UncertaintySettings
from r3chain.uncertainty.resilience import ResilienceOutcome, evaluate_resilience_cases
from r3chain.uncertainty.risk import (
    AdoptionStageOutcome,
    AdoptionThresholdSummary,
    CandidateSampleOutcome,
    DecisionRuleConfig,
    PreDrillingInputs,
    compare_candidate_uncertainty,
    estimate_breakeven_adoption_fraction,
)
from r3chain.uncertainty.sampling import (
    JointSampleSet,
    ProgressiveAssessment,
    generate_joint_samples,
    run_progressive_allocation,
)

_UNRESOLVED_COUPLING_STATES = frozenset(
    {
        StageStatus.NON_CONVERGED.value,
        StageStatus.NOT_ESTABLISHED.value,
        StageStatus.UNSUPPORTED_MODEL.value,
        StageStatus.INVALID_INPUT.value,
        StageStatus.TOOL_FAILURE.value,
    }
)


def _patch_scenario_realization(
    base_doc: dict[str, Any],
    model: UncertaintyModel,
    realization: Mapping[str, float],
) -> dict[str, Any]:
    operations: list[PatchOperation] = []
    for var in model.variables:
        if var.variable_id not in realization:
            continue
        sampled = float(realization[var.variable_id])
        current = get_at(base_doc, var.field_path)
        if isinstance(current, dict) and current.get("state") in {"known", "unknown", "conflict"}:
            replacement: Any = {
                **current,
                "state": "known",
                "value": sampled,
                "provenance": var.evidence.to_json_dict(),
            }
        else:
            replacement = sampled
        operations.append(PatchOperation("set", var.field_path, replacement))
    if not operations:
        return base_doc
    return apply_patch(base_doc, operations)


def _affects_candidate_coupling(cand: Candidate, path: str) -> bool:
    if path.startswith("well_scenarios["):
        return f"well_scenarios[{cand.source_scenario_id}]" in path
    if path.startswith("plants["):
        return f"plants[{cand.plant_id}]" in path
    if path.startswith("routes["):
        return f"routes[{cand.route_id}]" in path
    if path.startswith("connections["):
        return f"connections[{cand.connection_id}]" in path
    return path.startswith(("networks[", "coupling_cases[", "operating_cases[", "sites["))


def _affects_dispatch(path: str) -> bool:
    return path.startswith(("operating_scenarios[", "time_series["))


def _coupling_signature(
    cand: Candidate, model: UncertaintyModel, real: Mapping[str, float]
) -> tuple[tuple[str, float], ...]:
    return tuple(
        (var.variable_id, float(real[var.variable_id]))
        for var in model.variables
        if var.variable_id in real and _affects_candidate_coupling(cand, var.field_path)
    )


def _dispatch_signature(
    model: UncertaintyModel, real: Mapping[str, float]
) -> tuple[tuple[str, float], ...]:
    return tuple(
        (var.variable_id, float(real[var.variable_id]))
        for var in model.variables
        if var.variable_id in real and _affects_dispatch(var.field_path)
    )


class CompareStage:
    """Joint uncertainty sampling, pre-drilling risk, resilience, Pareto, and driver analysis."""

    name = "compare"
    version = "1.0.0"
    requires: tuple[str, ...] = ("candidates", "routes", "couple", "assess", "operate", "cost")
    stochastic = True

    def __init__(
        self,
        source_models: Sequence[SourceModel],
        network_model: NetworkModel,
    ) -> None:
        self._source_models = tuple(source_models)
        self._network_model = network_model

    def input_fingerprint(self, context: StageContext) -> Any:
        u_policy = single_policy(context.policies, PolicyKind.UNCERTAINTY)
        r_policy = single_policy(context.policies, PolicyKind.RANKING)
        d_policy = single_policy(context.policies, PolicyKind.DISPATCH)
        e_policy = single_policy(context.policies, PolicyKind.ECONOMIC)
        scenario = context.scenario
        return {
            "uncertainty_models": [m.to_json_dict() for m in scenario.uncertainty_models],
            "operating_scenarios": [o.to_json_dict() for o in scenario.operating_scenarios],
            "economic_scenarios": [e.to_json_dict() for e in scenario.economic_scenarios],
            "well_scenarios": [w.to_json_dict() for w in scenario.well_scenarios],
            "plants": [p.to_json_dict() for p in scenario.plants],
            "operating_cases": [c.to_json_dict() for c in scenario.operating_cases],
            "policies": {
                "uncertainty": u_policy.to_json_dict() if u_policy else None,
                "ranking": r_policy.to_json_dict() if r_policy else None,
                "dispatch": d_policy.to_json_dict() if d_policy else None,
                "economic": e_policy.to_json_dict() if e_policy else None,
            },
            "sources": [
                (m.capabilities().engine, m.capabilities().engine_version)
                for m in self._source_models
            ],
            "network": (
                self._network_model.capabilities().engine,
                self._network_model.capabilities().engine_version,
            ),
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        u_policy = single_policy(context.policies, PolicyKind.UNCERTAINTY)
        if u_policy is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="uncertainty comparison needs one policy of kind 'uncertainty'",
                missing_inputs=(
                    MissingInput(
                        field_path="policies[kind=uncertainty]",
                        state="unknown",
                        reason="the scenario references no uncertainty policy",
                        owner="study owner",
                    ),
                ),
            )
        r_policy = single_policy(context.policies, PolicyKind.RANKING)
        if r_policy is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="uncertainty comparison needs one policy of kind 'ranking'",
            )
        try:
            u_settings = UncertaintySettings.from_policy(u_policy)
            r_settings = DecisionRuleSettings.from_policy(r_policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        u_model, u_err = _select_scenario_item(
            scenario.uncertainty_models,
            selected_id=u_settings.uncertainty_model_id,
            id_attr="uncertainty_model_id",
            noun="uncertainty model",
            policy_field="uncertainty_model_id",
        )
        if u_model is None:
            status = (
                StageStatus.NOT_ESTABLISHED
                if not scenario.uncertainty_models
                else StageStatus.INVALID_INPUT
            )
            return self._output(
                status,
                reason=u_err or "the scenario defines no uncertainty models",
            )

        if u_settings.source_outage_case_id is not None:
            known_op_cases = {c.operating_case_id for c in scenario.operating_cases}
            if u_settings.source_outage_case_id not in known_op_cases:
                return self._output(
                    StageStatus.INVALID_INPUT,
                    reason=(
                        f"source_outage_case_id {u_settings.source_outage_case_id!r} is not "
                        f"defined in operating_cases ({', '.join(sorted(known_op_cases))})"
                    ),
                )

        d_policy = single_policy(context.policies, PolicyKind.DISPATCH)
        e_policy = single_policy(context.policies, PolicyKind.ECONOMIC)
        if d_policy is None or e_policy is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="dispatch and economic policies are required for uncertainty evaluation",
            )
        try:
            d_settings = DispatchSettings.from_policy(d_policy)
            e_settings = EconomicSettings.from_policy(e_policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        operating, op_err = _select_scenario_item(
            scenario.operating_scenarios,
            selected_id=d_settings.operating_scenario_id,
            id_attr="operating_scenario_id",
            noun="operating scenario",
            policy_field="operating_scenario_id",
        )
        economic, ec_err = _select_scenario_item(
            scenario.economic_scenarios,
            selected_id=e_settings.economic_scenario_id,
            id_attr="economic_scenario_id",
            noun="economic scenario",
            policy_field="economic_scenario_id",
        )
        if operating is None or economic is None:
            return self._output(
                StageStatus.INVALID_INPUT, reason=op_err or ec_err or "missing scenario"
            )

        # Generate sample plan according to the named uncertainty policy (uncertainty / V03 / V04).
        try:
            if u_settings.mode == "point_estimate":
                samples = generate_joint_samples(
                    u_model,
                    sample_count=1,
                    seed=context.seed,
                    method="point_estimate",
                    max_sweep_count=u_settings.max_sweep_count,
                )
            elif u_settings.mode == "progressive":
                samples = generate_joint_samples(
                    u_model,
                    sample_count=u_settings.progressive_max_count,
                    seed=context.seed,
                    method=u_settings.sampling_method,
                    max_sweep_count=u_settings.max_sweep_count,
                )
            else:
                samples = generate_joint_samples(
                    u_model,
                    sample_count=u_settings.sample_count,
                    seed=context.seed,
                    method=u_settings.sampling_method,
                    max_sweep_count=u_settings.max_sweep_count,
                )
        except ValueError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        cand_payload = context.upstream["candidates"].payload
        couple_payload = context.upstream["couple"].payload.get("candidates", {})
        assess_payload = context.upstream["assess"].payload
        operate_payload = context.upstream["operate"].payload
        cost_payload = context.upstream["cost"].payload
        routes_by_id = {
            key: Route.model_validate(val) for key, val in cand_payload.get("routes", {}).items()
        }
        cand_coupling_cases = {
            item["coupling_case_id"]: CouplingCase.model_validate(item)
            for item in cand_payload.get("coupling_cases", [])
        }
        memo_network = MemoNetworkModel(self._network_model)
        generated_routes = _generated_routes_by_id(context)
        candidates = [Candidate.model_validate(raw) for raw in cand_payload.get("candidates", [])]
        assessments = {a["candidate_id"]: a for a in assess_payload.get("assessments", [])}
        op_candidates = operate_payload.get("candidates", {})
        cost_candidates = cost_payload.get("candidates", {})

        contenders = [
            cand
            for cand in candidates
            if assessments.get(cand.candidate_id, {}).get("ranking_eligibility", {}).get("eligible")
            and op_candidates.get(cand.candidate_id, {}).get("status") == "completed"
            and cost_candidates.get(cand.candidate_id, {}).get("status") == "completed"
        ]
        resilience_candidates = [
            cand
            for cand in candidates
            if op_candidates.get(cand.candidate_id, {}).get("status") == "completed"
        ]

        # Collect explicit missing inputs declared on the UncertaintyModel (e.g., O05).
        model_missing = tuple(
            missing_inputs(u_model, prefix=f"uncertainty_models[{u_model.uncertainty_model_id}].")
        )
        p_succ = (
            value_or_none(u_model.success_probability_fraction)
            if u_model.success_probability_fraction is not None
            else None
        )
        fail_frac = (
            value_or_none(u_model.failure_drilling_cost_fraction)
            if u_model.failure_drilling_cost_fraction is not None
            else None
        )
        aband_cost = (
            value_or_none(u_model.abandonment_cost_currency)
            if u_model.abandonment_cost_currency is not None
            else None
        )
        success_def = value_or_none(u_model.success_definition)
        pre_drilling_enabled = (
            success_def is not None
            and p_succ is not None
            and fail_frac is not None
            and aband_cost is not None
        )

        # Evaluate resilience cases (V02) across all operating candidates cleanly.
        resilience_by_candidate: dict[str, tuple[ResilienceOutcome, ...]] = {}
        dispatch_strategy = RuleBasedDispatchStrategy()
        if u_settings.resilience_cases:
            base_series_map = {item.series_id: item.series for item in scenario.time_series}
            base_avail, base_min_frac, _ = _geothermal_operating_params(operating, base_series_map)
            for cand in resilience_candidates:
                res_geo_spec = _base_geothermal_spec(
                    cand,
                    couple_payload.get(cand.candidate_id, {}),
                    availability=base_avail,
                    min_output_fraction=base_min_frac,
                )
                req = build_dispatch_request(
                    scenario,
                    operating,
                    request_id=f"{cand.candidate_id}-resilience",
                    settings=d_settings,
                    geothermal=res_geo_spec,
                )
                resilience_by_candidate[cand.candidate_id] = evaluate_resilience_cases(
                    req,
                    strategy=dispatch_strategy,
                    cases=u_settings.resilience_cases,
                    has_heat_pump=cand.arrangement == "heat_pump",
                )

        # Evaluate paired uncertainty samples with sub-layer memoization.
        realizations = samples.realizations()
        base_doc = scenario.to_json_dict()
        source_pol = single_policy(context.policies, PolicyKind.SOURCE)
        num_pol = single_policy(context.policies, PolicyKind.NUMERICAL)
        if source_pol is None or num_pol is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="source and numerical policies are required for uncertainty sampling",
            )

        coupling_cache: dict[
            tuple[str, tuple[tuple[str, float], ...]],
            tuple[bool, DispatchGeothermalSpec | None, str],
        ] = {}
        dispatch_cache: dict[tuple[Any, ...], Any] = {}
        baseline_cache: dict[tuple[tuple[str, float], ...], tuple[Any, float | None]] = {}

        outcomes_by_cand: dict[str, list[CandidateSampleOutcome]] = {
            cand.candidate_id: [] for cand in contenders
        }
        pre_drilling_templates: dict[str, PreDrillingInputs] = {}
        base_series_map = {item.series_id: item.series for item in scenario.time_series}
        base_geo_avail, base_geo_min, _ = _geothermal_operating_params(operating, base_series_map)

        # Extract static source-drilling CAPEX per contender from base M4 ledger.
        drilling_capex_by_cand: dict[str, float] = {}
        for cand in contenders:
            rows = cost_candidates[cand.candidate_id]["reconciliation"]["rows"]
            drilling_capex_by_cand[cand.candidate_id] = sum(
                float(r["amount_currency"])
                for r in rows
                if r.get("included")
                and r.get("category") == "capex"
                and r.get("location") == "source"
            )

        synthetic_flag = bool(
            scenario.study.synthetic
            or any(
                context.upstream[name].synthetic
                for name in self.requires
                if name in context.upstream
            )
            or any(model.capabilities().synthetic for model in self._source_models)
        )
        for s_idx, real_map in enumerate(realizations):
            patched_doc = _patch_scenario_realization(base_doc, u_model, real_map)
            try:
                patched_scen = Scenario.model_validate(patched_doc)
            except (ValidationError, ValueError) as error:
                for cand in contenders:
                    outcomes_by_cand[cand.candidate_id].append(
                        CandidateSampleOutcome(
                            sample_index=s_idx,
                            candidate_id=cand.candidate_id,
                            evaluated=False,
                            feasible=False,
                            failure_kind="unresolved_numerical",
                            reason=str(error),
                        )
                    )
                continue

            patched_op = next(
                o
                for o in patched_scen.operating_scenarios
                if o.operating_scenario_id == operating.operating_scenario_id
            )
            patched_econ = next(
                e
                for e in patched_scen.economic_scenarios
                if e.economic_scenario_id == economic.economic_scenario_id
            )
            p_series_map = {item.series_id: item.series for item in patched_scen.time_series}
            d_sig = _dispatch_signature(u_model, real_map)
            if d_sig not in baseline_cache:
                base_req = build_dispatch_request(
                    patched_scen,
                    patched_op,
                    request_id=f"u-base-{s_idx}",
                    settings=d_settings,
                    geothermal=None,
                )
                base_disp = dispatch_strategy.dispatch(base_req)
                if base_disp.state is not AdapterState.COMPLETED or base_disp.totals is None:
                    for cand in contenders:
                        outcomes_by_cand[cand.candidate_id].append(
                            CandidateSampleOutcome(
                                sample_index=s_idx,
                                candidate_id=cand.candidate_id,
                                evaluated=False,
                                feasible=False,
                                failure_kind="unresolved_numerical",
                                reason=f"baseline dispatch failed: {base_disp.reason}",
                            )
                        )
                    continue
                base_rev = _annual_revenue(
                    patched_op,
                    p_series_map,
                    base_disp.totals.delivered_heat_j,
                    base_req.durations_s,
                    base_req.demand_heat_w,
                    base_disp.unserved_w,
                )
                baseline_cache[d_sig] = (base_disp.totals, base_rev)
            base_totals, base_rev = baseline_cache[d_sig]
            base_ledger = assemble_baseline_ledger(
                patched_econ,
                baseline_id=patched_scen.study.baseline_id,
                dispatch_totals=base_totals,
            )
            base_econ = evaluate_boundary_cashflows(
                patched_econ,
                base_ledger,
                boundary=CostBoundary.SYSTEM_DELIVERED,
                annual_heat_j=base_totals.delivered_heat_j,
                annual_revenue_currency=base_rev,
                residual_value_method=e_settings.residual_value_method,
                case_label="unsubsidised",
            )
            if d_sig:
                p_geo_avail, p_geo_min, _ = _geothermal_operating_params(patched_op, p_series_map)
            else:
                p_geo_avail, p_geo_min = base_geo_avail, base_geo_min

            for cand in contenders:
                cid = cand.candidate_id
                c_sig = _coupling_signature(cand, u_model, real_map)
                if not c_sig:
                    geo_spec: DispatchGeothermalSpec | None = _base_geothermal_spec(
                        cand,
                        couple_payload[cid],
                        availability=p_geo_avail,
                        min_output_fraction=p_geo_min,
                    )
                    coupled_ok, c_reason = True, ""
                else:
                    cache_k = (cid, c_sig)
                    if cache_k not in coupling_cache:
                        ok_flag, spec_obj, fail_r = self._recouple_candidate(
                            patched_scen,
                            cand,
                            cases_by_id=cand_coupling_cases,
                            routes_by_id=routes_by_id,
                            network_model=memo_network,
                            source_pol=source_pol,
                            num_pol=num_pol,
                            availability=p_geo_avail,
                            min_output_fraction=p_geo_min,
                        )
                        coupling_cache[cache_k] = (ok_flag, spec_obj, fail_r)
                    coupled_ok, cached_spec, c_reason = coupling_cache[cache_k]
                    geo_spec = (
                        cached_spec.model_copy(
                            update={
                                "availability_fraction": p_geo_avail,
                                "min_output_fraction": p_geo_min,
                            }
                        )
                        if cached_spec is not None
                        else None
                    )

                if not coupled_ok:
                    outcomes_by_cand[cid].append(
                        CandidateSampleOutcome(
                            sample_index=s_idx,
                            candidate_id=cid,
                            evaluated=False,
                            feasible=False,
                            failure_kind="unresolved_numerical",
                            reason=c_reason,
                        )
                    )
                    continue
                if geo_spec is None or geo_spec.max_delivered_heat_w <= 0.0:
                    outcomes_by_cand[cid].append(
                        CandidateSampleOutcome(
                            sample_index=s_idx,
                            candidate_id=cid,
                            evaluated=True,
                            feasible=False,
                            failure_kind="physical_failure",
                            reason=c_reason
                            or "insufficient temperature or zero delivered capacity",
                        )
                    )
                    continue

                d_key = (cid, geo_spec.model_dump_json(), d_sig)
                if d_key not in dispatch_cache:
                    c_req = build_dispatch_request(
                        patched_scen,
                        patched_op,
                        request_id=f"{cid}-u-{s_idx}",
                        settings=d_settings,
                        geothermal=geo_spec,
                    )
                    c_disp = dispatch_strategy.dispatch(c_req)
                    c_rev = (
                        _annual_revenue(
                            patched_op,
                            p_series_map,
                            c_disp.totals.delivered_heat_j,
                            c_req.durations_s,
                            c_req.demand_heat_w,
                            c_disp.unserved_w,
                        )
                        if c_disp.totals is not None
                        else None
                    )
                    dispatch_cache[d_key] = (c_disp, c_rev)
                c_disp, c_rev = dispatch_cache[d_key]
                if c_disp.state is not AdapterState.COMPLETED or c_disp.totals is None:
                    outcomes_by_cand[cid].append(
                        CandidateSampleOutcome(
                            sample_index=s_idx,
                            candidate_id=cid,
                            evaluated=False,
                            feasible=False,
                            failure_kind="unresolved_numerical",
                            reason=c_disp.reason or c_disp.state.value,
                        )
                    )
                    continue

                try:
                    c_ledger = assemble_candidate_ledger(
                        patched_econ,
                        subject_id=cid,
                        site_id=cand.site_id,
                        plant_id=cand.plant_id,
                        route=routes_by_id[cand.route_id],
                        generated_route=generated_routes.get(cand.route_id),
                        connection_id=cand.connection_id,
                        candidate_id=cid,
                        alternative_id=None,
                        dispatch_totals=c_disp.totals,
                        economic_settings=e_settings,
                    )
                    if not c_ledger.clean:
                        raise ValueError("; ".join(c_ledger.duplicate_ownership_errors))
                    _, geo_b, sys_b = _evaluate_three_boundaries(
                        patched_econ,
                        c_ledger,
                        totals=c_disp.totals,
                        annual_revenue=c_rev,
                        residual_value_method=e_settings.residual_value_method,
                    )
                except (NotEstablished, ValueError) as error:
                    outcomes_by_cand[cid].append(
                        CandidateSampleOutcome(
                            sample_index=s_idx,
                            candidate_id=cid,
                            evaluated=False,
                            feasible=False,
                            failure_kind="unresolved_numerical",
                            reason=str(error),
                        )
                    )
                    continue

                share = (
                    c_disp.totals.geothermal_delivered_heat_j / c_disp.totals.demand_heat_j
                    if c_disp.totals.demand_heat_j > 0.0
                    else 0.0
                )
                outcomes_by_cand[cid].append(
                    CandidateSampleOutcome(
                        sample_index=s_idx,
                        candidate_id=cid,
                        evaluated=True,
                        feasible=True,
                        pv_cost_currency=sys_b.pv_cost_currency,
                        baseline_pv_cost_currency=base_econ.pv_cost_currency,
                        lcoh_currency_per_j=geo_b.lcoh_currency_per_j,
                        geothermal_demand_share_fraction=share,
                        pv_heat_j=sys_b.pv_heat_j,
                        drilling_capex_currency=drilling_capex_by_cand[cid],
                    )
                )
                if pre_drilling_enabled and cid not in pre_drilling_templates:
                    assert p_succ is not None and fail_frac is not None and aband_cost is not None
                    pre_drilling_templates[cid] = PreDrillingInputs(
                        success_probability_fraction=p_succ,
                        drilling_capex_currency=drilling_capex_by_cand[cid],
                        failure_drilling_cost_fraction=fail_frac,
                        abandonment_cost_currency=aband_cost,
                        conditional_mean_pv_cost_currency=sys_b.pv_cost_currency,
                        conditional_mean_pv_heat_j=sys_b.pv_heat_j,
                        baseline_mean_pv_cost_currency=base_econ.pv_cost_currency,
                        baseline_mean_pv_heat_j=base_econ.pv_heat_j,
                    )

        # Refine pre-drilling baseline averages across evaluated samples if available.
        if pre_drilling_enabled:
            for cid, tmpl in list(pre_drilling_templates.items()):
                feasible_rows = [o for o in outcomes_by_cand[cid] if o.evaluated and o.feasible]
                if feasible_rows:
                    mean_base_pv = sum(
                        float(o.baseline_pv_cost_currency or 0.0) for o in feasible_rows
                    ) / len(feasible_rows)
                    mean_cand_heat = sum(float(o.pv_heat_j or 0.0) for o in feasible_rows) / len(
                        feasible_rows
                    )
                    pre_drilling_templates[cid] = tmpl.model_copy(
                        update={
                            "conditional_mean_pv_heat_j": mean_cand_heat,
                            "baseline_mean_pv_cost_currency": mean_base_pv,
                            "baseline_mean_pv_heat_j": mean_cand_heat,
                        }
                    )

        # Progressive sample verification (V04): verify early prefix error against full batch.
        prog_assessment: ProgressiveAssessment | None = None
        effective_count = len(realizations)
        if u_settings.mode == "progressive" and samples.has_probabilities and contenders:

            def _eval_prefix(n_sub: int) -> float:
                sub = {cid: rows[:n_sub] for cid, rows in outcomes_by_cand.items()}
                sub_cmp = compare_candidate_uncertainty(
                    sub,
                    sample_inputs=realizations[:n_sub],
                    has_probabilities=samples.has_probabilities,
                    rules=DecisionRuleConfig(
                        decision_metric=r_settings.decision_metric,
                        decision_rule=r_settings.decision_rule,
                        tie_tolerance_fraction=r_settings.tie_tolerance_fraction,
                        preferable_cost_margin_fraction=r_settings.preferable_cost_margin_fraction,
                        max_unresolved_fraction=u_settings.max_unresolved_fraction,
                        pareto_objectives=r_settings.pareto_objectives,
                    ),
                )
                if not sub_cmp.ordered_candidate_ids:
                    return 0.0
                top_cid = sub_cmp.ordered_candidate_ids[0]
                return float(sub_cmp.candidates[top_cid].decision_score or 0.0)

            prog_assessment = run_progressive_allocation(
                total_count=len(realizations),
                initial_count=u_settings.progressive_initial_count,
                step_count=u_settings.progressive_step_count,
                max_error_fraction=u_settings.progressive_max_error_fraction,
                evaluate_prefix=_eval_prefix,
            )
            effective_count = prog_assessment.chosen_count
        elif u_settings.mode == "progressive":
            prog_assessment = ProgressiveAssessment(
                accepted=True,
                chosen_count=len(realizations),
                full_count=len(realizations),
                relative_error_vs_full=0.0,
                max_error_fraction=u_settings.progressive_max_error_fraction,
                reason="deterministic scenario sweep uses all combinations cleanly",
            )

        trimmed_outcomes = {cid: rows[:effective_count] for cid, rows in outcomes_by_cand.items()}
        comparison = compare_candidate_uncertainty(
            trimmed_outcomes,
            sample_inputs=realizations[:effective_count],
            has_probabilities=samples.has_probabilities,
            rules=DecisionRuleConfig(
                decision_metric=r_settings.decision_metric,
                decision_rule=r_settings.decision_rule,
                tie_tolerance_fraction=r_settings.tie_tolerance_fraction,
                preferable_cost_margin_fraction=r_settings.preferable_cost_margin_fraction,
                max_unresolved_fraction=u_settings.max_unresolved_fraction,
                pareto_objectives=r_settings.pareto_objectives,
            ),
            pre_drilling_by_candidate=pre_drilling_templates,
        )

        # Evaluate connection adoption scenarios and break-even threshold (W18, section 14.4).
        adoption_by_cand = self._evaluate_adoption_thresholds(
            scenario=scenario,
            operating=operating,
            economic=economic,
            d_settings=d_settings,
            e_settings=e_settings,
            fractions=u_settings.adoption_fractions,
            contenders=contenders,
            couple_payload=couple_payload,
            routes_by_id=routes_by_id,
            generated_routes=generated_routes,
            base_avail=base_geo_avail,
            base_min=base_geo_min,
            series_map=base_series_map,
        )

        # Build subject KPIs, checks, and diagnostics for every contender + resilience candidates.
        subject_results = self._build_subject_results(
            u_model=u_model,
            u_settings=u_settings,
            r_settings=r_settings,
            samples=samples,
            comparison=comparison,
            resilience_by_candidate=resilience_by_candidate,
            prog_assessment=prog_assessment,
            success_def_present=success_def is not None,
        )

        return self._output(
            StageStatus.COMPLETED,
            payload={
                "uncertainty_model_id": u_model.uncertainty_model_id,
                "policy_id": u_policy.policy_id,
                "ranking_policy_id": r_policy.policy_id,
                "mode": u_settings.mode,
                "seed": context.seed,
                "probability_basis": samples.probability_basis,
                "has_probabilities": samples.has_probabilities,
                "success_event_established": pre_drilling_enabled,
                "sample_count": effective_count,
                "progressive": prog_assessment.to_json_dict() if prog_assessment else None,
                "comparison": comparison.to_json_dict(),
                "adoption_thresholds": {
                    cid: item.to_json_dict() for cid, item in adoption_by_cand.items()
                },
                "resilience": {
                    cid: [r.to_json_dict() for r in rows]
                    for cid, rows in resilience_by_candidate.items()
                },
                "subject_results": subject_results,
            },
            missing_inputs=model_missing,
            synthetic=synthetic_flag,
        )

    def _evaluate_adoption_thresholds(
        self,
        *,
        scenario: Scenario,
        operating: OperatingScenario,
        economic: EconomicScenario,
        d_settings: DispatchSettings,
        e_settings: EconomicSettings,
        fractions: tuple[float, ...],
        contenders: Sequence[Candidate],
        couple_payload: Mapping[str, Any],
        routes_by_id: Mapping[str, Route],
        generated_routes: Mapping[str, GeneratedRoute],
        base_avail: tuple[float, ...],
        base_min: float,
        series_map: Mapping[str, Any],
    ) -> dict[str, AdoptionThresholdSummary]:
        if not fractions or not contenders:
            return {}
        strategy = RuleBasedDispatchStrategy()
        base_req = build_dispatch_request(
            scenario,
            operating,
            request_id="adoption-base",
            settings=d_settings,
            geothermal=None,
        )
        baseline_pv_by_frac: dict[float, float] = {}
        for frac in fractions:
            scaled_dem = tuple(d * frac for d in base_req.demand_heat_w)
            b_disp = strategy.dispatch(base_req.model_copy(update={"demand_heat_w": scaled_dem}))
            if b_disp.totals is None:
                return {}
            b_rev = _annual_revenue(
                operating,
                dict(series_map),
                b_disp.totals.delivered_heat_j,
                base_req.durations_s,
                scaled_dem,
                b_disp.unserved_w,
            )
            b_ledger = assemble_baseline_ledger(
                economic,
                baseline_id=scenario.study.baseline_id,
                dispatch_totals=b_disp.totals,
            )
            b_econ = evaluate_boundary_cashflows(
                economic,
                b_ledger,
                boundary=CostBoundary.SYSTEM_DELIVERED,
                annual_heat_j=b_disp.totals.delivered_heat_j,
                annual_revenue_currency=b_rev,
                residual_value_method=e_settings.residual_value_method,
            )
            baseline_pv_by_frac[frac] = b_econ.pv_cost_currency

        out: dict[str, AdoptionThresholdSummary] = {}
        for cand in contenders:
            cid = cand.candidate_id
            geo_spec = _base_geothermal_spec(
                cand,
                couple_payload[cid],
                availability=base_avail,
                min_output_fraction=base_min,
            )
            c_req = base_req.model_copy(
                update={"request_id": f"{cid}-adoption", "geothermal": geo_spec}
            )
            stages: list[AdoptionStageOutcome] = []
            for frac in fractions:
                scaled_dem = tuple(d * frac for d in c_req.demand_heat_w)
                c_disp = strategy.dispatch(c_req.model_copy(update={"demand_heat_w": scaled_dem}))
                if c_disp.totals is None:
                    continue
                c_rev = _annual_revenue(
                    operating,
                    dict(series_map),
                    c_disp.totals.delivered_heat_j,
                    c_req.durations_s,
                    scaled_dem,
                    c_disp.unserved_w,
                )
                c_ledger = assemble_candidate_ledger(
                    economic,
                    subject_id=cid,
                    site_id=cand.site_id,
                    plant_id=cand.plant_id,
                    route=routes_by_id[cand.route_id],
                    generated_route=generated_routes.get(cand.route_id),
                    connection_id=cand.connection_id,
                    candidate_id=cid,
                    alternative_id=None,
                    dispatch_totals=c_disp.totals,
                    economic_settings=e_settings,
                )
                if not c_ledger.clean:
                    continue
                _, geo_b, sys_b = _evaluate_three_boundaries(
                    economic,
                    c_ledger,
                    totals=c_disp.totals,
                    annual_revenue=c_rev,
                    residual_value_method=e_settings.residual_value_method,
                )
                stages.append(
                    AdoptionStageOutcome(
                        adoption_fraction=frac,
                        geothermal_delivered_heat_j=c_disp.totals.geothermal_delivered_heat_j,
                        lcoh_geothermal_delivered_currency_per_j=geo_b.lcoh_currency_per_j,
                        pv_total_cost_currency=sys_b.pv_cost_currency,
                        incremental_cost_vs_baseline_currency=(
                            sys_b.pv_cost_currency - baseline_pv_by_frac[frac]
                        ),
                    )
                )
            be_frac, note = estimate_breakeven_adoption_fraction(stages)
            out[cid] = AdoptionThresholdSummary(
                candidate_id=cid,
                stages=tuple(stages),
                breakeven_adoption_fraction=be_frac,
                note=note,
            )
        return out

    def _recouple_candidate(
        self,
        scenario: Scenario,
        cand: Candidate,
        *,
        cases_by_id: Mapping[str, CouplingCase],
        routes_by_id: Mapping[str, Route],
        network_model: NetworkModel,
        source_pol: Policy,
        num_pol: Policy,
        availability: tuple[float, ...],
        min_output_fraction: float,
    ) -> tuple[bool, DispatchGeothermalSpec | None, str]:
        coupled: list[CouplingResult] = []
        for cid in cand.coupling_case_ids:
            outcome = evaluate_case(
                scenario,
                cases_by_id[cid],
                source_models=self._source_models,
                network_model=network_model,
                source_policy=source_pol,
                numerical_policy=num_pol,
                routes=routes_by_id,
            )
            if outcome.status.value in _UNRESOLVED_COUPLING_STATES or outcome.result is None:
                return False, None, f"{cid}: {outcome.reason or outcome.status.value}"
            coupled.append(outcome.result)
        for cid in cand.capacity_coupling_case_ids:
            outcome = evaluate_case(
                scenario,
                cases_by_id[cid],
                source_models=self._source_models,
                network_model=network_model,
                source_policy=source_pol,
                numerical_policy=num_pol,
                routes=routes_by_id,
            )
            if (
                outcome.status.value not in _UNRESOLVED_COUPLING_STATES
                and outcome.result is not None
                and outcome.result.operating_point is not None
            ):
                coupled.append(outcome.result)
        spec = geothermal_spec_from_coupling(
            cand.candidate_id,
            coupled,
            availability=availability,
            min_output_fraction=min_output_fraction,
        )
        fail_causes = [
            c.value
            for r in coupled
            for c in r.shortfall_causes
            if c is ShortfallCause.INSUFFICIENT_TEMPERATURE
        ]
        reason = ", ".join(dict.fromkeys(fail_causes)) if fail_causes else ""
        return True, spec, reason

    @staticmethod
    def _build_subject_results(
        *,
        u_model: UncertaintyModel,
        u_settings: UncertaintySettings,
        r_settings: DecisionRuleSettings,
        samples: JointSampleSet,
        comparison: Any,
        resilience_by_candidate: Mapping[str, tuple[ResilienceOutcome, ...]],
        prog_assessment: ProgressiveAssessment | None,
        success_def_present: bool,
    ) -> list[dict[str, Any]]:
        derived = (
            f"uncertainty_models[{u_model.uncertainty_model_id}]",
            u_settings.origin,
            r_settings.origin,
        )
        stability_map = {row.candidate_id: row for row in comparison.ranking_stability}
        all_cids = sorted(set(comparison.candidates) | set(resilience_by_candidate))
        results: list[dict[str, Any]] = []
        for cid in all_cids:
            kpis: list[KpiValue] = []
            checks: list[Check] = []
            diags: list[str] = []
            risk = comparison.candidates.get(cid)
            if risk is not None:
                acc = risk.sample_account
                stab = stability_map.get(cid)
                kpis.extend(
                    [
                        make_kpi(
                            "conditional_service_probability_fraction",
                            acc.conditional_service_probability,
                            derived,
                        ),
                        make_kpi(
                            "expected_lcoh_pre_drilling_currency_per_j",
                            risk.pre_drilling.expected_lcoh_currency_per_j
                            if risk.pre_drilling
                            else None,
                            derived,
                        ),
                        make_kpi(
                            "expected_pv_cost_pre_drilling_currency",
                            risk.pre_drilling.expected_pv_cost_currency
                            if risk.pre_drilling
                            else None,
                            derived,
                        ),
                        make_kpi("max_regret_currency", risk.max_regret_currency, derived),
                        make_kpi(
                            "first_rank_share_fraction",
                            stab.first_rank_share if stab else None,
                            derived,
                        ),
                    ]
                )
                checks.append(
                    Check(
                        check_id="uncertainty_sample_accounting",
                        description=(
                            "unresolved numerical samples do not exceed policy tolerance and stay "
                            "separate from physical failures"
                        ),
                        outcome=(
                            CheckOutcome.PASS
                            if acc.unresolved_fraction <= u_settings.max_unresolved_fraction
                            else CheckOutcome.FAIL
                        ),
                        observed=acc.unresolved_fraction,
                        limit=u_settings.max_unresolved_fraction,
                        margin=u_settings.max_unresolved_fraction - acc.unresolved_fraction,
                        unit="1",
                        threshold_origin=u_settings.origin,
                    )
                )
                if prog_assessment is not None:
                    checks.append(
                        Check(
                            check_id="progressive_sample_error",
                            description="progressive sample error relative to full batch (V04)",
                            outcome=(
                                CheckOutcome.PASS
                                if (
                                    prog_assessment.accepted
                                    or prog_assessment.chosen_count == prog_assessment.full_count
                                )
                                else CheckOutcome.FAIL
                            ),
                            observed=prog_assessment.relative_error_vs_full,
                            limit=prog_assessment.max_error_fraction,
                            margin=(
                                prog_assessment.max_error_fraction
                                - prog_assessment.relative_error_vs_full
                            ),
                            unit="1",
                            threshold_origin=u_settings.origin,
                        )
                    )
                checks.append(
                    Check(
                        check_id="geological_success_definition",
                        description="geological success event defined for pre-drilling risk (O05)",
                        outcome=(
                            CheckOutcome.PASS if success_def_present else CheckOutcome.NOT_EVALUATED
                        ),
                        observed=1.0 if success_def_present else None,
                        limit=1.0,
                        margin=0.0 if success_def_present else None,
                        unit="1",
                        threshold_origin="specification section 14.2 (W02, O05)",
                    )
                )
                diags.append(f"uncertainty ({samples.mode}): {acc.service_probability_denominator}")
            for res in resilience_by_candidate.get(cid, ()):
                kpis.extend(
                    [
                        make_kpi(
                            f"resilience_{res.case_id}_unserved_heat_j",
                            res.unserved_heat_j,
                            derived,
                        ),
                        make_kpi(
                            f"resilience_{res.case_id}_unserved_hours",
                            res.unserved_hours,
                            derived,
                        ),
                        make_kpi(
                            f"resilience_{res.case_id}_largest_deficit_w",
                            res.largest_deficit_w,
                            derived,
                        ),
                    ]
                )
            results.append(
                {
                    "subject": cid,
                    "kpis": [k.to_json_dict() for k in kpis],
                    "checks": [c.to_json_dict() for c in checks],
                    "diagnostics": diags,
                }
            )
        return results

    @staticmethod
    def _output(
        status: StageStatus,
        *,
        reason: str = "",
        payload: dict[str, Any] | None = None,
        missing_inputs: tuple[MissingInput, ...] = (),
        synthetic: bool = False,
    ) -> StageOutput:
        return StageOutput(
            stage=CompareStage.name,
            stage_version=CompareStage.version,
            status=status,
            reason=reason,
            payload=payload or {},
            missing_inputs=missing_inputs,
            synthetic=synthetic,
        )


def _base_geothermal_spec(
    cand: Candidate,
    couple_entry: Mapping[str, Any],
    *,
    availability: tuple[float, ...],
    min_output_fraction: float,
) -> DispatchGeothermalSpec:
    results: list[CouplingResult] = []
    cases_raw = couple_entry.get("cases") if "cases" in couple_entry else couple_entry
    assert isinstance(cases_raw, Mapping)
    for item in cases_raw.values():
        if not isinstance(item, Mapping):
            continue
        raw = item.get("result")
        if raw is None:
            continue
        parsed = CouplingResult.model_validate(raw)
        if parsed.operating_point is not None:
            results.append(parsed)
    return geothermal_spec_from_coupling(
        cand.candidate_id,
        results,
        availability=availability,
        min_output_fraction=min_output_fraction,
    )
