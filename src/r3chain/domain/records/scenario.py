"""The scenario aggregate and its revisions.

A scenario is the complete, frozen input of a study. A revision is one immutable version of it,
identified by the hash of its content, with a record of what changed from its parent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import (
    Identifier,
    JsonDict,
    NonEmptyStr,
    Record,
    VersionedRecord,
    register_migration,
)
from r3chain.domain.hashing import short_hash
from r3chain.domain.records.common import require_unique
from r3chain.domain.records.network import Connection, Network, Route
from r3chain.domain.records.plant import CouplingCase, OperatingCase, Plant
from r3chain.domain.records.runs import PolicyRef
from r3chain.domain.records.scenarios import (
    EconomicScenario,
    OperatingScenario,
    StoredSeries,
    UncertaintyModel,
)
from r3chain.domain.records.site import Site, WellScenario
from r3chain.domain.records.spatial import SpatialLayer
from r3chain.domain.records.study import Dataset, Study
from r3chain.domain.timeseries import UtcDatetime


class Alternative(Record):
    """A supply concept compared in the study. One of them is the baseline."""

    alternative_id: Identifier
    label: NonEmptyStr
    description: str = ""


class Scenario(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "scenario"
    schema_version: Literal["0.5.0"] = "0.5.0"

    scenario_id: Identifier
    study: Study
    alternatives: tuple[Alternative, ...] = Field(min_length=1)
    datasets: tuple[Dataset, ...] = ()
    sites: tuple[Site, ...] = ()
    well_scenarios: tuple[WellScenario, ...] = ()
    networks: tuple[Network, ...] = ()
    connections: tuple[Connection, ...] = ()
    routes: tuple[Route, ...] = ()
    # Imported GIS layers in the study's reference system (milestone M3).
    spatial_layers: tuple[SpatialLayer, ...] = ()
    # Imported interval time series frozen with the scenario (milestone M4).
    time_series: tuple[StoredSeries, ...] = ()
    plants: tuple[Plant, ...] = ()
    operating_cases: tuple[OperatingCase, ...] = ()
    coupling_cases: tuple[CouplingCase, ...] = ()
    operating_scenarios: tuple[OperatingScenario, ...] = ()
    economic_scenarios: tuple[EconomicScenario, ...] = ()
    uncertainty_models: tuple[UncertaintyModel, ...] = ()
    policies: tuple[PolicyRef, ...] = ()

    @model_validator(mode="after")
    def _identifiers_are_unique(self) -> Scenario:
        require_unique((item.alternative_id for item in self.alternatives), "alternative_id")
        require_unique((item.dataset_id for item in self.datasets), "dataset_id")
        require_unique((item.site_id for item in self.sites), "site_id")
        require_unique(
            (item.source_scenario_id for item in self.well_scenarios), "source_scenario_id"
        )
        require_unique((item.network_id for item in self.networks), "network_id")
        require_unique((item.connection_id for item in self.connections), "connection_id")
        require_unique((item.route_id for item in self.routes), "route_id")
        require_unique((item.layer_id for item in self.spatial_layers), "layer_id")
        require_unique((item.series_id for item in self.time_series), "series_id")
        require_unique((item.plant_id for item in self.plants), "plant_id")
        require_unique(
            (item.operating_case_id for item in self.operating_cases), "operating_case_id"
        )
        require_unique((item.coupling_case_id for item in self.coupling_cases), "coupling_case_id")
        require_unique(
            (item.operating_scenario_id for item in self.operating_scenarios),
            "operating_scenario_id",
        )
        require_unique(
            (item.economic_scenario_id for item in self.economic_scenarios),
            "economic_scenario_id",
        )
        require_unique(
            (item.uncertainty_model_id for item in self.uncertainty_models),
            "uncertainty_model_id",
        )
        require_unique((item.policy_id for item in self.policies), "policy_id")
        return self

    def revision_id(self) -> str:
        return "rev-" + short_hash(self.content_hash())


class ChangeKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class FieldChange(Record):
    """One field level difference between two scenario revisions."""

    field_path: str
    kind: ChangeKind
    before: Any = None
    after: Any = None


class ScenarioRevision(VersionedRecord):
    SCHEMA_NAME: ClassVar[str] = "scenario_revision"
    schema_version: Literal["0.1.0"] = "0.1.0"

    revision_id: Identifier
    scenario_id: Identifier
    scenario_hash: str
    parent_revision_id: Identifier | None = None
    created_at: UtcDatetime
    author: NonEmptyStr
    rationale: NonEmptyStr
    changes: tuple[FieldChange, ...] = ()


# --- schema migrations ----------------------------------------------------------------------


@register_migration("scenario", "0.1.0", "0.2.0")
def _scenario_gains_plants_and_cases(data: JsonDict) -> JsonDict:
    """0.2.0 adds plants, operating cases and coupling cases. Older scenarios have none."""
    return data


@register_migration("scenario", "0.2.0", "0.3.0")
def _scenario_gains_spatial_layers(data: JsonDict) -> JsonDict:
    """0.3.0 adds imported spatial layers. Older scenarios have none."""
    return data


@register_migration("scenario", "0.3.0", "0.4.0")
def _scenario_gains_time_series(data: JsonDict) -> JsonDict:
    """0.4.0 adds frozen time series. Older scenarios have none."""
    return data


@register_migration("scenario", "0.4.0", "0.5.0")
def _scenario_gains_uncertainty_model_v2(data: JsonDict) -> JsonDict:
    """0.5.0 adopts uncertainty_model 0.2.0 (correlation groups and pre-drilling parameters)."""
    return data
