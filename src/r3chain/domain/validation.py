"""Scenario validation.

A single record validates itself when it is created. This module checks what only the whole
scenario can show: references between records, source references, pressure references and time
coverage. It reports everything it finds instead of stopping at the first problem.

Severity:

error
    The scenario must not be executed.
warning
    The scenario can be executed. The reader should know about this.
unverified
    A check could not be carried out. It is reported so that it is never mistaken for a pass.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from r3chain.domain.assumptions import missing_inputs
from r3chain.domain.base import Record
from r3chain.domain.evidence import MissingInput, ProvenanceType
from r3chain.domain.paths import get_at, iter_provenance
from r3chain.domain.records.common import value_or_none
from r3chain.domain.records.network import Circuit
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.timeseries import IntervalSeries, same_interval_set
from r3chain.domain.topology import TopologySeverity, check_topology
from r3chain.domain.units import Pressure


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    UNVERIFIED = "unverified"


class Issue(Record):
    severity: Severity
    code: str
    field_path: str
    message: str


class ValidationReport(Record):
    scenario_id: str | None
    revision_id: str | None
    issues: tuple[Issue, ...] = ()
    missing_inputs: tuple[MissingInput, ...] = ()
    contains_synthetic_values: bool = False

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def executable(self) -> bool:
        return not self.errors


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, severity: Severity, code: str, field_path: str, message: str) -> None:
        self.issues.append(
            Issue(severity=severity, code=code, field_path=field_path, message=message)
        )

    def error(self, code: str, field_path: str, message: str) -> None:
        self.add(Severity.ERROR, code, field_path, message)


def validate_scenario(
    scenario: Scenario, series: Mapping[str, IntervalSeries] | None = None
) -> ValidationReport:
    """Check a scenario as a whole.

    ``series`` maps series identifiers to loaded time series. When it is not supplied, time
    coverage cannot be checked and is reported as unverified.
    """
    found = _Collector()
    _check_references(scenario, found)
    synthetic = _check_sources(scenario, found)
    _check_pressure_references(scenario, found)
    _check_topology(scenario, found)
    _check_time_coverage(scenario, series, found)
    return ValidationReport(
        scenario_id=scenario.scenario_id,
        revision_id=scenario.revision_id(),
        issues=tuple(found.issues),
        missing_inputs=missing_inputs(scenario),
        contains_synthetic_values=synthetic,
    )


def _check_references(scenario: Scenario, found: _Collector) -> None:
    alternatives = {item.alternative_id for item in scenario.alternatives}
    datasets = {item.dataset_id for item in scenario.datasets}
    sites = {item.site_id for item in scenario.sites}
    networks = {item.network_id: item for item in scenario.networks}
    connections = {item.connection_id for item in scenario.connections}
    policies = {item.policy_id for item in scenario.policies}

    if scenario.study.baseline_id not in alternatives:
        found.error(
            "unknown_reference",
            "study.baseline_id",
            f"baseline {scenario.study.baseline_id!r} is not one of the alternatives",
        )

    for well_scenario in scenario.well_scenarios:
        base = f"well_scenarios[{well_scenario.source_scenario_id}]"
        if well_scenario.site_id not in sites:
            found.error(
                "unknown_reference",
                f"{base}.site_id",
                f"site {well_scenario.site_id!r} is not defined",
            )
        if well_scenario.parameter_package_id not in datasets:
            found.error(
                "unknown_reference",
                f"{base}.parameter_package_id",
                f"parameter package {well_scenario.parameter_package_id!r} is not a dataset "
                "registered in this scenario",
            )
        for well in well_scenario.wells:
            if well.surface_site_id not in sites:
                found.error(
                    "unknown_reference",
                    f"{base}.wells[{well.well_id}].surface_site_id",
                    f"site {well.surface_site_id!r} is not defined",
                )

    for network in scenario.networks:
        if network.dataset_id not in datasets:
            found.error(
                "unknown_reference",
                f"networks[{network.network_id}].dataset_id",
                f"dataset {network.dataset_id!r} is not registered in this scenario",
            )

    for connection in scenario.connections:
        base = f"connections[{connection.connection_id}]"
        connected = networks.get(connection.network_id)
        if connected is None:
            found.error(
                "unknown_reference",
                f"{base}.network_id",
                f"network {connection.network_id!r} is not defined",
            )
            continue
        nodes = {node.node_id: node for node in connected.nodes}
        for field, expected in (("supply_node", Circuit.SUPPLY), ("return_node", Circuit.RETURN)):
            node_id = getattr(connection, field)
            node = nodes.get(node_id)
            if node is None:
                found.error(
                    "unknown_reference",
                    f"{base}.{field}",
                    f"node {node_id!r} is not in network {connected.network_id!r}",
                )
            else:
                if node.circuit is not expected:
                    found.error(
                        "wrong_circuit",
                        f"{base}.{field}",
                        f"node {node_id!r} is in the {node.circuit.value} circuit",
                    )
                if node.zone_id != connection.zone_id:
                    found.error(
                        "zone_mismatch",
                        f"{base}.zone_id",
                        f"node {node_id!r} is in zone {node.zone_id!r}, "
                        f"the connection states {connection.zone_id!r}",
                    )
        if connection.heat_carrier is not connected.heat_carrier:
            found.error(
                "heat_carrier_mismatch",
                f"{base}.heat_carrier",
                f"connection carries {connection.heat_carrier.value}, "
                f"network {connected.network_id!r} carries {connected.heat_carrier.value}",
            )

    for route in scenario.routes:
        base = f"routes[{route.route_id}]"
        if route.from_site_id not in sites:
            found.error(
                "unknown_reference",
                f"{base}.from_site_id",
                f"site {route.from_site_id!r} is not defined",
            )
        if route.to_connection_id not in connections:
            found.error(
                "unknown_reference",
                f"{base}.to_connection_id",
                f"connection {route.to_connection_id!r} is not defined",
            )

    _check_coupling_references(scenario, found)
    _check_layers(scenario, found)

    document = scenario.to_json_dict()
    for model in scenario.uncertainty_models:
        for variable in model.variables:
            try:
                get_at(document, variable.field_path)
            except (KeyError, ValueError):
                found.error(
                    "unknown_reference",
                    f"uncertainty_models[{model.uncertainty_model_id}]"
                    f".variables[{variable.variable_id}].field_path",
                    f"{variable.field_path!r} does not address an input of this scenario",
                )

    for operating in scenario.operating_scenarios:
        base = f"operating_scenarios[{operating.operating_scenario_id}]"
        if operating.dispatch_policy_id not in policies:
            found.error(
                "unknown_reference",
                f"{base}.dispatch_policy_id",
                f"policy {operating.dispatch_policy_id!r} is not referenced by this scenario",
            )
        for reference in operating.series_refs():
            if reference.dataset_id not in datasets:
                found.error(
                    "unknown_reference",
                    f"{base}.series[{reference.series_id}].dataset_id",
                    f"dataset {reference.dataset_id!r} is not registered in this scenario",
                )


def _check_coupling_references(scenario: Scenario, found: _Collector) -> None:
    """Operating cases and coupling cases must refer to records that exist and fit together."""
    networks = {item.network_id: item for item in scenario.networks}
    well_scenarios = {item.source_scenario_id: item for item in scenario.well_scenarios}
    plants = {item.plant_id for item in scenario.plants}
    connections = {item.connection_id: item for item in scenario.connections}
    operating_cases = {item.operating_case_id: item for item in scenario.operating_cases}
    routes = {item.route_id: item for item in scenario.routes}
    sites = {item.site_id for item in scenario.sites}

    for operating_case in scenario.operating_cases:
        base = f"operating_cases[{operating_case.operating_case_id}]"
        network = networks.get(operating_case.network_id)
        if network is None:
            found.error(
                "unknown_reference",
                f"{base}.network_id",
                f"network {operating_case.network_id!r} is not defined",
            )
            continue
        loads = value_or_none(operating_case.loads)
        if loads is None:
            continue
        consumers = {consumer.consumer_id for consumer in network.consumers}
        given = {load.consumer_id for load in loads}
        for unknown in sorted(given - consumers):
            found.error(
                "unknown_reference",
                f"{base}.loads",
                f"consumer {unknown!r} is not in network {network.network_id!r}",
            )
        for absent in sorted(consumers - given):
            found.error(
                "missing_load",
                f"{base}.loads",
                f"no load is given for consumer {absent!r}. State a heat of zero explicitly",
            )

    for case in scenario.coupling_cases:
        base = f"coupling_cases[{case.coupling_case_id}]"
        for field, known, what in (
            ("source_scenario_id", well_scenarios, "well scenario"),
            ("plant_id", plants, "plant"),
            ("connection_id", connections, "connection"),
            ("operating_case_id", operating_cases, "operating case"),
        ):
            identifier = getattr(case, field)
            if identifier not in known:
                found.error(
                    "unknown_reference", f"{base}.{field}", f"{what} {identifier!r} is not defined"
                )
        connection = connections.get(case.connection_id)
        operated = operating_cases.get(case.operating_case_id)
        if (
            connection is not None
            and operated is not None
            and connection.network_id != operated.network_id
        ):
            found.error(
                "network_mismatch",
                f"{base}.operating_case_id",
                f"operating case {operated.operating_case_id!r} is for network "
                f"{operated.network_id!r}, connection {connection.connection_id!r} is on "
                f"{connection.network_id!r}",
            )
        if case.route_id is None:
            continue
        route = routes.get(case.route_id)
        if route is None:
            found.error(
                "unknown_reference", f"{base}.route_id", f"route {case.route_id!r} is not defined"
            )
            continue
        well_scenario = well_scenarios.get(case.source_scenario_id)
        # A reference that is itself broken is reported once, where it is broken. Only a route
        # and a source that both exist can disagree.
        from_other_site = (
            well_scenario is not None
            and well_scenario.site_id in sites
            and route.from_site_id in sites
            and route.from_site_id != well_scenario.site_id
        )
        to_other_connection = (
            case.connection_id in connections
            and route.to_connection_id in connections
            and route.to_connection_id != case.connection_id
        )
        if from_other_site or to_other_connection:
            found.error(
                "route_mismatch",
                f"{base}.route_id",
                f"route {route.route_id!r} runs from site {route.from_site_id!r} to connection "
                f"{route.to_connection_id!r}. It cannot be combined with a source at another "
                "site or with another connection",
            )


def _check_layers(scenario: Scenario, found: _Collector) -> None:
    """Imported layers must be in the study's reference system and come from a registered
    dataset. Every repair and every rejected feature is reported."""
    datasets = {item.dataset_id for item in scenario.datasets}
    study_crs = scenario.study.study_area.crs
    for layer in scenario.spatial_layers:
        base = f"spatial_layers[{layer.layer_id}]"
        if layer.crs != study_crs:
            found.error(
                "wrong_reference_system",
                f"{base}.crs",
                f"the layer is in {layer.crs}, the study uses {study_crs}. Import it again",
            )
        if layer.dataset_id not in datasets:
            found.error(
                "unknown_reference",
                f"{base}.dataset_id",
                f"dataset {layer.dataset_id!r} is not registered in this scenario",
            )
        for repair in layer.repairs:
            found.add(
                Severity.WARNING,
                "geometry_repaired",
                f"{base}.features[{repair.feature_id}]",
                f"repaired at import: {repair.defect}. Size changed by "
                f"{repair.size_change_fraction:.3%}",
            )
        for item in layer.rejected:
            found.add(
                Severity.WARNING,
                "feature_rejected",
                f"{base}.features[{item.feature_id}]",
                f"rejected at import and not used: {item.reason}",
            )
        for message in layer.import_warnings:
            found.add(Severity.WARNING, "import_note", base, message)


def _check_sources(scenario: Scenario, found: _Collector) -> bool:
    """Every sourced value must point at a registered dataset. Returns True if any value is
    synthetic."""
    datasets = {item.dataset_id: item for item in scenario.datasets}
    synthetic = False
    for path, provenance in iter_provenance(scenario):
        if provenance.provenance_type is ProvenanceType.SYNTHETIC:
            synthetic = True
        if provenance.source_id is None:
            continue
        if provenance.source_id not in datasets:
            found.error(
                "unknown_source",
                path,
                f"source {provenance.source_id!r} is not a dataset registered in this scenario",
            )
    if synthetic and not scenario.study.synthetic:
        found.add(
            Severity.WARNING,
            "synthetic_value_in_pilot",
            "study.synthetic",
            "the study is not marked synthetic but uses synthetic values. Results that depend "
            "on them are labelled synthetic and cannot be ranked as pilot results",
        )
    return synthetic


def _check_pressure_references(scenario: Scenario, found: _Collector) -> None:
    """Within one network and its connections, pressures must share one reference."""
    for network in scenario.networks:
        located: list[tuple[str, Pressure]] = [
            (
                f"networks[{network.network_id}].boundary_conditions[{boundary.boundary_id}]"
                ".pressure",
                boundary.pressure,
            )
            for boundary in network.boundary_conditions
            if boundary.pressure is not None
        ]
        for connection in scenario.connections:
            if connection.network_id != network.network_id:
                continue
            for field in ("min_pressure", "max_pressure"):
                pressure = value_or_none(getattr(connection.limits, field))
                if pressure is not None:
                    located.append(
                        (f"connections[{connection.connection_id}].limits.{field}", pressure)
                    )
        references = {pressure.reference for _, pressure in located}
        if len(references) > 1:
            first_reference = located[0][1].reference
            for path, pressure in located:
                if pressure.reference is not first_reference:
                    found.error(
                        "inconsistent_pressure_reference",
                        path,
                        f"this pressure is {pressure.reference.value}, others in network "
                        f"{network.network_id!r} are {first_reference.value}. Convert with an "
                        "explicit ambient pressure",
                    )


def _check_topology(scenario: Scenario, found: _Collector) -> None:
    """Graph checks of every network: islands, unpiped consumers, duplicates and dead ends."""
    for network in scenario.networks:
        pairs = [
            (connection.supply_node, connection.return_node)
            for connection in scenario.connections
            if connection.network_id == network.network_id
        ]
        for issue in check_topology(network, pairs):
            severity = (
                Severity.ERROR if issue.severity is TopologySeverity.ERROR else Severity.WARNING
            )
            found.add(
                severity,
                f"topology_{issue.code}",
                f"networks[{network.network_id}]",
                issue.message,
            )


def _check_time_coverage(
    scenario: Scenario, series: Mapping[str, IntervalSeries] | None, found: _Collector
) -> None:
    for operating in scenario.operating_scenarios:
        base = f"operating_scenarios[{operating.operating_scenario_id}]"
        if series is None:
            found.add(
                Severity.UNVERIFIED,
                "time_coverage_not_checked",
                base,
                "no time series were supplied, so interval coverage was not checked",
            )
            continue
        loaded: list[IntervalSeries] = []
        for reference in operating.series_refs():
            item = series.get(reference.series_id)
            if item is None:
                found.error(
                    "missing_series",
                    f"{base}.series[{reference.series_id}]",
                    f"series {reference.series_id!r} was not supplied",
                )
                continue
            loaded.append(item)
            basis = operating.time_basis
            if not item.covers(basis.start_utc, basis.end_utc):
                found.error(
                    "incomplete_time_coverage",
                    f"{base}.series[{reference.series_id}]",
                    "the series has gaps or does not cover the whole time basis",
                )
        if len(loaded) > 1 and not same_interval_set(*loaded):
            found.error(
                "different_interval_sets",
                base,
                "demand, availability, temperature and tariff series must share one interval set",
            )


# --- loading --------------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    raise TypeError(f"cannot serialise {type(value).__name__}")


def read_data_file(path: Path) -> Any:
    """Read a YAML or JSON file into plain JSON compatible data."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        loaded = yaml.safe_load(text)
        # Normalise YAML dates and timestamps to ISO strings.
        return json.loads(json.dumps(loaded, default=_json_default))
    return json.loads(text)


