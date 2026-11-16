"""The line graph, its geometric checks, and route generation (specification 8.3 and 11.1)."""

from __future__ import annotations

from typing import Any

import pytest
from shapely.geometry import LineString

from r3chain.domain.records.scenario import Scenario
from r3chain.geospatial.graph import build_line_graph
from r3chain.geospatial.routes import (
    GeneratedRoute,
    RoutePair,
    RouteSettings,
    build_route_network,
    generate_routes,
)
from r3chain.geospatial.screening import ScreeningSettings, screen_site
from tests.conftest import policy_variant, repository_policies

POLICY = repository_policies()["route-synthetic"]


def lines(*items: tuple[str, list[tuple[float, float]]]) -> list[tuple[str, Any, dict[str, Any]]]:
    return [(name, LineString(points), {}) for name, points in items]


def codes(graph: Any) -> list[tuple[str, tuple[str, ...]]]:
    return [(issue.code, issue.element_ids) for issue in graph.issues]


# --- the line graph -------------------------------------------------------------------------


def test_end_points_within_the_snap_tolerance_become_one_node() -> None:
    graph = build_line_graph(
        lines(("a", [(0, 0), (100, 0)]), ("b", [(100.4, 0.3), (200, 0)])),
        snap_tolerance_m=1.0,
        near_miss_m=10.0,
    )
    assert len(graph.nodes) == 3
    a, b = graph.edges
    assert a.end == b.start
    assert codes(graph) == [("snapped_end_points", ("a", "b"))]


@pytest.mark.requirement("W06")
def test_near_misses_and_unsplit_junctions_are_reported_not_joined() -> None:
    graph = build_line_graph(
        lines(
            ("a", [(0, 0), (100, 0)]),
            ("b", [(104, 0), (200, 0)]),
            ("c", [(50, 3), (50, 100)]),
        ),
        snap_tolerance_m=1.0,
        near_miss_m=10.0,
    )
    assert len(graph.nodes) == 6
    found = codes(graph)
    assert ("near_miss", ("n2", "n3")) in found or any(code == "near_miss" for code, _ in found)
    assert any(code == "end_point_near_line" and ids[1] == "a" for code, ids in found)
    message = next(issue.message for issue in graph.issues if issue.code == "near_miss")
    assert "4.00 m apart" in message and "They are not joined" in message


@pytest.mark.requirement("W06")
def test_lines_that_cross_without_a_junction_are_reported() -> None:
    graph = build_line_graph(
        lines(("road", [(0, 0), (100, 0)]), ("bridge", [(50, -50), (50, 50)])),
        snap_tolerance_m=1.0,
        near_miss_m=10.0,
    )
    assert codes(graph) == [("crossing_without_junction", ("bridge", "road"))]
    assert len(graph.nodes) == 4


# --- route generation -----------------------------------------------------------------------


def pair(scenario: Scenario, site_id: str, connection_id: str, policy: Any = POLICY) -> RoutePair:
    settings = RouteSettings.from_policy(policy)
    network = build_route_network(scenario.spatial_layers, settings)
    site = next(item for item in scenario.sites if item.site_id == site_id)
    screening = screen_site(
        site,
        well_scenarios=scenario.well_scenarios,
        layers=scenario.spatial_layers,
        settings=ScreeningSettings.from_policy(repository_policies()["screening-synthetic"]),
        study_crs=scenario.study.study_area.crs,
    )
    assert screening.pad_point is not None
    connection = next(item for item in scenario.connections if item.connection_id == connection_id)
    return generate_routes(
        site,
        screening.pad_point,
        connection,
        scenario.networks[0],
        network,
        settings,
        scenario.study.study_area.crs,
    )


def route(found: RoutePair, rank: int) -> GeneratedRoute:
    return next(item for item in found.routes if item.rank == rank)


@pytest.mark.requirement("W06")
def test_the_synthetic_corridors_form_a_clean_graph(scenario: Scenario) -> None:
    network = build_route_network(scenario.spatial_layers, RouteSettings.from_policy(POLICY))
    assert network.issues == ()
    assert len(network.lines.edges) == 14


