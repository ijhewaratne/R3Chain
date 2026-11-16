"""Study and dataset records (specification 7.2)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import field_validator

from r3chain.domain.base import Identifier, NonEmptyStr, VersionedRecord
from r3chain.domain.hashing import is_checksum
from r3chain.domain.records.common import Geometry, TimeExtent
from r3chain.domain.timeseries import UtcDatetime


class Study(VersionedRecord):
    """A declared study inside a project. All candidates and documents belong to one."""

    SCHEMA_NAME: ClassVar[str] = "study"
    schema_version: Literal["0.1.0"] = "0.1.0"

    study_id: Identifier
    project_id: Identifier
    title: NonEmptyStr
    objective: NonEmptyStr
    study_area: Geometry
    planning_date: date
    baseline_id: Identifier
    # True for demonstration studies. Their results never enter a pilot ranking.
    synthetic: bool


class DatasetKind(StrEnum):
    GIS = "gis"
    TIMESERIES = "timeseries"
    MEASUREMENT = "measurement"
    DOCUMENT = "document"
    PARAMETER_PACKAGE = "parameter_package"
    DEFINITIONS = "definitions"


class AllowedUse(StrEnum):
    PUBLIC = "public"
    PROJECT_INTERNAL = "project_internal"
    RESTRICTED = "restricted"


class QualityStatus(StrEnum):
    REVIEWED = "reviewed"
    UNREVIEWED = "unreviewed"
    KNOWN_ISSUES = "known_issues"


class Dataset(VersionedRecord):
    """One immutable version of a source file or package, identified by its checksum."""

    SCHEMA_NAME: ClassVar[str] = "dataset"
    schema_version: Literal["0.1.0"] = "0.1.0"

    dataset_id: Identifier
    version: NonEmptyStr
    kind: DatasetKind
    title: NonEmptyStr
    source_uri: NonEmptyStr
    checksum: str
    owner: NonEmptyStr
    acquired_at: UtcDatetime
    allowed_use: AllowedUse
    access_group: NonEmptyStr
    spatial_extent: Geometry | None = None
    time_extent: TimeExtent | None = None
    quality: QualityStatus
    quality_note: str = ""
    synthetic: bool = False

    @field_validator("checksum")
    @classmethod
    def _checksum_format(cls, value: str) -> str:
        if not is_checksum(value):
            raise ValueError("checksum must look like 'sha256:' followed by 64 hex characters")
        return value
