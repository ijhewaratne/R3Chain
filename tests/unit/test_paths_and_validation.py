"""Field paths, the assumption register and whole scenario validation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import IMPORT_WARNINGS, SYNTHETIC_SCENARIO, changed, known

from r3chain.domain.assumptions import assumption_register
from r3chain.domain.paths import (
    PatchOperation,
    apply_patch,
    diff_json,
    get_at,
    iter_evidence,
    parse_path,
)
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.timeseries import IntervalSeries, SeriesKind
from r3chain.domain.validation import (
    Severity,
    load_scenario_file,
    parse_scenario,
    validate_scenario,
)

# --- field paths ----------------------------------------------------------------------------


def _count_inputs(node: Any) -> int:
    """Count engineering inputs in raw data, independently of the walker under test."""
    if isinstance(node, dict):
        if node.get("state") in {"known", "unknown", "conflict"}:
            return 1
        return sum(_count_inputs(value) for value in node.values())
    if isinstance(node, list):
        return sum(_count_inputs(item) for item in node)
    return 0


@pytest.mark.requirement("W24")
def test_register_lists_every_input(scenario: Scenario, scenario_data: dict[str, Any]) -> None:
    rows = assumption_register(scenario)
    assert len(rows) == _count_inputs(scenario_data) == 141
    assert len({row.field_path for row in rows}) == len(rows)
    assert len({row.assumption_id for row in rows}) == len(rows)
    by_path = {row.field_path: row for row in rows}

    # A known value shows its value, unit and provenance without being asked.
    area = by_path["sites[alpha].usable_pad_area_m2"]
    assert (area.status.value, area.value, area.unit) == ("active", 6000.0, "m2")
    assert (area.provenance_type.value, area.source_id, area.locator) == (
        "synthetic",
        "syn-definitions",
        "section 2",
    )
    # An unknown value shows why it is missing and who resolves it.
    pad = by_path["sites[delta].usable_pad_area_m2"]
    assert (pad.status.value, pad.value, pad.unit) == ("unknown", None, "m2")
    assert pad.owner == "Site specialist (open decision O09)"
    # A conflict shows every candidate.
    access = by_path["sites[delta].access"]
    assert access.status.value == "conflict"
    assert access.value == ["Gravel track, 3 m wide", "Paved road, 5 m wide"]

    # Units come with their reference.
    assert by_path["connections[conn-west].limits.max_pressure"].unit == "Pa (absolute)"
    depth = "well_scenarios[source-alpha].wells[alpha-production].vertical_depth"
    assert by_path[depth].unit == "m (vertical)"
    assert by_path["sites[alpha].elevation"].unit == "m (DHHN2016)"
    temperature = "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]"
    assert by_path[temperature].unit == "K"
    cost = "economic_scenarios[econ-base].cost_items[wells-alpha-capex].amount"
    assert by_path[cost].unit == "EUR 2026 real, total"
    assert by_path["economic_scenarios[econ-base].discount_rate_per_year"].unit == "1/a"


def test_paths_do_not_depend_on_list_order(
    scenario: Scenario, scenario_data: dict[str, Any]
) -> None:
    reordered = dict(scenario_data)
    reordered["sites"] = list(reversed(scenario_data["sites"]))
    before = {visit.path for visit in iter_evidence(scenario)}
    after = {visit.path for visit in iter_evidence(Scenario.model_validate(reordered))}
    assert before == after
    assert get_at(reordered, "sites[alpha].name") == "Synthetic site Alpha"


def test_paths_address_and_change_data(scenario_data: dict[str, Any]) -> None:
    assert parse_path("sites[alpha].constraints[housing-setback].outcome") == [
        ("field", "sites"),
        ("key", "alpha"),
        ("field", "constraints"),
        ("key", "housing-setback"),
        ("field", "outcome"),
    ]
    path = "well_scenarios[source-beta].reservoir_parameters[reservoir_temperature_k]"
    assert get_at(scenario_data, path)["value"] == 348.15

    patched = apply_patch(
        scenario_data,
        [
            PatchOperation("set", path, known(352.0)),
            PatchOperation("remove", "routes[route-beta-west]"),
            PatchOperation(
                "add", "alternatives", {"alternative_id": "storage", "label": "With storage"}
            ),
        ],
    )
    assert get_at(patched, path)["value"] == 352.0
    assert [route["route_id"] for route in patched["routes"]] == [
        "route-alpha-west",
        "route-gamma-east",
    ]
    assert patched["alternatives"][-1]["alternative_id"] == "storage"
    # The original data is untouched.
    assert get_at(scenario_data, path)["value"] == 348.15
    assert len(scenario_data["routes"]) == 3

    for bad in ("", "sites[", "sites..name"):
        with pytest.raises(ValueError):
            parse_path(bad)
    with pytest.raises(KeyError):
        get_at(scenario_data, "sites[omega].name")
    with pytest.raises(KeyError):
        apply_patch(scenario_data, [PatchOperation("add", "study", {})])


def test_diff_reports_field_level_changes(scenario_data: dict[str, Any]) -> None:
    assert diff_json(scenario_data, scenario_data) == []

    path = "economic_scenarios[econ-base].discount_rate_per_year"
    new_value = {
        "state": "known",
        "value": 0.05,
        "provenance": {"provenance_type": "user_supplied", "author": "reviewer"},
    }
    changes = diff_json(scenario_data, changed(scenario_data, path, new_value))
    # Value and provenance changed together and are reported as one change of one input.
    assert [(change.field_path, change.kind.value) for change in changes] == [(path, "changed")]
    assert changes[0].before["value"] == 0.04 and changes[0].after["value"] == 0.05

    removed = apply_patch(scenario_data, [PatchOperation("remove", "sites[beta]")])
    assert [(c.field_path, c.kind.value) for c in diff_json(scenario_data, removed)] == [
        ("sites[beta]", "removed")
    ]
    assert [(c.field_path, c.kind.value) for c in diff_json(removed, scenario_data)] == [
        ("sites[beta]", "added")
    ]
    plain = changed(scenario_data, "routes[route-alpha-west].corridor_length_m", 1250)
    assert [(c.field_path, c.before, c.after) for c in diff_json(scenario_data, plain)] == [
        ("routes[route-alpha-west].corridor_length_m", 1200, 1250)
    ]
    reordered = dict(scenario_data)
    reordered["sites"] = list(reversed(scenario_data["sites"]))
    order = diff_json(scenario_data, reordered)
    assert [(c.field_path, c.after) for c in order] == [
        ("sites", ["delta", "gamma", "beta", "alpha"])
    ]


# --- whole scenario validation --------------------------------------------------------------


def codes(data: dict[str, Any], severity: Severity = Severity.ERROR) -> list[tuple[str, str]]:
    scenario, report = parse_scenario(data)
    assert scenario is not None, report.issues
    return [(issue.code, issue.field_path) for issue in report.issues if issue.severity is severity]


def test_synthetic_scenario_is_executable_and_lists_its_gaps() -> None:
    scenario, report = load_scenario_file(SYNTHETIC_SCENARIO)
    assert scenario is not None
    # The only issues are the two geometry defects the study contains on purpose.
    assert report.executable
    assert [(issue.code, issue.field_path) for issue in report.issues] == list(IMPORT_WARNINGS)
    assert report.revision_id == scenario.revision_id()
    assert report.contains_synthetic_values
    missing = {item.field_path: item.state for item in report.missing_inputs}
    assert len(missing) == 15
    assert missing["sites[delta].access"] == "conflict"
    assert missing["connections[conn-east].limits.max_pressure"] == "unknown"
    assert missing["uncertainty_models[uncertainty-base].success_definition"] == "unknown"


def test_the_definitions_file_matches_its_recorded_checksum(scenario: Scenario) -> None:
    from r3chain.domain.hashing import file_checksum

    dataset = scenario.datasets[0]
    assert file_checksum(SYNTHETIC_SCENARIO.parent / dataset.source_uri) == dataset.checksum


@pytest.mark.requirement("W25")
def test_unregistered_source_is_an_error(scenario_data: dict[str, Any]) -> None:
    path = "sites[alpha].usable_pad_area_m2"
    value = {
        "state": "known",
        "value": 6000,
        "provenance": {"provenance_type": "measured", "source_id": "survey-2031"},
    }
    assert codes(changed(scenario_data, path, value)) == [("unknown_source", f"{path}.provenance")]


def test_broken_references_are_errors(scenario_data: dict[str, Any]) -> None:
    def broken(path: str, value: Any) -> list[tuple[str, str]]:
        return codes(changed(scenario_data, path, value))

    assert broken("study.baseline_id", "nothing") == [("unknown_reference", "study.baseline_id")]
    assert broken("routes[route-alpha-west].to_connection_id", "conn-north") == [
        ("unknown_reference", "routes[route-alpha-west].to_connection_id")
    ]
    assert broken("well_scenarios[source-alpha].site_id", "omega") == [
        ("unknown_reference", "well_scenarios[source-alpha].site_id")
    ]
    assert broken("well_scenarios[source-alpha].parameter_package_id", "geology-v9") == [
        ("unknown_reference", "well_scenarios[source-alpha].parameter_package_id")
    ]
    assert broken("connections[conn-west].supply_node", "r1") == [
        ("wrong_circuit", "connections[conn-west].supply_node")
    ]
    assert broken("connections[conn-west].zone_id", "zone-high") == [
        ("zone_mismatch", "connections[conn-west].zone_id"),
        ("zone_mismatch", "connections[conn-west].zone_id"),
    ]
    # A steam interface on a hot water network is a contradiction, not a detail.
    assert broken("connections[conn-west].heat_carrier", "steam") == [
        ("heat_carrier_mismatch", "connections[conn-west].heat_carrier")
    ]
    variable = "uncertainty_models[uncertainty-base].variables[discount-rate].field_path"
    assert broken(variable, "economic_scenarios[econ-base].interest") == [
        ("unknown_reference", variable)
    ]


def test_pressure_references_must_agree_across_a_network(scenario_data: dict[str, Any]) -> None:
    # Both limits of the connection are gauge, which is consistent inside the connection, while
    # the network boundary is absolute.
    data = scenario_data
    for field, value in (("min_pressure", 100_000), ("max_pressure", 900_000)):
        data = changed(
            data,
            f"connections[conn-west].limits.{field}",
            known({"value_pa": value, "reference": "gauge"}),
        )
    found = codes(data)
    assert (
        "inconsistent_pressure_reference",
        "connections[conn-west].limits.min_pressure",
    ) in found
    assert (
        "inconsistent_pressure_reference",
        "connections[conn-west].limits.max_pressure",
    ) in found


def test_record_errors_are_reported_with_field_paths(scenario_data: dict[str, Any]) -> None:
    data = changed(scenario_data, "sites[gamma].usable_pad_area_m2", known("large"))
    scenario, report = parse_scenario(data)
    assert scenario is None and not report.executable
    assert report.errors[0].field_path == "sites[gamma].usable_pad_area_m2.value"
    assert report.scenario_id == "synthetic-study"


@pytest.mark.requirement("W12")
def test_synthetic_values_in_a_pilot_study_are_flagged(scenario_data: dict[str, Any]) -> None:
    data = changed(scenario_data, "study.synthetic", False)
    assert codes(data) == []
    assert codes(data, Severity.WARNING) == [
        *IMPORT_WARNINGS,
        ("synthetic_value_in_pilot", "study.synthetic"),
    ]


def _with_operating_scenario(data: dict[str, Any]) -> dict[str, Any]:
    reference = {"dataset_id": "syn-definitions"}
    operating = {
        "operating_scenario_id": "operation-base",
        "demand": {"series_id": "demand", **reference},
        "source_availability": [
            {"source_id": "waste-heat", "availability": {"series_id": "waste-heat", **reference}}
        ],
        "tariffs": [{"series_id": "electricity-price", **reference}],
        "dispatch_policy_id": "numerical-coupling-default",
        "time_basis": {
            "start_utc": "2026-01-01T00:00:00Z",
            "end_utc": "2026-01-02T00:00:00Z",
            "interval_s": 3600,
        },
    }
    return {**data, "operating_scenarios": [operating], "time_series": []}


def _hourly(series_id: str, hours: int = 24, step_s: float = 3600.0) -> IntervalSeries:
    from datetime import UTC, datetime

    return IntervalSeries.regular(
        series_id=series_id,
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        start_utc=datetime(2026, 1, 1, tzinfo=UTC),
        step_s=step_s,
        values=[1.0] * hours,
    )


def test_time_coverage_is_checked_against_the_series(scenario_data: dict[str, Any]) -> None:
    scenario = Scenario.model_validate(_with_operating_scenario(scenario_data))
    base = "operating_scenarios[operation-base]"

    # Without the series the check cannot run. That is reported and is not a pass.
    report = validate_scenario(scenario)
    assert report.executable
    assert [(i.severity, i.code) for i in report.issues if i.severity is not Severity.WARNING] == [
        (Severity.UNVERIFIED, "time_coverage_not_checked")
    ]

    complete = {name: _hourly(name) for name in ("demand", "waste-heat", "electricity-price")}
    found = validate_scenario(scenario, complete).issues
    assert [(i.code, i.field_path) for i in found] == list(IMPORT_WARNINGS)

    def error_codes(series: dict[str, IntervalSeries]) -> list[tuple[str, str]]:
        return [(i.code, i.field_path) for i in validate_scenario(scenario, series).errors]

    assert error_codes({**complete, "electricity-price": _hourly("electricity-price", 12)}) == [
        ("incomplete_time_coverage", f"{base}.series[electricity-price]"),
        ("different_interval_sets", base),
    ]
    quarter_hourly = _hourly("demand", 96, 900.0)
    assert error_codes({**complete, "demand": quarter_hourly}) == [
        ("different_interval_sets", base)
    ]
    del complete["waste-heat"]
    assert error_codes(complete) == [("missing_series", f"{base}.series[waste-heat]")]

    gappy = _hourly("demand")
    gappy = IntervalSeries(
        series_id="demand",
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        starts_utc=(
            *gappy.starts_utc[:10],
            *(t + timedelta(hours=1) for t in gappy.starts_utc[10:]),
        ),
        durations_s=gappy.durations_s,
        values=gappy.values,
    )
    assert ("incomplete_time_coverage", f"{base}.series[demand]") in error_codes(
        {"demand": gappy, "waste-heat": _hourly("waste-heat"), "electricity-price": _hourly("p")}
    )


def test_an_unknown_dispatch_policy_is_an_error(scenario_data: dict[str, Any]) -> None:
    data = _with_operating_scenario(scenario_data)
    data["operating_scenarios"][0]["dispatch_policy_id"] = "dispatch-not-registered"
    assert codes(data) == [
        ("unknown_reference", "operating_scenarios[operation-base].dispatch_policy_id")
    ]


def test_json_and_yaml_files_load_alike(tmp_path: Path, scenario: Scenario) -> None:
    path = tmp_path / "scenario.json"
    path.write_text(scenario.model_dump_json(), encoding="utf-8")
    loaded, report = load_scenario_file(path)
    assert loaded == scenario and report.executable


def test_economic_scenario_validation_rejects_fractional_lifetimes_and_negative_residuals(
    scenario_data: dict[str, Any],
) -> None:
    # Fractional horizon_years.
    bad_horizon = changed(scenario_data, "economic_scenarios[econ-base].horizon_years", known(20.5))
    assert (
        "invalid_economic_horizon",
        "economic_scenarios[econ-base].horizon_years",
    ) in codes(bad_horizon)

    # Discount rate at or below -100% is already rejected by parse_scenario.
    bad_rate = changed(
        scenario_data, "economic_scenarios[econ-base].discount_rate_per_year", known(-1.0)
    )
    parsed_bad_rate, rate_report = parse_scenario(bad_rate)
    assert parsed_bad_rate is None and not rate_report.executable

    # Fractional CAPEX lifetime_years.
    bad_life = changed(
        scenario_data,
        "economic_scenarios[econ-base].cost_items[wells-alpha-capex].lifetime_years",
        known(12.5),
    )
    assert (
        "invalid_cost_lifetime",
        "economic_scenarios[econ-base].cost_items[wells-alpha-capex].lifetime_years",
    ) in codes(bad_life)

    # Replacement item with out-of-range year (> horizon_years) or fractional year.
    e_base = "economic_scenarios[econ-base]"
    items = list(scenario_data["economic_scenarios"][0]["cost_items"])
    base_item = {
        "item_id": "bad-repl",
        "asset_id": "wells-alpha",
        "category": "replacement",
        "cost_owner": "system",
        "location": "source",
        "included": True,
        "amount": known(500_000.0),
        "amount_basis": "total",
        "currency": "EUR",
        "price_year": 2026,
        "price_convention": "real",
        "lifetime_years": known(25.0),
        "applies_to": {"site_ids": ["alpha"]},
    }
    econ_entry = scenario_data["economic_scenarios"][0]
    bad_repl_data = {
        **scenario_data,
        "economic_scenarios": [{**econ_entry, "cost_items": [*items, base_item]}],
    }
    assert ("invalid_replacement_year", f"{e_base}.cost_items[bad-repl].lifetime_years") in codes(
        bad_repl_data
    )

    # Negative residual value is rejected (residual credit must be entered as positive).
    res_item = {
        **base_item,
        "item_id": "bad-res",
        "category": "residual_value",
        "lifetime_years": None,
        "amount": known(-100_000.0),
    }
    bad_res_data = {
        **scenario_data,
        "economic_scenarios": [{**econ_entry, "cost_items": [*items, res_item]}],
    }
    assert ("invalid_residual_value", f"{e_base}.cost_items[bad-res].amount") in codes(bad_res_data)


def test_negative_demand_series_is_rejected_with_series_and_interval(
    scenario_data: dict[str, Any],
) -> None:
    from datetime import UTC, datetime

    scenario = Scenario.model_validate(_with_operating_scenario(scenario_data))
    values = [100.0] * 24
    values[5] = -25.0
    neg_demand = IntervalSeries.regular(
        series_id="demand",
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        start_utc=datetime(2026, 1, 1, tzinfo=UTC),
        step_s=3600.0,
        values=values,
    )
    errors = validate_scenario(
        scenario,
        {
            "demand": neg_demand,
            "waste-heat": _hourly("waste-heat"),
            "electricity-price": _hourly("electricity-price"),
        },
    ).errors
    assert len(errors) == 1
    assert errors[0].code == "negative_demand"
    assert errors[0].field_path == "operating_scenarios[operation-base].series[demand]"
    assert "interval 5" in errors[0].message
    assert "2026-01-01T05:00:00" in errors[0].message
