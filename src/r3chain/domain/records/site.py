"""Site and well scenario records (specification 7.2).

A surface pad, a bottom hole target, the vertical depth and the measured well length are four
different things and are stored separately.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import Identifier, NonEmptyStr, Real, Record, VersionedRecord
from r3chain.domain.evidence import Evidence
from r3chain.domain.records.common import Elevation, Geometry, require_unique, value_or_none
from r3chain.domain.status import SiteEvidenceStatus
from r3chain.domain.units import Depth, DepthKind, require_unit_suffix


class LandStatus(StrEnum):
    AVAILABLE = "available"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"


class ConstraintOutcome(StrEnum):
    SATISFIED = "satisfied"
    VIOLATED = "violated"


class SiteConstraint(Record):
    """One documented site criterion, for example a permit or a setback."""

    constraint_id: Identifier
    criterion: NonEmptyStr
    outcome: Evidence[ConstraintOutcome]


class Site(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "site"
    schema_version: Literal["0.1.0"] = "0.1.0"

    site_id: Identifier
    name: NonEmptyStr
    parcel_geometry: Geometry
    usable_pad_geometry: Evidence[Geometry]
    usable_pad_area_m2: Evidence[Real]
    elevation: Evidence[Elevation]
    access: Evidence[NonEmptyStr]
    land_status: Evidence[LandStatus]
    constraints: tuple[SiteConstraint, ...] = ()

    @model_validator(mode="after")
    def _is_consistent(self) -> Site:
        require_unique((item.constraint_id for item in self.constraints), "constraint_id")
        area = value_or_none(self.usable_pad_area_m2)
        if area is not None and area <= 0.0:
            raise ValueError("usable_pad_area_m2 must be positive")
        return self

    def evidence_status(self) -> SiteEvidenceStatus:
        """Summarise how well the site is documented.

        Any conflict gives ``conflict``. All inputs known gives ``documented``. No input known
        gives ``unknown``. A mixture of known and unknown gives ``conditional``.
        """
        states = [
            self.usable_pad_geometry.state,
            self.usable_pad_area_m2.state,
            self.elevation.state,
            self.access.state,
            self.land_status.state,
            *(constraint.outcome.state for constraint in self.constraints),
        ]
        if "conflict" in states:
            return SiteEvidenceStatus.CONFLICT
        known = states.count("known")
        if known == len(states):
            return SiteEvidenceStatus.DOCUMENTED
        if known == 0:
            return SiteEvidenceStatus.UNKNOWN
        return SiteEvidenceStatus.CONDITIONAL


class WellRole(StrEnum):
    PRODUCTION = "production"
    INJECTION = "injection"


class Well(Record):
    well_id: Identifier
    role: WellRole
    surface_site_id: Identifier
    bottom_hole_target_id: Identifier
    vertical_depth: Evidence[Depth]
    measured_length: Evidence[Depth]
    trajectory: Evidence[Geometry]

    @model_validator(mode="after")
    def _depths_keep_their_meaning(self) -> Well:
        vertical = value_or_none(self.vertical_depth)
        measured = value_or_none(self.measured_length)
        if vertical is not None and vertical.kind is not DepthKind.VERTICAL:
            raise ValueError("vertical_depth must be a depth of kind 'vertical'")
        if measured is not None and measured.kind is not DepthKind.MEASURED:
            raise ValueError("measured_length must be a depth of kind 'measured'")
        if vertical is not None and measured is not None and measured.value_m < vertical.value_m:
            raise ValueError("measured well length cannot be shorter than the vertical depth")
        return self


class WellScenario(VersionedRecord):
    """One subsurface concept for a site: target, wells and reservoir parameters."""

    SCHEMA_NAME: ClassVar[str] = "well_scenario"
    schema_version: Literal["0.1.0"] = "0.1.0"

    source_scenario_id: Identifier
    site_id: Identifier
    target_id: Identifier
    model_family: NonEmptyStr
    parameter_package_id: Identifier
    well_configuration_id: Identifier
    wells: tuple[Well, ...] = Field(min_length=1)
    # The required keys depend on the model family. Every key carries its unit as a suffix.
    reservoir_parameters: dict[str, Evidence[Real]]

    @model_validator(mode="after")
    def _is_consistent(self) -> WellScenario:
        require_unique((well.well_id for well in self.wells), "well_id")
        if not any(well.role is WellRole.PRODUCTION for well in self.wells):
            raise ValueError("a well scenario needs at least one production well")
        for name in self.reservoir_parameters:
            require_unit_suffix(name)
        return self
