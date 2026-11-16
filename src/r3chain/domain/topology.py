"""Topology checks on a canonical network (specification 11.1).

These checks need only the network graph: disconnected parts, isolated nodes, consumers that are
not piped, duplicate pipes, dead ends, and supply pipes without a matching return pipe.

Two further checks of the specification need pipe geometry: snapping of nearby nodes, and pipes
that cross without being connected. They belong to the GIS import of milestone M3, where the
geometry is available.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from enum import StrEnum

from r3chain.domain.base import Record
from r3chain.domain.records.network import BoundaryKind, Circuit, Network


class TopologySeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class TopologyIssue(Record):
    code: str
    severity: TopologySeverity
    element_ids: tuple[str, ...]
    message: str


def _components(nodes: list[str], edges: Iterable[tuple[str, str]]) -> list[set[str]]:
    neighbours: dict[str, set[str]] = defaultdict(set)
    for first, second in edges:
        neighbours[first].add(second)
        neighbours[second].add(first)
    seen: set[str] = set()
    components: list[set[str]] = []
    for start in nodes:
        if start in seen:
            continue
        component = {start}
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for neighbour in neighbours[current]:
                if neighbour not in component:
                    component.add(neighbour)
                    frontier.append(neighbour)
        seen |= component
        components.append(component)
    return components


def check_topology(
    network: Network, node_pairs: Iterable[tuple[str, str]] = ()
) -> tuple[TopologyIssue, ...]:
    """Check the graph of a network.

    ``node_pairs`` lists supply and return nodes that belong together at a connection or supply
    point. They join the two circuits hydraulically, in the same way a consumer does.
    """
    pairs = [pair for pair in node_pairs if all(node in network.node_ids() for node in pair)]
    node_ids = [node.node_id for node in network.nodes]
    circuits = {node.node_id: node.circuit for node in network.nodes}
    issues: list[TopologyIssue] = []

    def add(code: str, severity: TopologySeverity, elements: Iterable[str], message: str) -> None:
        issues.append(
            TopologyIssue(
                code=code, severity=severity, element_ids=tuple(elements), message=message
            )
        )

    pipes_at: dict[str, list[str]] = defaultdict(list)
    for pipe in network.pipes:
        pipes_at[pipe.from_node].append(pipe.pipe_id)
        pipes_at[pipe.to_node].append(pipe.pipe_id)
    attached: set[str] = set(pipes_at)
    edges: list[tuple[str, str]] = [(pipe.from_node, pipe.to_node) for pipe in network.pipes]
    for consumer in network.consumers:
        edges.append((consumer.supply_node, consumer.return_node))
        attached |= {consumer.supply_node, consumer.return_node}
    for item in network.equipment:
        attached |= set(item.node_ids)
        edges.extend(zip(item.node_ids, item.node_ids[1:], strict=False))
    for supply, back in pairs:
        edges.append((supply, back))
        attached |= {supply, back}

    # Nodes with nothing attached.
    isolated = [node_id for node_id in node_ids if node_id not in attached]
    if isolated:
        add(
            "isolated_node",
            TopologySeverity.ERROR,
            isolated,
            f"nodes with no pipe, consumer or equipment attached: {', '.join(isolated)}",
        )

    # Parts that are not connected to the main network.
    components = [
        component for component in _components(node_ids, edges) if not component <= set(isolated)
    ]
    if len(components) > 1:
        anchors = {
            boundary.node_id
            for boundary in network.boundary_conditions
            if boundary.kind is BoundaryKind.PRESSURE_REFERENCE
        } | {node for pair in pairs for node in pair}
        main = next(
            (component for component in components if component & anchors),
            max(components, key=len),
        )
        for component in components:
            if component is main:
                continue
            members = sorted(component)
            add(
                "island",
                TopologySeverity.ERROR,
                members,
                f"{len(members)} nodes are not connected to the rest of the network: "
                f"{', '.join(members)}",
            )

    # Consumers that are not piped on both sides.
    for consumer in network.consumers:
        unpiped = [
            node for node in (consumer.supply_node, consumer.return_node) if not pipes_at.get(node)
        ]
        if unpiped:
            add(
                "consumer_not_connected",
                TopologySeverity.ERROR,
                (consumer.consumer_id,),
                f"consumer {consumer.consumer_id} has no pipe at {', '.join(unpiped)}",
            )

    # Two pipes between the same pair of nodes.
    between: dict[frozenset[str], list[str]] = defaultdict(list)
    for pipe in network.pipes:
        between[frozenset((pipe.from_node, pipe.to_node))].append(pipe.pipe_id)
    for pipe_ids in between.values():
        if len(pipe_ids) > 1:
            add(
                "duplicate_pipe",
                TopologySeverity.WARNING,
                pipe_ids,
                f"pipes {', '.join(pipe_ids)} join the same two nodes. Confirm that they are "
                "parallel pipes and not a duplicated record",
            )

    # Pipe ends where water cannot circulate.
    used = {
        node
        for consumer in network.consumers
        for node in (consumer.supply_node, consumer.return_node)
    }
    used |= {node for item in network.equipment for node in item.node_ids}
    used |= {node for pair in pairs for node in pair}
    for node_id in node_ids:
        if len(pipes_at.get(node_id, ())) == 1 and node_id not in used:
            add(
                "dead_end",
                TopologySeverity.WARNING,
                (node_id,),
                f"node {node_id} ends a pipe without a consumer, equipment or connection",
            )

    # Supply pipes without a matching return pipe, and the reverse.
    partner: dict[str, str] = {}
    for consumer in network.consumers:
        partner[consumer.supply_node] = consumer.return_node
        partner[consumer.return_node] = consumer.supply_node
    for supply, back in pairs:
        partner[supply] = back
        partner[back] = supply
    for pipe in network.pipes:
        if pipe.from_node not in partner or pipe.to_node not in partner:
            continue
        mirrored = frozenset((partner[pipe.from_node], partner[pipe.to_node]))
        if mirrored not in between:
            other = Circuit.RETURN if circuits[pipe.from_node] is Circuit.SUPPLY else Circuit.SUPPLY
            add(
                "unpaired_pipe",
                TopologySeverity.WARNING,
                (pipe.pipe_id,),
                f"pipe {pipe.pipe_id} has no matching {other.value} pipe between "
                f"{' and '.join(sorted(mirrored))}",
            )
    return tuple(issues)
