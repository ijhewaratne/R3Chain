"""A graph from line features, with the geometric topology checks (specification 8.3 and 11.1).

Lines are expected to be split at every junction, as a network or corridor dataset is drawn.
Their end points become the nodes of the graph:

* End points closer than the snap tolerance are one node. Each snap is reported.
* End points that are further apart than the snap tolerance but closer than the near miss
  distance are probably meant to meet. They stay apart and are reported for review.
* An end point near the middle of another line probably marks a junction that was not split.
  It is reported for review.
* Two lines that cross away from a shared node are not connected, as at a bridge. Each such
  crossing is reported, so that nobody mistakes it for a junction.

Nothing is changed silently. The graph uses only the snaps.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import shapely
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

from r3chain.domain.topology import TopologyIssue, TopologySeverity


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    x: float
    y: float
    z: float | None


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    feature_id: str
    start: str
    end: str
    geometry: LineString
    length_m: float
    properties: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LineGraph:
    nodes: dict[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    issues: tuple[TopologyIssue, ...]


def _parts(feature_id: str, geometry: Any) -> list[tuple[str, LineString]]:
    if geometry.geom_type == "LineString":
        return [(feature_id, geometry)]
    return [
        (f"{feature_id}.{index + 1}", part)
        for index, part in enumerate(shapely.get_parts(geometry))
    ]


def _warning(code: str, ids: Iterable[str], message: str) -> TopologyIssue:
    return TopologyIssue(
        code=code, severity=TopologySeverity.WARNING, element_ids=tuple(ids), message=message
    )


def build_line_graph(
    lines: Iterable[tuple[str, Any, Mapping[str, Any]]],
    *,
    snap_tolerance_m: float,
    near_miss_m: float,
) -> LineGraph:
    """Build a graph from (feature id, line geometry, properties) and check its geometry."""
    pieces: list[tuple[str, str, LineString, Mapping[str, Any]]] = []
    for feature_id, geometry, properties in lines:
        for edge_id, part in _parts(feature_id, geometry):
            if part.length > 0.0:
                pieces.append((edge_id, feature_id, part, properties))
    pieces.sort(key=lambda item: item[0])

    # End points, two per line, and the union of those within the snap tolerance.
    ends: list[tuple[float, float, float | None]] = []
    for _, _, line, _ in pieces:
        for coordinate in (line.coords[0], line.coords[-1]):
            ends.append(
                (
                    float(coordinate[0]),
                    float(coordinate[1]),
                    float(coordinate[2]) if len(coordinate) > 2 else None,
                )
            )
    points = [Point(x, y) for x, y, _ in ends]
    parent = list(range(len(points)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    tree = STRtree(points)
    issues: list[TopologyIssue] = []
    if points:
        first, second = tree.query(points, predicate="dwithin", distance=snap_tolerance_m)
        for a, b in zip(first.tolist(), second.tolist(), strict=True):
            if a < b:
                ra, rb = root(a), root(b)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)

    clusters: dict[int, list[int]] = {}
    for index in range(len(points)):
        clusters.setdefault(root(index), []).append(index)
    order = sorted(clusters, key=lambda key: (ends[key][0], ends[key][1]))
    node_of: dict[int, str] = {}
    nodes: dict[str, GraphNode] = {}
    for number, key in enumerate(order, start=1):
        node_id = f"n{number}"
        x, y, z = ends[key]
        nodes[node_id] = GraphNode(node_id, x, y, z)
        members = clusters[key]
        for member in members:
            node_of[member] = node_id
        spread = max(points[key].distance(points[member]) for member in members)
        if spread > 0.0:
            names = sorted({pieces[member // 2][0] for member in members})
            issues.append(
                _warning(
                    "snapped_end_points",
                    names,
                    f"end points of {', '.join(names)} lie up to {spread:.3f} m apart and were "
                    f"joined at node {node_id}",
                )
            )

    edges = tuple(
        GraphEdge(
            edge_id=edge_id,
            feature_id=feature_id,
            start=node_of[2 * index],
            end=node_of[2 * index + 1],
            geometry=line,
            length_m=float(line.length),
            properties=properties,
        )
        for index, (edge_id, feature_id, line, properties) in enumerate(pieces)
    )

    if points:
        # End points that almost meet.
        first, second = tree.query(points, predicate="dwithin", distance=near_miss_m)
        reported: set[tuple[str, str]] = set()
        for a, b in zip(first.tolist(), second.tolist(), strict=True):
            pair = tuple(sorted((node_of[a], node_of[b])))
            if node_of[a] == node_of[b] or pair in reported:
                continue
            reported.add(pair)  # type: ignore[arg-type]
            distance = points[a].distance(points[b])
            issues.append(
                _warning(
                    "near_miss",
                    pair,
                    f"nodes {pair[0]} and {pair[1]} are {distance:.2f} m apart, more than the snap "
                    f"tolerance of {snap_tolerance_m:g} m. They are not joined. Check whether "
                    "they should meet",
                )
            )

        # End points near the middle of another line.
        geometries = [edge.geometry for edge in edges]
        line_tree = STRtree(geometries)
        first, second = line_tree.query(points, predicate="dwithin", distance=near_miss_m)
        for a, b in zip(first.tolist(), second.tolist(), strict=True):
            edge = edges[b]
            if node_of[a] in (edge.start, edge.end):
                continue
            distance = points[a].distance(edge.geometry)
            issues.append(
                _warning(
                    "end_point_near_line",
                    (node_of[a], edge.edge_id),
                    f"node {node_of[a]} lies {distance:.2f} m from line {edge.edge_id} but not at "
                    "its end. Split the line if this is a junction",
                )
            )

        # Lines that cross away from a shared node.
        first, second = line_tree.query(geometries, predicate="intersects")
        for a, b in zip(first.tolist(), second.tolist(), strict=True):
            if a >= b:
                continue
            shared = {edges[a].start, edges[a].end} & {edges[b].start, edges[b].end}
            touching = [Point(nodes[node].x, nodes[node].y) for node in shared]
            crossing = shapely.intersection(edges[a].geometry, edges[b].geometry)
            # End points on the other line are reported as end points near a line.
            line_ends = [
                Point(edge.geometry.coords[index])
                for edge in (edges[a], edges[b])
                for index in (0, -1)
            ]
            for spot in shapely.get_parts(crossing):
                if any(spot.distance(node) <= snap_tolerance_m for node in touching):
                    continue
                if any(spot.distance(end) <= near_miss_m for end in line_ends):
                    continue
                issues.append(
                    _warning(
                        "crossing_without_junction",
                        (edges[a].edge_id, edges[b].edge_id),
                        f"lines {edges[a].edge_id} and {edges[b].edge_id} cross without a "
                        "junction. They are not connected",
                    )
                )
                break
    return LineGraph(nodes=nodes, edges=edges, issues=tuple(issues))
