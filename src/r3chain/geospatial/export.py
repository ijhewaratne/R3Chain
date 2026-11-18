"""Map layers of a run (specification 8.4 and 18.1).

Builds GeoJSON in WGS84, as RFC 7946 requires, so both file exports and the browser map viewer
reuse one projection and feature assembly helper:

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

from r3chain.domain.records.common import value_or_none
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.records.spatial import LayerKind

WGS84 = "EPSG:4326"

_LAYER_KIND_KEY: dict[LayerKind, str] = {
    LayerKind.GEOLOGICAL_TARGET: "targets",
    LayerKind.RESTRICTION: "restrictions",
    LayerKind.JURISDICTION: "jurisdictions",
    LayerKind.CORRIDOR: "corridors",
    LayerKind.CROSSING_BARRIER: "barriers",
    LayerKind.DEMAND_POINT: "demand",
    LayerKind.NETWORK_PIPE: "network_pipes",
}


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


def _collection(name: str, features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "name": name, "features": features}


def _write(path: Path, name: str, collection: dict[str, Any]) -> Path:
    target = path / f"{name}.geojson"
    target.write_text(json.dumps(collection, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def build_map_layers(
    scenario: Scenario,
    outputs: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    allowed_dataset_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Assemble WGS84 GeoJSON collections, bounds, and accessible text summaries for a scenario."""
    stage_outputs = outputs or {}
    crs = scenario.study.study_area.crs

    study_area_features = [
        _feature(
            _to_wgs84(scenario.study.study_area.geojson, crs),
            {
                "study_id": scenario.study.study_id,
                "title": scenario.study.title,
                "crs": crs,
                "synthetic": scenario.study.synthetic,
            },
        )
    ]

    screenings = stage_outputs.get("screen", {}).get("sites", {})
    sites: list[dict[str, Any]] = []
    text_summary: list[dict[str, Any]] = []
    for site in scenario.sites:
        screening = screenings.get(site.site_id, {})
        reasons = [
            f"{item['criterion_id']} {item['outcome']}: {item['reason']}"
            for item in screening.get("criteria", [])
            if item["outcome"] != "pass"
        ]
        s_status = screening.get("status", "not screened")
        land_val = value_or_none(site.land_status)
        land_label = land_val.value if land_val is not None else site.land_status.state
        sites.append(
            _feature(
                _to_wgs84(site.parcel_geometry.geojson, site.parcel_geometry.crs),
                {
                    "site_id": site.site_id,
                    "name": site.name,
                    "part": "parcel",
                    "land_status": land_label,
                    "screening_status": s_status,
                    "reasons": "; ".join(reasons),
                    "jurisdictions": ", ".join(screening.get("jurisdictions", [])),
                },
            )
        )
        text_summary.append(
            {
                "kind": "site",
                "id": site.site_id,
                "label": site.name,
                "status": s_status,
                "details": "; ".join(reasons) or f"land={land_label}",
            }
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

    routes: list[dict[str, Any]] = []
    for pair in stage_outputs.get("routes", {}).get("pairs", []):
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

    connections = [
        _feature(
            _to_wgs84(connection.location.geojson, connection.location.crs),
            {
                "connection_id": connection.connection_id,
                "network_id": connection.network_id,
                "zone_id": connection.zone_id,
                "interface_type": connection.interface_type.value,
            },
        )
        for connection in scenario.connections
        if connection.location is not None
    ]
    for conn in scenario.connections:
        text_summary.append(
            {
                "kind": "connection",
                "id": conn.connection_id,
                "label": f"{conn.network_id} / {conn.zone_id}",
                "status": conn.interface_type.value,
                "details": conn.heat_carrier.value,
            }
        )

    route_lines = {
        key: value["geometry"]
        for key, value in stage_outputs.get("candidates", {}).get("routes", {}).items()
    }
    assessments = {
        item["candidate_id"]: item
        for item in stage_outputs.get("assess", {}).get("assessments", [])
    }
    candidates: list[dict[str, Any]] = []
    for candidate in stage_outputs.get("candidates", {}).get("candidates", []):
        geometry = route_lines.get(candidate["route_id"])
        if geometry is None:
            continue
        assessment = assessments.get(candidate["candidate_id"], {})
        eligible = (assessment.get("ranking_eligibility") or {}).get("eligible")
        tech_status = assessment.get("technical_status")
        eval_status = assessment.get("evaluation_status")
        limiting = ", ".join(assessment.get("limiting_conditions", []))
        candidates.append(
            _feature(
                _to_wgs84(geometry["geojson"], geometry["crs"]),
                {
                    "candidate_id": candidate["candidate_id"],
                    "label": candidate["label"],
                    "plant_id": candidate["plant_id"],
                    "route_id": candidate["route_id"],
                    "technical_status": tech_status,
                    "evaluation_status": eval_status,
                    "screening_status": candidate["screening_status"],
                    "eligible": eligible,
                    "rank": assessment.get("rank"),
                    "limiting_conditions": limiting,
                },
            )
        )
        text_summary.append(
            {
                "kind": "candidate",
                "id": candidate["candidate_id"],
                "label": candidate["label"],
                "status": (
                    "eligible"
                    if eligible
                    else (tech_status or eval_status or candidate["screening_status"])
                ),
                "details": limiting or f"route={candidate['route_id']}",
            }
        )

    spatial_buckets: dict[str, list[dict[str, Any]]] = {key: [] for key in _LAYER_KIND_KEY.values()}
    hidden_layer_count = 0
    for layer in scenario.spatial_layers:
        if allowed_dataset_ids is not None and layer.dataset_id not in allowed_dataset_ids:
            hidden_layer_count += 1
            continue
        bucket = _LAYER_KIND_KEY[layer.kind]
        for feat in layer.features:
            spatial_buckets[bucket].append(
                _feature(
                    _to_wgs84(feat.geometry, layer.crs),
                    {
                        "feature_id": feat.feature_id,
                        "layer_id": layer.layer_id,
                        "layer_kind": layer.kind.value,
                        "dataset_id": layer.dataset_id,
                        **feat.properties,
                    },
                )
            )
            if layer.kind in {
                LayerKind.GEOLOGICAL_TARGET,
                LayerKind.RESTRICTION,
                LayerKind.DEMAND_POINT,
            }:
                props = feat.properties
                desc = ", ".join(f"{k}={v}" for k, v in props.items())
                text_summary.append(
                    {
                        "kind": layer.kind.value,
                        "id": feat.feature_id,
                        "label": str(
                            props.get("target_id")
                            or props.get("restriction_kind")
                            or feat.feature_id
                        ),
                        "status": layer.dataset_id,
                        "details": desc,
                    }
                )

    collections = {
        "study_area": _collection("study_area", study_area_features),
        "sites": _collection("sites", sites),
        "routes": _collection("routes", routes),
        "connections": _collection("connections", connections),
        "candidates": _collection("candidates", candidates),
        **{name: _collection(name, feats) for name, feats in spatial_buckets.items()},
    }

    bounds_list = [
        shape(feat["geometry"]).bounds
        for col in collections.values()
        for feat in col["features"]
        if feat.get("geometry") is not None
    ]
    bbox = (
        [
            min(b[0] for b in bounds_list),
            min(b[1] for b in bounds_list),
            max(b[2] for b in bounds_list),
            max(b[3] for b in bounds_list),
        ]
        if bounds_list
        else None
    )

    return {
        "bbox": bbox,
        "layers": collections,
        "text_summary": text_summary,
        "hidden_spatial_layers_count": hidden_layer_count,
    }


def export_map_layers(
    scenario: Scenario, outputs: Mapping[str, Mapping[str, Any]], directory: Path
) -> list[Path]:
    """Write the map layers of a run. ``outputs`` maps stage names to their payloads."""
    directory.mkdir(parents=True, exist_ok=True)
    built = build_map_layers(scenario, outputs)
    layers = built["layers"]
    return [
        _write(directory, name, layers[name])
        for name in ("sites", "routes", "connections", "candidates")
    ]
