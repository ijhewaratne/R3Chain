"""Site screening (specification 8.2).

Each site is checked against the criteria of the screening policy. Every criterion ends as pass,
fail or unknown, with its observed value, its limit, its reason and its evidence.

* Any failed criterion excludes the site. The site stays in the evidence with its reasons.
* Otherwise any unknown criterion makes the site conditional. Unknown is never a pass.
* A site that passes every criterion has passed the screening. That is not a permit: a site is
  never permit ready from a GIS intersection alone.

Criteria

geological_target
    A well concept of the site has a target within the horizontal reach of the pad.
land_status
    The land is available. Restricted land passes only if the policy allows it.
constraint-<id>
    Each documented constraint of the site record, for example a permit.
usable_pad_area
    The largest contiguous area of the parcel that remains after every restriction and its
    setback, compared with the minimum pad area. A restriction kind without a setback in the
    policy, or a rejected restriction feature near the parcel, makes it unknown.
access
    A corridor of the access kind lies within reach of the parcel and is wide enough.

The jurisdiction is recorded as an attribute. A site outside the municipality is not excluded
for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import shapely
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

from r3chain.domain import policies
from r3chain.domain.base import Identifier, NonEmptyStr, Real, Record
from r3chain.domain.evidence import Conflict, Known
from r3chain.domain.records.runs import Policy
from r3chain.domain.records.site import ConstraintOutcome, LandStatus, Site, WellScenario
from r3chain.domain.records.spatial import LayerKind, SpatialLayer

NOT_ASSESSED = (
    "terrain_and_grading",
    "turning_and_delivery",
    "utilities",
    "noise_and_acceptance",
    "permitted_use",
)
NOT_A_PERMIT = "A site is not permit ready from a GIS screening alone."


class CriterionOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class ScreeningStatus(StrEnum):
    PASSED = "passed"
    CONDITIONAL = "conditional"
    EXCLUDED = "excluded"


class ScreeningCriterion(Record):
    criterion_id: Identifier
    description: NonEmptyStr
    outcome: CriterionOutcome
    observed: Real | str | None = None
    limit: Real | str | None = None
    unit: str | None = None
    reason: str = ""
    evidence: tuple[str, ...] = ()


class TargetReach(Record):
    source_scenario_id: Identifier
    target_id: Identifier
    distance_m: Real | None = None
    reachable: bool | None = None
    reason: str = ""


class SiteScreening(Record):
    site_id: Identifier
    status: ScreeningStatus
    criteria: tuple[ScreeningCriterion, ...]
    jurisdictions: tuple[str, ...] = ()
    # The largest contiguous usable area, as GeoJSON in the study's reference system.
    usable_pad: dict[str, Any] | None = None
    usable_pad_area_m2: Real | None = None
    # Where a plant on the pad would stand. Routes start here.
    pad_point: tuple[Real, Real] | None = None
    targets: tuple[TargetReach, ...] = ()
    not_assessed: tuple[str, ...] = NOT_ASSESSED
    note: str = NOT_A_PERMIT

    def criterion(self, criterion_id: str) -> ScreeningCriterion:
        return next(item for item in self.criteria if item.criterion_id == criterion_id)

    def reasons(self) -> tuple[str, ...]:
        """Why the site is excluded or conditional, one line per criterion."""
        return tuple(
            f"{item.criterion_id} {item.outcome.value}: {item.reason}"
            for item in self.criteria
            if item.outcome is not CriterionOutcome.PASS
        )


@dataclass(frozen=True)
class ScreeningSettings:
    min_usable_pad_area_m2: float
    restriction_search_distance_m: float
    max_access_distance_m: float
    min_access_width_m: float
    access_corridor_kind: str
    max_horizontal_reach_m: float
    allow_restricted_land: bool
    setbacks_m: dict[str, float]
    origin: str

    @classmethod
    def from_policy(cls, policy: Policy) -> ScreeningSettings:
        return cls(
            min_usable_pad_area_m2=policies.number(policy, "min_usable_pad_area_m2"),
            restriction_search_distance_m=policies.number(policy, "restriction_search_distance_m"),
            max_access_distance_m=policies.number(policy, "max_access_distance_m"),
            min_access_width_m=policies.number(policy, "min_access_width_m"),
            access_corridor_kind=policies.text(policy, "access_corridor_kind"),
            max_horizontal_reach_m=policies.number(policy, "max_horizontal_reach_m"),
            allow_restricted_land=policies.flag(policy, "allow_restricted_land"),
            setbacks_m=policies.numbers_by_key(policy, "setback_", "_m"),
            origin=policies.origin(policy),
        )


def _layers(layers: tuple[SpatialLayer, ...], kind: LayerKind) -> list[SpatialLayer]:
    return [layer for layer in layers if layer.kind is kind]


def _ref(layer: SpatialLayer, feature_id: str) -> str:
    return f"spatial_layers[{layer.layer_id}].features[{feature_id}]"


def _status(criteria: list[ScreeningCriterion]) -> ScreeningStatus:
    outcomes = {item.outcome for item in criteria}
    if CriterionOutcome.FAIL in outcomes:
        return ScreeningStatus.EXCLUDED
    if CriterionOutcome.UNKNOWN in outcomes:
        return ScreeningStatus.CONDITIONAL
    return ScreeningStatus.PASSED


def _land_status(site: Site, settings: ScreeningSettings) -> ScreeningCriterion:
    path = f"sites[{site.site_id}].land_status"
    evidence = site.land_status
    description = "the land is available for a drilling pad"
    if not isinstance(evidence, Known):
        state = "in conflict" if isinstance(evidence, Conflict) else "unknown"
        return ScreeningCriterion(
            criterion_id="land_status",
            description=description,
            outcome=CriterionOutcome.UNKNOWN,
            reason=f"the land status is {state}",
            evidence=(path,),
        )
    status = evidence.value
    allowed = status is LandStatus.AVAILABLE or (
        status is LandStatus.RESTRICTED and settings.allow_restricted_land
    )
    return ScreeningCriterion(
        criterion_id="land_status",
        description=description,
        outcome=CriterionOutcome.PASS if allowed else CriterionOutcome.FAIL,
        observed=status.value,
        limit="available" + (" or restricted" if settings.allow_restricted_land else ""),
        reason="" if allowed else f"the land is {status.value}",
        evidence=(path, settings.origin),
    )


def _constraints(site: Site) -> list[ScreeningCriterion]:
    found: list[ScreeningCriterion] = []
    for constraint in site.constraints:
        path = f"sites[{site.site_id}].constraints[{constraint.constraint_id}].outcome"
        evidence = constraint.outcome
        if isinstance(evidence, Known):
            satisfied = evidence.value is ConstraintOutcome.SATISFIED
            outcome = CriterionOutcome.PASS if satisfied else CriterionOutcome.FAIL
            reason = "" if satisfied else "the documented criterion is violated"
            observed: str | None = evidence.value.value
        else:
            outcome = CriterionOutcome.UNKNOWN
            reason = "the outcome is " + (
                "in conflict" if isinstance(evidence, Conflict) else "unknown"
            )
            observed = None
        found.append(
            ScreeningCriterion(
                criterion_id=f"constraint-{constraint.constraint_id}",
                description=constraint.criterion,
                outcome=outcome,
                observed=observed,
                limit="satisfied",
                reason=reason,
                evidence=(path,),
            )
        )
    return found


def _usable_pad(
    site: Site, parcel: BaseGeometry, layers: tuple[SpatialLayer, ...], settings: ScreeningSettings
) -> tuple[ScreeningCriterion, BaseGeometry | None]:
    description = "largest contiguous usable pad area after restrictions and their setbacks"
    restriction_layers = _layers(layers, LayerKind.RESTRICTION)
    search_distance = max(
        settings.restriction_search_distance_m,
        max(settings.setbacks_m.values(), default=0.0),
    )
    search = parcel.buffer(search_distance)
    zones: list[BaseGeometry] = []
    evidence = [f"sites[{site.site_id}].parcel_geometry", settings.origin]
    open_points: list[str] = []
    for layer in restriction_layers:
        for feature in layer.features:
            geometry = shape(feature.geometry)
            if not geometry.intersects(search):
                continue
            evidence.append(_ref(layer, feature.feature_id))
            kind = feature.properties.get("restriction_kind")
            setback = settings.setbacks_m.get(str(kind)) if kind is not None else None
            if setback is None:
                open_points.append(
                    f"restriction {feature.feature_id} of kind {kind or 'unknown'} has no "
                    "documented setback in the screening policy"
                )
                setback = 0.0
            zones.append(geometry.buffer(setback) if setback > 0.0 else geometry)
        for item in layer.rejected:
            if item.bounds is not None and box(*item.bounds).intersects(search):
                open_points.append(
                    f"restriction {item.feature_id} was rejected at import and could not be "
                    "taken into account"
                )
                evidence.append(_ref(layer, item.feature_id))
    usable = parcel.difference(shapely.union_all(zones)) if zones else parcel
    parts = [
        part
        for part in shapely.get_parts(usable)
        if part.geom_type == "Polygon" and not part.is_empty
    ]
    largest = max(parts, key=lambda part: part.area) if parts else None
    area = float(largest.area) if largest is not None else 0.0
    limit = settings.min_usable_pad_area_m2
    if not restriction_layers:
        outcome, reason = CriterionOutcome.UNKNOWN, "the scenario has no restriction layer"
    elif open_points:
        outcome, reason = CriterionOutcome.UNKNOWN, "; ".join(open_points)
    elif area >= limit:
        outcome, reason = CriterionOutcome.PASS, ""
    else:
        outcome, reason = CriterionOutcome.FAIL, f"only {area:.0f} m2 remain usable"
    criterion = ScreeningCriterion(
        criterion_id="usable_pad_area",
        description=description,
        outcome=outcome,
        observed=area,
        limit=limit,
        unit="m2",
        reason=reason,
        evidence=tuple(evidence),
    )
    return criterion, largest


def _access(
    site: Site, parcel: BaseGeometry, layers: tuple[SpatialLayer, ...], settings: ScreeningSettings
) -> ScreeningCriterion:
    description = (
        f"a {settings.access_corridor_kind} within {settings.max_access_distance_m:g} m that is "
        f"at least {settings.min_access_width_m:g} m wide"
    )
    corridor_layers = _layers(layers, LayerKind.CORRIDOR)
    base = {
        "criterion_id": "access",
        "description": description,
        "limit": settings.min_access_width_m,
        "unit": "m",
    }
    if not corridor_layers:
        return ScreeningCriterion(
            **base, outcome=CriterionOutcome.UNKNOWN, reason="the scenario has no corridor layer"
        )
    near: list[tuple[float, float | None, str]] = []
    nearest = float("inf")
    for layer in corridor_layers:
        for feature in layer.features:
            if feature.properties.get("corridor_kind") != settings.access_corridor_kind:
                continue
            distance = float(parcel.distance(shape(feature.geometry)))
            nearest = min(nearest, distance)
            if distance <= settings.max_access_distance_m:
                width = feature.properties.get("width_m")
                near.append(
                    (
                        distance,
                        float(width) if isinstance(width, int | float) else None,
                        _ref(layer, feature.feature_id),
                    )
                )
    evidence = (f"sites[{site.site_id}].parcel_geometry", settings.origin)
    if not near:
        where = "none in the corridor layers" if nearest == float("inf") else f"{nearest:.0f} m"
        return ScreeningCriterion(
            **base,
            outcome=CriterionOutcome.FAIL,
            observed=None,
            reason=f"no {settings.access_corridor_kind} within reach. Nearest: {where}",
            evidence=evidence,
        )
    widths = [width for _, width, _ in near if width is not None]
    best = max(widths, default=None)
    references = tuple(reference for _, _, reference in near)
    if best is not None and best >= settings.min_access_width_m:
        return ScreeningCriterion(
            **base,
            outcome=CriterionOutcome.PASS,
            observed=best,
            evidence=evidence + references,
        )
    if len(widths) == len(near):
        return ScreeningCriterion(
            **base,
            outcome=CriterionOutcome.FAIL,
            observed=best,
            reason=f"the widest {settings.access_corridor_kind} within reach is {best:g} m wide",
            evidence=evidence + references,
        )
    return ScreeningCriterion(
        **base,
        outcome=CriterionOutcome.UNKNOWN,
        observed=best,
        reason=f"the width of a {settings.access_corridor_kind} within reach is not recorded",
        evidence=evidence + references,
    )


def _targets(
    site: Site,
    pad: BaseGeometry,
    well_scenarios: tuple[WellScenario, ...],
    layers: tuple[SpatialLayer, ...],
    settings: ScreeningSettings,
) -> tuple[ScreeningCriterion, tuple[TargetReach, ...]]:
    description = (
        f"a well concept reaches its target within {settings.max_horizontal_reach_m:g} m of the pad"
    )
    concepts = [item for item in well_scenarios if item.site_id == site.site_id]
    target_layers = _layers(layers, LayerKind.GEOLOGICAL_TARGET)
    base = {
        "criterion_id": "geological_target",
        "description": description,
        "limit": settings.max_horizontal_reach_m,
        "unit": "m",
    }
    if not concepts:
        return (
            ScreeningCriterion(
                **base,
                outcome=CriterionOutcome.UNKNOWN,
                reason="no subsurface concept is defined for this site",
            ),
            (),
        )
    if not target_layers:
        return (
            ScreeningCriterion(
                **base,
                outcome=CriterionOutcome.UNKNOWN,
                reason="the scenario has no geological target layer",
            ),
            (),
        )
    reaches: list[TargetReach] = []
    evidence: list[str] = [settings.origin]
    for concept in concepts:
        shapes = [
            (layer, feature, shape(feature.geometry))
            for layer in target_layers
            for feature in layer.features
            if feature.properties.get("target_id") == concept.target_id
        ]
        if not shapes:
            reaches.append(
                TargetReach(
                    source_scenario_id=concept.source_scenario_id,
                    target_id=concept.target_id,
                    reason=f"target {concept.target_id} is not in any target layer",
                )
            )
            continue
        distance = min(float(pad.distance(geometry)) for _, _, geometry in shapes)
        evidence.extend(_ref(layer, feature.feature_id) for layer, feature, _ in shapes)
        reachable = distance <= settings.max_horizontal_reach_m
        reaches.append(
            TargetReach(
                source_scenario_id=concept.source_scenario_id,
                target_id=concept.target_id,
                distance_m=distance,
                reachable=reachable,
                reason="" if reachable else f"the target lies {distance:.0f} m from the pad",
            )
        )
    known = [reach for reach in reaches if reach.reachable is not None]
    closest = min(
        (reach.distance_m for reach in known if reach.distance_m is not None), default=None
    )
    if any(reach.reachable for reach in known):
        outcome, reason = CriterionOutcome.PASS, ""
    elif len(known) == len(reaches):
        outcome, reason = CriterionOutcome.FAIL, "no target lies within reach of the pad"
    else:
        outcome = CriterionOutcome.UNKNOWN
        reason = "; ".join(reach.reason for reach in reaches if reach.reachable is None)
    return (
        ScreeningCriterion(
            **base, outcome=outcome, observed=closest, reason=reason, evidence=tuple(evidence)
        ),
        tuple(reaches),
    )


def _jurisdictions(parcel: BaseGeometry, layers: tuple[SpatialLayer, ...]) -> tuple[str, ...]:
    found: list[str] = []
    for layer in _layers(layers, LayerKind.JURISDICTION):
        for feature in layer.features:
            if shape(feature.geometry).intersects(parcel):
                name = feature.properties.get("jurisdiction_name")
                found.append(str(name) if name is not None else feature.feature_id)
    return tuple(sorted(set(found)))


def screen_site(
    site: Site,
    *,
    well_scenarios: tuple[WellScenario, ...],
    layers: tuple[SpatialLayer, ...],
    settings: ScreeningSettings,
    study_crs: str,
) -> SiteScreening:
    """Screen one site against the policy, using the layers of the scenario."""
    criteria: list[ScreeningCriterion] = []
    parcel = shape(site.parcel_geometry.geojson)
    usable: BaseGeometry | None = None
    if site.parcel_geometry.crs != study_crs or not parcel.is_valid:
        problem = (
            f"the parcel geometry is in {site.parcel_geometry.crs}, the study uses {study_crs}"
            if site.parcel_geometry.crs != study_crs
            else "the parcel geometry is not valid"
        )
        criteria.append(
            ScreeningCriterion(
                criterion_id="parcel_geometry",
                description="the parcel geometry can be used for spatial checks",
                outcome=CriterionOutcome.UNKNOWN,
                reason=problem,
                evidence=(f"sites[{site.site_id}].parcel_geometry",),
            )
        )
        criteria.append(_land_status(site, settings))
        criteria.extend(_constraints(site))
        return SiteScreening(
            site_id=site.site_id, status=_status(criteria), criteria=tuple(criteria)
        )

    pad_criterion, usable = _usable_pad(site, parcel, layers, settings)
    pad = usable if usable is not None else parcel
    target_criterion, reaches = _targets(site, pad, well_scenarios, layers, settings)
    criteria.append(target_criterion)
    criteria.append(_land_status(site, settings))
    criteria.extend(_constraints(site))
    criteria.append(pad_criterion)
    criteria.append(_access(site, parcel, layers, settings))
    point = pad.centroid if pad.contains(pad.centroid) else pad.representative_point()
    return SiteScreening(
        site_id=site.site_id,
        status=_status(criteria),
        criteria=tuple(criteria),
        jurisdictions=_jurisdictions(parcel, layers),
        usable_pad=shapely.geometry.mapping(usable) if usable is not None else None,
        usable_pad_area_m2=float(usable.area) if usable is not None else 0.0,
        pad_point=(float(point.x), float(point.y)),
        targets=reaches,
    )