@pytest.mark.requirement("A11")
def test_the_shortest_route_is_not_assumed_to_be_the_cheapest(scenario: Scenario) -> None:
    found = pair(scenario, "alpha", "conn-west")
    assert found.status == "routed" and len(found.routes) == 2
    cheapest, shortest = route(found, 1), route(found, 2)
    assert cheapest.bases == ("least_cost",)
    assert shortest.bases == ("least_cost", "shortest_length")
    # Hand calculation with the rates of the route policy, per metre of corridor:
    #   pipes: 2 x 1.05 x 250 = 525. Street trench 900, field path trench 350.
    # Shortest: 30 m link, 600 m and 600 m of street, one stream crossing.
    #   trench 30 x 350 + 1200 x 900 = 1 090 500
    #   pipes 1230 x 525 = 645 750, crossing 150 000, total 1 886 250
    assert shortest.route.corridor_length_m == pytest.approx(1230.0, abs=1e-3)
    assert shortest.cost.trench_currency == pytest.approx(1_090_500.0, abs=0.1)
    assert shortest.cost.pipes_currency == pytest.approx(645_750.0, abs=0.1)
    assert shortest.cost.crossings_currency == pytest.approx(150_000.0)
    assert shortest.cost.total_currency == pytest.approx(1_886_250.0, abs=0.1)
    # Cheapest: 30 m link, 707.1 m and 500 m of field path, 100 m of street, no crossing.
    field = (700**2 + 100**2) ** 0.5 + 500.0
    length = 30.0 + field + 100.0
    expected = 30.0 * 350 + field * 350 + 100.0 * 900 + length * 525
    assert cheapest.route.corridor_length_m == pytest.approx(length, abs=1e-3)
    assert cheapest.cost.total_currency == pytest.approx(expected, abs=0.1)
    assert cheapest.route.corridor_length_m > shortest.route.corridor_length_m
    assert cheapest.cost.total_currency < shortest.cost.total_currency


def test_trench_and_pipe_quantities_are_kept_apart(scenario: Scenario) -> None:
    generated = route(pair(scenario, "alpha", "conn-west"), 2)
    item = generated.route
    # Supply and return run in one trench. Each pipe is the corridor plus the 5 % allowance.
    assert item.supply_pipe_length_m == pytest.approx(1230.0 * 1.05, abs=1e-3)
    assert item.return_pipe_length_m == pytest.approx(1230.0 * 1.05, abs=1e-3)
    assert generated.lengths_by_kind_m == pytest.approx(
        {"link (field_path)": 30.0, "street": 1200.0}, abs=1e-3
    )
    # The stream runs from (0, 760) to (920, 700), so it crosses Alpha's street at x = 450 at
    # y = 760 - 60 x 450 / 920 = 730.65. That is 30 m of link plus 1000 - 730.65 m of street.
    (crossing,) = item.crossings
    assert crossing.kind == "watercourse" and crossing.crossing_id == "stream-1-1"
    assert crossing.chainage_m == pytest.approx(
        30.0 + 1000.0 - (760.0 - 60.0 * 450 / 920), abs=1e-6
    )
    # The profile starts at Alpha's recorded height and ends at the connection node.
    profile = [(point.chainage_m, point.elevation_m) for point in item.elevation_profile]
    assert profile[0] == (0.0, 165.0)
    assert profile[-1][1] == 160.0
    assert item.vertical_datum == "DHHN2016"


def test_a_longer_route_records_the_customers_it_passes(scenario: Scenario) -> None:
    found = pair(scenario, "gamma", "conn-east")
    field, street = route(found, 1), route(found, 2)
    assert field.linked_customers == ()
    assert [(item.customer_id, item.customer_status) for item in street.linked_customers] == [
        ("committed-customer-1", "committed"),
        ("potential-customer-1", "potential"),
        ("potential-customer-2", "potential"),
    ]
    assert street.route.linked_customer_ids == (
        "committed-customer-1",
        "potential-customer-1",
        "potential-customer-2",
    )
    assert [item.design_heat_w for item in street.linked_customers] == [
        200_000.0,
        300_000.0,
        250_000.0,
    ]
    assert street.route.corridor_length_m > field.route.corridor_length_m


def test_alternatives_that_share_most_of_their_way_are_not_offered(scenario: Scenario) -> None:
    # Beta's only other way to the west follows the same streets for three quarters.
    found = pair(scenario, "beta", "conn-west")
    assert [item.bases for item in found.routes] == [("least_cost", "shortest_length")]
    looser = policy_variant(POLICY, max_shared_length_fraction=0.9)
    assert len(pair(scenario, "beta", "conn-west", looser).routes) == 2


@pytest.mark.requirement("A12")
def test_a_missing_rate_leaves_the_cost_unknown(scenario: Scenario) -> None:
    parameters = {
        name: value
        for name, value in POLICY.parameters.items()
        if name != "crossing_cost_railway_currency"
    }
    without = POLICY.model_copy(update={"version": "test", "parameters": parameters})
    found = pair(scenario, "alpha", "conn-east", without)
    (generated,) = found.routes
    assert generated.cost.total_currency is None
    assert generated.cost.crossings_currency is None
    assert generated.cost.trench_currency is not None
    assert generated.cost.unknown == ("no crossing cost for barrier kind railway",)


def test_pairs_without_a_route_say_why(scenario: Scenario) -> None:
    near = policy_variant(POLICY, max_site_link_m=10.0)
    found = pair(scenario, "alpha", "conn-west", near)
    assert found.status == "no_route" and "30 m from the pad" in found.reason

    without = scenario.model_copy(
        update={
            "connections": tuple(
                item.model_copy(update={"location": None}) for item in scenario.connections
            )
        }
    )
    found = pair(without, "alpha", "conn-west")
    assert found.status == "not_established"
    assert [item.field_path for item in found.missing_inputs] == ["connections[conn-west].location"]
