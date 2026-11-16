"""Spatial layers: imported GIS data in canonical form (specification 8.1).

A layer is one imported dataset of one kind, in the coordinate reference system of the study,
with canonical property names that carry their units. It records which geometry defects were
repaired at import and which features were rejected, so that nothing is dropped silently.

Each kind of layer is an entry of ``LAYER_SPECS``: the geometry it holds and the properties it
carries. A new kind of spatial input is one entry (extension point X1).

A property that a feature lacks is unknown. A calculation that needs it treats it as unknown and
never as zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import Identifier, NonEmptyStr, Real, Record, VersionedRecord
from r3chain.domain.records.common import require_unique
from r3chain.domain.units import unit_for_field


class LayerKind(StrEnum):
    GEOLOGICAL_TARGET = "geological_target"
    RESTRICTION = "restriction"
    JURISDICTION = "jurisdiction"
    CORRIDOR = "corridor"
    CROSSING_BARRIER = "crossing_barrier"
    DEMAND_POINT = "demand_point"
    NETWORK_PIPE = "network_pipe"


GeometryFamily = Literal["polygon", "line", "point"]

GEOMETRY_TYPES: dict[GeometryFamily, frozenset[str]] = {
    "polygon": frozenset({"Polygon", "MultiPolygon"}),
    "line": frozenset({"LineString", "MultiLineString"}),
    "point": frozenset({"Point", "MultiPoint"}),
}


@dataclass(frozen=True)
class PropertySpec:
    name: str
    kind: Literal["number", "text"]
    # A required property must be mapped at import. Single features may still lack a value.
    required: bool
    description: str


@dataclass(frozen=True)
class LayerSpec:
    kind: LayerKind
    description: str
    geometry: GeometryFamily
    properties: tuple[PropertySpec, ...]

    def property(self, name: str) -> PropertySpec | None:
        return next((item for item in self.properties if item.name == name), None)


LAYER_SPECS: dict[LayerKind, LayerSpec] = {
    spec.kind: spec
    for spec in (
        LayerSpec(
            LayerKind.GEOLOGICAL_TARGET,
            "Areas of a geological target that wells may reach",
            "polygon",
            (
                PropertySpec("target_id", "text", True, "identifier used by well scenarios"),
                PropertySpec("horizon", "text", False, "name of the geological horizon"),
            ),
        ),
        LayerSpec(
            LayerKind.RESTRICTION,
            "Areas that limit where a drilling pad may lie, such as housing or protection zones",
            "polygon",
            (
                PropertySpec(
                    "restriction_kind",
                    "text",
                    True,
                    "kind of restriction. Its setback comes from the screening policy",
                ),
            ),
        ),
        LayerSpec(
            LayerKind.JURISDICTION,
            "Municipal or other administrative areas. Recorded as an attribute only",
            "polygon",
            (PropertySpec("jurisdiction_name", "text", True, "name of the jurisdiction"),),
        ),
        LayerSpec(
            LayerKind.CORRIDOR,
            "Corridors where pipes may be laid. Lines must be split at every junction",
            "line",
            (
                PropertySpec("corridor_kind", "text", True, "kind of corridor, for example street"),
                PropertySpec("width_m", "number", False, "usable width of the corridor"),
            ),
        ),
        LayerSpec(
            LayerKind.CROSSING_BARRIER,
            "Lines that a route crosses at extra cost, such as watercourses and railways",
            "line",
            (PropertySpec("barrier_kind", "text", True, "kind of barrier"),),
        ),
        LayerSpec(
            LayerKind.DEMAND_POINT,
            "Buildings that might connect along a route",
            "point",
            (
                PropertySpec("design_heat_w", "number", True, "design heat demand"),
                PropertySpec("customer_status", "text", True, "committed or potential connection"),
            ),
        ),
        LayerSpec(
            LayerKind.NETWORK_PIPE,
            "Pipes of an existing network, for geometric topology checks",
            "line",
            (
                PropertySpec("circuit", "text", True, "supply or return"),
                PropertySpec("inner_diameter_m", "number", False, "inner diameter"),
            ),
        ),
    )
}


class Feature(Record):
    """One feature. Its geometry is GeoJSON in the coordinate reference system of the layer."""

    feature_id: NonEmptyStr
    geometry: dict[str, Any]
    properties: dict[str, Real | str] = Field(default_factory=dict)


class GeometryRepair(Record):
    feature_id: NonEmptyStr
    defect: NonEmptyStr
    # Change of area (polygons) or length (lines) caused by the repair, relative to before.
    size_change_fraction: Real


class RejectedFeature(Record):
    feature_id: NonEmptyStr
    reason: NonEmptyStr
    # Bounding box in the layer's reference system, when the geometry had coordinates.
    bounds: tuple[Real, Real, Real, Real] | None = Field(
        default=None, json_schema_extra={"unit": "unit of the layer reference system"}
    )


class SpatialLayer(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "spatial_layer"
    schema_version: Literal["0.1.0"] = "0.1.0"

    layer_id: Identifier
    kind: LayerKind
    dataset_id: Identifier
    mapping_id: Identifier
    mapping_hash: str
    source_crs: NonEmptyStr
    crs: str = Field(pattern=r"^[A-Za-z]+:\d+$")
    # Needed when geometries carry heights.
    vertical_datum: NonEmptyStr | None = None
    features: tuple[Feature, ...]
    repairs: tuple[GeometryRepair, ...] = ()
    rejected: tuple[RejectedFeature, ...] = ()
    import_warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _fits_its_kind(self) -> SpatialLayer:
        require_unique((feature.feature_id for feature in self.features), "feature_id")
        spec = LAYER_SPECS[self.kind]
        allowed = GEOMETRY_TYPES[spec.geometry]
        for feature in self.features:
            geometry_type = feature.geometry.get("type")
            if geometry_type not in allowed:
                raise ValueError(
                    f"feature {feature.feature_id}: a {self.kind.value} layer holds "
                    f"{spec.geometry} geometries, not {geometry_type}"
                )
            for name, value in feature.properties.items():
                declared = spec.property(name)
                if declared is None:
                    raise ValueError(
                        f"feature {feature.feature_id}: {name!r} is not a property of a "
                        f"{self.kind.value} layer"
                    )
                if declared.kind == "number" and isinstance(value, str):
                    raise ValueError(f"feature {feature.feature_id}: {name} must be a number")
                if declared.kind == "text" and not isinstance(value, str):
                    raise ValueError(f"feature {feature.feature_id}: {name} must be text")
        return self

    def has_heights(self) -> bool:
        return any(_has_z(feature.geometry.get("coordinates")) for feature in self.features)


def _has_z(coordinates: Any) -> bool:
    if isinstance(coordinates, list | tuple) and coordinates:
        if isinstance(coordinates[0], int | float):
            return len(coordinates) > 2
        return _has_z(coordinates[0])
    return False


def number_properties_carry_units() -> list[str]:
    """Names of numeric properties without a unit suffix. Empty when the registry is sound."""
    return [
        item.name
        for spec in LAYER_SPECS.values()
        for item in spec.properties
        if item.kind == "number" and unit_for_field(item.name) is None
    ]
