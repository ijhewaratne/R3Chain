"""Import GIS files into canonical spatial layers (specification 8.1 and 8.2).

A mapping file states how a raw file becomes a canonical layer: which column is the identifier,
which raw columns become which canonical properties, in which unit, and how raw text values are
translated. The raw file itself is never changed.

What the import does, in this order:

1. Read the file: GeoPackage, GeoParquet or GeoJSON.
2. Reproject to the reference system of the study.
3. Repair only well understood geometry defects, and log every repair. A repair that changes the
   area or length of a feature by more than the mapping allows is ambiguous: the feature is
   rejected for review instead.
4. Round coordinates to the precision the mapping states, so that the same file always gives the
   same layer.
5. Translate the properties. Numbers are converted to SI units. A value that cannot be
   translated is left out and reported. It never becomes a default.

The GIS readers come with the optional ``gis`` dependencies.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, ClassVar, Literal

import numpy as np
import shapely
import yaml
from pydantic import model_validator
from pyproj import CRS
from shapely.geometry import mapping as to_geojson
from shapely.geometry.base import BaseGeometry
from shapely.validation import explain_validity

from r3chain.domain.base import (
    Identifier,
    NonEmptyStr,
    NonNegativeReal,
    PositiveReal,
    Record,
    VersionedRecord,
)
from r3chain.domain.hashing import content_hash
from r3chain.domain.records.common import require_unique
from r3chain.domain.records.spatial import (
    GEOMETRY_TYPES,
    LAYER_SPECS,
    Feature,
    GeometryRepair,
    LayerKind,
    RejectedFeature,
    SpatialLayer,
)
from r3chain.domain.units import UnitError, to_canonical

SUPPORTED_FORMATS = {".gpkg": "GeoPackage", ".parquet": "GeoParquet", ".geojson": "GeoJSON"}


class PropertyMapping(Record):
    """One raw column that becomes one canonical property."""

    source: NonEmptyStr
    target: NonEmptyStr
    # Unit of the raw values. Leave empty when they are already in the canonical unit.
    unit: str | None = None
    # Raw text value to canonical text value. A raw value that is not listed is reported.
    values: dict[str, str] | None = None


class LayerMapping(VersionedRecord):
    """How one raw GIS file becomes one canonical layer."""

    SCHEMA_NAME: ClassVar[str] = "layer_mapping"
    schema_version: Literal["0.1.0"] = "0.1.0"

    mapping_id: Identifier
    kind: LayerKind
    description: str = ""
    # Name of the layer inside a GeoPackage. Not used for other formats.
    source_layer: str | None = None
    id_column: NonEmptyStr
    # Used only when the file states no reference system.
    source_crs: str | None = None
    # Needed when the geometries carry heights.
    vertical_datum: NonEmptyStr | None = None
    coordinate_precision_m: PositiveReal
    max_repair_size_change_fraction: NonNegativeReal
    properties: tuple[PropertyMapping, ...]

    @model_validator(mode="after")
    def _fits_the_layer_kind(self) -> LayerMapping:
        spec = LAYER_SPECS[self.kind]
        require_unique((item.target for item in self.properties), "mapped property")
        for item in self.properties:
            declared = spec.property(item.target)
            if declared is None:
                raise ValueError(f"{item.target!r} is not a property of a {self.kind.value} layer")
            if declared.kind == "number" and item.values is not None:
                raise ValueError(f"{item.target} is a number and cannot have a value map")
            if declared.kind == "text" and item.unit is not None:
                raise ValueError(f"{item.target} is text and cannot have a unit")
        mapped = {item.target for item in self.properties}
        absent = [
            item.name for item in spec.properties if item.required and item.name not in mapped
        ]
        if absent:
            raise ValueError(f"a {self.kind.value} mapping must map: {', '.join(absent)}")
        return self


class LayerImportError(Exception):
    """The file cannot be imported with this mapping at all."""


def load_mapping(path: Path) -> LayerMapping:
    return LayerMapping.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _read_frame(path: Path, source_layer: str | None) -> Any:
    try:
        import geopandas
    except ModuleNotFoundError as error:
        raise LayerImportError(
            f"reading GIS files needs the optional dependency {error.name}. Install r3chain[gis]"
        ) from error
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_FORMATS:
        raise LayerImportError(
            f"{path.name}: unsupported format {suffix!r}. Supported: "
            + ", ".join(sorted(SUPPORTED_FORMATS))
        )
    if suffix == ".parquet":
        return geopandas.read_parquet(path)
    if suffix == ".gpkg":
        if source_layer is None:
            raise LayerImportError(f"{path.name}: a GeoPackage mapping must name its source_layer")
        return geopandas.read_file(path, layer=source_layer)
    return geopandas.read_file(path)


def _json_coordinates(value: Any) -> Any:
    if isinstance(value, tuple | list):
        return [_json_coordinates(item) for item in value]
    return float(value)


def _geojson(geometry: BaseGeometry) -> dict[str, Any]:
    shaped = to_geojson(geometry)
    return {"type": shaped["type"], "coordinates": _json_coordinates(shaped["coordinates"])}


def _size(geometry: BaseGeometry) -> float:
    return (
        float(geometry.area) if geometry.geom_type.endswith("Polygon") else float(geometry.length)
    )


def _of_family(geometry: BaseGeometry, allowed: frozenset[str]) -> BaseGeometry | None:
    """The parts of a geometry that belong to a family, or None when there are none."""
    if geometry.geom_type in allowed:
        return geometry
    if geometry.geom_type == "GeometryCollection":
        parts = [part for part in shapely.get_parts(geometry) if part.geom_type in allowed]
        if parts:
            merged = shapely.union_all(parts)
            return merged if merged.geom_type in allowed else None
    return None


def _raw_id(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float | np.floating):
        return bool(np.isnan(value))
    return False


def import_layer(
    path: Path,
    *,
    layer_id: str,
    dataset_id: str,
    mapping: LayerMapping,
    target_crs: str,
) -> SpatialLayer:
    """Import one GIS file as a canonical layer. Raises ``LayerImportError`` if it cannot be."""
    frame = _read_frame(path, mapping.source_layer)
    if frame.crs is None:
        if mapping.source_crs is None:
            raise LayerImportError(
                f"{path.name} states no reference system and the mapping gives no source_crs"
            )
        frame = frame.set_crs(mapping.source_crs)
    columns = [str(name) for name in frame.columns if name != frame.geometry.name]
    absent = [
        name
        for name in (mapping.id_column, *(item.source for item in mapping.properties))
        if name not in columns
    ]
    if absent:
        raise LayerImportError(f"{path.name} lacks the mapped columns: {', '.join(absent)}")
    source_crs = CRS.from_user_input(frame.crs)
    target = CRS.from_user_input(target_crs)
    source_name = source_crs.to_string()
    if not source_crs.equals(target):
        frame = frame.to_crs(target)

    spec = LAYER_SPECS[mapping.kind]
    allowed = GEOMETRY_TYPES[spec.geometry]
    grid = mapping.coordinate_precision_m
    used = {mapping.id_column, *(item.source for item in mapping.properties)}
    warnings: list[str] = [
        f"column {name} is not mapped and is not imported" for name in sorted(set(columns) - used)
    ]
    features: list[Feature] = []
    repairs: list[GeometryRepair] = []
    rejected: list[RejectedFeature] = []
    heights = False

    for index, row in enumerate(frame.itertuples(index=False)):
        values = row._asdict()
        geometry = getattr(row, frame.geometry.name)
        feature_id = _raw_id(values.get(mapping.id_column)) or f"row-{index + 1}"
        bounds: tuple[float, float, float, float] | None = None
        if geometry is not None and not geometry.is_empty:
            bounds = tuple(float(value) for value in geometry.bounds)  # type: ignore[assignment]

        def reject(
            reason: str,
            *,
            at: tuple[float, float, float, float] | None = bounds,
            name: str = feature_id,
        ) -> None:
            rejected.append(RejectedFeature(feature_id=name, reason=reason, bounds=at))

        if _raw_id(values.get(mapping.id_column)) is None:
            reject(f"no value in the identifier column {mapping.id_column}")
            continue
        if geometry is None or geometry.is_empty:
            reject("no geometry", at=None)
            continue
        if geometry.geom_type not in allowed:
            reject(
                f"a {mapping.kind.value} layer holds {spec.geometry} geometries, not "
                f"{geometry.geom_type}"
            )
            continue
        if not geometry.is_valid:
            defect = explain_validity(geometry)
            repaired = _of_family(shapely.make_valid(geometry), allowed)
            before = abs(_size(geometry))
            if repaired is None or repaired.is_empty:
                reject(f"irreparable geometry: {defect}")
                continue
            after = abs(_size(repaired))
            change = abs(after - before) / before if before > 0.0 else math.inf
            if change > mapping.max_repair_size_change_fraction:
                unit = "m2" if geometry.geom_type.endswith("Polygon") else "m"
                reject(
                    f"ambiguous geometry: {defect}. A repair would change its size from "
                    f"{before:.2f} {unit} to {after:.2f} {unit}. Review it"
                )
                continue
            repairs.append(
                GeometryRepair(feature_id=feature_id, defect=defect, size_change_fraction=change)
            )
            geometry = repaired
        has_z = bool(shapely.has_z(geometry))
        heights = heights or has_z
        geometry = shapely.transform(
            geometry, lambda points: np.round(points / grid) * grid, include_z=has_z
        )
        if not geometry.is_valid:
            reject(f"invalid after rounding to {grid:g} m: {explain_validity(geometry)}")
            continue

        properties: dict[str, float | str] = {}
        for item in mapping.properties:
            raw = values.get(item.source)
            if _is_missing(raw):
                continue
            declared = spec.property(item.target)
            assert declared is not None
            if declared.kind == "number":
                try:
                    number = float(raw)
                    if item.unit is not None:
                        number = to_canonical(number, item.unit).value
                except (TypeError, ValueError, UnitError) as error:
                    warnings.append(
                        f"feature {feature_id}: {item.source} value {raw!r} is not a number in "
                        f"{item.unit or 'the canonical unit'} ({error}). It is left out"
                    )
                    continue
                if not math.isfinite(number):
                    warnings.append(f"feature {feature_id}: {item.source} is not finite")
                    continue
                properties[item.target] = number
                continue
            text = str(raw).strip()
            if item.values is not None:
                if text not in item.values:
                    warnings.append(
                        f"feature {feature_id}: {item.source} value {text!r} is not in the value "
                        "map. It is left out"
                    )
                    continue
                text = item.values[text]
            if text:
                properties[item.target] = text
        features.append(
            Feature(feature_id=feature_id, geometry=_geojson(geometry), properties=properties)
        )

    identifiers = [feature.feature_id for feature in features]
    duplicates = sorted({item for item in identifiers if identifiers.count(item) > 1})
    if duplicates:
        raise LayerImportError(f"{path.name}: duplicate identifiers {', '.join(duplicates)}")
    if heights and mapping.vertical_datum is None:
        raise LayerImportError(
            f"{path.name} carries heights, so the mapping must state their vertical_datum"
        )
    return SpatialLayer(
        layer_id=layer_id,
        kind=mapping.kind,
        dataset_id=dataset_id,
        mapping_id=mapping.mapping_id,
        mapping_hash=content_hash(json.loads(mapping.model_dump_json())),
        source_crs=source_name,
        crs=target_crs,
        vertical_datum=mapping.vertical_datum if heights else None,
        features=tuple(sorted(features, key=lambda feature: feature.feature_id)),
        repairs=tuple(repairs),
        rejected=tuple(rejected),
        import_warnings=tuple(warnings),
    )
