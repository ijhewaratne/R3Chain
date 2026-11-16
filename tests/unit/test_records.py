"""The core records: round trips, schema versions and what each record rejects."""

from __future__ import annotations

import json
import types
from typing import Annotated, Any, ClassVar, Literal, Union, get_args, get_origin

import pytest
from pydantic import BaseModel, ValidationError
from tests.conftest import changed, known

from r3chain.domain.base import VersionedRecord, register_migration
from r3chain.domain.evidence import Conflict, Known
from r3chain.domain.records import CORE_RECORDS
from r3chain.domain.records.runs import Check, KpiValue, Policy
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import SiteEvidenceStatus
from r3chain.domain.units import unit_for_field
from r3chain.schemas import VERSIONED_RECORDS

# --- structure ------------------------------------------------------------------------------


def test_the_thirteen_core_records_exist() -> None:
    assert [record.SCHEMA_NAME for record in CORE_RECORDS] == [
        "study",
        "dataset",
        "site",
        "well_scenario",
        "connection",
        "route",
        "network",
        "operating_scenario",
        "economic_scenario",
        "uncertainty_model",
        "assumption",
        "run",
        "result",
    ]
    names = [record.SCHEMA_NAME for record in VERSIONED_RECORDS]
    assert len(names) == len(set(names))


def _contains_float(annotation: Any) -> bool:
    """True when a type annotation can hold a plain number that needs a unit."""
    if annotation is float:
        return True
    origin = get_origin(annotation)
    if origin is Annotated:
        return _contains_float(get_args(annotation)[0])
    if origin in (Union, types.UnionType, tuple, list):
        return any(_contains_float(argument) for argument in get_args(annotation))
    if hasattr(annotation, "__value__"):  # a named alias such as Evidence[Real]
        return _contains_float(annotation.__value__)
    if isinstance(annotation, type) and issubclass(annotation, Known | Conflict):
        arguments = annotation.__pydantic_generic_metadata__["args"]
        return any(_contains_float(argument) for argument in arguments)
    return False


def _all_models(model: type[BaseModel], seen: set[type[BaseModel]]) -> None:
    if model in seen:
        return
    seen.add(model)

    def visit(annotation: Any) -> None:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            _all_models(annotation, seen)
        if hasattr(annotation, "__value__"):
            visit(annotation.__value__)
        for argument in get_args(annotation):
            visit(argument)

    for field in model.model_fields.values():
        visit(field.annotation)


def test_every_numeric_field_declares_a_unit() -> None:
    """No number without a unit: a suffix in the field name, or an explicit unit note."""
    models: set[type[BaseModel]] = set()
    for record in VERSIONED_RECORDS:
        _all_models(record, models)
    undeclared = []
    for model in models:
        if issubclass(model, Known | Conflict):
            continue  # the unit belongs to the field that holds the evidence
        for name, field in model.model_fields.items():
            if not _contains_float(field.annotation):
                continue
            extra = field.json_schema_extra
            explicit = isinstance(extra, dict) and "unit" in extra
            if unit_for_field(name) is None and not explicit:
                undeclared.append(f"{model.__name__}.{name}")
    assert sorted(undeclared) == []


# --- round trips ----------------------------------------------------------------------------


