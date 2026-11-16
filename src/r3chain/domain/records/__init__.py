"""The core records of specification section 7.2, plus the scenario aggregate."""

from r3chain.domain.base import VersionedRecord
from r3chain.domain.records.network import Connection, Network, Route
from r3chain.domain.records.plant import CouplingCase, OperatingCase, Plant
from r3chain.domain.records.runs import Assumption, Policy, Result, Run
from r3chain.domain.records.scenario import Scenario, ScenarioRevision
from r3chain.domain.records.scenarios import EconomicScenario, OperatingScenario, UncertaintyModel
from r3chain.domain.records.site import Site, WellScenario
from r3chain.domain.records.study import Dataset, Study

# The thirteen records of specification 7.2, in the order of that table.
CORE_RECORDS: tuple[type[VersionedRecord], ...] = (
    Study,
    Dataset,
    Site,
    WellScenario,
    Connection,
    Route,
    Network,
    OperatingScenario,
    EconomicScenario,
    UncertaintyModel,
    Assumption,
    Run,
    Result,
)

# Every record whose schema is exported and version checked.
VERSIONED_RECORDS: tuple[type[VersionedRecord], ...] = (
    *CORE_RECORDS,
    Plant,
    OperatingCase,
    CouplingCase,
    Policy,
    Scenario,
    ScenarioRevision,
)

__all__ = [
    "CORE_RECORDS",
    "VERSIONED_RECORDS",
    "Assumption",
    "Connection",
    "CouplingCase",
    "Dataset",
    "EconomicScenario",
    "Network",
    "OperatingCase",
    "OperatingScenario",
    "Plant",
    "Policy",
    "Result",
    "Route",
    "Run",
    "Scenario",
    "ScenarioRevision",
    "Site",
    "Study",
    "UncertaintyModel",
    "WellScenario",
]
