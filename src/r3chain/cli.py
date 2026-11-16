"""Command line interface.

    r3chain validate FILE              check a scenario file
    r3chain assumptions FILE|REVISION  list every engineering input with its provenance
    r3chain run FILE                   freeze the scenario as a revision and evaluate it
    r3chain variant REVISION ...       create a changed revision with a field level change record
    r3chain show RUN|REVISION          show a stored run or revision
    r3chain replay RUN                 execute a stored run again and compare the result
    r3chain network SCENARIO CASE      solve one operating case of a network
    r3chain couple SCENARIO            evaluate the coupling cases of a scenario file
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
from r3chain.domain.paths import PatchOperation, apply_patch
from r3chain.domain.records.runs import Assumption, CheckOutcome, PolicyKind, Result, Run
from r3chain.domain.records.scenario import FieldChange, Scenario
from r3chain.domain.status import AdapterState
from r3chain.domain.units import kelvin_to_celsius, pa_to_bar
from r3chain.domain.validation import (
    Severity,
    ValidationReport,
    load_scenario_file,
    parse_scenario,
    read_data_file,
)
from r3chain.pipeline.runner import PipelineRunner, ScenarioNotExecutableError
from r3chain.pipeline.stages import (
    coupling_case_summary,
    default_source_models,
    default_stages,
    single_policy,
)
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


def _store(arguments: argparse.Namespace) -> Store:
    return Store(Path(arguments.store or os.environ.get("R3CHAIN_HOME", DEFAULT_STORE)))


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
            for item in output.missing_inputs:
                print(f"      needs {item.field_path} ({item.state})")
            for site_id, summary in output.payload.get("sites", {}).items():
                print(f"      site {site_id}: {summary['evidence_status']}")
            for source_id, source in output.payload.get("sources", {}).items():
                print(f"      source {source_id}: {_describe_source(source)}")
            for case_id, case in output.payload.get("cases", {}).items():
                print(f"      case {case_id}: {_describe_coupling_case(case['summary'])}")
    for result in results:
        label = "synthetic" if result.synthetic else "not synthetic"
        print(
            f"\nResult    {result.subject}: technical {result.technical_status.value}, "
            f"evaluation {result.evaluation_status.value}, {label}"
        )
        if not result.ranking_eligibility.eligible:
            print(f"          not rankable: {'; '.join(result.ranking_eligibility.reasons)}")
    print(f"Result hash  {run.result_hash}")


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


def _register_inputs(store: Store, scenario: Scenario, base: Path, policies: Path) -> None:
    """Register the datasets and policies a scenario refers to.

    A dataset whose source file is available is verified against its checksum and stored.
    """
    for dataset in scenario.datasets:
        candidates = [Path(dataset.source_uri), base / dataset.source_uri]
        file = next((path for path in candidates if path.is_file()), None)
        store.register_dataset(dataset, file)
    for policy in load_policies(policies):
        store.register_policy(policy)


def cmd_run(arguments: argparse.Namespace) -> int:
    path = Path(arguments.file)
    scenario = _load_or_report(path)
    if scenario is None:
        return 1
    store = _store(arguments)
    _register_inputs(store, scenario, path.parent, Path(arguments.policies))
    revision = store.add_revision(
        scenario,
        author=arguments.author,
        rationale=arguments.rationale,
        parent_revision_id=arguments.parent,
    )
    runner = PipelineRunner(store, default_stages())
    run, result = runner.run(
        revision.revision_id, seed=arguments.seed, use_cache=not arguments.no_cache
    )
    _print_run(run, (result,), detail=False)
    return 0


def cmd_variant(arguments: argparse.Namespace) -> int:
    store = _store(arguments)
    parent = store.get_scenario(arguments.revision)
    operations: list[PatchOperation] = []
    for assignment in arguments.set or []:
        field_path, _, text = assignment.partition("=")
        operations.append(PatchOperation("set", field_path, json.loads(text)))
    for assignment in arguments.set_known or []:
        field_path, _, text = assignment.partition("=")
        evidence = {
            "state": "known",
            "value": json.loads(text),
            "provenance": {"provenance_type": "user_supplied", "author": arguments.author},
        }
        operations.append(PatchOperation("set", field_path, evidence))
    for field_path in arguments.remove or []:
        operations.append(PatchOperation("remove", field_path))
    if not operations:
        print("error: give at least one --set, --set-known or --remove", file=sys.stderr)
        return 2
    scenario, report = parse_scenario(apply_patch(parent.to_json_dict(), operations))
    if scenario is None or not report.executable:
        _print_report(report)
        return 1
    revision = store.add_revision(
        scenario,
        author=arguments.author,
        rationale=arguments.rationale,
        parent_revision_id=arguments.revision,
    )
    print(f"Revision  {revision.revision_id}")
    print(f"Parent    {revision.parent_revision_id}")
    print(f"Changes ({len(revision.changes)})")
    _print_changes(revision.changes)
    if arguments.run:
        run, result = PipelineRunner(store, default_stages()).run(revision.revision_id)
        print()
        _print_run(run, (result,), detail=False)
    return 0


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
    store = _store(arguments)
    outcome = PipelineRunner(store, default_stages()).replay(arguments.run_id)
    print(f"Original  {outcome.original_run_id}  {outcome.original_hash}")
    print(f"Replay    {outcome.replay_run_id}  {outcome.replay_hash}")
    if outcome.engine_versions_changed:
        print(
            f"Versions changed since the original run: {', '.join(outcome.engine_versions_changed)}"
        )
    print("Identical" if outcome.identical else "DIFFERENT")
    return 0 if outcome.identical else 1


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
    directory = Path(arguments.directory)
    if arguments.action == "check":
        problems = check_schemas(directory)
        for problem in problems:
            print(problem)
        if not problems:
            print("schemas are current")
        return 1 if problems else 0
    written = export_schemas(directory, force=arguments.force)
    for path in written:
        print(f"wrote {path}")
    if not written:
        print("schemas are current")
    return 0


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
    except (NotFoundError, ImmutabilityError, SchemaChangedError, KeyError, ValueError) as error:
        message = error.args[0] if error.args else str(error)
        print(f"error: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