def test_synthetic_scenario_round_trips_without_loss(
    scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    as_dict = scenario.to_json_dict()
    again = Scenario.model_validate(as_dict)
    assert again == scenario
    assert again.content_hash() == scenario.content_hash()
    # Through JSON text as well, as it is stored.
    from_text = Scenario.model_validate_json(scenario.model_dump_json())
    assert from_text == scenario
    assert from_text.to_json_dict() == as_dict
    # Values and provenance survive exactly.
    area = as_dict["sites"][0]["usable_pad_area_m2"]
    assert area == {
        "state": "known",
        "value": 6000.0,
        "provenance": {
            "provenance_type": "synthetic",
            "source_id": "syn-definitions",
            "locator": "section 2",
            "author": None,
            "reviewer": None,
            "method": None,
        },
    }
    assert json.loads(json.dumps(as_dict)) == as_dict


def test_revision_id_follows_content(scenario: Scenario, scenario_data: dict[str, Any]) -> None:
    same = Scenario.model_validate(scenario_data)
    assert same.revision_id() == scenario.revision_id()
    other = Scenario.model_validate(
        changed(scenario_data, "sites[alpha].usable_pad_area_m2", known(6100))
    )
    assert other.revision_id() != scenario.revision_id()


# --- schema versions ------------------------------------------------------------------------


class _Sample(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "test_sample"
    schema_version: Literal["2.0.0"] = "2.0.0"
    sample_id: str
    area_m2: float


@register_migration("test_sample", "1.0.0", "2.0.0")
def _rename_area(data: dict[str, Any]) -> dict[str, Any]:
    data["area_m2"] = data.pop("area")
    return data


def test_old_data_is_migrated_and_unknown_versions_are_rejected() -> None:
    migrated = _Sample.model_validate({"schema_version": "1.0.0", "sample_id": "s", "area": 12.0})
    assert migrated.area_m2 == 12.0 and migrated.schema_version == "2.0.0"
    # Data without a version is taken to be current.
    assert _Sample.model_validate({"sample_id": "s", "area_m2": 1.0}).schema_version == "2.0.0"
    with pytest.raises(ValidationError, match="no migration"):
        _Sample.model_validate({"schema_version": "0.5.0", "sample_id": "s", "area_m2": 1.0})
    with pytest.raises(ValueError, match="already exists"):
        register_migration("test_sample", "1.0.0", "2.0.0")(_rename_area)


def test_unknown_fields_are_rejected(scenario_data: dict[str, Any]) -> None:
    scenario_data["sites"][0]["colour"] = "green"
    with pytest.raises(ValidationError, match="colour"):
        Scenario.model_validate(scenario_data)


# --- what records reject --------------------------------------------------------------------


def expect_rejection(data: dict[str, Any], path: str, value: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Scenario.model_validate(changed(data, path, value))


def test_inconsistent_pressure_references_are_rejected(scenario_data: dict[str, Any]) -> None:
    expect_rejection(
        scenario_data,
        "connections[conn-west].limits.min_pressure",
        known({"value_pa": 200000, "reference": "gauge"}),
        "inconsistent pressure references",
    )
    # A pressure without any reference does not validate at all.
    expect_rejection(
        scenario_data,
        "connections[conn-west].limits.min_pressure",
        known({"value_pa": 200000}),
        "reference",
    )
    expect_rejection(
        scenario_data,
        "connections[conn-west].limits.min_pressure",
        known({"value_pa": 2_000_000, "reference": "absolute"}),
        "min_pressure must be below max_pressure",
    )


def test_connection_limits_must_be_physical(scenario_data: dict[str, Any]) -> None:
    limits = "connections[conn-west].limits"
    expect_rejection(
        scenario_data, f"{limits}.return_temperature_k", known(360.0), "above the return"
    )
    # A temperature entered in degrees Celsius by mistake is caught when it is not positive.
    expect_rejection(scenario_data, f"{limits}.return_temperature_k", known(-5.0), "above 0 K")
    expect_rejection(scenario_data, f"{limits}.max_heat_w", known(0.0), "must be positive")
    expect_rejection(scenario_data, "connections[conn-west].return_node", "s0", "distinct supply")


def test_depths_keep_their_meaning(scenario_data: dict[str, Any]) -> None:
    well = "well_scenarios[source-alpha].wells[alpha-production]"
    expect_rejection(
        scenario_data,
        f"{well}.measured_length",
        known({"value_m": 2500, "kind": "measured"}),
        "cannot be shorter than the vertical depth",
    )
    expect_rejection(
        scenario_data,
        f"{well}.vertical_depth",
        known({"value_m": 3000, "kind": "measured"}),
        "kind 'vertical'",
    )
    expect_rejection(scenario_data, f"{well}.vertical_depth", known({"value_m": 3000}), "kind")


def test_reservoir_parameters_need_a_unit_suffix(scenario_data: dict[str, Any]) -> None:
    expect_rejection(
        scenario_data,
        "well_scenarios[source-alpha].reservoir_parameters[porosity]",
        known(0.12),
        "no unit suffix",
    )


def test_well_scenario_needs_a_production_well(scenario_data: dict[str, Any]) -> None:
    wells = scenario_data["well_scenarios"][0]["wells"]
    scenario_data["well_scenarios"][0]["wells"] = [
        well for well in wells if well["role"] != "production"
    ]
    with pytest.raises(ValidationError, match="production well"):
        Scenario.model_validate(scenario_data)


def test_network_topology_is_checked(scenario_data: dict[str, Any]) -> None:
    pipe = "networks[syn-network].pipes[supply-0-1]"
    expect_rejection(scenario_data, f"{pipe}.to_node", "r1", "joins the supply and return")
    expect_rejection(scenario_data, f"{pipe}.to_node", "nowhere", "unknown node")
    expect_rejection(scenario_data, f"{pipe}.to_node", "s0", "two different nodes")
    expect_rejection(scenario_data, f"{pipe}.inner_diameter_m", 0, "greater than 0")
    expect_rejection(scenario_data, f"{pipe}.estimated_fields", ["colour"], "unknown pipe fields")
    consumer = "networks[syn-network].consumers[consumer-1]"
    expect_rejection(scenario_data, f"{consumer}.supply_node", "r1", "supply circuit")
    boundary = "networks[syn-network].boundary_conditions[pressure-reference]"
    expect_rejection(scenario_data, f"{boundary}.pressure", None, "needs a pressure")


def test_duplicate_identifiers_are_rejected(scenario_data: dict[str, Any]) -> None:
    scenario_data["sites"].append(dict(scenario_data["sites"][0]))
    with pytest.raises(ValidationError, match="duplicate site_id: alpha"):
        Scenario.model_validate(scenario_data)


def test_identifiers_cannot_contain_path_syntax(scenario_data: dict[str, Any]) -> None:
    expect_rejection(scenario_data, "sites[alpha].site_id", "alpha.one", "pattern")
    expect_rejection(scenario_data, "sites[alpha].site_id", "alpha[1]", "pattern")


def test_route_quantities_are_checked(scenario_data: dict[str, Any]) -> None:
    route = "routes[route-gamma-east]"
    expect_rejection(scenario_data, f"{route}.corridor_length_m", 2000, "beyond the corridor")
    expect_rejection(scenario_data, f"{route}.supply_pipe_length_m", 0, "greater than 0")
    expect_rejection(
        scenario_data,
        f"{route}.elevation_profile",
        [{"chainage_m": 100, "elevation_m": 1}, {"chainage_m": 50, "elevation_m": 2}],
        "ordered by chainage",
    )


def test_spatial_values_need_their_reference_system(scenario_data: dict[str, Any]) -> None:
    geometry = {"geojson": {"type": "Point", "coordinates": [1, 2]}}
    expect_rejection(scenario_data, "sites[alpha].parcel_geometry", geometry, "crs")
    expect_rejection(
        scenario_data,
        "sites[alpha].parcel_geometry",
        {"crs": "EPSG:25832", "geojson": {"type": "Circle", "coordinates": [1, 2]}},
        "geometry type",
    )
    # A height without a vertical datum is not accepted.
    expect_rejection(
        scenario_data, "sites[alpha].elevation", known({"value_m": 165}), "vertical_datum"
    )
    expect_rejection(scenario_data, "datasets[syn-definitions].checksum", "abc123", "sha256")


@pytest.mark.requirement("A09")
@pytest.mark.requirement("W03")
def test_duplicate_cost_ownership_is_rejected(scenario_data: dict[str, Any]) -> None:
    # The same well CAPEX claimed once by the source model and once by the system ledger.
    items = scenario_data["economic_scenarios"][0]["cost_items"]
    duplicate = {**items[0], "item_id": "wells-alpha-from-source-model", "cost_owner": "pydoublet"}
    items.append(duplicate)
    with pytest.raises(ValidationError, match="duplicate cost ownership"):
        Scenario.model_validate(scenario_data)
    # Marking one of them as not included resolves the double counting explicitly.
    duplicate["included"] = False
    Scenario.model_validate(scenario_data)


def test_mixed_price_conventions_are_rejected(scenario_data: dict[str, Any]) -> None:
    item = "economic_scenarios[econ-base].cost_items[wells-alpha-capex]"
    expect_rejection(scenario_data, f"{item}.price_convention", "nominal", "mixes nominal")
    expect_rejection(scenario_data, f"{item}.currency", "USD", "Mixed currencies")
    expect_rejection(scenario_data, f"{item}.price_year", 2020, "price year 2020")
    economics = "economic_scenarios[econ-base]"
    expect_rejection(scenario_data, f"{economics}.horizon_years", known(0), "must be positive")
    expect_rejection(scenario_data, f"{economics}.discount_rate_per_year", known(-1.5), "above -1")


def test_a_range_alone_is_not_a_distribution(scenario_data: dict[str, Any]) -> None:
    variable = "uncertainty_models[uncertainty-base].variables[alpha-reservoir-temperature]"
    expect_rejection(scenario_data, f"{variable}.scenario_values", [], "range alone")
    expect_rejection(scenario_data, f"{variable}.kind", "distribution", "named distribution")
    expect_rejection(scenario_data, f"{variable}.scenario_values", [500.0], "outside the support")
    expect_rejection(scenario_data, f"{variable}.unit", "degC", "canonical unit")
    expect_rejection(
        scenario_data, f"{variable}.support", {"lower": 2, "upper": 1}, "lower < upper"
    )


def test_site_evidence_status(scenario: Scenario, scenario_data: dict[str, Any]) -> None:
    by_id = {site.site_id: site for site in scenario.sites}
    assert by_id["alpha"].evidence_status() is SiteEvidenceStatus.DOCUMENTED
    assert by_id["delta"].evidence_status() is SiteEvidenceStatus.CONFLICT

    # Resolving the conflict leaves unknown inputs, so the site is conditional.
    resolved = changed(scenario_data, "sites[delta].access", known("Paved road, 5 m wide"))
    delta = Scenario.model_validate(resolved).sites[3]
    assert delta.evidence_status() is SiteEvidenceStatus.CONDITIONAL

    unknown = {"state": "unknown", "reason": "not assessed", "owner": "site specialist"}
    for field in (
        "usable_pad_geometry",
        "usable_pad_area_m2",
        "elevation",
        "access",
        "land_status",
    ):
        resolved = changed(resolved, f"sites[delta].{field}", unknown)
    assert (
        Scenario.model_validate(resolved).sites[3].evidence_status() is SiteEvidenceStatus.UNKNOWN
    )


def test_results_cannot_hold_an_incomplete_check_or_kpi() -> None:
    with pytest.raises(ValidationError, match="must state"):
        Check(check_id="velocity", description="pipe velocity", outcome="fail", observed=3.1)
    Check(check_id="velocity", description="pipe velocity", outcome="not_evaluated")
    Check(
        check_id="velocity",
        description="pipe velocity",
        outcome="pass",
        observed=1.2,
        limit=2.0,
        margin=0.8,
        unit="m/s",
        threshold_origin="policy screening-synthetic v1",
    )
    definition = {"kpi_id": "lcoh", "name": "LCOH", "unit": "currency/J", "boundary": "delivered"}
    with pytest.raises(ValidationError):
        KpiValue.model_validate({"definition": definition, "value": 1.0, "derived_from": []})
    with pytest.raises(ValidationError):
        KpiValue.model_validate(
            {"definition": {**definition, "boundary": ""}, "value": 1.0, "derived_from": ["cost"]}
        )


def test_policy_parameters_carry_units_and_reviews_name_a_reviewer() -> None:
    base = {
        "policy_id": "p",
        "kind": "screening",
        "version": "1",
        "title": "t",
        "origin": "o",
        "status": "unreviewed",
    }
    Policy.model_validate({**base, "parameters": {"min_usable_pad_area_m2": 5000, "strict": True}})
    with pytest.raises(ValidationError, match="no unit suffix"):
        Policy.model_validate({**base, "parameters": {"min_pad_area": 5000}})
    with pytest.raises(ValidationError, match="reviewer"):
        Policy.model_validate({**base, "status": "reviewed", "parameters": {}})
