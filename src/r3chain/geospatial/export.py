"""Map layers of a run (specification 8.4).

Writes GeoJSON in WGS84, as RFC 7946 requires, so that any map viewer can show it:

sites.geojson
    Each parcel with its screening status and reasons, and each usable pad.
routes.geojson
    Each generated route with its rank, bases, lengths, crossings and indicative cost.
connections.geojson
    Each connection that has a location.
candidates.geojson
    Each candidate as the line of its route, with its statuses and rank.

Every feature carries the identifiers that lead back to the result records.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import mapping, shape

from r3chain.domain.records.scenario import Scenario

WGS84 = "EPSG:4326"


def _to_wgs84(geojson: Mapping[str, Any], crs: str) -> dict[str, Any]:
    transformer = Transformer.from_crs(crs, WGS84, always_xy=True)

    def project(points: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
        longitudes, latitudes = transformer.transform(points[:, 0], points[:, 1])
        return np.column_stack((longitudes, latitudes))

    projected = shapely.transform(shape(dict(geojson)), project)
    converted: dict[str, Any] = json.loads(json.dumps(mapping(projected)))
    return converted


def _feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "properties": properties, "geometry": geometry}


def _write(path: Path, name: str, features: list[dict[str, Any]]) -> Path:
    target = path / f"{name}.geojson"
    collection = {"type": "FeatureCollection", "name": name, "features": features}
    target.write_text(json.dumps(collection, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def export_map_layers(
    scenario: Scenario, outputs: Mapping[str, Mapping[str, Any]], directory: Path
) -> list[Path]:
    """Write the map layers of a run. ``outputs`` maps stage names to their payloads."""
    directory.mkdir(parents=True, exist_ok=True)
    crs = scenario.study.study_area.crs
    written: list[Path] = []

    screenings = outputs.get("screen", {}).get("sites", {})
    sites: list[dict[str, Any]] = []
    for site in scenario.sites:
        screening = screenings.get(site.site_id, {})
        reasons = [
            f"{item['criterion_id']} {item['outcome']}: {item['reason']}"
            for item in screening.get("criteria", [])
            if item["outcome"] != "pass"
        ]
        sites.append(
            _feature(
                _to_wgs84(site.parcel_geometry.geojson, site.parcel_geometry.crs),
                {
                    "site_id": site.site_id,
                    "part": "parcel",
                    "screening_status": screening.get("status", "not screened"),
                    "reasons": "; ".join(reasons),
                    "jurisdictions": ", ".join(screening.get("jurisdictions", [])),
                },
            )
        )
        if screening.get("usable_pad"):
            sites.append(
                _feature(
                    _to_wgs84(screening["usable_pad"], crs),
                    {
                        "site_id": site.site_id,
                        "part": "usable pad",
                        "usable_pad_area_m2": screening.get("usable_pad_area_m2"),
                    },
                )
            )
    written.append(_write(directory, "sites", sites))

    routes: list[dict[str, Any]] = []
    for pair in outputs.get("routes", {}).get("pairs", []):
        for generated in pair.get("routes", []):
            route = generated["route"]
            routes.append(
                _feature(
                    _to_wgs84(route["geometry"]["geojson"], route["geometry"]["crs"]),
                    {
                        "route_id": route["route_id"],
                        "site_id": route["from_site_id"],
                        "connection_id": route["to_connection_id"],
                        "rank": generated["rank"],
                        "bases": ", ".join(generated["bases"]),
                        "corridor_length_m": route["corridor_length_m"],
                        "supply_pipe_length_m": route["supply_pipe_length_m"],
                        "return_pipe_length_m": route["return_pipe_length_m"],
                        "crossings": ", ".join(item["kind"] for item in route["crossings"]),
                        "indicative_cost_currency": generated["cost"]["total_currency"],
                        "linked_customers": ", ".join(route["linked_customer_ids"]),
                    },
                )
            )
    written.append(_write(directory, "routes", routes))

    connections = [
        _feature(
            _to_wgs84(connection.location.geojson, connection.location.crs),
            {"connection_id": connection.connection_id, "network_id": connection.network_id},
        )
        for connection in scenario.connections
        if connection.location is not None
    ]
    written.append(_write(directory, "connections", connections))

    route_lines = {
        key: value["geometry"]
        for key, value in outputs.get("candidates", {}).get("routes", {}).items()
    }
    assessments = {
        item["candidate_id"]: item for item in outputs.get("assess", {}).get("assessments", [])
    }
    candidates: list[dict[str, Any]] = []
    for candidate in outputs.get("candidates", {}).get("candidates", []):
        geometry = route_lines.get(candidate["route_id"])
        if geometry is None:
            continue
        assessment = assessments.get(candidate["candidate_id"], {})
        candidates.append(
            _feature(
                _to_wgs84(geometry["geojson"], geometry["crs"]),
                {
                    "candidate_id": candidate["candidate_id"],
                    "label": candidate["label"],
                    "plant_id": candidate["plant_id"],
                    "technical_status": assessment.get("technical_status"),
                    "evaluation_status": assessment.get("evaluation_status"),
                    "screening_status": candidate["screening_status"],
                    "eligible": (assessment.get("ranking_eligibility") or {}).get("eligible"),
                    "rank": assessment.get("rank"),
                    "limiting_conditions": ", ".join(assessment.get("limiting_conditions", [])),
                },
            )
        )
    written.append(_write(directory, "candidates", candidates))
    return written
