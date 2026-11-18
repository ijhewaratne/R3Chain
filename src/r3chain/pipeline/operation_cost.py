"""Pipeline stages for milestone M4: chronological operation and discounted economics."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from r3chain.coupling.loop import CouplingResult
from r3chain.domain import policies
from r3chain.domain.evidence import InputReader, MissingInput, NotEstablished
from r3chain.domain.records.network import Route
from r3chain.domain.records.runs import (
    Check,
    CheckOutcome,
    Policy,
    PolicyKind,
    StageOutput,
)
from r3chain.domain.records.scenarios import OperatingScenario
from r3chain.domain.status import AdapterState, EvaluationStatus, StageStatus
from r3chain.economics.cashflow import (
    BaselineComparison,
    BoundaryEconomics,
    compare_to_baseline,
    evaluate_boundary_cashflows,
)
from r3chain.economics.kpis import (
    RouteDemandOptionResult,
    economic_kpis,
    operation_kpis,
)
from r3chain.economics.ledger import (
    CostBoundary,
    ReconciliationTable,
    assemble_baseline_ledger,
    assemble_candidate_ledger,
)
from r3chain.economics.policies import EconomicSettings
from r3chain.geospatial.routes import GeneratedRoute
from r3chain.operation.dispatch import (
    DispatchSettings,
    build_dispatch_request,
    default_dispatch_strategy,
    geothermal_spec_from_coupling,
)
from r3chain.operation.reduction import measure_reduction_error
from r3chain.pipeline.evaluation import _Base, candidates_of
from r3chain.pipeline.runner import StageContext
from r3chain.ports.dispatch import (
    DispatchRequest,
    DispatchResult,
    DispatchStrategy,
    DispatchTotals,
)
from r3chain.search.candidates import Candidate

_UNRESOLVED_COUPLING = {
    StageStatus.NON_CONVERGED.value,
    StageStatus.NOT_ESTABLISHED.value,
    StageStatus.UNSUPPORTED_MODEL.value,
    StageStatus.INVALID_INPUT.value,
    StageStatus.TOOL_FAILURE.value,
}


def _select_scenario_item[T](
    items: Sequence[T],
    *,
    selected_id: str | None,
    id_attr: str,
    noun: str,
    policy_field: str,
) -> tuple[T | None, str | None]:
    if not items:
        return None, f"the scenario defines no {noun}s"
    if selected_id is not None:
        for item in items:
            if str(getattr(item, id_attr)) == selected_id:
                return item, None
        choices = ", ".join(str(getattr(item, id_attr)) for item in items)
        return None, (
            f"{noun} {selected_id!r} selected by {policy_field} is not defined "
            f"(available: {choices})"
        )
    if len(items) > 1:
        choices = ", ".join(str(getattr(item, id_attr)) for item in items)
        return None, (
            f"the scenario defines multiple {noun}s ({choices}); set {policy_field} in policy "
            "to choose one"
        )
    return items[0], None


def _evaluate_dispatch(
    request: DispatchRequest,
    strategy: DispatchStrategy,
    settings: DispatchSettings,
) -> tuple[DispatchResult, dict[str, Any] | None]:
    if settings.interval_mode == "reduced":
        # Verify reduction error against the full run while retaining the exact chronological
        # result rather than discarding an already-computed full run for a coarser subset.
        result, reduction = measure_reduction_error(
            request,
            strategy,
            stride=settings.reduced_stride_count,
            max_error_fraction=settings.max_reduction_error_fraction,
            return_full=True,
        )
        return result, reduction.to_json_dict()
    return strategy.dispatch(request), None


def _annual_revenue(
    scenario_operating: OperatingScenario,
    series_map: dict[str, Any],
    delivered_heat_j: float,
    durations_s: tuple[float, ...],
    demand_w: tuple[float, ...],
    unserved_w: tuple[float, ...],
) -> float | None:
    if scenario_operating.heat_revenue_tariff is None:
        return None
    ref = scenario_operating.heat_revenue_tariff
    tariff = series_map.get(ref.series_id)
    if tariff is None:
        return None
    if tariff.unit != "currency/J":
        raise ValueError(
            f"heat revenue tariff {ref.series_id!r} must use canonical unit 'currency/J'"
        )
    if len(tariff.values) == len(durations_s):
        return math.fsum(
            max(dem - uns, 0.0) * dt * float(price)
            for dem, uns, dt, price in zip(
                demand_w, unserved_w, durations_s, tariff.values, strict=True
            )
        )
    return float(delivered_heat_j * tariff.time_weighted_mean())


def _generated_routes_by_id(context: StageContext) -> dict[str, GeneratedRoute]:
    routes_payload = context.upstream["routes"].payload if "routes" in context.upstream else {}
    by_id: dict[str, GeneratedRoute] = {}
    for pair in routes_payload.get("pairs", []):
        for raw_route in pair.get("routes", []):
            generated = GeneratedRoute.model_validate(raw_route)
            by_id[generated.route.route_id] = generated
    return by_id


def _additional_customer_heat_w(generated: GeneratedRoute | None) -> float:
    if generated is None:
        return 0.0
    return math.fsum(
        float(customer.design_heat_w)
        for customer in generated.linked_customers
        if customer.design_heat_w is not None and customer.design_heat_w > 0.0
    )


class OperateStage(_Base):
    """Chronological annual dispatch for baseline and every evaluated candidate."""

    name = "operate"
    version = "1.0.0"
    requires: tuple[str, ...] = ("candidates", "routes", "couple")

    def __init__(self, strategy: DispatchStrategy | None = None) -> None:
        self._strategy = default_dispatch_strategy() if strategy is None else strategy

    def input_fingerprint(self, context: StageContext) -> Any:
        scenario = context.scenario
        policy = self._policy(context, PolicyKind.DISPATCH)
        caps = self._strategy.capabilities()
        return {
            "operating_scenarios": [item.to_json_dict() for item in scenario.operating_scenarios],
            "time_series": [item.to_json_dict() for item in scenario.time_series],
            "policy": policy.to_json_dict() if policy else None,
            "strategy": [caps.strategy, caps.strategy_version],
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        if not scenario.operating_scenarios:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="the scenario defines no operating scenarios",
            )
        ctx_policy = self._policy(context, PolicyKind.DISPATCH)
        pre_selected_id: str | None = None
        if ctx_policy is not None:
            try:
                pre_selected_id = DispatchSettings.from_policy(ctx_policy).operating_scenario_id
            except policies.PolicyError as error:
                return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        operating, sel_err = _select_scenario_item(
            scenario.operating_scenarios,
            selected_id=pre_selected_id,
            id_attr="operating_scenario_id",
            noun="operating scenario",
            policy_field="operating_scenario_id",
        )
        if operating is None:
            return self._output(
                StageStatus.INVALID_INPUT, reason=sel_err or "no operating scenario"
            )
        dispatch_policy = self._lookup_dispatch_policy(context, operating)
        if dispatch_policy is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason=f"dispatch policy {operating.dispatch_policy_id!r} is not supplied",
            )
        try:
            settings = DispatchSettings.from_policy(dispatch_policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))
        if (
            settings.operating_scenario_id is not None
            and settings.operating_scenario_id != operating.operating_scenario_id
        ):
            _, mismatch_err = _select_scenario_item(
                scenario.operating_scenarios,
                selected_id=settings.operating_scenario_id,
                id_attr="operating_scenario_id",
                noun="operating scenario",
                policy_field="operating_scenario_id",
            )
            return self._output(
                StageStatus.INVALID_INPUT,
                reason=mismatch_err
                or f"operating_scenario_id {settings.operating_scenario_id!r} does not match",
            )

        series_map = {item.series_id: item.series for item in scenario.time_series}
        geo_avail, geo_min_frac, extra_missing = _geothermal_operating_params(operating, series_map)
        if extra_missing:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="required operating inputs or series are not established",
                missing_inputs=tuple(extra_missing),
            )

        try:
            baseline_request = build_dispatch_request(
                scenario,
                operating,
                request_id=f"{operating.operating_scenario_id}-baseline",
                settings=settings,
                geothermal=None,
            )
        except NotEstablished as error:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="required operating inputs or series are not established",
                missing_inputs=error.missing,
            )
        except ValueError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        baseline_res, baseline_reduction = _evaluate_dispatch(
            baseline_request, self._strategy, settings
        )
        if baseline_res.state is not AdapterState.COMPLETED or baseline_res.totals is None:
            return StageOutput(
                stage=self.name,
                stage_version=self.version,
                status=(
                    StageStatus.UNSUPPORTED_MODEL
                    if baseline_res.state is AdapterState.UNSUPPORTED_MODEL
                    else StageStatus.INVALID_INPUT
                ),
                reason=f"baseline dispatch failed: {baseline_res.reason}",
            )

        generated_routes = _generated_routes_by_id(context)
        coupled_payload = context.upstream["couple"].payload.get("candidates", {})
        caps = self._strategy.capabilities()
        synthetic = scenario.study.synthetic or caps.synthetic
        policy_origin = policies.origin(dispatch_policy)
        derived = (policy_origin, f"dispatch {caps.strategy} {caps.strategy_version}")

        candidate_entries: dict[str, dict[str, Any]] = {}
        subject_results: list[dict[str, Any]] = []
        missing_all: list[MissingInput] = []
        not_modelled_all: list[str] = list(baseline_res.not_modelled)
        counts = {"completed": 0, "unresolved": 0, "not_established": 0}

        for candidate in candidates_of(context.upstream["candidates"]):
            cases = coupled_payload.get(candidate.candidate_id, {})
            entry, kpis_list, cand_syn, cand_missing, cand_not_modelled = _operate_one_candidate(
                scenario=scenario,
                operating=operating,
                settings=settings,
                strategy=self._strategy,
                candidate=candidate,
                cases=cases,
                baseline_request=baseline_request,
                geo_avail=geo_avail,
                geo_min_frac=geo_min_frac,
                generated_route=generated_routes.get(candidate.route_id),
                series_map=series_map,
                derived_from=derived,
            )
            synthetic = synthetic or cand_syn
            candidate_entries[candidate.candidate_id] = entry
            missing_all.extend(cand_missing)
            not_modelled_all.extend(cand_not_modelled)
            if entry["status"] == "completed":
                counts["completed"] += 1
            elif entry["status"] == "not_established":
                counts["not_established"] += 1
            else:
                counts["unresolved"] += 1
            if entry["status"] == "completed" and kpis_list:
                diags = list(entry.get("diagnostics", ()))
                if cand_not_modelled:
                    diags.append(f"dispatch: {', '.join(cand_not_modelled)}")
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "synthetic": cand_syn,
                        "kpis": [kpi.to_json_dict() for kpi in kpis_list],
                        "diagnostics": diags,
                    }
                )
            elif entry["status"] == "not_established":
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "synthetic": cand_syn,
                        "evaluation_status": EvaluationStatus.NOT_EVALUATED.value,
                        "ranking_ineligibility_reason": (
                            "operational dispatch inputs are not established"
                        ),
                        "diagnostics": [
                            *entry.get("diagnostics", ()),
                            f"operation not established: {entry['reason']}",
                        ],
                    }
                )

        baseline_rev = _annual_revenue(
            operating,
            series_map,
            baseline_res.totals.delivered_heat_j,
            baseline_request.durations_s,
            baseline_request.demand_heat_w,
            baseline_res.unserved_w,
        )
        return self._output(
            StageStatus.COMPLETED,
            payload={
                "operating_scenario_id": operating.operating_scenario_id,
                "policy_id": dispatch_policy.policy_id,
                "strategy": f"{caps.strategy} {caps.strategy_version}",
                "baseline": {
                    "alternative_id": scenario.study.baseline_id,
                    "totals": baseline_res.totals.to_json_dict(),
                    "annual_revenue_currency": baseline_rev,
                    "reduction": baseline_reduction,
                },
                "candidates": candidate_entries,
                "counts": counts,
                "not_modelled": list(dict.fromkeys(not_modelled_all)),
                "subject_results": subject_results,
            },
            missing_inputs=tuple(dict.fromkeys(missing_all)),
            synthetic=synthetic,
        )

    def _lookup_dispatch_policy(
        self, context: StageContext, operating: OperatingScenario
    ) -> Policy | None:
        policy = context.policies.get(operating.dispatch_policy_id)
        if policy is not None and policy.kind is PolicyKind.DISPATCH:
            return policy
        return self._policy(context, PolicyKind.DISPATCH)


def _geothermal_operating_params(
    operating: OperatingScenario, series_map: dict[str, Any]
) -> tuple[tuple[float, ...], float, list[MissingInput]]:
    missing: list[MissingInput] = []
    base = f"operating_scenarios[{operating.operating_scenario_id}]"
    avail: tuple[float, ...] = ()
    if operating.geothermal_availability is not None:
        ref = operating.geothermal_availability
        if ref.series_id not in series_map:
            missing.append(
                MissingInput(
                    field_path=f"{base}.geothermal_availability",
                    state="unknown",
                    reason=f"availability series {ref.series_id!r} is not loaded",
                    owner="operation specialist",
                )
            )
        else:
            avail = tuple(float(v) for v in series_map[ref.series_id].values)
    reader = InputReader()
    min_frac = 0.0
    if operating.geothermal_min_output_fraction is not None:
        read = reader.get(
            operating.geothermal_min_output_fraction, f"{base}.geothermal_min_output_fraction"
        )
        if read is not None:
            min_frac = float(read)
    missing.extend(reader.missing)
    return avail, min_frac, missing


def _operate_one_candidate(
    *,
    scenario: Any,
    operating: OperatingScenario,
    settings: DispatchSettings,
    strategy: DispatchStrategy,
    candidate: Candidate,
    cases: dict[str, Any],
    baseline_request: DispatchRequest,
    geo_avail: tuple[float, ...],
    geo_min_frac: float,
    generated_route: GeneratedRoute | None,
    series_map: dict[str, Any],
    derived_from: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[Any, ...], bool, tuple[MissingInput, ...], tuple[str, ...]]:
    coupled_results: list[CouplingResult] = []
    unresolved_reasons: list[str] = []
    cap_diagnostics: list[str] = []
    capacity_case_summaries: list[dict[str, Any]] = []
    cap_case_to_op = (
        dict(zip(candidate.capacity_coupling_case_ids, candidate.capacity_case_ids, strict=True))
        if len(candidate.capacity_coupling_case_ids) == len(candidate.capacity_case_ids)
        else {}
    )
    has_operating = False
    synthetic = False
    for case_id, item in cases.items():
        role = str(
            item.get("role")
            or ("capacity" if case_id in candidate.capacity_coupling_case_ids else "operating")
        )
        summary = item.get("summary", {})
        state = str(summary.get("state", ""))
        synthetic = synthetic or bool(summary.get("synthetic"))
        if role == "capacity":
            op_id = cap_case_to_op.get(case_id, case_id)
            raw_res = item.get("result")
            parsed = CouplingResult.model_validate(raw_res) if raw_res is not None else None
            if state in _UNRESOLVED_COUPLING or parsed is None or parsed.operating_point is None:
                limits = (
                    parsed.limiting_conditions
                    if parsed is not None and parsed.limiting_conditions
                    else tuple(str(x) for x in summary.get("limiting_conditions", ()))
                )
                base_reason = (parsed.reason if parsed is not None else "") or str(
                    summary.get("reason") or state
                )
                reason = (
                    f"{', '.join(limits)}: {base_reason}"
                    if limits and base_reason
                    else (", ".join(limits) or base_reason)
                )
                cap_diagnostics.append(
                    f"capacity case {op_id} ({case_id}): operating point not established "
                    f"({reason}); capacity remains lower_bound"
                )
                capacity_case_summaries.append(
                    {
                        "coupling_case_id": case_id,
                        "operating_case_id": op_id,
                        "outcome": "not_established",
                        "delivered_heat_w": None,
                        "reason": reason,
                    }
                )
                continue
            coupled_results.append(parsed)
            point = parsed.operating_point
            causes = tuple(cause.value for cause in parsed.shortfall_causes)
            delivered_w = (
                0.0
                if "insufficient_temperature" in causes
                else max(point.plant_heat_w - (point.transmission_heat_loss_w or 0.0), 0.0)
            )
            limit_hit = (
                parsed.state.value == "physical_limit_exceeded"
                or point.deficit_heat_w > 1.0
                or bool(causes)
            )
            reason = (
                ", ".join(parsed.limiting_conditions)
                if parsed.limiting_conditions
                else parsed.reason
            )
            capacity_case_summaries.append(
                {
                    "coupling_case_id": case_id,
                    "operating_case_id": op_id,
                    "outcome": "limit_found" if limit_hit else "demand_covered",
                    "delivered_heat_w": delivered_w,
                    "reason": reason,
                }
            )
            continue

        if state in _UNRESOLVED_COUPLING or item.get("result") is None:
            unresolved_reasons.append(f"{case_id}: {summary.get('reason') or state}")
            continue
        coupled_results.append(CouplingResult.model_validate(item["result"]))
        has_operating = True

    if unresolved_reasons or not has_operating or not coupled_results:
        return (
            {
                "status": "unresolved",
                "reason": "; ".join(unresolved_reasons) or "no coupling results available",
                "totals": None,
                "reduction": None,
                "annual_revenue_currency": None,
                "with_additional_demand": None,
                "capacity_basis": None,
                "capacity_cases": capacity_case_summaries,
                "diagnostics": cap_diagnostics,
                "not_modelled": [],
            },
            (),
            synthetic,
            (),
            (),
        )

    geo_spec = geothermal_spec_from_coupling(
        candidate.candidate_id,
        coupled_results,
        availability=geo_avail,
        min_output_fraction=geo_min_frac,
    )
    request = baseline_request.model_copy(
        update={
            "request_id": f"{candidate.candidate_id}-{operating.operating_scenario_id}",
            "geothermal": geo_spec,
        }
    )
    try:
        res, reduction = _evaluate_dispatch(request, strategy, settings)
    except NotEstablished as error:
        return (
            {
                "status": "not_established",
                "reason": "; ".join(item.reason for item in error.missing),
                "totals": None,
                "reduction": None,
                "annual_revenue_currency": None,
                "with_additional_demand": None,
                "capacity_basis": geo_spec.capacity_basis,
                "capacity_cases": capacity_case_summaries,
                "diagnostics": cap_diagnostics,
                "not_modelled": [],
            },
            (),
            synthetic,
            error.missing,
            (),
        )
    if res.state is not AdapterState.COMPLETED or res.totals is None:
        return (
            {
                "status": "unresolved",
                "reason": res.reason or res.state.value,
                "totals": None,
                "reduction": reduction,
                "annual_revenue_currency": None,
                "with_additional_demand": None,
                "capacity_basis": geo_spec.capacity_basis,
                "capacity_cases": capacity_case_summaries,
                "diagnostics": cap_diagnostics,
                "not_modelled": list(res.not_modelled),
            },
            (),
            synthetic,
            (),
            res.not_modelled,
        )

    rev = _annual_revenue(
        operating,
        series_map,
        res.totals.delivered_heat_j,
        request.durations_s,
        request.demand_heat_w,
        res.unserved_w,
    )
    extra_peak_w = _additional_customer_heat_w(generated_route)
    with_extra: dict[str, Any] | None = None
    if extra_peak_w > 0.0 and generated_route is not None:
        extra_req = build_dispatch_request(
            scenario,
            operating,
            request_id=f"{candidate.candidate_id}-{operating.operating_scenario_id}-with-demand",
            settings=settings,
            geothermal=geo_spec,
            additional_peak_heat_w=extra_peak_w,
        )
        extra_res, _ = _evaluate_dispatch(extra_req, strategy, settings)
        if extra_res.state is AdapterState.COMPLETED and extra_res.totals is not None:
            extra_rev = _annual_revenue(
                operating,
                series_map,
                extra_res.totals.delivered_heat_j,
                extra_req.durations_s,
                extra_req.demand_heat_w,
                extra_res.unserved_w,
            )
            with_extra = {
                "linked_customer_count": len(generated_route.linked_customers),
                "additional_peak_heat_w": extra_peak_w,
                "totals": extra_res.totals.to_json_dict(),
                "annual_revenue_currency": extra_rev,
            }

    kpis = operation_kpis(res.totals, derived_from)
    return (
        {
            "status": "completed",
            "reason": "",
            "totals": res.totals.to_json_dict(),
            "reduction": reduction,
            "annual_revenue_currency": rev,
            "with_additional_demand": with_extra,
            "capacity_basis": geo_spec.capacity_basis,
            "capacity_cases": capacity_case_summaries,
            "diagnostics": cap_diagnostics,
            "not_modelled": list(res.not_modelled),
        },
        kpis,
        synthetic,
        (),
        res.not_modelled,
    )


# --- cost and baseline comparison stage -----------------------------------------------------


class CostStage(_Base):
    """Reconciled cost ledgers, asset-life cash flows, LCOH and baseline comparisons."""

    name = "cost"
    version = "1.0.0"
    requires: tuple[str, ...] = ("candidates", "routes", "operate")

    def input_fingerprint(self, context: StageContext) -> Any:
        policy = self._policy(context, PolicyKind.ECONOMIC)
        return {
            "economic_scenarios": [
                item.to_json_dict() for item in context.scenario.economic_scenarios
            ],
            "policy": policy.to_json_dict() if policy else None,
        }

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        if not scenario.economic_scenarios:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="the scenario defines no economic scenarios",
            )
        policy = self._policy(context, PolicyKind.ECONOMIC)
        if policy is None:
            return self._no_policy(PolicyKind.ECONOMIC)
        try:
            settings = EconomicSettings.from_policy(policy)
        except policies.PolicyError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        economic, sel_err = _select_scenario_item(
            scenario.economic_scenarios,
            selected_id=settings.economic_scenario_id,
            id_attr="economic_scenario_id",
            noun="economic scenario",
            policy_field="economic_scenario_id",
        )
        if economic is None:
            return self._output(StageStatus.INVALID_INPUT, reason=sel_err or "no economic scenario")
        operate = context.upstream["operate"].payload
        baseline_op = operate.get("baseline", {})
        baseline_totals = DispatchTotals.model_validate(baseline_op["totals"])
        baseline_rev = baseline_op.get("annual_revenue_currency")

        try:
            baseline_ledger = assemble_baseline_ledger(
                economic,
                baseline_id=scenario.study.baseline_id,
                dispatch_totals=baseline_totals,
            )
        except NotEstablished as error:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="baseline economic inputs are not established",
                missing_inputs=error.missing,
            )
        if not baseline_ledger.clean:
            return self._output(
                StageStatus.INVALID_INPUT,
                reason="; ".join(baseline_ledger.duplicate_ownership_errors),
            )

        try:
            base_unsub = evaluate_boundary_cashflows(
                economic,
                baseline_ledger,
                boundary=CostBoundary.SYSTEM_DELIVERED,
                annual_heat_j=baseline_totals.delivered_heat_j,
                annual_revenue_currency=baseline_rev,
                residual_value_method=settings.residual_value_method,
                case_label="unsubsidised",
            )
            funding = economic.funding_rules if settings.evaluate_funded_case else ()
            base_funded = (
                evaluate_boundary_cashflows(
                    economic,
                    baseline_ledger,
                    boundary=CostBoundary.SYSTEM_DELIVERED,
                    annual_heat_j=baseline_totals.delivered_heat_j,
                    annual_revenue_currency=baseline_rev,
                    residual_value_method=settings.residual_value_method,
                    funding_rules=funding,
                    case_label="funded",
                )
                if funding
                else None
            )
        except NotEstablished as error:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="baseline cash flow inputs are not established",
                missing_inputs=error.missing,
            )
        except ValueError as error:
            return self._output(StageStatus.INVALID_INPUT, reason=str(error))

        routes = {
            key: Route.model_validate(value)
            for key, value in context.upstream["candidates"].payload.get("routes", {}).items()
        }
        generated_routes = _generated_routes_by_id(context)
        op_candidates = operate.get("candidates", {})

        cand_entries: dict[str, dict[str, Any]] = {}
        route_comparisons: list[dict[str, Any]] = []
        subject_results: list[dict[str, Any]] = []
        missing_all: list[MissingInput] = []
        invalid_candidate_reasons: dict[str, str] = {}

        for candidate in candidates_of(context.upstream["candidates"]):
            op_entry = op_candidates.get(candidate.candidate_id, {})
            if op_entry.get("status") != "completed" or op_entry.get("totals") is None:
                cand_entries[candidate.candidate_id] = {
                    "status": "not_evaluated",
                    "reason": op_entry.get("reason", "operation was not evaluated"),
                }
                continue

            totals = DispatchTotals.model_validate(op_entry["totals"])
            route = routes[candidate.route_id]
            generated = generated_routes.get(candidate.route_id)
            rev = op_entry.get("annual_revenue_currency")
            derived = (
                f"economic_scenarios[{economic.economic_scenario_id}]",
                settings.origin,
                str(operate.get("strategy", "dispatch")),
            )

            try:
                ledger = assemble_candidate_ledger(
                    economic,
                    subject_id=candidate.candidate_id,
                    site_id=candidate.site_id,
                    plant_id=candidate.plant_id,
                    route=route,
                    generated_route=generated,
                    connection_id=candidate.connection_id,
                    candidate_id=candidate.candidate_id,
                    alternative_id=None,
                    dispatch_totals=totals,
                    economic_settings=settings,
                )
            except NotEstablished as error:
                missing_all.extend(error.missing)
                cand_entries[candidate.candidate_id] = {
                    "status": "not_established",
                    "reason": "; ".join(item.reason for item in error.missing),
                    "missing_inputs": [item.to_json_dict() for item in error.missing],
                }
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "evaluation_status": EvaluationStatus.NOT_EVALUATED.value,
                        "ranking_ineligibility_reason": "economic evaluation is not established",
                        "diagnostics": [
                            f"economics not established: needs {item.field_path}"
                            for item in error.missing
                        ],
                    }
                )
                continue

            if not ledger.clean:
                reason = "; ".join(ledger.duplicate_ownership_errors)
                invalid_candidate_reasons[candidate.candidate_id] = reason
                cand_entries[candidate.candidate_id] = {
                    "status": "invalid_input",
                    "reason": reason,
                    "reconciliation": ledger.to_json_dict(),
                }
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "evaluation_status": EvaluationStatus.INVALID_INPUT.value,
                        "ranking_ineligibility_reason": "cost ownership reconciliation failed",
                        "checks": [
                            Check(
                                check_id="cost_reconciliation",
                                description="no cost item is claimed by more than one owner",
                                outcome=CheckOutcome.FAIL,
                                observed=float(len(ledger.duplicate_ownership_errors)),
                                limit=0.0,
                                margin=-float(len(ledger.duplicate_ownership_errors)),
                                unit="count",
                                threshold_origin="specification section 13.1 (A09)",
                            ).to_json_dict()
                        ],
                        "diagnostics": list(ledger.duplicate_ownership_errors),
                    }
                )
                continue

            try:
                plant_b, geo_b, sys_b = _evaluate_three_boundaries(
                    economic,
                    ledger,
                    totals=totals,
                    annual_revenue=rev,
                    residual_value_method=settings.residual_value_method,
                )
                comparison = compare_to_baseline(
                    baseline_id=scenario.study.baseline_id,
                    baseline=base_unsub,
                    candidate=sys_b,
                    geothermal_displaced_heat_j=totals.geothermal_delivered_heat_j,
                    baseline_annual_heat_j=baseline_totals.delivered_heat_j,
                    candidate_annual_heat_j=totals.delivered_heat_j,
                )
                funded_geo: BoundaryEconomics | None = None
                funded_sys: BoundaryEconomics | None = None
                funded_comp: BaselineComparison | None = None
                if base_funded is not None:
                    _, funded_geo, funded_sys = _evaluate_three_boundaries(
                        economic,
                        ledger,
                        totals=totals,
                        annual_revenue=rev,
                        residual_value_method=settings.residual_value_method,
                        funding_rules=funding,
                        case_label="funded",
                    )
                    funded_comp = compare_to_baseline(
                        baseline_id=scenario.study.baseline_id,
                        baseline=base_funded,
                        candidate=funded_sys,
                        geothermal_displaced_heat_j=totals.geothermal_delivered_heat_j,
                        baseline_annual_heat_j=baseline_totals.delivered_heat_j,
                        candidate_annual_heat_j=totals.delivered_heat_j,
                    )
                extra_cmp = (
                    _evaluate_with_additional_demand(
                        economic=economic,
                        settings=settings,
                        candidate=candidate,
                        route=route,
                        generated=generated,
                        base_totals=totals,
                        base_geo=geo_b,
                        base_sys=sys_b,
                        extra_op=op_entry["with_additional_demand"],
                    )
                    if op_entry.get("with_additional_demand") is not None
                    else None
                )
            except NotEstablished as error:
                missing_all.extend(error.missing)
                cand_entries[candidate.candidate_id] = {
                    "status": "not_established",
                    "reason": "; ".join(item.reason for item in error.missing),
                    "missing_inputs": [item.to_json_dict() for item in error.missing],
                }
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "evaluation_status": EvaluationStatus.NOT_EVALUATED.value,
                        "ranking_ineligibility_reason": "economic evaluation is not established",
                        "diagnostics": [
                            f"economics not established: needs {item.field_path}"
                            for item in error.missing
                        ],
                    }
                )
                continue
            except ValueError as error:
                reason = str(error)
                invalid_candidate_reasons[candidate.candidate_id] = reason
                cand_entries[candidate.candidate_id] = {
                    "status": "invalid_input",
                    "reason": reason,
                    "reconciliation": ledger.to_json_dict(),
                }
                subject_results.append(
                    {
                        "subject": candidate.candidate_id,
                        "evaluation_status": EvaluationStatus.INVALID_INPUT.value,
                        "ranking_ineligibility_reason": reason,
                        "diagnostics": [f"economics invalid input: {reason}"],
                    }
                )
                continue

            kpis = economic_kpis(
                plant=plant_b,
                geothermal_delivered=geo_b,
                system_delivered=sys_b,
                comparison=comparison,
                funded_geothermal=funded_geo,
                funded_comparison=funded_comp,
                derived_from=derived,
            )
            reconcile_check = Check(
                check_id="cost_reconciliation",
                description="no asset cost is claimed by more than one owner",
                outcome=CheckOutcome.PASS,
                observed=0.0,
                limit=0.0,
                margin=0.0,
                unit="count",
                threshold_origin="specification section 13.1 (A09)",
            )
            with_extra_entry: dict[str, Any] | None = None
            if extra_cmp is not None:
                with_extra_entry = extra_cmp.to_json_dict()
                route_comparisons.append(with_extra_entry)

            cand_entries[candidate.candidate_id] = {
                "status": "completed",
                "reconciliation": ledger.to_json_dict(),
                "boundaries": {
                    CostBoundary.PLANT.value: plant_b.to_json_dict(),
                    CostBoundary.GEOTHERMAL_DELIVERED.value: geo_b.to_json_dict(),
                    CostBoundary.SYSTEM_DELIVERED.value: sys_b.to_json_dict(),
                },
                "baseline_comparison": comparison.to_json_dict(),
                "funded": (
                    {
                        CostBoundary.GEOTHERMAL_DELIVERED.value: funded_geo.to_json_dict(),
                        CostBoundary.SYSTEM_DELIVERED.value: funded_sys.to_json_dict(),
                        "baseline_comparison": funded_comp.to_json_dict(),
                    }
                    if funded_geo is not None and funded_sys is not None and funded_comp is not None
                    else None
                ),
                "with_additional_demand": with_extra_entry,
            }
            subject_results.append(
                {
                    "subject": candidate.candidate_id,
                    "kpis": [kpi.to_json_dict() for kpi in kpis],
                    "checks": [reconcile_check.to_json_dict()],
                    "diagnostics": [f"economics: {line}" for line in sys_b.not_modelled],
                }
            )

        if invalid_candidate_reasons and len(cand_entries) == len(invalid_candidate_reasons):
            return self._output(
                StageStatus.INVALID_INPUT,
                reason="; ".join(dict.fromkeys(invalid_candidate_reasons.values())),
                payload={
                    "economic_scenario_id": economic.economic_scenario_id,
                    "candidates": cand_entries,
                    "subject_results": subject_results,
                },
            )

        return self._output(
            StageStatus.COMPLETED,
            payload={
                "economic_scenario_id": economic.economic_scenario_id,
                "policy_id": policy.policy_id,
                "baseline": {
                    "baseline_id": scenario.study.baseline_id,
                    "reconciliation": baseline_ledger.to_json_dict(),
                    "unsubsidised": base_unsub.to_json_dict(),
                    "funded": base_funded.to_json_dict() if base_funded is not None else None,
                },
                "candidates": cand_entries,
                "route_demand_comparisons": route_comparisons,
                "not_modelled": list(base_unsub.not_modelled),
                "subject_results": subject_results,
            },
            missing_inputs=tuple(dict.fromkeys(missing_all)),
        )


def _evaluate_three_boundaries(
    economic: Any,
    ledger: ReconciliationTable,
    *,
    totals: DispatchTotals,
    annual_revenue: float | None,
    residual_value_method: str,
    funding_rules: tuple[Any, ...] = (),
    case_label: Any = "unsubsidised",
) -> tuple[BoundaryEconomics, BoundaryEconomics, BoundaryEconomics]:
    elec_j = totals.source_pump_electricity_j + totals.compressor_electricity_j
    plant_b = evaluate_boundary_cashflows(
        economic,
        ledger,
        boundary=CostBoundary.PLANT,
        annual_heat_j=totals.geothermal_plant_handover_heat_j,
        annual_electricity_j=elec_j,
        residual_value_method=residual_value_method,
        funding_rules=funding_rules,
        case_label=case_label,
    )
    geo_b = evaluate_boundary_cashflows(
        economic,
        ledger,
        boundary=CostBoundary.GEOTHERMAL_DELIVERED,
        annual_heat_j=totals.geothermal_delivered_heat_j,
        annual_electricity_j=elec_j,
        residual_value_method=residual_value_method,
        funding_rules=funding_rules,
        case_label=case_label,
    )
    sys_b = evaluate_boundary_cashflows(
        economic,
        ledger,
        boundary=CostBoundary.SYSTEM_DELIVERED,
        annual_heat_j=totals.delivered_heat_j,
        annual_electricity_j=elec_j,
        annual_revenue_currency=annual_revenue,
        residual_value_method=residual_value_method,
        funding_rules=funding_rules,
        case_label=case_label,
    )
    return plant_b, geo_b, sys_b


def _evaluate_with_additional_demand(
    *,
    economic: Any,
    settings: EconomicSettings,
    candidate: Candidate,
    route: Route,
    generated: GeneratedRoute | None,
    base_totals: DispatchTotals,
    base_geo: BoundaryEconomics,
    base_sys: BoundaryEconomics,
    extra_op: dict[str, Any],
) -> RouteDemandOptionResult | None:
    if generated is None:
        return None
    extra_totals = DispatchTotals.model_validate(extra_op["totals"])
    extra_ledger = assemble_candidate_ledger(
        economic,
        subject_id=f"{candidate.candidate_id}-with-demand",
        site_id=candidate.site_id,
        plant_id=candidate.plant_id,
        route=route,
        generated_route=generated,
        connection_id=candidate.connection_id,
        candidate_id=candidate.candidate_id,
        alternative_id=None,
        dispatch_totals=extra_totals,
        economic_settings=settings,
        include_additional_customers=True,
    )
    if not extra_ledger.clean:
        return None
    _, extra_geo, extra_sys = _evaluate_three_boundaries(
        economic,
        extra_ledger,
        totals=extra_totals,
        annual_revenue=extra_op.get("annual_revenue_currency"),
        residual_value_method=settings.residual_value_method,
    )
    return RouteDemandOptionResult(
        candidate_id=candidate.candidate_id,
        route_id=route.route_id,
        linked_customer_count=int(extra_op["linked_customer_count"]),
        additional_peak_heat_w=float(extra_op["additional_peak_heat_w"]),
        base_geothermal_delivered_j=base_totals.geothermal_delivered_heat_j,
        with_demand_geothermal_delivered_j=extra_totals.geothermal_delivered_heat_j,
        base_lcoh_geothermal_delivered_currency_per_j=base_geo.lcoh_currency_per_j,
        with_demand_lcoh_geothermal_delivered_currency_per_j=extra_geo.lcoh_currency_per_j,
        base_pv_cost_currency=base_sys.pv_cost_currency,
        with_demand_pv_cost_currency=extra_sys.pv_cost_currency,
        base_npv_currency=base_sys.npv_currency,
        with_demand_npv_currency=extra_sys.npv_currency,
    )
