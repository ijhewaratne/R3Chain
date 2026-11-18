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
from r3chain.domain.records.scenarios import CostCategory, UncertaintyKind
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
    _check_economic_scenarios(scenario, found)
    _check_uncertainty_models(scenario, found)
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
    _check_stored_series(scenario, found)

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

    for economic in scenario.economic_scenarios:
        e_base = f"economic_scenarios[{economic.economic_scenario_id}]"
        for rule in economic.funding_rules:
            if rule.source_id not in datasets:
                found.error(
                    "unknown_reference",
                    f"{e_base}.funding_rules[{rule.rule_id}].source_id",
                    f"dataset {rule.source_id!r} is not registered in this scenario",
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
    for site in scenario.sites:
        if site.parcel_geometry.crs != study_crs:
            found.error(
                "wrong_reference_system",
                f"sites[{site.site_id}].parcel_geometry.crs",
                f"site parcel is in {site.parcel_geometry.crs}, the study uses {study_crs}",
            )
    for connection in scenario.connections:
        if connection.location is not None and connection.location.crs != study_crs:
            found.error(
                "wrong_reference_system",
                f"connections[{connection.connection_id}].location.crs",
                f"connection location is in {connection.location.crs}, the study uses {study_crs}",
            )
    for route in scenario.routes:
        if route.geometry.crs != study_crs:
            found.error(
                "wrong_reference_system",
                f"routes[{route.route_id}].geometry.crs",
                f"route geometry is in {route.geometry.crs}, the study uses {study_crs}",
            )
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


def _check_stored_series(scenario: Scenario, found: _Collector) -> None:
    """Stored time series must point at a registered dataset matching operating references."""
    datasets = {item.dataset_id for item in scenario.datasets}
    stored = {item.series_id: item for item in scenario.time_series}
    for item in scenario.time_series:
        if item.dataset_id not in datasets:
            found.error(
                "unknown_reference",
                f"time_series[{item.series_id}].dataset_id",
                f"dataset {item.dataset_id!r} is not registered in this scenario",
            )
    if not stored:
        return
    for operating in scenario.operating_scenarios:
        base = f"operating_scenarios[{operating.operating_scenario_id}]"
        for reference in operating.series_refs():
            loaded = stored.get(reference.series_id)
            if loaded is not None and loaded.dataset_id != reference.dataset_id:
                found.error(
                    "dataset_mismatch",
                    f"{base}.series[{reference.series_id}].dataset_id",
                    f"operating scenario expects dataset {reference.dataset_id!r}, stored series "
                    f"comes from {loaded.dataset_id!r}",
                )


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
    effective = (
        series
        if series is not None
        else (
            {item.series_id: item.series for item in scenario.time_series}
            if scenario.time_series
            else None
        )
    )
    for operating in scenario.operating_scenarios:
        base = f"operating_scenarios[{operating.operating_scenario_id}]"
        if effective is None:
            found.add(
                Severity.UNVERIFIED,
                "time_coverage_not_checked",
                base,
                "no time series were supplied, so interval coverage was not checked",
            )
            continue
        loaded: list[IntervalSeries] = []
        for reference in operating.series_refs():
            item = effective.get(reference.series_id)
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
            if reference.series_id == operating.demand.series_id:
                for idx, (val, start) in enumerate(zip(item.values, item.starts_utc, strict=True)):
                    if val < 0.0:
                        found.error(
                            "negative_demand",
                            f"{base}.series[{reference.series_id}]",
                            f"demand series {item.series_id!r} cannot be negative "
                            f"(interval {idx} at {start.isoformat()} is {val})",
                        )
                        break
        if len(loaded) > 1 and not same_interval_set(*loaded):
            found.error(
                "different_interval_sets",
                base,
                "demand, availability, temperature and tariff series must share one interval set",
            )


def _check_economic_scenarios(scenario: Scenario, found: _Collector) -> None:
    """Cash-flow inputs must use whole-year horizons/lifetimes and non-negative residuals."""
    for economic in scenario.economic_scenarios:
        e_base = f"economic_scenarios[{economic.economic_scenario_id}]"
        rate = value_or_none(economic.discount_rate_per_year)
        if rate is not None and rate <= -1.0:
            found.error(
                "invalid_discount_rate",
                f"{e_base}.discount_rate_per_year",
                f"discount_rate_per_year ({rate}) must be greater than -1",
            )
        horizon_raw = value_or_none(economic.horizon_years)
        horizon_int: int | None = None
        if horizon_raw is not None:
            rounded = round(horizon_raw)
            if rounded <= 0 or abs(rounded - horizon_raw) > 1e-6:
                found.error(
                    "invalid_economic_horizon",
                    f"{e_base}.horizon_years",
                    f"horizon_years ({horizon_raw}) must be a positive whole number of years",
                )
            else:
                horizon_int = rounded
        for item in economic.cost_items:
            i_base = f"{e_base}.cost_items[{item.item_id}]"
            life = value_or_none(item.lifetime_years) if item.lifetime_years is not None else None
            if item.category is CostCategory.CAPEX and life is not None:
                step = round(life)
                if step <= 0 or abs(step - life) > 1e-6:
                    found.error(
                        "invalid_cost_lifetime",
                        f"{i_base}.lifetime_years",
                        f"lifetime_years ({life}) must be a positive whole number of years",
                    )
            elif item.category is CostCategory.REPLACEMENT and life is not None:
                target = round(life)
                if (
                    abs(target - life) > 1e-6
                    or target < 1
                    or (horizon_int is not None and target > horizon_int)
                ):
                    upper = horizon_int if horizon_int is not None else "horizon"
                    found.error(
                        "invalid_replacement_year",
                        f"{i_base}.lifetime_years",
                        f"replacement item {item.item_id!r} year ({life}) must be a whole year "
                        f"in 1..{upper}",
                    )
            elif item.category is CostCategory.RESIDUAL_VALUE:
                amount = value_or_none(item.amount)
                if amount is not None and amount < 0.0:
                    found.error(
                        "invalid_residual_value",
                        f"{i_base}.amount",
                        f"residual_value item {item.item_id!r} cannot be negative; enter positive "
                        "credit",
                    )


_SUPPORTED_DISTRIBUTIONS = frozenset({"normal", "lognormal", "triangular", "uniform", "bernoulli"})


def _check_uncertainty_models(scenario: Scenario, found: _Collector) -> None:
    """Check distribution parameters and correlation group bindings in uncertainty models."""
    for model in scenario.uncertainty_models:
        m_base = f"uncertainty_models[{model.uncertainty_model_id}]"
        group_ids = {group.group_id for group in model.correlation_groups}
        for var in model.variables:
            v_base = f"{m_base}.variables[{var.variable_id}]"
            if var.correlation_group is not None and var.correlation_group not in group_ids:
                found.error(
                    "unknown_correlation_group",
                    f"{v_base}.correlation_group",
                    f"correlation_group {var.correlation_group!r} has no matching group in "
                    f"{model.uncertainty_model_id!r}",
                )
            if var.kind is not UncertaintyKind.DISTRIBUTION or var.distribution is None:
                continue
            fam = var.distribution.family
            params = var.distribution.parameters
            if fam not in _SUPPORTED_DISTRIBUTIONS:
                found.error(
                    "unsupported_distribution_family",
                    f"{v_base}.distribution.family",
                    f"unsupported distribution family {fam!r}",
                )
                continue
            if fam == "normal":
                if "mean" not in params or params.get("std", 0.0) <= 0.0:
                    found.error(
                        "invalid_distribution_parameters",
                        f"{v_base}.distribution.parameters",
                        "normal distribution requires 'mean' and positive 'std'",
                    )
            elif fam == "lognormal":
                has_mu = "mu" in params and params.get("sigma", 0.0) > 0.0
                has_mean = params.get("mean", 0.0) > 0.0 and params.get("std", 0.0) > 0.0
                if not (has_mu or has_mean):
                    found.error(
                        "invalid_distribution_parameters",
                        f"{v_base}.distribution.parameters",
                        "lognormal distribution requires ('mu' and 'sigma' > 0) or positive "
                        "('mean' and 'std')",
                    )
            elif fam == "triangular":
                low = params.get("low", var.support.lower)
                high = params.get("high", var.support.upper)
                mode = params.get("mode")
                if mode is None or not (low <= mode <= high) or low >= high:
                    found.error(
                        "invalid_distribution_parameters",
                        f"{v_base}.distribution.parameters",
                        "triangular distribution requires low <= mode <= high with low < high",
                    )
            elif fam == "uniform":
                low = params.get("low", var.support.lower)
                high = params.get("high", var.support.upper)
                if low >= high:
                    found.error(
                        "invalid_distribution_parameters",
                        f"{v_base}.distribution.parameters",
                        "uniform distribution requires low < high",
                    )
            elif fam == "bernoulli":
                p = params.get("p")
                if p is None or not 0.0 <= p <= 1.0:
                    found.error(
                        "invalid_distribution_parameters",
                        f"{v_base}.distribution.parameters",
                        "bernoulli distribution requires 'p' in [0, 1]",
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
    """Read a scenario file and import the GIS layers and time series it names.

    Returns the scenario data and the import problems as pairs of field path and message.
    """
    data = read_data_file(path)
    problems: list[tuple[str, str]] = []
    if isinstance(data, dict) and "layer_sources" in data:
        from r3chain.geospatial.sources import resolve_layer_sources

        data, layer_problems = resolve_layer_sources(data, path.parent)
        problems.extend(layer_problems)
    if isinstance(data, dict) and "series_sources" in data:
        from r3chain.operation.series import resolve_series_sources

        data, series_problems = resolve_series_sources(data, path.parent)
        problems.extend(series_problems)
    return data, problems


def load_scenario_file(path: Path) -> tuple[Scenario | None, ValidationReport]:
    data, problems = read_scenario_data(path)
    scenario, report = parse_scenario(data)
    if problems:
        imported = tuple(
            Issue(
                severity=Severity.ERROR,
                code="series_import" if where.startswith("series_sources[") else "layer_import",
                field_path=where,
                message=message,
            )
            for where, message in problems
        )
        report = report.model_copy(update={"issues": (*report.issues, *imported)})
    return scenario, report
