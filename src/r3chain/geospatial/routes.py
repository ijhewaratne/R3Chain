"""Route generation between sites and connections (specification 8.3).

Routes follow the corridors of the corridor layers. Each corridor is an edge of a route graph
with its length, the trench rate of its kind and the crossings with barriers such as watercourses
and railways. The pad of a site and the location of a connection are linked to the nearest
corridor node.

For each pair of site and connection the generator produces a few distinct alternatives in order
of indicative cost, and adds the shortest route by length if it is not among them. The shortest
route is therefore always visible, and it is never assumed to be the cheapest.

For each route it records, separately:

* the trench length (the corridor length) and the supply and return pipe lengths,
* the crossings with their kind and chainage,
* the elevation profile,
* the customers along the route and whether their connection is committed or only potential,
* the indicative cost of trench, pipes and crossings from the unit rates of the route policy.

The indicative cost compares routes. It is not a cost estimate. A rate that the policy does not
give leaves the cost unknown and says which rate is missing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import islice, pairwise
from typing import Any, Literal

import networkx as nx
from shapely.geometry import LineString, Point, shape

from r3chain.domain import policies
from r3chain.domain.base import Identifier, NonNegativeReal, Real, Record
from r3chain.domain.evidence import Known, MissingInput
from r3chain.domain.records.common import Geometry
from r3chain.domain.records.network import Connection, Crossing, Network, ProfilePoint, Route
from r3chain.domain.records.runs import Policy
from r3chain.domain.records.site import Site
from r3chain.domain.records.spatial import LayerKind, SpatialLayer
from r3chain.domain.topology import TopologyIssue
from r3chain.geospatial.graph import LineGraph, build_line_graph

SITE = "site"
CONNECTION = "connection"


class RouteCost(Record):
    """Indicative cost of a route, by term, in the currency of the route policy."""

    trench_currency: Real | None = None
    pipes_currency: Real | None = None
    crossings_currency: Real | None = None
    total_currency: Real | None = None
    # What makes a term unknown, for example a corridor kind without a trench rate.
    unknown: tuple[str, ...] = ()


class LinkedCustomer(Record):
    customer_id: Identifier
    customer_status: str | None = None
    design_heat_w: Real | None = None
    distance_m: NonNegativeReal


class GeneratedRoute(Record):
    route: Route
    # 1 is the lowest indicative cost of the pair. Routes of unknown cost come last.
    rank: int
    bases: tuple[Literal["least_cost", "shortest_length"], ...]
    cost: RouteCost
    lengths_by_kind_m: dict[str, Real]
    site_link_m: NonNegativeReal
    connection_link_m: NonNegativeReal
    corridor_ids: tuple[str, ...]
    linked_customers: tuple[LinkedCustomer, ...] = ()


class RoutePair(Record):
    site_id: Identifier
    connection_id: Identifier
    status: Literal["routed", "no_route", "not_established"]
    reason: str = ""
    routes: tuple[GeneratedRoute, ...] = ()
    missing_inputs: tuple[MissingInput, ...] = ()


@dataclass(frozen=True)
class RouteSettings:
    snap_tolerance_m: float
    near_miss_distance_m: float
    max_site_link_m: float
    max_connection_link_m: float
    alternatives_count: int
    max_shared_length_fraction: float
    simple_paths_examined_count: int
    pipe_cost_currency_per_m: float
    pipe_length_allowance_fraction: float
    service_connection_max_distance_m: float
    link_corridor_kind: str
    trench_rates: dict[str, float]
    crossing_costs: dict[str, float]
    origin: str

    @classmethod
    def from_policy(cls, policy: Policy) -> RouteSettings:
        return cls(
            snap_tolerance_m=policies.number(policy, "snap_tolerance_m"),
            near_miss_distance_m=policies.number(policy, "near_miss_distance_m"),
            max_site_link_m=policies.number(policy, "max_site_link_m"),
            max_connection_link_m=policies.number(policy, "max_connection_link_m"),
            alternatives_count=policies.count(policy, "route_alternatives_count"),
            max_shared_length_fraction=policies.number(policy, "max_shared_length_fraction"),
            simple_paths_examined_count=policies.count(policy, "simple_paths_examined_count"),
            pipe_cost_currency_per_m=policies.number(policy, "pipe_cost_currency_per_m"),
            pipe_length_allowance_fraction=policies.number(
                policy, "pipe_length_allowance_fraction"
            ),
            service_connection_max_distance_m=policies.number(
                policy, "service_connection_max_distance_m"
            ),
            link_corridor_kind=policies.text(policy, "link_corridor_kind"),
            trench_rates=policies.numbers_by_key(policy, "trench_cost_", "_currency_per_m"),
            crossing_costs=policies.numbers_by_key(policy, "crossing_cost_", "_currency"),
            origin=policies.origin(policy),
        )


def safe_identifier(value: str) -> str:
    """An identifier made from a raw name: characters outside the allowed set become dashes."""
    cleaned = re.sub(r"[^A-Za-z0-9_\-]", "-", value).strip("-")
    return cleaned or "x"


@dataclass(frozen=True)
class _Barrier:
    feature_id: str
    kind: str | None
    geometry: Any


@dataclass
class _EdgeData:
    edge_id: str
    feature_id: str
    kind: str | None
    geometry: LineString
    length_m: float
    crossings: list[tuple[_Barrier, Point]] = field(default_factory=list)


@dataclass(frozen=True)
class RouteNetwork:
    """The route graph of a scenario, built once and used for every pair."""

    graph: Any
    lines: LineGraph
    barriers: tuple[_Barrier, ...]
    customers: tuple[tuple[str, Point, dict[str, Any]], ...]
    vertical_datum: str | None
    issues: tuple[TopologyIssue, ...]


def build_route_network(layers: Sequence[SpatialLayer], settings: RouteSettings) -> RouteNetwork:
    corridors = [layer for layer in layers if layer.kind is LayerKind.CORRIDOR]
    lines = build_line_graph(
        (
            (
                f"{layer.layer_id}:{feature.feature_id}",
                shape(feature.geometry),
                dict(feature.properties),
            )
            for layer in corridors
            for feature in layer.features
        ),
        snap_tolerance_m=settings.snap_tolerance_m,
        near_miss_m=settings.near_miss_distance_m,
    )
    barriers = tuple(
        _Barrier(
            feature_id=feature.feature_id,
            kind=(
                str(feature.properties["barrier_kind"])
                if "barrier_kind" in feature.properties
                else None
            ),
            geometry=shape(feature.geometry),
        )
        for layer in layers
        if layer.kind is LayerKind.CROSSING_BARRIER
        for feature in layer.features
    )
    customers = tuple(
        (
            feature.feature_id,
            Point(shape(feature.geometry).representative_point()),
            dict(feature.properties),
        )
        for layer in layers
        if layer.kind is LayerKind.DEMAND_POINT
        for feature in layer.features
    )
    graph: Any = nx.Graph()
    for node in lines.nodes.values():
        graph.add_node(node.node_id, x=node.x, y=node.y, z=node.z)
    for edge in lines.edges:
        data = _EdgeData(
            edge_id=edge.edge_id,
            feature_id=edge.feature_id,
            kind=(
                str(edge.properties["corridor_kind"])
                if "corridor_kind" in edge.properties
                else None
            ),
            geometry=edge.geometry,
            length_m=edge.length_m,
        )
        for barrier in barriers:
            crossing = edge.geometry.intersection(barrier.geometry)
            for spot in getattr(crossing, "geoms", [crossing]):
                if not spot.is_empty and spot.geom_type == "Point":
                    data.crossings.append((barrier, spot))
        cost, _ = _edge_cost(data, settings)
        existing = graph.get_edge_data(edge.start, edge.end)
        if existing is not None and existing["cost"] <= cost:
            continue
        graph.add_edge(edge.start, edge.end, data=data, cost=cost, length=data.length_m)
    datum = next((layer.vertical_datum for layer in corridors if layer.vertical_datum), None)
    return RouteNetwork(
        graph=graph,
        lines=lines,
        barriers=barriers,
        customers=customers,
        vertical_datum=datum,
        issues=lines.issues,
    )


def _pipe_rate(settings: RouteSettings) -> float:
    """Cost per metre of corridor for the supply and the return pipe together."""
    return 2.0 * (1.0 + settings.pipe_length_allowance_fraction) * settings.pipe_cost_currency_per_m


def _edge_cost(data: _EdgeData, settings: RouteSettings) -> tuple[float, bool]:
    """Indicative cost of one corridor, and whether every term of it is known.

    A corridor with an unknown term is still searchable. It is weighted with the highest known
    trench rate, so that it is neither preferred nor hidden, and the route that uses it reports
    its cost as unknown.
    """
    known = True
    rate = settings.trench_rates.get(data.kind or "")
    if rate is None:
        known = False
        rate = max(settings.trench_rates.values(), default=0.0)
    total = data.length_m * (rate + _pipe_rate(settings))
    for barrier, _ in data.crossings:
        cost = settings.crossing_costs.get(barrier.kind or "")
        if cost is None:
            known = False
            cost = max(settings.crossing_costs.values(), default=0.0)
        total += cost
    return total, known


def _nearest_node(graph: Any, point: Point) -> tuple[str | None, float]:
    best: str | None = None
    distance = math.inf
    for node, data in graph.nodes(data=True):
        if node in (SITE, CONNECTION):
            continue
        gap = math.hypot(data["x"] - point.x, data["y"] - point.y)
        if gap < distance or (gap == distance and best is not None and node < best):
            best, distance = node, gap
    return best, distance


def _oriented(data: _EdgeData, start: tuple[float, float]) -> list[tuple[float, ...]]:
    coordinates = [tuple(float(value) for value in point) for point in data.geometry.coords]
    first = coordinates[0]
    if math.hypot(first[0] - start[0], first[1] - start[1]) > math.hypot(
        coordinates[-1][0] - start[0], coordinates[-1][1] - start[1]
    ):
        coordinates.reverse()
    return coordinates


def _shared_length(first: list[_EdgeData], second: list[_EdgeData]) -> float:
    names = {item.edge_id for item in second}
    return sum(item.length_m for item in first if item.edge_id in names)


@dataclass(frozen=True)
class _Path:
    nodes: tuple[str, ...]
    edges: tuple[_EdgeData, ...]
    cost: float
    length_m: float


def _paths(graph: Any, nodes: Sequence[str]) -> _Path:
    edges = tuple(
        graph.edges[first, second]["data"]
        for first, second in pairwise(nodes)
        if graph.edges[first, second].get("data") is not None
    )
    cost = sum(graph.edges[first, second]["cost"] for first, second in pairwise(nodes))
    length = sum(graph.edges[first, second]["length"] for first, second in pairwise(nodes))
    return _Path(tuple(nodes), edges, cost, length)


def _candidate_paths(graph: Any, settings: RouteSettings) -> Iterator[_Path]:
    generator = nx.shortest_simple_paths(graph, SITE, CONNECTION, weight="cost")
    for nodes in islice(generator, settings.simple_paths_examined_count):
        yield _paths(graph, nodes)


def _elevation(
    site: Site, connection: Connection, network: Network
) -> tuple[float | None, float | None]:
    site_height = site.elevation.value.value_m if isinstance(site.elevation, Known) else None
    heights = {node.node_id: node.elevation_m for node in network.nodes}
    return site_height, heights.get(connection.supply_node)


def generate_routes(
    site: Site,
    pad_point: tuple[float, float],
    connection: Connection,
    network: Network,
    routes: RouteNetwork,
    settings: RouteSettings,
    crs: str,
) -> RoutePair:
    """Generate the route alternatives between one site and one connection."""
    pair = {"site_id": site.site_id, "connection_id": connection.connection_id}
    if connection.location is None:
        return RoutePair(
            **pair,
            status="not_established",
            reason="the connection has no location",
            missing_inputs=(
                MissingInput(
                    field_path=f"connections[{connection.connection_id}].location",
                    state="unknown",
                    reason="a route cannot end at a connection without a location",
                ),
            ),
        )
    graph = routes.graph.copy()
    start = Point(pad_point)
    end = Point(shape(connection.location.geojson).representative_point())
    first, site_gap = _nearest_node(graph, start)
    last, connection_gap = _nearest_node(graph, end)
    if first is None or last is None:
        return RoutePair(**pair, status="no_route", reason="the scenario has no corridors")
    if site_gap > settings.max_site_link_m:
        return RoutePair(
            **pair,
            status="no_route",
            reason=(
                f"the nearest corridor node is {site_gap:.0f} m from the pad, more than the "
                f"{settings.max_site_link_m:g} m a site link may span"
            ),
        )
    if connection_gap > settings.max_connection_link_m:
        return RoutePair(
            **pair,
            status="no_route",
            reason=(
                f"the nearest corridor node is {connection_gap:.0f} m from the connection, more "
                f"than the {settings.max_connection_link_m:g} m a link may span"
            ),
        )
    link_rate = settings.trench_rates.get(settings.link_corridor_kind)
    link_weight = (link_rate or 0.0) + _pipe_rate(settings)
    graph.add_edge(SITE, first, cost=site_gap * link_weight, length=site_gap)
    graph.add_edge(last, CONNECTION, cost=connection_gap * link_weight, length=connection_gap)
    if not nx.has_path(graph, SITE, CONNECTION):
        return RoutePair(
            **pair,
            status="no_route",
            reason=f"no corridor path joins site {site.site_id} and connection "
            f"{connection.connection_id}",
        )

    accepted: list[_Path] = []
    for path in _candidate_paths(graph, settings):
        if len(accepted) >= settings.alternatives_count:
            break
        distinct = all(
            _shared_length(list(path.edges), list(other.edges))
            <= settings.max_shared_length_fraction * min(path.length_m, other.length_m)
            for other in accepted
        )
        if distinct:
            accepted.append(path)
    shortest = _paths(graph, nx.shortest_path(graph, SITE, CONNECTION, weight="length"))
    bases: dict[tuple[str, ...], list[Literal["least_cost", "shortest_length"]]] = {
        path.nodes: ["least_cost"] for path in accepted
    }
    if shortest.nodes in bases:
        bases[shortest.nodes].append("shortest_length")
    else:
        accepted.append(shortest)
        bases[shortest.nodes] = ["shortest_length"]

    site_height, connection_height = _elevation(site, connection, network)
    built = [
        _assemble(
            path,
            graph,
            site=site,
            connection=connection,
            start=start,
            end=end,
            site_gap=site_gap,
            connection_gap=connection_gap,
            site_height=site_height,
            connection_height=connection_height,
            routes=routes,
            settings=settings,
            crs=crs,
        )
        for path in accepted
    ]
    order = sorted(
        range(len(built)),
        key=lambda index: (
            built[index][1].total_currency is None,
            built[index][1].total_currency or 0.0,
            accepted[index].length_m,
        ),
    )
    generated = []
    for rank, index in enumerate(order, start=1):
        route, cost, kinds, corridor_ids, customers = built[index]
        route_id = f"{site.site_id}-{connection.connection_id}-{rank}"
        generated.append(
            GeneratedRoute(
                route=route.model_copy(update={"route_id": route_id}),
                rank=rank,
                bases=tuple(bases[accepted[index].nodes]),
                cost=cost,
                lengths_by_kind_m=kinds,
                site_link_m=site_gap,
                connection_link_m=connection_gap,
                corridor_ids=corridor_ids,
                linked_customers=customers,
            )
        )
    return RoutePair(**pair, status="routed", routes=tuple(generated))


def _assemble(
    path: _Path,
    graph: Any,
    *,
    site: Site,
    connection: Connection,
    start: Point,
    end: Point,
    site_gap: float,
    connection_gap: float,
    site_height: float | None,
    connection_height: float | None,
    routes: RouteNetwork,
    settings: RouteSettings,
    crs: str,
) -> tuple[Route, RouteCost, dict[str, float], tuple[str, ...], tuple[LinkedCustomer, ...]]:
    # The line from the pad to the connection, with heights where the corridors carry them.
    coordinates: list[tuple[float, ...]] = [
        (start.x, start.y) if site_height is None else (start.x, start.y, site_height)
    ]
    for first, second in zip(path.nodes, path.nodes[1:], strict=False):
        data = graph.edges[first, second].get("data")
        if data is None:
            node = second if second not in (SITE, CONNECTION) else first
            attributes = graph.nodes[node]
            node_point: tuple[float, ...] = (attributes["x"], attributes["y"])
            if attributes.get("z") is not None:
                node_point = (*node_point, attributes["z"])
            coordinates.append(node_point)
            continue
        oriented = _oriented(data, (coordinates[-1][0], coordinates[-1][1]))
        coordinates.extend(oriented[1:] if _same(coordinates[-1], oriented[0]) else oriented)
    coordinates.append(
        (end.x, end.y) if connection_height is None else (end.x, end.y, connection_height)
    )
    cleaned: list[tuple[float, ...]] = []
    for point in coordinates:
        if not cleaned or not _same(cleaned[-1], point):
            cleaned.append(point)
    line = LineString([(point[0], point[1]) for point in cleaned])
    length = float(line.length)

    profile: list[ProfilePoint] = []
    chainage = 0.0
    for index, point in enumerate(cleaned):
        if index:
            previous = cleaned[index - 1]
            chainage += math.hypot(point[0] - previous[0], point[1] - previous[1])
        if len(point) > 2:
            profile.append(ProfilePoint(chainage_m=min(chainage, length), elevation_m=point[2]))

    kinds: dict[str, float] = {}
    unknown: list[str] = []
    trench = 0.0
    link_kind = settings.link_corridor_kind
    link_length = site_gap + connection_gap
    if link_length > 0.0:
        kinds[f"link ({link_kind})"] = link_length
        link_rate = settings.trench_rates.get(link_kind)
        if link_rate is None:
            unknown.append(f"no trench rate for the link kind {link_kind}")
        else:
            trench += link_length * link_rate
    crossings: list[Crossing] = []
    crossing_cost = 0.0
    counts: dict[str, int] = {}
    for data in path.edges:
        label = data.kind or "unknown kind"
        kinds[label] = kinds.get(label, 0.0) + data.length_m
        rate = settings.trench_rates.get(data.kind or "")
        if rate is None:
            unknown.append(f"no trench rate for corridor kind {label} ({data.feature_id})")
        else:
            trench += data.length_m * rate
        for barrier, spot in data.crossings:
            name = safe_identifier(barrier.feature_id)
            counts[name] = counts.get(name, 0) + 1
            crossings.append(
                Crossing(
                    crossing_id=f"{name}-{counts[name]}",
                    kind=barrier.kind or "unknown",
                    chainage_m=min(float(line.project(spot)), length),
                )
            )
            cost = settings.crossing_costs.get(barrier.kind or "")
            if cost is None:
                unknown.append(f"no crossing cost for barrier kind {barrier.kind or 'unknown'}")
            else:
                crossing_cost += cost
    pipes = length * _pipe_rate(settings)
    known_trench = None if any("trench rate" in item for item in unknown) else trench
    known_crossings = None if any("crossing cost" in item for item in unknown) else crossing_cost
    total = (
        known_trench + pipes + known_crossings
        if known_trench is not None and known_crossings is not None
        else None
    )
    customers = tuple(
        sorted(
            (
                LinkedCustomer(
                    customer_id=safe_identifier(customer_id),
                    customer_status=(
                        str(properties["customer_status"])
                        if "customer_status" in properties
                        else None
                    ),
                    design_heat_w=(
                        float(properties["design_heat_w"])
                        if isinstance(properties.get("design_heat_w"), int | float)
                        else None
                    ),
                    distance_m=float(line.distance(point)),
                )
                for customer_id, point, properties in routes.customers
                if line.distance(point) <= settings.service_connection_max_distance_m
            ),
            key=lambda item: item.customer_id,
        )
    )
    piped = length * (1.0 + settings.pipe_length_allowance_fraction)
    route = Route(
        route_id="pending",
        from_site_id=site.site_id,
        to_connection_id=connection.connection_id,
        geometry=Geometry(
            crs=crs,
            geojson={"type": "LineString", "coordinates": [list(point) for point in line.coords]},
        ),
        corridor_length_m=length,
        supply_pipe_length_m=piped,
        return_pipe_length_m=piped,
        crossings=tuple(sorted(crossings, key=lambda item: item.chainage_m)),
        elevation_profile=tuple(profile),
        vertical_datum=routes.vertical_datum or "not stated",
        linked_customer_ids=tuple(item.customer_id for item in customers),
    )
    route_cost = RouteCost(
        trench_currency=known_trench,
        pipes_currency=pipes,
        crossings_currency=known_crossings,
        total_currency=total,
        unknown=tuple(dict.fromkeys(unknown)),
    )
    corridor_ids = tuple(data.feature_id for data in path.edges)
    return route, route_cost, kinds, corridor_ids, customers


def _same(first: tuple[float, ...], second: tuple[float, ...]) -> bool:
    return math.hypot(first[0] - second[0], first[1] - second[1]) < 1e-6