def _format_location(location: tuple[int | str, ...], data: Any) -> str:
    """Turn a validation error location into a field path, naming list elements by identifier."""
    path = ""
    current = data
    for part in location:
        if isinstance(part, int):
            item = current[part] if isinstance(current, list) and part < len(current) else None
            identifier = next(
                (
                    value
                    for name, value in (item.items() if isinstance(item, dict) else ())
                    if name.endswith("_id") and isinstance(value, str)
                ),
                str(part),
            )
            path += f"[{identifier}]"
            current = item
        elif isinstance(current, dict) and part in current:
            path += f".{part}" if path else part
            current = current[part]
        # Any other part names a branch of a tagged union, which is not part of the path.
    return path


def parse_scenario(data: Any) -> tuple[Scenario | None, ValidationReport]:
    """Build a scenario from data and validate it. Record level errors become report issues."""
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as error:
        issues = tuple(
            Issue(
                severity=Severity.ERROR,
                code=str(item["type"]),
                field_path=_format_location(item["loc"], data),
                message=str(item["msg"]),
            )
            for item in error.errors()
        )
        identifier = data.get("scenario_id") if isinstance(data, dict) else None
        return None, ValidationReport(
            scenario_id=identifier if isinstance(identifier, str) else None,
            revision_id=None,
            issues=issues,
        )
    return scenario, validate_scenario(scenario)


def read_scenario_data(path: Path) -> tuple[Any, list[tuple[str, str]]]:
    """Read a scenario file and import the GIS layers it names.

    Returns the scenario data and the problems of the layer import as pairs of field path and
    message.
    """
    data = read_data_file(path)
    if isinstance(data, dict) and "layer_sources" in data:
        from r3chain.geospatial.sources import resolve_layer_sources

        return resolve_layer_sources(data, path.parent)
    return data, []


def load_scenario_file(path: Path) -> tuple[Scenario | None, ValidationReport]:
    data, problems = read_scenario_data(path)
    scenario, report = parse_scenario(data)
    if problems:
        imported = tuple(
            Issue(severity=Severity.ERROR, code="layer_import", field_path=where, message=message)
            for where, message in problems
        )
        report = report.model_copy(update={"issues": (*report.issues, *imported)})
    return scenario, report
