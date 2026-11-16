"""The default evaluation chain.

A placeholder declares the inputs its future calculation depends on and always ends as
``not_established``. It never produces a number. Each milestone replaces placeholders with real
calculations.

    site_evidence   real          how well each site is documented            milestone M0
    source          real          source performance envelope per well concept milestone M1
    screen          placeholder   site and route screening                     milestone M3
    couple          placeholder   plant and network coupling                   milestone M2
    operate         placeholder   dispatch and annual operation                milestone M4
    cost            placeholder   cost ledger and discounted cost              milestone M4
    compare         placeholder   uncertainty, eligibility, ranking            milestone M5

The network engine is available behind its port from milestone M1. It joins the chain with the
coupling stage, because a network calculation needs an operating case.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from r3chain.adapters.source_synthetic import SyntheticSourceModel
from r3chain.coupling.envelope import build_envelope
from r3chain.domain.assumptions import missing_inputs
from r3chain.domain.base import Record
from r3chain.domain.evidence import MissingInput, NotEstablished
from r3chain.domain.paths import identity_of
from r3chain.domain.records.runs import Policy, PolicyKind, StageOutput
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState, StageStatus
from r3chain.pipeline.runner import Stage, StageContext
from r3chain.ports.source import (
    SourceModel,
    SourceNumericalSettings,
    SourceOperatingPoint,
    build_source_request,
)


class SiteEvidenceStage:
    """Summarise, for each site, how well its land, access and constraints are documented."""

    name = "site_evidence"
    version = "1.0.0"
    requires: tuple[str, ...] = ()
    stochastic = False

    def input_fingerprint(self, context: StageContext) -> Any:
        return [site.to_json_dict() for site in context.scenario.sites]

    def run(self, context: StageContext) -> StageOutput:
        sites = context.scenario.sites
        if not sites:
            return StageOutput(
                stage=self.name,
                stage_version=self.version,
                status=StageStatus.NOT_ESTABLISHED,
                reason="the scenario defines no sites",
            )
        summary = {
            site.site_id: {
                "evidence_status": site.evidence_status().value,
                "inputs_not_established": [
                    item.field_path
                    for item in missing_inputs(site, prefix=f"sites[{site.site_id}].")
                ],
            }
            for site in sites
        }
        return StageOutput(
            stage=self.name,
            stage_version=self.version,
            status=StageStatus.COMPLETED,
            payload={"sites": summary},
        )


def _grid(low: float, high: float, count: int) -> list[float]:
    if count < 2 or high <= low:
        raise ValueError("a grid axis needs at least two points and an increasing range")
    step = (high - low) / (count - 1)
    return [low + step * index for index in range(count)]


class SourceEnvelopeStage:
    """Evaluate each well concept on a grid of mass flow and injection temperature.

    The grid and the numerical settings come from the scenario's policy of kind ``source``. The
    engine is chosen by the model family of the well concept. A family that no engine supports
    ends as ``unsupported_model``. It is never given a default performance.
    """

    name = "source"
    version = "1.0.0"
    requires: tuple[str, ...] = ()
    stochastic = False

    def __init__(self, models: Sequence[SourceModel]) -> None:
        self._models = tuple(models)

    def _policy(self, context: StageContext) -> Policy | None:
        policies = [
            policy for policy in context.policies.values() if policy.kind is PolicyKind.SOURCE
        ]
        return policies[0] if len(policies) == 1 else None

    def input_fingerprint(self, context: StageContext) -> Any:
        policy = self._policy(context)
        return {
            "study_id": context.scenario.study.study_id,
            "well_scenarios": [item.to_json_dict() for item in context.scenario.well_scenarios],
            "policy": policy.to_json_dict() if policy else None,
            "engines": [
                [capabilities.engine, capabilities.engine_version]
                for capabilities in (model.capabilities() for model in self._models)
            ],
        }

    def _output(self, status: StageStatus, **fields: Any) -> StageOutput:
        return StageOutput(stage=self.name, stage_version=self.version, status=status, **fields)

    def run(self, context: StageContext) -> StageOutput:
        scenario = context.scenario
        if not scenario.well_scenarios:
            return self._output(
                StageStatus.NOT_ESTABLISHED, reason="the scenario defines no well scenarios"
            )
        policy = self._policy(context)
        if policy is None:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="the scenario must reference exactly one policy of kind 'source'",
            )
        values = policy.parameters
        flows = _grid(
            float(values["min_mass_flow_kg_s"]),
            float(values["max_mass_flow_kg_s"]),
            int(values["mass_flow_points_count"]),
        )
        temperatures = _grid(
            float(values["min_injection_temperature_k"]),
            float(values["max_injection_temperature_k"]),
            int(values["injection_temperature_points_count"]),
        )
        numerical = SourceNumericalSettings(
            max_iterations_count=int(values["max_iterations_count"]),
            temperature_tolerance_delta_k=float(values["temperature_tolerance_delta_k"]),
        )

        sources: dict[str, Any] = {}
        missing: list[MissingInput] = []
        unsupported: list[str] = []
        synthetic = False
        for well_scenario in scenario.well_scenarios:
            identifier = well_scenario.source_scenario_id
            model = next(
                (
                    candidate
                    for candidate in self._models
                    if candidate.capabilities().supports(well_scenario.model_family)
                ),
                None,
            )
            if model is None:
                unsupported.append(identifier)
                sources[identifier] = {
                    "state": AdapterState.UNSUPPORTED_MODEL.value,
                    "reason": f"no engine supports model family {well_scenario.model_family!r}",
                }
                continue
            capabilities = model.capabilities()
            try:
                request = build_source_request(
                    study_id=scenario.study.study_id,
                    well_scenario=well_scenario,
                    operating_point=SourceOperatingPoint(
                        requested_mass_flow_kg_s=flows[0],
                        injection_temperature_k=temperatures[0],
                    ),
                    numerical=numerical,
                    capabilities=capabilities,
                )
            except NotEstablished as error:
                missing.extend(error.missing)
                sources[identifier] = {
                    "state": StageStatus.NOT_ESTABLISHED.value,
                    "reason": "required inputs are not established",
                }
                continue
            envelope = build_envelope(model, request, flows, temperatures)
            synthetic = synthetic or capabilities.synthetic
            best = envelope.best_point()
            error_record = envelope.approximation_error
            sources[identifier] = {
                "state": AdapterState.COMPLETED.value,
                "engine": capabilities.engine,
                "engine_version": capabilities.engine_version,
                "fidelity": capabilities.fidelity,
                "synthetic": capabilities.synthetic,
                "summary": {
                    "points": envelope.state_counts(),
                    "validated_cells_count": len(envelope.validated_cells),
                    "largest_heat_w": best.geothermal_extracted_heat_w if best else None,
                    "at_mass_flow_kg_s": best.mass_flow_kg_s if best else None,
                    "at_injection_temperature_k": best.injection_temperature_k if best else None,
                    "max_rel_heat_error_fraction": (
                        error_record.max_rel_heat_error_fraction if error_record else None
                    ),
                },
                "envelope": envelope.to_json_dict(),
            }

        payload = {"sources": sources, "policy_id": policy.policy_id}
        if missing:
            return self._output(
                StageStatus.NOT_ESTABLISHED,
                reason="required source inputs are not established",
                missing_inputs=tuple(missing),
                payload=payload,
                synthetic=synthetic,
            )
        if unsupported:
            return self._output(
                StageStatus.UNSUPPORTED_MODEL,
                reason=f"no engine supports the model family of: {', '.join(unsupported)}",
                payload=payload,
                synthetic=synthetic,
            )
        return self._output(StageStatus.COMPLETED, payload=payload, synthetic=synthetic)


Selection = Sequence[tuple[str, Sequence[Record]]]


class PlaceholderStage:
    """Stands in for a calculation that a later milestone delivers."""

    version = "0.0.0"
    stochastic = False

    def __init__(
        self,
        name: str,
        requires: tuple[str, ...],
        milestone: str,
        select: Callable[[Scenario], Selection],
    ) -> None:
        self.name = name
        self.requires = requires
        self._milestone = milestone
        self._select = select

    def input_fingerprint(self, context: StageContext) -> Any:
        return {
            collection: [record.to_json_dict() for record in records]
            for collection, records in self._select(context.scenario)
        }

    def run(self, context: StageContext) -> StageOutput:
        missing: list[MissingInput] = []
        for collection, records in self._select(context.scenario):
            for record in records:
                prefix = f"{collection}[{identity_of(record)}]."
                missing.extend(missing_inputs(record, prefix=prefix))
        return StageOutput(
            stage=self.name,
            stage_version=self.version,
            status=StageStatus.NOT_ESTABLISHED,
            reason=(
                f"the {self.name} calculation is not implemented yet. "
                f"It arrives in milestone {self._milestone}"
            ),
            missing_inputs=tuple(missing),
        )


def default_source_models() -> tuple[SourceModel, ...]:
    """The source engines that are available. PyDoublet joins this list when it is supplied."""
    return (SyntheticSourceModel(),)


def default_stages(source_models: Sequence[SourceModel] | None = None) -> tuple[Stage, ...]:
    models = default_source_models() if source_models is None else tuple(source_models)
    return (
        SiteEvidenceStage(),
        SourceEnvelopeStage(models),
        PlaceholderStage(
            "screen",
            ("site_evidence",),
            "M3",
            lambda scenario: (
                ("sites", scenario.sites),
                ("routes", scenario.routes),
                ("connections", scenario.connections),
            ),
        ),
        PlaceholderStage(
            "couple",
            ("screen", "source"),
            "M2",
            lambda scenario: (
                ("connections", scenario.connections),
                ("networks", scenario.networks),
            ),
        ),
        PlaceholderStage(
            "operate",
            ("couple",),
            "M4",
            lambda scenario: (("operating_scenarios", scenario.operating_scenarios),),
        ),
        PlaceholderStage(
            "cost",
            ("operate",),
            "M4",
            lambda scenario: (("economic_scenarios", scenario.economic_scenarios),),
        ),
        PlaceholderStage(
            "compare",
            ("cost",),
            "M5",
            lambda scenario: (("uncertainty_models", scenario.uncertainty_models),),
        ),
    )
