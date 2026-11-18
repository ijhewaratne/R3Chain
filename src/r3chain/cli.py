"""Command line interface.

    r3chain validate FILE              check a scenario file
    r3chain assumptions FILE|REVISION  list every engineering input with its provenance
    r3chain run FILE                   freeze the scenario as a revision and evaluate it
    r3chain variant REVISION ...       create a changed revision with a field level change record
    r3chain show RUN|REVISION          show a stored run or revision
    r3chain replay RUN                 execute a stored run again and compare the result
    r3chain network SCENARIO CASE      solve one operating case of a network
    r3chain couple SCENARIO            evaluate the coupling cases of a scenario file
    r3chain candidates RUN             candidate, ranking and rejection tables of a run
    r3chain economics RUN              operation, cost ledger, LCOH and baseline comparison of a run
    r3chain compare RUN                uncertainty, Pareto and resilience comparison of a run
    r3chain report RUN                 export reproducible Markdown or JSON evidence report
    r3chain layers RUN DIRECTORY       write the map layers of a run as GeoJSON
    r3chain policies [DIRECTORY]       list policy files with their hashes
    r3chain schemas export|check       write or verify the committed JSON Schemas

Every command works without an AI model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from r3chain.coupling.cases import CaseOutcome, MemoNetworkModel, evaluate_case
from r3chain.coupling.loop import CouplingResult
from r3chain.domain.assumptions import assumption_register
from r3chain.domain.records.runs import (
    Assumption,
    CheckOutcome,
    PolicyKind,
    Result,
    Run,
    StageOutput,
)
from r3chain.domain.records.scenario import FieldChange, Scenario, ScenarioRevision
from r3chain.domain.status import AdapterState
from r3chain.domain.units import JOULES_PER_MWH, joule_to_mwh, kelvin_to_celsius, pa_to_bar
from r3chain.domain.validation import (
    Severity,
    ValidationReport,
    load_scenario_file,
    read_data_file,
)
from r3chain.pipeline.evaluation import coupling_case_summary, single_policy
from r3chain.pipeline.runner import ReplayOutcome, ScenarioNotExecutableError
from r3chain.pipeline.stages import default_source_models
from r3chain.ports.network import NetworkCase, NetworkResult
from r3chain.schemas import (
    DEFAULT_SCHEMA_DIRECTORY,
    SchemaChangedError,
    check_schemas,
    export_schemas,
)
from r3chain.store.store import (
    ImmutabilityError,
    NotFoundError,
    Store,
    load_policies,
    policy_ref,
)

DEFAULT_STORE = ".r3chain"
DEFAULT_POLICY_DIRECTORY = "policies"


def _store_root(arguments: argparse.Namespace) -> Path:
    return Path(arguments.store or os.environ.get("R3CHAIN_HOME", DEFAULT_STORE))


def _store(arguments: argparse.Namespace) -> Store:
    return Store(_store_root(arguments))


def _service(arguments: argparse.Namespace) -> Any:
    from r3chain.services import PlanningService

    policies = Path(getattr(arguments, "policies", DEFAULT_POLICY_DIRECTORY))
    return PlanningService(_store_root(arguments), policy_directory=policies)


def _compact(value: Any, limit: int = 60) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _describe_evidence(value: Any) -> str:
    """Render one side of a change. Engineering inputs are shown with their provenance."""
    if isinstance(value, dict) and value.get("state") == "known":
        provenance = value.get("provenance", {}).get("provenance_type", "?")
        return f"{_compact(value.get('value'), 40)} ({provenance})"
    if isinstance(value, dict) and value.get("state") == "unknown":
        return f"unknown ({value.get('reason', '')})"
    if isinstance(value, dict) and value.get("state") == "conflict":
        return f"conflict between {len(value.get('candidates', []))} values"
    return _compact(value)


def _describe_source(source: dict[str, Any]) -> str:
    if source.get("state") != "completed":
        return f"{source.get('state')} ({source.get('reason', '')})"
    summary = source["summary"]
    points = summary["points"]
    engine = f"{source['engine']} {source['engine_version']}"
    if source.get("synthetic"):
        engine += ", a synthetic test engine"
    text = f"{engine}. {points.get('completed', 0)} of {sum(points.values())} grid points feasible"
    if summary["largest_heat_w"] is not None:
        text += (
            f", largest heat {summary['largest_heat_w'] / 1e6:.2f} MW "
            f"at {summary['at_mass_flow_kg_s']:g} kg/s"
        )
    return text


def _print_network_result(result: NetworkResult) -> None:
    print(f"Case      {result.case_id} on network {result.network_id}")
    print(f"Engine    {result.engine} {result.engine_version}, {result.calculation.value}")
    print(f"State     {result.state.value}" + (f": {result.reason}" if result.reason else ""))
    if not result.nodes:
        return
    reference = result.pressure_reference.value if result.pressure_reference else ""
    for point in result.supply_points:
        heat = (
            "" if point.heat_supplied_w is None else f", heat {point.heat_supplied_w / 1e3:.1f} kW"
        )
        print(
            f"\nSupply point {point.supply_point_id}: {point.mass_flow_kg_s:.3f} kg/s{heat}, "
            f"lift {pa_to_bar(point.pressure_lift_pa):.3f} bar, "
            f"hydraulic power {point.hydraulic_power_w / 1e3:.2f} kW"
        )
        print("  This is an ideal source. Its heat is what a finite source would have to provide.")
    print("\nConsumers")
    for consumer in result.consumers:
        if consumer.closed:
            print(f"  {consumer.consumer_id:<14}closed")
            continue
        temperatures = ""
        if consumer.supply_temperature_k and consumer.return_temperature_k:
            temperatures = (
                f", {kelvin_to_celsius(consumer.supply_temperature_k):.2f} degC in, "
                f"{kelvin_to_celsius(consumer.return_temperature_k):.2f} degC out"
            )
        print(
            f"  {consumer.consumer_id:<14}{consumer.mass_flow_kg_s:7.3f} kg/s, "
            f"{consumer.delivered_heat_w / 1e3:8.1f} kW, "
            f"{pa_to_bar(consumer.differential_pressure_pa):.3f} bar across{temperatures}"
        )
    lowest = min(result.nodes, key=lambda node: node.pressure.value_pa)
    highest = max(result.nodes, key=lambda node: node.pressure.value_pa)
    print(f"\nNode pressure ({reference})")
    print(f"  lowest  {pa_to_bar(lowest.pressure.value_pa):7.3f} bar at {lowest.node_id}")
    print(f"  highest {pa_to_bar(highest.pressure.value_pa):7.3f} bar at {highest.node_id}")
    if result.energy_balance is not None:
        balance = result.energy_balance
        print("\nEnergy balance")
        print(f"  supplied  {balance.heat_supplied_w / 1e3:10.1f} kW")
        print(f"  delivered {balance.heat_delivered_w / 1e3:10.1f} kW")
        print(f"  pipe loss {balance.pipe_heat_loss_w / 1e3:10.1f} kW")
        print(f"  residual  {balance.residual_w / 1e3:10.3f} kW")
    print("\nChecks")
    for check in result.checks:
        margin = "" if check.margin is None else f", margin {check.margin:.0f} {check.unit}"
        print(f"  {check.outcome.value:<5}{check.check_id}: {check.description}{margin}")
    for warning in result.warnings:
        print(f"Warning: {warning}")


def _kw(power_w: float) -> str:
    return f"{power_w / 1e3:10.1f} kW"


def _degc(temperature_k: float | None) -> str:
    return "-" if temperature_k is None else f"{kelvin_to_celsius(temperature_k):.2f} degC"


def _describe_coupling_case(summary: dict[str, Any]) -> str:
    """One line for a coupling case in a run listing."""
    point = summary.get("operating_point")
    if point is None:
        return f"{summary['state']} ({summary.get('reason', '')})"
    text = (
        f"{summary['state']}. Plant {point['plant_heat_w'] / 1e6:.2f} of "
        f"{point['demand_heat_w'] / 1e6:.2f} MW"
    )
    if point["backup_heat_w"] > 0.0:
        text += f", backup {point['backup_heat_w'] / 1e6:.2f} MW"
    if point["deficit_heat_w"] > 0.0 and summary["state"] != "completed":
        text += f", deficit {point['deficit_heat_w'] / 1e6:.2f} MW"
    if summary["limiting_conditions"]:
        text += f", limited by {', '.join(summary['limiting_conditions'])}"
    if summary.get("performance_basis") == "screening_model":
        text += ". Heat pump numbers are from a screening model"
    return text


def _print_coupling_result(result: CouplingResult) -> None:
    print(f"Case      {result.coupling_case_id}, arrangement {result.arrangement}")
    print(f"State     {result.state.value}" + (f": {result.reason}" if result.reason else ""))
    label = ", a synthetic test engine" if result.synthetic else ""
    print(f"Engines   {result.engines['source']}{label}; {result.engines['network']}")
    if result.performance_basis == "screening_model":
        print("          Heat pump numbers come from a screening model, not from equipment data.")
    point = result.operating_point
    if point is not None:
        print("\nOperating point")
        print(
            f"  network     {point.network_mass_flow_kg_s:7.3f} kg/s, return "
            f"{_degc(point.network_return_temperature_k)}, required supply "
            f"{_degc(point.required_supply_temperature_k)}, plant outlet "
            f"{_degc(point.plant_outlet_temperature_k)}"
        )
        if point.source_operated:
            print(
                f"  geothermal  {point.geothermal_mass_flow_kg_s:7.3f} kg/s, wellhead "
                f"{_degc(point.wellhead_temperature_k)}, injection "
                f"{_degc(point.injection_temperature_k)}"
            )
        else:
            print(
                f"  geothermal  not operated. Wellhead {_degc(point.wellhead_temperature_k)} "
                "at the trial flow"
            )
        print("\nHeat at the connection")
        print(f"  demand    {_kw(point.demand_heat_w)}")
        print(f"  plant     {_kw(point.plant_heat_w)}")
        if result.backup_id is not None:
            print(f"  backup    {_kw(point.backup_heat_w)}   {result.backup_id}")
        print(f"  deficit   {_kw(point.deficit_heat_w)}")
        print("Electricity")
        print(f"  well pumps{_kw(point.source_pump_electricity_w)}")
        print(f"  compressor{_kw(point.compressor_electricity_w)}")
    if result.limiting_conditions:
        causes = ", ".join(cause.value for cause in result.shortfall_causes)
        print(f"\nLimited by  {', '.join(result.limiting_conditions)} ({causes})")
    if result.checks:
        print("\nChecks")
        for check in result.checks:
            if check.outcome is CheckOutcome.NOT_EVALUATED:
                print(f"  open  {check.check_id}: {check.description}")
                continue
            margin = ""
            if check.margin is not None:
                # A margin of a few rounding errors reads as zero.
                shown = 0.0 if abs(check.margin) < 1e-9 else check.margin
                margin = f", margin {shown:.4g} {check.unit}"
            print(f"  {check.outcome.value:<6}{check.check_id}{margin}")
    if result.iterations:
        line = f"\nIterations  {len(result.iterations)}, source evaluations "
        line += f"{result.source_evaluations_count}"
        if result.residuals is not None:
            line += (
                f". Residuals: {result.residuals.temperature_delta_k:.2g} K, "
                f"{result.residuals.heat_fraction:.2g} of the heat, "
                f"{result.residuals.flow_fraction:.2g} of the flow"
            )
        print(line)
        for record in result.iterations:
            if record.note:
                print(f"  pass {record.iteration}: {record.note}")
    if result.ledger is not None:
        print("\nEnergy ledger")
        for entry in result.ledger.entries:
            print(
                f"  {entry.entry_id:<28}{_kw(entry.power_w)}  {entry.quantity.value:<16}"
                f"{entry.from_boundary} -> {entry.to_boundary}"
            )
        for balance in result.ledger.balances:
            state = "closed" if balance.closed else "NOT CLOSED"
            print(
                f"  balance of {balance.boundary}: residual {balance.residual_w / 1e3:.3f} kW, "
                f"{state}"
            )
    for warning in result.warnings:
        print(f"Note: {warning}")
    if result.not_modelled:
        print("Not modelled: " + "; ".join(result.not_modelled))


def _print_case_outcome(outcome: CaseOutcome) -> None:
    if outcome.result is not None:
        _print_coupling_result(outcome.result)
        return
    print(f"Case      {outcome.coupling_case_id}")
    print(f"State     {outcome.status.value}: {outcome.reason}")
    for item in outcome.missing_inputs:
        print(f"  needs {item.field_path} ({item.state})")


# --- printing ---------------------------------------------------------------------------------


def _print_report(report: ValidationReport) -> None:
    print(f"Scenario  {report.scenario_id or '(unreadable)'}")
    if report.revision_id:
        print(f"Revision  {report.revision_id}")
    state = "executable" if report.executable else f"NOT executable, {len(report.errors)} error(s)"
    print(f"Status    {state}")
    if report.contains_synthetic_values:
        print("Synthetic values are present. Results are labelled synthetic.")
    for severity, title in (
        (Severity.ERROR, "Errors"),
        (Severity.WARNING, "Warnings"),
        (Severity.UNVERIFIED, "Checks that could not be carried out"),
    ):
        issues = [issue for issue in report.issues if issue.severity is severity]
        if not issues:
            continue
        print(f"\n{title} ({len(issues)})")
        for issue in issues:
            print(f"  {issue.field_path or '(scenario)'}  [{issue.code}]")
            print(f"      {issue.message}")
    if report.missing_inputs:
        print(f"\nInputs not established ({len(report.missing_inputs)})")
        for item in report.missing_inputs:
            owner = f"  owner: {item.owner}" if item.owner else ""
            print(f"  {item.state:<9}{item.field_path}{owner}")
            print(f"      {item.reason}")


def _print_assumptions(rows: Sequence[Assumption]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status.value] = counts.get(row.status.value, 0) + 1
        origin = row.provenance_type.value if row.provenance_type else "-"
        source = f" from {row.source_id}" if row.source_id else ""
        value = "-" if row.value is None else _compact(row.value, 48)
        unit = f" {row.unit}" if row.unit else ""
        print(f"{row.status.value:<9}{row.field_path}")
        print(f"         {value}{unit}   [{origin}{source}]")
        if row.note:
            print(f"         {row.note}" + (f" (owner: {row.owner})" if row.owner else ""))
    summary = ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
    print(f"\n{len(rows)} inputs: {summary}")


def _print_changes(changes: Sequence[FieldChange]) -> None:
    for change in changes:
        if change.kind.value == "changed":
            before = _describe_evidence(change.before)
            after = _describe_evidence(change.after)
            print(f"  changed  {change.field_path}: {before}  ->  {after}")
        elif change.kind.value == "added":
            print(f"  added    {change.field_path}: {_describe_evidence(change.after)}")
        else:
            print(f"  removed  {change.field_path}: {_describe_evidence(change.before)}")


def _print_run(run: Run, results: Sequence[Result], *, detail: bool) -> None:
    print(f"Run       {run.run_id}")
    print(f"Revision  {run.scenario_revision}")
    print(f"Code      {run.code_version}   seed {run.seed}   status {run.status.value}")
    if run.parent_run_ids:
        print(f"Parents   {', '.join(run.parent_run_ids)}")
    outputs = {output.stage: output for result in results for output in result.stage_outputs}
    print("\nStages")
    for stage in run.stages:
        if stage.cache_hit:
            how = f"reused from {stage.reused_from_run_id}"
        elif stage.status.value == "not_run":
            how = "-"
        else:
            how = "computed"
        print(f"  {stage.stage:<15}{stage.status.value:<17}{how}")
        output = outputs.get(stage.stage)
        if output is not None and output.reason:
            print(f"      {output.reason}")
        if detail and output is not None:
            _print_stage_detail(output)
    study = next((result for result in results if result.subject == "study"), None)
    if study is not None:
        label = "synthetic" if study.synthetic else "not synthetic"
        print(
            f"\nResult    study: technical {study.technical_status.value}, "
            f"evaluation {study.evaluation_status.value}, {label}"
        )
        if not study.ranking_eligibility.eligible:
            print(f"          not rankable: {'; '.join(study.ranking_eligibility.reasons)}")
    assess = outputs.get("assess")
    if assess is not None and assess.payload.get("assessments"):
        _print_candidate_summary(assess.payload)
    print(f"Result hash  {run.result_hash}")


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"{value / 1e6:.2f} M"


def _print_stage_detail(output: StageOutput) -> None:
    for item in output.missing_inputs:
        print(f"      needs {item.field_path} ({item.state})")
    payload = output.payload
    if output.stage == "site_evidence":
        for site_id, summary in payload.get("sites", {}).items():
            print(f"      site {site_id}: {summary['evidence_status']}")
    elif output.stage == "source":
        for source_id, source in payload.get("sources", {}).items():
            print(f"      source {source_id}: {_describe_source(source)}")
    elif output.stage == "screen":
        for site_id, screening in payload.get("sites", {}).items():
            area = screening.get("usable_pad_area_m2")
            pad = f", usable pad {area:.0f} m2" if isinstance(area, int | float) else ""
            print(f"      site {site_id}: {screening['status']}{pad}")
            for item in screening["criteria"]:
                if item["outcome"] != "pass":
                    print(f"          {item['criterion_id']} {item['outcome']}: {item['reason']}")
    elif output.stage == "routes":
        for pair in payload.get("pairs", []):
            head = f"      {pair['site_id']} to {pair['connection_id']}: {pair['status']}"
            print(head + (f" ({pair['reason']})" if pair["reason"] else ""))
            for generated in pair["routes"]:
                route = generated["route"]
                print(
                    f"          {route['route_id']:<20}{route['corridor_length_m']:7.0f} m  "
                    f"{_money(generated['cost']['total_currency']):>10}  "
                    f"{', '.join(generated['bases'])}"
                )
        for site_id, reason in payload.get("skipped_sites", {}).items():
            print(f"      site {site_id} skipped: {reason}")
    elif output.stage == "candidates":
        counts = payload.get("counts", {})
        print(
            f"      {counts.get('candidates', 0)} candidates, {counts.get('rejected', 0)} rejected"
        )
        for item in payload.get("rejected", []):
            option = ", ".join(f"{key} {value}" for key, value in item["option"].items())
            print(f"      rejected {option}: {item['reason']}")
    elif output.stage == "couple":
        counts = ", ".join(f"{value} {key}" for key, value in payload.get("counts", {}).items())
        if counts:
            print(f"      coupling cases: {counts}")
    elif output.stage == "operate":
        baseline = payload.get("baseline", {})
        totals = baseline.get("totals")
        if totals:
            print(
                f"      baseline {baseline.get('alternative_id', '?')}: "
                f"{joule_to_mwh(totals['delivered_heat_j']):.0f} MWh delivered, "
                f"base load {joule_to_mwh(totals['base_load_heat_j']):.0f} MWh, "
                f"backup {joule_to_mwh(totals['backup_heat_j']):.0f} MWh"
            )
        op_counts = payload.get("counts", {})
        if op_counts:
            print(
                f"      candidates operated: {op_counts.get('completed', 0)} completed, "
                f"{op_counts.get('unresolved', 0)} unresolved"
            )
    elif output.stage == "cost":
        baseline = payload.get("baseline", {}).get("unsubsidised", {})
        if baseline:
            lcoh = baseline.get("lcoh_currency_per_j")
            lcoh_txt = f"{lcoh * JOULES_PER_MWH:.2f}/MWh" if lcoh is not None else "undefined"
            print(
                f"      baseline PV cost {_money(baseline.get('pv_cost_currency'))}, "
                f"LCOH {lcoh_txt} ({baseline.get('currency', '')})"
            )


def _candidate_line(item: dict[str, Any]) -> str:
    rank = str(item["rank"]) if item.get("rank") is not None else "-"
    cost = next(
        (
            kpi["value"]
            for kpi in item["kpis"]
            if kpi["definition"]["kpi_id"] == "indicative_route_cost_currency"
        ),
        None,
    )
    note = ", ".join(item["limiting_conditions"][:2])
    if not note and not item["ranking_eligibility"]["eligible"]:
        note = "; ".join(item["ranking_eligibility"]["reasons"][:1])
    return (
        f"  {rank:>4}  {item['label'][:49]:<50}{item['technical_status']:<25}"
        f"{item['screening_status']:<12}{_money(cost):>10}  {note}"
    )


def _print_candidate_summary(payload: dict[str, Any]) -> None:
    assessments = sorted(
        payload["assessments"],
        key=lambda item: (item.get("rank") is None, item.get("rank") or 0, item["label"]),
    )
    print(f"\nCandidates ({len(assessments)})")
    header = f"  {'rank':>4}  {'candidate':<50}{'technical status':<25}{'screening':<12}"
    print(header + f"{'route cost':>10}")
    for item in assessments:
        print(_candidate_line(item))
    ranking = payload.get("ranking", {})
    if ranking:
        print(f"  {ranking['statement']}")


# --- commands -------------------------------------------------------------------------------


def _load_or_report(path: Path) -> Scenario | None:
    scenario, report = load_scenario_file(path)
    if scenario is None or not report.executable:
        _print_report(report)
        return None
    return scenario


def cmd_validate(arguments: argparse.Namespace) -> int:
    _, report = load_scenario_file(Path(arguments.file))
    if arguments.json:
        print(report.model_dump_json(indent=2))
    else:
        _print_report(report)
    return 0 if report.executable else 1


def cmd_assumptions(arguments: argparse.Namespace) -> int:
    target = arguments.target
    if Path(target).exists():
        scenario, report = load_scenario_file(Path(target))
        if scenario is None:
            _print_report(report)
            return 1
    else:
        store = _store(arguments)
        scenario = store.get_scenario(target)
    rows = assumption_register(scenario)
    if arguments.status:
        rows = tuple(row for row in rows if row.status.value == arguments.status)
    if arguments.json:
        print(json.dumps([row.to_json_dict() for row in rows], indent=2))
    else:
        _print_assumptions(rows)
    return 0


def cmd_run(arguments: argparse.Namespace) -> int:
    from r3chain.services import AccessPrincipal

    path = Path(arguments.file)
    scenario = _load_or_report(path)
    if scenario is None:
        return 1
    svc = _service(arguments)
    principal = AccessPrincipal.local_default()
    try:
        revision, _ = svc.register_inputs_and_scenario(
            principal,
            scenario,
            base_dir=path.parent,
            policies_dir=Path(arguments.policies),
            author=arguments.author,
            rationale=arguments.rationale,
            parent_revision_id=arguments.parent,
        )
        res = svc.evaluate_study(
            principal,
            study_id=scenario.study.study_id,
            revision=revision.revision_id,
            policies_dir=Path(arguments.policies),
            seed=arguments.seed,
            use_cache=not arguments.no_cache,
            wait=True,
        )
        run = Run.model_validate(res.data["run"])
        store = _store(arguments)
        results = store.get_results(run.run_id)
        store.close()
        _print_run(run, results, detail=False)
        return 0
    finally:
        svc.close()


def cmd_variant(arguments: argparse.Namespace) -> int:
    from r3chain.services import AccessPrincipal

    patches: list[dict[str, Any]] = []
    for assignment in arguments.set or []:
        field_path, _, text = assignment.partition("=")
        patches.append({"op": "set", "path": field_path, "value": json.loads(text)})
    for assignment in arguments.set_known or []:
        field_path, _, text = assignment.partition("=")
        patches.append({"op": "set_known", "path": field_path, "value": json.loads(text)})
    for field_path in arguments.remove or []:
        patches.append({"op": "remove", "path": field_path})
    if not patches:
        print("error: give at least one --set, --set-known or --remove", file=sys.stderr)
        return 2

    svc = _service(arguments)
    principal = AccessPrincipal.local_default()
    try:
        res = svc.create_scenario_variant(
            principal,
            parent_revision=arguments.revision,
            explicit_patch=patches,
            rationale=arguments.rationale,
            author=arguments.author,
        )
        if res.failure_class is not None:
            _print_report(ValidationReport.model_validate(res.data["validation"]))
            return 1
        revision = ScenarioRevision.model_validate(res.data["revision"])
        print(f"Revision  {revision.revision_id}")
        print(f"Parent    {revision.parent_revision_id}")
        print(f"Changes ({len(revision.changes)})")
        _print_changes(revision.changes)
        if arguments.run:
            eval_res = svc.evaluate_study(
                principal,
                study_id=res.study_id or "",
                revision=revision.revision_id,
                wait=True,
            )
            run = Run.model_validate(eval_res.data["run"])
            store = _store(arguments)
            results = store.get_results(run.run_id)
            store.close()
            print()
            _print_run(run, results, detail=False)
        return 0
    finally:
        svc.close()


def cmd_show(arguments: argparse.Namespace) -> int:
    store = _store(arguments)
    identifier = arguments.identifier
    if identifier.startswith("run-"):
        _print_run(store.get_run(identifier), store.get_results(identifier), detail=True)
        return 0
    revision = store.get_revision(identifier)
    print(f"Revision  {revision.revision_id}")
    print(f"Scenario  {revision.scenario_id}")
    print(f"Parent    {revision.parent_revision_id or '-'}")
    print(f"Created   {revision.created_at.isoformat()} by {revision.author}")
    print(f"Rationale {revision.rationale}")
    if revision.changes:
        print(f"Changes ({len(revision.changes)})")
        _print_changes(revision.changes)
    runs = store.list_runs(identifier)
    if runs:
        print(f"Runs ({len(runs)})")
        for run in runs:
            print(f"  {run.run_id}  {run.status.value}  {run.result_hash}")
    return 0


def cmd_replay(arguments: argparse.Namespace) -> int:
    from r3chain.services import AccessPrincipal

    svc = _service(arguments)
    try:
        res = svc.replay_run(AccessPrincipal.local_default(), arguments.run_id)
        outcome = ReplayOutcome.model_validate(res.data["replay"])
        print(f"Original  {outcome.original_run_id}  {outcome.original_hash}")
        print(f"Replay    {outcome.replay_run_id}  {outcome.replay_hash}")
        if outcome.engine_versions_changed:
            print(
                f"Versions changed since the original run: "
                f"{', '.join(outcome.engine_versions_changed)}"
            )
        print("Identical" if outcome.identical else "DIFFERENT")
        return 0 if outcome.identical else 1
    finally:
        svc.close()


def cmd_network(arguments: argparse.Namespace) -> int:
    scenario = _load_or_report(Path(arguments.scenario))
    if scenario is None:
        return 1
    case = NetworkCase.model_validate(read_data_file(Path(arguments.case)))
    network = next((item for item in scenario.networks if item.network_id == case.network_id), None)
    if network is None:
        raise NotFoundError(f"network {case.network_id} is not in the scenario")
    try:
        from r3chain.adapters.pandapipes_network import PandapipesNetworkModel

        result = PandapipesNetworkModel().solve(network, case)
    except ModuleNotFoundError as error:
        raise ValueError(
            f"the network engine is not installed ({error.name}). Install r3chain[network]"
        ) from error
    if arguments.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_network_result(result)
    return 0 if result.state is AdapterState.COMPLETED else 1


def cmd_couple(arguments: argparse.Namespace) -> int:
    """Evaluate the coupling cases of a scenario file directly, without the store."""
    scenario = _load_or_report(Path(arguments.scenario))
    if scenario is None:
        return 1
    available = {
        (policy.policy_id, policy.version): policy
        for policy in load_policies(Path(arguments.policies))
    }
    policies = {}
    for reference in scenario.policies:
        policy = available.get((reference.policy_id, reference.version))
        if policy is None or policy_ref(policy).policy_hash != reference.policy_hash:
            raise ValueError(
                f"policy {reference.policy_id} version {reference.version} with the hash "
                f"recorded in the scenario is not in {arguments.policies}"
            )
        policies[reference.policy_id] = policy
    numerical = single_policy(policies, PolicyKind.NUMERICAL)
    source = single_policy(policies, PolicyKind.SOURCE)
    if numerical is None or source is None:
        raise ValueError(
            "the scenario must reference exactly one policy of kind 'numerical' and exactly one "
            "of kind 'source'"
        )
    cases = [
        case
        for case in scenario.coupling_cases
        if arguments.case is None or case.coupling_case_id == arguments.case
    ]
    if not cases:
        raise NotFoundError(
            f"coupling case {arguments.case} is not in the scenario"
            if arguments.case
            else "the scenario defines no coupling cases"
        )
    from r3chain.adapters.pandapipes_network import PandapipesNetworkModel

    network_model = MemoNetworkModel(PandapipesNetworkModel())
    try:
        outcomes = [
            evaluate_case(
                scenario,
                case,
                source_models=default_source_models(),
                network_model=network_model,
                source_policy=source,
                numerical_policy=numerical,
            )
            for case in cases
        ]
    except ModuleNotFoundError as error:
        raise ValueError(
            f"the network engine is not installed ({error.name}). Install r3chain[network]"
        ) from error
    if arguments.json:
        print(
            json.dumps(
                {
                    outcome.coupling_case_id: {
                        "summary": coupling_case_summary(outcome),
                        "result": outcome.result.to_json_dict() if outcome.result else None,
                    }
                    for outcome in outcomes
                },
                indent=2,
            )
        )
    elif len(outcomes) == 1:
        _print_case_outcome(outcomes[0])
    else:
        for outcome in outcomes:
            summary = coupling_case_summary(outcome)
            print(f"{outcome.coupling_case_id}: {_describe_coupling_case(summary)}")
        print("\nUse --case to see one case in full.")
    return 0 if all(outcome.status.value == "completed" for outcome in outcomes) else 1


def _stage_payloads(store: Store, run_id: str) -> dict[str, dict[str, Any]]:
    study = next(
        (result for result in store.get_results(run_id) if result.subject == "study"), None
    )
    if study is None:
        raise NotFoundError(f"run {run_id} has no study result")
    return {output.stage: output.payload for output in study.stage_outputs}


def cmd_candidates(arguments: argparse.Namespace) -> int:
    store = _store(arguments)
    payloads = _stage_payloads(store, arguments.run_id)
    assess = payloads.get("assess", {})
    if arguments.json:
        print(
            json.dumps(
                {
                    "assessments": assess.get("assessments", []),
                    "ranking": assess.get("ranking"),
                    "rejected": payloads.get("candidates", {}).get("rejected", []),
                    "candidates": payloads.get("candidates", {}).get("candidates", []),
                },
                indent=2,
            )
        )
        return 0
    if not assess.get("assessments"):
        print("this run has no assessed candidates")
        return 1
    _print_candidate_summary(assess)
    candidates = {
        item["candidate_id"]: item for item in payloads.get("candidates", {}).get("candidates", [])
    }
    print("\nExplanations")
    for item in assess["assessments"]:
        candidate = candidates.get(item["candidate_id"], {})
        cap_lines = [_format_capacity_case(cap) for cap in item.get("capacity_cases", [])]
        lines = [*item["explanation"], *item["ranking_eligibility"]["reasons"], *cap_lines]
        print(
            f"  {item['candidate_id']}  {item['label']}  route {candidate.get('route_id', '?')}  "
            f"evaluation {item['evaluation_status']}"
        )
        for line in dict.fromkeys(lines):
            print(f"      {line}")
    rejected = payloads.get("candidates", {}).get("rejected", [])
    print(f"\nRejected before evaluation ({len(rejected)})")
    for entry in rejected:
        option = ", ".join(f"{key} {value}" for key, value in entry["option"].items())
        print(f"  {option}: {entry['reason']} [{entry['stage']}]")
        for detail in entry.get("details", []):
            print(f"      {detail}")
    return 0


def cmd_layers(arguments: argparse.Namespace) -> int:
    from r3chain.geospatial.export import export_map_layers

    store = _store(arguments)
    run = store.get_run(arguments.run_id)
    scenario = store.get_scenario(run.scenario_revision)
    for path in export_map_layers(
        scenario, _stage_payloads(store, arguments.run_id), Path(arguments.directory)
    ):
        print(f"wrote {path}")
    return 0


def _lcoh_mwh(value: float | None, currency: str) -> str:
    if value is None:
        return "undefined"
    return f"{value * JOULES_PER_MWH:.2f} {currency}/MWh"


def _format_capacity_case(cap: dict[str, Any]) -> str:
    op_id = cap.get("operating_case_id", cap.get("coupling_case_id", "?"))
    outcome = cap.get("outcome", "not_established")
    delivered_w = cap.get("delivered_heat_w")
    reason = str(cap.get("reason", "")).strip()
    if outcome == "limit_found":
        mw = f"{float(delivered_w) / 1e6:.2f} MW" if delivered_w is not None else "0.00 MW"
        detail = f"; {reason}" if reason else ""
        return f"capacity {op_id}: limit found ({mw}{detail})"
    if outcome == "demand_covered":
        mw = f"{float(delivered_w) / 1e6:.2f} MW" if delivered_w is not None else "-"
        return f"capacity {op_id}: demand covered ({mw})"
    return f"capacity {op_id}: not established ({reason or 'no operating point'})"


def cmd_economics(arguments: argparse.Namespace) -> int:
    store = _store(arguments)
    payloads = _stage_payloads(store, arguments.run_id)
    operate = payloads.get("operate", {})
    cost = payloads.get("cost", {})
    assess = payloads.get("assess", {})
    labels = {item["candidate_id"]: item["label"] for item in assess.get("assessments", [])}
    if arguments.json:
        print(json.dumps({"operate": operate, "cost": cost}, indent=2))
        return 0
    cand_costs = cost.get("candidates", {})
    if not cand_costs:
        print("this run has no economic evaluation yet")
        return 1
    baseline = cost.get("baseline", {})
    base_unsub = baseline.get("unsubsidised", {})
    currency = base_unsub.get("currency", "EUR")
    print(
        f"Baseline  {baseline.get('baseline_id', '?')}  "
        f"{base_unsub.get('price_convention', '')} {base_unsub.get('price_base_year', '')} "
        f"{currency}  VAT {'included' if base_unsub.get('vat_included') else 'excluded'}  "
        f"carbon {'monetised' if base_unsub.get('carbon_monetised') else 'not monetised'}"
    )
    print(
        f"          PV cost {_money(base_unsub.get('pv_cost_currency'))}  "
        f"LCOH {_lcoh_mwh(base_unsub.get('lcoh_currency_per_j'), currency)}"
    )
    print(f"\nCandidates ({len(cand_costs)})")
    for candidate_id, entry in cand_costs.items():
        label = labels.get(candidate_id, candidate_id)
        if entry.get("status") != "completed":
            print(f"  {candidate_id}  {label}: {entry.get('status')} ({entry.get('reason', '')})")
            continue
        bounds = entry["boundaries"]
        plant_b = bounds["plant"]
        geo_b = bounds["geothermal_delivered"]
        sys_b = bounds["system_delivered"]
        comp = entry["baseline_comparison"]
        op_cand = operate.get("candidates", {}).get(candidate_id, {}) or {}
        op_totals = op_cand.get("totals") or {}
        geo_mwh = joule_to_mwh(op_totals.get("geothermal_delivered_heat_j", 0.0))
        curt_mwh = joule_to_mwh(op_totals.get("geothermal_curtailed_heat_j", 0.0))
        cap_mw = op_totals.get("reference_capacity_w", 0.0) / 1e6
        cap_basis = str(op_totals.get("capacity_basis", "lower_bound")).replace("_", " ")
        reconcile = entry["reconciliation"]
        rec_state = (
            "reconciled"
            if not reconcile.get("duplicate_ownership_errors")
            else "DUPLICATE OWNERSHIP"
        )
        print(
            f"  {candidate_id}  {label}  [{rec_state}]  "
            f"cap {cap_mw:.2f} MW ({cap_basis})  geo {geo_mwh:.0f} MWh "
            f"(curtailed {curt_mwh:.0f} MWh)"
        )
        print(
            f"      LCOH plant: {_lcoh_mwh(plant_b.get('lcoh_currency_per_j'), currency)}  "
            f"geo delivered: {_lcoh_mwh(geo_b.get('lcoh_currency_per_j'), currency)}  "
            f"system delivered: {_lcoh_mwh(sys_b.get('lcoh_currency_per_j'), currency)}"
        )
        print(
            f"      CAPEX {_money(geo_b.get('capex_currency'))}  "
            f"PV cost {_money(sys_b.get('pv_cost_currency'))}  "
            f"delta vs baseline {_money(comp.get('incremental_discounted_cost_currency'))}"
            + (
                f"  NPV {_money(sys_b.get('npv_currency'))}"
                if sys_b.get("npv_currency") is not None
                else ""
            )
        )
        funded = entry.get("funded")
        if funded is not None:
            f_geo = funded["geothermal_delivered"]
            f_comp = funded["baseline_comparison"]
            lcoh_txt = _lcoh_mwh(f_geo.get("lcoh_currency_per_j"), currency)
            delta_txt = _money(f_comp.get("incremental_discounted_cost_currency"))
            print(f"      funded case: LCOH geo {lcoh_txt}  delta vs baseline {delta_txt}")
        cap_cases = op_cand.get("capacity_cases", ())
        for cap in cap_cases:
            print(f"      {_format_capacity_case(cap)}")
    extra = cost.get("route_demand_comparisons", [])
    if extra:
        print(f"\nRoutes with linked additional customers ({len(extra)})")
        for row in extra:
            b_heat = joule_to_mwh(row["base_geothermal_delivered_j"])
            w_heat = joule_to_mwh(row["with_demand_geothermal_delivered_j"])
            b_lcoh = _lcoh_mwh(row["base_lcoh_geothermal_delivered_currency_per_j"], currency)
            w_lcoh = _lcoh_mwh(
                row["with_demand_lcoh_geothermal_delivered_currency_per_j"], currency
            )
            count = row["linked_customer_count"]
            print(
                f"  {row['candidate_id']} route {row['route_id']} (+{count}): "
                f"geo {b_heat:.0f} -> {w_heat:.0f} MWh  LCOH {b_lcoh} -> {w_lcoh}"
            )
    not_modelled = tuple(
        dict.fromkeys((*operate.get("not_modelled", ()), *cost.get("not_modelled", ())))
    )
    if not_modelled:
        print(f"\nNot modelled: {'; '.join(not_modelled)}")
    return 0


def cmd_policies(arguments: argparse.Namespace) -> int:
    policies = load_policies(Path(arguments.directory))
    if not policies:
        print(f"no policy files below {arguments.directory}")
        return 0
    for policy in policies:
        reference = policy_ref(policy)
        print(
            f"{policy.policy_id:<28}{policy.kind.value:<11}{policy.version:<8}"
            f"{policy.status.value:<11}{reference.policy_hash}"
        )
    return 0


def cmd_schemas(arguments: argparse.Namespace) -> int:
    if arguments.action == "check":
        problems = check_schemas(Path(arguments.directory))
        for problem in problems:
            print(problem)
        if not problems:
            print("schemas are current")
        return 1 if problems else 0
    written = export_schemas(Path(arguments.directory), force=arguments.force)
    for path in written:
        print(f"wrote {path}")
    if not written:
        print("schemas are current")
    return 0


def cmd_compare(arguments: argparse.Namespace) -> int:
    store = _store(arguments)
    payloads = _stage_payloads(store, arguments.run_id)
    compare_payload = payloads.get("compare", {})
    if arguments.json:
        print(json.dumps(compare_payload, indent=2))
        return 0
    if not compare_payload:
        print("compare stage has no completed output")
        return 1
    comp = compare_payload.get("comparison", {})
    print(f"Uncertainty compare  {compare_payload.get('uncertainty_model_id', '')}")
    print(f"  mode: {compare_payload.get('mode')}  samples: {compare_payload.get('sample_count')}")
    print(f"  probability basis: {compare_payload.get('probability_basis')}")
    print(f"  preference: {comp.get('preference_outcome')}  ({comp.get('preference_reason', '')})")
    assess_labels = {
        a["candidate_id"]: a["label"] for a in payloads.get("assess", {}).get("assessments", [])
    }
    stability = {r["candidate_id"]: r for r in comp.get("ranking_stability", [])}
    for cid in comp.get("ordered_candidate_ids", []):
        row = comp["candidates"][cid]
        acc = row["sample_account"]
        stab = stability.get(cid)
        share_txt = f"{stab['first_rank_count']}/{stab['denominator']}" if stab else "-"
        print(
            f"  {assess_labels.get(cid, cid)}  eval {acc['evaluated_samples']}/"
            f"{acc['total_samples']}  feasible {acc['feasible_samples']}  "
            f"1st-share {share_txt}  pareto {row.get('pareto_optimal')}"
        )
    drivers = comp.get("decision_drivers", [])
    if drivers:
        print(f"Decision drivers ({len(drivers)})")
        for drv in drivers:
            print(
                f"  {drv['variable_id']}: rho={drv['spearman_rank_correlation']:.3f}  "
                f"switches={drv['switches_winner']}"
            )
    return 0


def cmd_report(arguments: argparse.Namespace) -> int:
    from r3chain.services import AccessPrincipal

    svc = _service(arguments)
    try:
        out = Path(arguments.output) if arguments.output else None
        res = svc.export_study_report(
            AccessPrincipal.local_default(),
            run_id=arguments.run_id,
            fmt=arguments.format,
            lang=arguments.lang,
            output_path=out,
        )
        text = str(res.data["content"])
        if arguments.output:
            print(f"wrote {arguments.output}")
        else:
            print(text, end="" if text.endswith("\n") else "\n")
        return 0
    finally:
        svc.close()


def cmd_explain(arguments: argparse.Namespace) -> int:
    from r3chain.services import AccessPrincipal

    svc = _service(arguments)
    try:
        res = svc.explain_run_target(
            AccessPrincipal.local_default(),
            run_id=arguments.run_id,
            target=arguments.target,
            kind=arguments.kind,
            lang=arguments.lang,
        )
        if arguments.json:
            print(json.dumps(res.explanation, indent=2))
        else:
            assert res.explanation is not None
            print(res.explanation["rendered"])
        return 0
    finally:
        svc.close()


def cmd_serve_api(arguments: argparse.Namespace) -> int:
    import uvicorn

    from r3chain.api import create_app
    from r3chain.services import AccessPrincipal, PlanningService

    store_root = _store_root(arguments)
    policies_dir = Path(arguments.policies)
    seed_file = getattr(arguments, "seed_scenario", None)
    if seed_file:
        seed_path = Path(seed_file)
        scenario = _load_or_report(seed_path)
        if scenario is None:
            return 1
        svc = PlanningService(store_root, policy_directory=policies_dir)
        try:
            svc.register_inputs_and_scenario(
                AccessPrincipal.local_default(),
                scenario,
                base_dir=seed_path.parent,
                policies_dir=policies_dir,
                author="serve-api",
                rationale=f"seed from {seed_path.name}",
            )
        finally:
            svc.close()

    fe_arg = getattr(arguments, "frontend_dir", None)
    app = create_app(
        store_root,
        policy_directory=policies_dir,
        frontend_dir=Path(fe_arg) if fe_arg else None,
    )
    uvicorn.run(app, host=arguments.host, port=int(arguments.port))
    return 0


def cmd_serve_mcp(arguments: argparse.Namespace) -> int:
    import asyncio

    from r3chain.mcp import create_mcp_server
    from r3chain.services import PlanningService

    svc = PlanningService(_store_root(arguments), policy_directory=Path(arguments.policies))
    try:
        server = create_mcp_server(svc)
        asyncio.run(server.run_stdio_async())
        return 0
    finally:
        svc.close()


# --- parser ---------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="r3chain", description="R3CHAIN planning system")
    parser.add_argument("--store", help=f"store directory (default {DEFAULT_STORE})")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="check a scenario file")
    validate.add_argument("file")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=cmd_validate)

    assumptions = commands.add_parser("assumptions", help="list inputs with their provenance")
    assumptions.add_argument("target", help="scenario file or revision identifier")
    assumptions.add_argument("--status", choices=["active", "unknown", "conflict"])
    assumptions.add_argument("--json", action="store_true")
    assumptions.set_defaults(handler=cmd_assumptions)

    run = commands.add_parser("run", help="freeze and evaluate a scenario file")
    run.add_argument("file")
    run.add_argument("--author", default="local")
    run.add_argument("--rationale", default="initial import")
    run.add_argument("--parent", help="parent revision, to record what changed")
    run.add_argument("--policies", default=DEFAULT_POLICY_DIRECTORY)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--no-cache", action="store_true")
    run.set_defaults(handler=cmd_run)

    variant = commands.add_parser("variant", help="create a changed revision")
    variant.add_argument("revision")
    variant.add_argument("--set", action="append", metavar="PATH=JSON")
    variant.add_argument(
        "--set-known",
        action="append",
        metavar="PATH=JSON",
        help="set a value as user supplied by --author",
    )
    variant.add_argument("--remove", action="append", metavar="PATH")
    variant.add_argument("--author", default="local")
    variant.add_argument("--rationale", required=True)
    variant.add_argument("--run", action="store_true", help="evaluate the new revision")
    variant.set_defaults(handler=cmd_variant)

    show = commands.add_parser("show", help="show a stored run or revision")
    show.add_argument("identifier")
    show.set_defaults(handler=cmd_show)

    replay = commands.add_parser("replay", help="execute a run again and compare")
    replay.add_argument("run_id")
    replay.set_defaults(handler=cmd_replay)

    network = commands.add_parser("network", help="solve one network operating case")
    network.add_argument("scenario", help="scenario file that contains the network")
    network.add_argument("case", help="network case file")
    network.add_argument("--json", action="store_true")
    network.set_defaults(handler=cmd_network)

    candidates = commands.add_parser("candidates", help="candidate and rejection tables of a run")
    candidates.add_argument("run_id")
    candidates.add_argument("--json", action="store_true")
    candidates.set_defaults(handler=cmd_candidates)

    economics = commands.add_parser(
        "economics", help="operation, reconciled cost ledgers and LCOH tables of a run"
    )
    economics.add_argument("run_id")
    economics.add_argument("--json", action="store_true")
    economics.set_defaults(handler=cmd_economics)

    compare = commands.add_parser(
        "compare", help="uncertainty, Pareto, drivers and resilience comparison of a run"
    )
    compare.add_argument("run_id")
    compare.add_argument("--json", action="store_true")
    compare.set_defaults(handler=cmd_compare)

    report = commands.add_parser("report", help="export reproducible evidence report of a run")
    report.add_argument("run_id")
    report.add_argument("--format", choices=["markdown", "json"], default="markdown")
    report.add_argument("--lang", choices=["en", "de"], default="en")
    report.add_argument("--output", help="write report to a file instead of stdout")
    report.set_defaults(handler=cmd_report)

    explain = commands.add_parser("explain", help="show verified claim explanations for a run")
    explain.add_argument("run_id")
    explain.add_argument("target", nargs="?", default="study")
    explain.add_argument("--kind", choices=["result", "rejection", "comparison"], default="result")
    explain.add_argument("--lang", choices=["en", "de"], default="en")
    explain.add_argument("--json", action="store_true")
    explain.set_defaults(handler=cmd_explain)

    serve_api = commands.add_parser("serve-api", help="start the HTTP API and workspace UI")
    serve_api.add_argument("--host", default="127.0.0.1")
    serve_api.add_argument("--port", type=int, default=8000)
    serve_api.add_argument("--policies", default=DEFAULT_POLICY_DIRECTORY)
    serve_api.add_argument(
        "--seed-scenario",
        help="optional scenario YAML/JSON to register into the store at startup",
    )
    serve_api.add_argument(
        "--frontend-dir",
        help="optional directory containing the built frontend bundle (defaults to frontend/dist)",
    )
    serve_api.set_defaults(handler=cmd_serve_api)

    serve_mcp = commands.add_parser("serve-mcp", help="start the stdio MCP server")
    serve_mcp.add_argument("--policies", default=DEFAULT_POLICY_DIRECTORY)
    serve_mcp.set_defaults(handler=cmd_serve_mcp)

    layers = commands.add_parser("layers", help="write the map layers of a run as GeoJSON")
    layers.add_argument("run_id")
    layers.add_argument("directory")
    layers.set_defaults(handler=cmd_layers)

    couple = commands.add_parser("couple", help="evaluate the coupling cases of a scenario")
    couple.add_argument("scenario", help="scenario file")
    couple.add_argument("--case", help="one coupling case, shown in full")
    couple.add_argument("--policies", default=DEFAULT_POLICY_DIRECTORY)
    couple.add_argument("--json", action="store_true")
    couple.set_defaults(handler=cmd_couple)

    policies = commands.add_parser("policies", help="list policy files with their hashes")
    policies.add_argument("directory", nargs="?", default=DEFAULT_POLICY_DIRECTORY)
    policies.set_defaults(handler=cmd_policies)

    schemas = commands.add_parser("schemas", help="write or verify the JSON Schemas")
    schemas.add_argument("action", choices=["export", "check"])
    schemas.add_argument("--directory", default=str(DEFAULT_SCHEMA_DIRECTORY))
    schemas.add_argument("--force", action="store_true")
    schemas.set_defaults(handler=cmd_schemas)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except ScenarioNotExecutableError as error:
        _print_report(error.report)
        return 1
    except (
        NotFoundError,
        ImmutabilityError,
        SchemaChangedError,
        PermissionError,
        KeyError,
        ValueError,
    ) as error:
        message = error.args[0] if error.args else str(error)
        print(f"error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
