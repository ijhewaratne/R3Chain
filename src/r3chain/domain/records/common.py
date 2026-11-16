"""Value objects shared by several records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field, model_validator

from r3chain.domain.base import NonEmptyStr, Real, Record
from r3chain.domain.evidence import Conflict, Known, Unknown
from r3chain.domain.timeseries import UtcDatetime

_GEOMETRY_TYPES = frozenset(
    {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}
)


class Geometry(Record):
    """A GeoJSON geometry with its coordinate reference system, for example ``EPSG:25832``."""

    crs: str = Field(pattern=r"^[A-Za-z]+:\d+$")
    geojson: dict[str, Any]

    @model_validator(mode="after")
    def _is_geojson_geometry(self) -> Geometry:
        if self.geojson.get("type") not in _GEOMETRY_TYPES:
            raise ValueError(f"geometry type must be one of {sorted(_GEOMETRY_TYPES)}")
        coordinates = self.geojson.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("geometry needs a non empty list of coordinates")
        return self


class Elevation(Record):
    """A height together with its vertical datum."""

    value_m: Real
    vertical_datum: NonEmptyStr


class TimeExtent(Record):
    start_utc: UtcDatetime
    end_utc: UtcDatetime

    @model_validator(mode="after")
    def _end_after_start(self) -> TimeExtent:
        if self.end_utc <= self.start_utc:
            raise ValueError("a time extent must end after it starts")
        return self


def value_or_none[V](evidence: Known[V] | Unknown | Conflict[V]) -> V | None:
    """Return the value of a known input, or None when it is unknown or in conflict."""
    return evidence.value if isinstance(evidence, Known) else None


def require_unique(identifiers: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for identifier in identifiers:
        if identifier in seen and identifier not in duplicates:
            duplicates.append(identifier)
        seen.add(identifier)
    if duplicates:
        raise ValueError(f"duplicate {what}: {', '.join(duplicates)}")
