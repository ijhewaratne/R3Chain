"""Unified application service layer shared by CLI, HTTP API, MCP server, and future UI.

Plan v2 section 3.1 rule 2: the command line, HTTP API, MCP server, and UI all call the same
application services, so the same request produces identical frozen revisions and run inputs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from r3chain.coupling.cases import CaseOutcome, MemoNetworkModel, evaluate_case
from r3chain.domain.assumptions import assumption_register
from r3chain.domain.hashing import bytes_checksum
from r3chain.domain.paths import PatchOperation, apply_patch
from r3chain.domain.records.runs import (
    Policy,
    PolicyKind,
    Result,
    RunStatus,
    StageOutput,
)
from r3chain.domain.records.scenario import Scenario, ScenarioRevision
from r3chain.domain.records.study import DatasetKind
from r3chain.domain.status import StageStatus
from r3chain.domain.validation import (
    ValidationReport,
    parse_scenario,
    validate_scenario,
)
from r3chain.explain.builder import (
    explain_comparison,
    explain_rejection,
    explain_result,
    explain_study,
)
from r3chain.explain.checker import check_explanation
from r3chain.explain.records import ExplanationRecord
from r3chain.explain.wording import Language, render_explanation
from r3chain.geospatial.export import build_map_layers
from r3chain.pipeline.evaluation import (
    AssessStage,
    CandidateStage,
    CouplingStage,
    RouteStage,
    ScreeningStage,
    coupling_case_summary,
    single_policy,
)
from r3chain.pipeline.operation_cost import CostStage, OperateStage
from r3chain.pipeline.runner import PipelineRunner, Stage, StageContext, code_version
from r3chain.pipeline.stages import (
    SiteEvidenceStage,
    SourceEnvelopeStage,
    default_network_model,
    default_source_models,
)
from r3chain.pipeline.uncertainty_compare import CompareStage
from r3chain.ports.network import NetworkModel
from r3chain.ports.source import (
    SourceModel,
    SourceNumericalSettings,
    SourceOperatingPoint,
    build_source_request,
)
from r3chain.reporting.evidence_report import EvidenceReport, build_evidence_report, render_markdown
from r3chain.search.candidates import Candidate
from r3chain.services.access import (
    AccessPrincipal,
    authorize_dataset,
    authorize_job,
    authorize_project,
    authorize_run,
    authorize_scenario,
    can_access_dataset,
    filter_accessible_datasets,
)
from r3chain.services.contracts import (
    FailureClass,
    ToolResult,
    classify_adapter_failure,
    classify_result_failure,
)
from r3chain.services.jobs import JobManager
from r3chain.store.store import NotFoundError, Store, load_policies, policy_ref, utc_now


class InvalidRequestError(ValueError):
    """Raised when caller arguments, identifiers, patches, or comparison boundaries are invalid."""


class ServiceFaultError(RuntimeError):
    """Raised on internal engine, store, or claim-verification faults."""


class PlanningService:
    """Authoritative application facade for study, revision, evaluation, and evidence workflows."""

    def __init__(
        self,
        store_root: Path,
        *,
        policy_directory: Path = Path("policies"),
        clock: Callable[[], datetime] = utc_now,
        code_version_text: str | None = None,
        source_models: Sequence[SourceModel] | None = None,
        network_model: NetworkModel | None = None,
        stages_factory: Callable[[], Sequence[Stage]] | None = None,
    ) -> None:
        self.store_root = store_root
        self.policy_directory = policy_directory
        self._clock = clock
        self._code_version = code_version_text or code_version()
        self._source_models = (
            default_source_models() if source_models is None else tuple(source_models)
        )
        self._network_model = default_network_model() if network_model is None else network_model
        self._stages_factory = stages_factory or self._default_stages_factory
        self._jobs = JobManager(
            store_root,
            stages_factory=self._stages_factory,
            clock=clock,
            code_version_text=self._code_version,
        )

    def _default_stages_factory(self) -> tuple[Stage, ...]:
        return (
            SiteEvidenceStage(),
            SourceEnvelopeStage(self._source_models),
            ScreeningStage(),
            RouteStage(),
            CandidateStage(),
            CouplingStage(self._source_models, self._network_model),
            AssessStage(),
            OperateStage(),
            CostStage(),
            CompareStage(self._source_models, self._network_model),
        )

    def _open_store(self) -> Store:
        return Store(self.store_root, clock=self._clock)

    def close(self) -> None:
        self._jobs.close()

    def _load_policies_dict(
        self,
        store: Store,
        scenario: Scenario,
        *,
        policies_dir: Path | None = None,
    ) -> dict[str, Policy]:
        if policies_dir is not None and policies_dir.exists():
            for policy in load_policies(policies_dir):
                store.register_policy(policy)
        loaded: dict[str, Policy] = {}
        for ref in scenario.policies:
            try:
                pol = store.get_policy(ref.policy_id, ref.version)
            except NotFoundError:
                pdir = policies_dir or self.policy_directory
                if pdir.exists():
                    for policy in load_policies(pdir):
                        store.register_policy(policy)
                    pol = store.get_policy(ref.policy_id, ref.version)
                else:
                    raise
            if policy_ref(pol).policy_hash != ref.policy_hash:
                raise InvalidRequestError(
                    f"policy {ref.policy_id} version {ref.version} hash mismatches scenario ref"
                )
            loaded[ref.policy_id] = pol
        return loaded

    def register_inputs_and_scenario(
        self,
        principal: AccessPrincipal,
        scenario: Scenario,
        *,
        base_dir: Path | None = None,
        policies_dir: Path | None = None,
        author: str = "service",
        rationale: str = "initial import",
        parent_revision_id: str | None = None,
        store: Store | None = None,
    ) -> tuple[ScenarioRevision, ValidationReport]:
        """Authorise, register inputs, validate, and freeze a scenario revision."""
        authorize_scenario(principal, scenario)
        report = validate_scenario(scenario)
        if not report.executable:
            raise InvalidRequestError(f"scenario has {len(report.errors)} validation error(s)")
        owned = store is None
        s = self._open_store() if store is None else store
        try:
            for dataset in scenario.datasets:
                file: Path | None = None
                candidates = [Path(dataset.source_uri)]
                if base_dir is not None:
                    candidates.append(base_dir / dataset.source_uri)
                for cand in candidates:
                    if cand.is_file():
                        # Restrict relative dataset loading inside declared workspace/base_dir.
                        if base_dir is not None:
                            try:
                                cand.resolve().relative_to(base_dir.resolve())
                            except ValueError:
                                continue
                        file = cand
                        break
                s.register_dataset(dataset, file)
            pdir = self.policy_directory if policies_dir is None else policies_dir
            if pdir.exists():
                for pol in load_policies(pdir):
                    s.register_policy(pol)
            revision = s.add_revision(
                scenario,
                author=author,
                rationale=rationale,
                parent_revision_id=parent_revision_id,
            )
            return revision, report
        finally:
            if owned:
                s.close()

    def register_study_document(
        self,
        principal: AccessPrincipal,
        scenario_data: dict[str, Any],
        *,
        base_dir: Path | None = None,
        policies_dir: Path | None = None,
        author: str = "service",
        rationale: str = "register study",
    ) -> ToolResult:
        """Create or register a study revision from a validated scenario JSON document."""
        scenario, report = parse_scenario(scenario_data)
        if scenario is None:
            return ToolResult(
                status="invalid_request",
                failure_class=FailureClass.INVALID_REQUEST,
                message=f"scenario could not be parsed ({len(report.errors)} error(s))",
                data={"validation": report.to_json_dict()},
            )
        authorize_scenario(principal, scenario)
        if not report.executable:
            return ToolResult(
                status="invalid_request",
                failure_class=FailureClass.INVALID_REQUEST,
                study_id=scenario.study.study_id,
                message=f"scenario has {len(report.errors)} validation error(s)",
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                synthetic=scenario.study.synthetic or report.contains_synthetic_values,
                data={"validation": report.to_json_dict()},
            )
        rev, _ = self.register_inputs_and_scenario(
            principal,
            scenario,
            base_dir=base_dir,
            policies_dir=policies_dir,
            author=author,
            rationale=rationale,
        )
        return ToolResult(
            status="completed",
            study_id=scenario.study.study_id,
            scenario_revision=rev.revision_id,
            input_hash=scenario.content_hash(),
            assumptions_used=tuple(a.to_json_dict() for a in assumption_register(scenario)),
            missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
            synthetic=scenario.study.synthetic or report.contains_synthetic_values,
            data={
                "revision": rev.to_json_dict(),
                "study": scenario.study.to_json_dict(),
                "validation": report.to_json_dict(),
            },
        )

    def _resolve_scenario(
        self,
        store: Store,
        principal: AccessPrincipal,
        *,
        study_id: str | None = None,
        revision: str | None = None,
        enforce_all_datasets: bool = True,
    ) -> tuple[ScenarioRevision, Scenario]:
        if revision is not None:
            rev = store.get_revision(revision)
            scen = store.get_scenario(revision)
            if (
                study_id is not None
                and scen.study.study_id != study_id
                and scen.scenario_id != study_id
            ):
                raise InvalidRequestError(
                    f"revision {revision!r} belongs to study {scen.study.study_id!r}, "
                    f"not {study_id!r}"
                )
        elif study_id is not None:
            rev, scen = store.latest_revision_for_study(study_id)
        else:
            raise InvalidRequestError("specify study_id or revision")
        authorize_project(principal, scen.study.project_id)
        if enforce_all_datasets:
            authorize_scenario(principal, scen)
        return rev, scen

    # --- 1. get_study_context ---------------------------------------------------------------

    def get_study_context(
        self,
        principal: AccessPrincipal,
        study_id: str,
        *,
        revision: str | None = None,
    ) -> ToolResult:
        """Authorised study metadata, active assumptions, accessible datasets, and gaps."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            assumptions = assumption_register(scen)
            accessible_ds = filter_accessible_datasets(principal, scen)
            report = validate_scenario(scen)
            runs = store.list_runs(rev.revision_id)
            return ToolResult(
                status="completed",
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                assumptions_used=tuple(a.to_json_dict() for a in assumptions),
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                synthetic=scen.study.synthetic or report.contains_synthetic_values,
                data={
                    "study": scen.study.to_json_dict(),
                    "revision": rev.to_json_dict(),
                    "alternatives": [a.to_json_dict() for a in scen.alternatives],
                    "datasets": [d.to_json_dict() for d in accessible_ds],
                    "policies": [p.to_json_dict() for p in scen.policies],
                    "runs": [r.to_json_dict() for r in runs],
                    "well_scenarios": [w.source_scenario_id for w in scen.well_scenarios],
                },
            )
        finally:
            store.close()

    # --- 2. search_project_evidence ---------------------------------------------------------

    def search_project_evidence(
        self,
        principal: AccessPrincipal,
        study_id: str,
        query: str,
        *,
        revision: str | None = None,
        filters: Mapping[str, Any] | None = None,
        base_dir: Path | None = None,
    ) -> ToolResult:
        """Permission-filtered search across accessible documents and dataset evidence."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=False
            )
            requested_group = filters.get("access_group") if filters else None
            if (
                requested_group is not None
                and "*" not in principal.access_groups
                and str(requested_group) not in principal.access_groups
            ):
                raise PermissionError(
                    "denied access: requested access_group is outside caller scope"
                )
            accessible = filter_accessible_datasets(principal, scen)
            if filters and filters.get("dataset_id"):
                target_id = str(filters["dataset_id"])
                all_ids = {d.dataset_id: d for d in scen.datasets}
                if target_id in all_ids:
                    authorize_dataset(principal, all_ids[target_id], scen.study.project_id)
                accessible = tuple(d for d in accessible if d.dataset_id == target_id)
            if filters and filters.get("kind"):
                want_kind = str(filters["kind"])
                accessible = tuple(d for d in accessible if d.kind.value == want_kind)

            needle = query.strip().lower()
            passages: list[dict[str, Any]] = []
            for ds in accessible:
                text: str | None = None
                if store.has_blob(ds.checksum):
                    raw = store.read_bytes(ds.checksum)
                    if ds.kind in {DatasetKind.DOCUMENT, DatasetKind.DEFINITIONS}:
                        text = raw.decode("utf-8", errors="replace")
                elif base_dir is not None:
                    candidate = (base_dir / ds.source_uri).resolve()
                    try:
                        candidate.relative_to(base_dir.resolve())
                    except ValueError:
                        candidate = None  # type: ignore[assignment]
                    if (
                        candidate is not None
                        and candidate.is_file()
                        and ds.kind in {DatasetKind.DOCUMENT, DatasetKind.DEFINITIONS}
                    ):
                        raw = candidate.read_bytes()
                        if bytes_checksum(raw) == ds.checksum:
                            text = raw.decode("utf-8", errors="replace")

                if text is not None:
                    for line_no, line in enumerate(text.splitlines(), start=1):
                        if not needle or needle in line.lower():
                            passages.append(
                                {
                                    "dataset_id": ds.dataset_id,
                                    "version": ds.version,
                                    "checksum": ds.checksum,
                                    "allowed_use": ds.allowed_use.value,
                                    "locator": f"line {line_no}",
                                    "excerpt": line[:300],
                                    "synthetic": ds.synthetic,
                                }
                            )
                            if len(passages) >= 25:
                                break
                elif not needle or needle in ds.title.lower() or needle in ds.quality_note.lower():
                    passages.append(
                        {
                            "dataset_id": ds.dataset_id,
                            "version": ds.version,
                            "checksum": ds.checksum,
                            "allowed_use": ds.allowed_use.value,
                            "locator": ds.source_uri,
                            "excerpt": f"{ds.title}: {ds.quality_note}".strip(": "),
                            "synthetic": ds.synthetic,
                        }
                    )

            # Also expose matching scenario assumption locators when permitted.
            if len(accessible) == len(scen.datasets):
                for row in assumption_register(scen):
                    hay = f"{row.field_path} {row.locator or ''} {row.note}".lower()
                    if not needle or needle in hay:
                        passages.append(
                            {
                                "dataset_id": row.source_id or "scenario",
                                "version": rev.revision_id,
                                "allowed_use": "scenario",
                                "locator": row.locator or row.field_path,
                                "excerpt": (
                                    f"{row.field_path} = {row.value} {row.unit or ''}"
                                ).strip(),
                                "synthetic": scen.study.synthetic,
                            }
                        )

            return ToolResult(
                status="completed",
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                synthetic=scen.study.synthetic,
                data={
                    "query": query,
                    "accessible_dataset_ids": [d.dataset_id for d in accessible],
                    "passages": passages[:50],
                },
            )
        finally:
            store.close()

    # --- 3. validate_scenario ---------------------------------------------------------------

    def validate_stored_scenario(
        self,
        principal: AccessPrincipal,
        *,
        study_id: str | None = None,
        revision: str | None = None,
        scenario_data: Mapping[str, Any] | None = None,
    ) -> ToolResult:
        """Validate either a stored revision or an inline scenario payload (spec 16.2, 19.4)."""
        if scenario_data is not None:
            scen, report = parse_scenario(dict(scenario_data))
            if scen is not None:
                authorize_scenario(principal, scen)
            return ToolResult(
                status="completed" if report.executable else "invalid_request",
                failure_class=None if report.executable else FailureClass.INVALID_REQUEST,
                study_id=scen.study.study_id if scen else None,
                scenario_revision=scen.revision_id() if scen else None,
                input_hash=scen.content_hash() if scen else None,
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                warnings=tuple(i.message for i in report.issues if i.severity.value == "warning"),
                synthetic=report.contains_synthetic_values,
                data={"validation": report.to_json_dict(), "executable": report.executable},
            )

        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            report = validate_scenario(scen)
            return ToolResult(
                status="completed" if report.executable else "invalid_request",
                failure_class=None if report.executable else FailureClass.INVALID_REQUEST,
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                warnings=tuple(i.message for i in report.issues if i.severity.value == "warning"),
                synthetic=scen.study.synthetic or report.contains_synthetic_values,
                data={"validation": report.to_json_dict(), "executable": report.executable},
            )
        finally:
            store.close()

    # --- 4. create_scenario_variant ---------------------------------------------------------

    def create_scenario_variant(
        self,
        principal: AccessPrincipal,
        *,
        parent_revision: str,
        explicit_patch: Sequence[Mapping[str, Any]],
        rationale: str,
        author: str = "service",
        study_id: str | None = None,
    ) -> ToolResult:
        """Apply explicit field-level patches to a frozen revision and store a child revision."""
        if not explicit_patch:
            raise InvalidRequestError("explicit_patch must contain at least one operation")
        if not rationale.strip():
            raise InvalidRequestError("rationale must be non-empty")

        store = self._open_store()
        try:
            _, parent = self._resolve_scenario(
                store,
                principal,
                study_id=study_id,
                revision=parent_revision,
                enforce_all_datasets=True,
            )
            ops: list[PatchOperation] = []
            for raw_op in explicit_patch:
                op_name = str(raw_op.get("op", "set"))
                path = str(raw_op.get("path", "")).strip()
                if not path:
                    raise InvalidRequestError("each patch operation must specify 'path'")
                if op_name == "set":
                    ops.append(PatchOperation("set", path, raw_op.get("value")))
                elif op_name == "set_known":
                    evidence = {
                        "state": "known",
                        "value": raw_op.get("value"),
                        "provenance": {"provenance_type": "user_supplied", "author": author},
                    }
                    ops.append(PatchOperation("set", path, evidence))
                elif op_name == "remove":
                    ops.append(PatchOperation("remove", path))
                else:
                    raise InvalidRequestError(f"unsupported patch op {op_name!r}")

            patched_doc = apply_patch(parent.to_json_dict(), ops)
            child, report = parse_scenario(patched_doc)
            if child is None or not report.executable:
                return ToolResult(
                    status="invalid_request",
                    failure_class=FailureClass.INVALID_REQUEST,
                    study_id=parent.study.study_id,
                    scenario_revision=parent_revision,
                    message=f"variant validation failed ({len(report.errors)} error(s))",
                    missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                    data={"validation": report.to_json_dict()},
                )
            authorize_scenario(principal, child)
            rev = store.add_revision(
                child,
                author=author,
                rationale=rationale,
                parent_revision_id=parent_revision,
            )
            return ToolResult(
                status="completed",
                study_id=child.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=child.content_hash(),
                assumptions_used=tuple(a.to_json_dict() for a in assumption_register(child)),
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                synthetic=child.study.synthetic or report.contains_synthetic_values,
                data={
                    "revision": rev.to_json_dict(),
                    "parent_revision_id": rev.parent_revision_id,
                    "changes": [c.to_json_dict() for c in rev.changes],
                },
            )
        finally:
            store.close()

    # --- 5. screen_candidates ---------------------------------------------------------------

    def screen_candidates(
        self,
        principal: AccessPrincipal,
        *,
        study_id: str,
        revision: str | None = None,
        screening_policy_id: str | None = None,
    ) -> ToolResult:
        """Run deterministic site screening, corridor route generation, and candidate listing."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            policies = self._load_policies_dict(store, scen)
            if screening_policy_id is not None:
                matched = [
                    p
                    for p in load_policies(self.policy_directory)
                    if p.policy_id == screening_policy_id and p.kind is PolicyKind.SCREENING
                ]
                if not matched:
                    raise InvalidRequestError(f"unknown screening policy {screening_policy_id!r}")
                for k, v in list(policies.items()):
                    if v.kind is PolicyKind.SCREENING:
                        policies.pop(k)
                policies[matched[0].policy_id] = matched[0]

            upstream: dict[str, StageOutput] = {}
            for stg in (SiteEvidenceStage(), SourceEnvelopeStage(self._source_models)):
                out = stg.run(StageContext(scen, rev.revision_id, policies, 0, upstream))
                upstream[stg.name] = out
            scr_out = ScreeningStage().run(
                StageContext(scen, rev.revision_id, policies, 0, upstream)
            )
            upstream["screen"] = scr_out
            if scr_out.status is not StageStatus.COMPLETED:
                return ToolResult(
                    status=scr_out.status.value,
                    failure_class=FailureClass.INVALID_REQUEST
                    if scr_out.status is StageStatus.INVALID_INPUT
                    else None,
                    study_id=scen.study.study_id,
                    scenario_revision=rev.revision_id,
                    input_hash=scen.content_hash(),
                    message=scr_out.reason,
                    missing_inputs=tuple(m.to_json_dict() for m in scr_out.missing_inputs),
                    synthetic=scen.study.synthetic,
                    data={"screen": scr_out.payload},
                )
            rt_out = RouteStage().run(StageContext(scen, rev.revision_id, policies, 0, upstream))
            upstream["routes"] = rt_out
            cand_out = (
                CandidateStage().run(StageContext(scen, rev.revision_id, policies, 0, upstream))
                if rt_out.status is StageStatus.COMPLETED
                else StageOutput(
                    stage="candidates",
                    stage_version="1.0.0",
                    status=StageStatus.NOT_RUN,
                    reason=rt_out.reason,
                )
            )
            return ToolResult(
                status="completed",
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                model_versions={
                    "screen": ScreeningStage.version,
                    "routes": RouteStage.version,
                    "candidates": CandidateStage.version,
                },
                missing_inputs=tuple(
                    m.to_json_dict()
                    for m in (
                        *scr_out.missing_inputs,
                        *rt_out.missing_inputs,
                        *cand_out.missing_inputs,
                    )
                ),
                synthetic=scen.study.synthetic,
                data={
                    "sites": scr_out.payload.get("sites", {}),
                    "site_counts": scr_out.payload.get("counts", {}),
                    "routes": rt_out.payload.get("pairs", ()),
                    "candidates": cand_out.payload.get("candidates", ()),
                    "rejected": cand_out.payload.get("rejected", ()),
                },
            )
        finally:
            store.close()

    # --- 6. evaluate_source -----------------------------------------------------------------

    def evaluate_source(
        self,
        principal: AccessPrincipal,
        *,
        source_scenario_id: str,
        operating_point: Mapping[str, Any],
        sample_id: str = "deterministic",
        study_id: str | None = None,
        revision: str | None = None,
        seed: int = 0,
    ) -> ToolResult:
        """Evaluate a single source operating point through the SourceModel port (spec 16.2)."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            well_scen = next(
                (w for w in scen.well_scenarios if w.source_scenario_id == source_scenario_id),
                None,
            )
            if well_scen is None:
                raise InvalidRequestError(
                    f"source_scenario_id {source_scenario_id!r} not found in revision "
                    f"{rev.revision_id!r}"
                )
            policies = self._load_policies_dict(store, scen)
            src_pol = single_policy(policies, PolicyKind.SOURCE)
            if src_pol is None:
                return ToolResult(
                    status="not_established",
                    study_id=scen.study.study_id,
                    scenario_revision=rev.revision_id,
                    message="scenario references no policy of kind 'source'",
                    synthetic=scen.study.synthetic,
                )
            model = next(
                (
                    m
                    for m in self._source_models
                    if m.capabilities().supports(well_scen.model_family)
                ),
                None,
            )
            if model is None:
                return ToolResult(
                    status="unsupported_model",
                    study_id=scen.study.study_id,
                    scenario_revision=rev.revision_id,
                    message=f"no engine supports model family {well_scen.model_family!r}",
                    synthetic=scen.study.synthetic,
                )
            caps = model.capabilities()
            op = SourceOperatingPoint.model_validate(dict(operating_point))
            num = SourceNumericalSettings(
                max_iterations_count=int(src_pol.parameters["max_iterations_count"]),
                temperature_tolerance_delta_k=float(
                    src_pol.parameters["temperature_tolerance_delta_k"]
                ),
            )
            req = build_source_request(
                study_id=scen.study.study_id,
                well_scenario=well_scen,
                operating_point=op,
                numerical=num,
                capabilities=caps,
                sample_id=sample_id,
                seed=seed,
            )
            res = model.evaluate(req)
            fail_cls = classify_adapter_failure(res.state)
            vals: dict[str, Any] = {}
            if res.performance is not None:
                vals["geothermal_extracted_heat_w"] = {
                    "value": res.performance.geothermal_extracted_heat_w,
                    "unit": "W",
                }
                vals["wellhead_production_temperature_k"] = {
                    "value": res.performance.wellhead_production_temperature_k,
                    "unit": "K",
                }
                vals["electrical_pump_power_w"] = {
                    "value": res.performance.electrical_pump_power_w,
                    "unit": "W",
                }
                vals["mass_flow_kg_s"] = {
                    "value": res.performance.mass_flow_kg_s,
                    "unit": "kg/s",
                }
            return ToolResult(
                status=res.state.value,
                failure_class=fail_cls,
                message=res.reason,
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=res.input_hash,
                model_versions={caps.engine: caps.engine_version},
                values=vals,
                checks=tuple(c.to_json_dict() for c in res.checks),
                warnings=res.warnings,
                synthetic=scen.study.synthetic or res.synthetic,
                data={"source_result": res.to_json_dict()},
            )
        finally:
            store.close()

    # --- 7. evaluate_candidate --------------------------------------------------------------

    def evaluate_candidate(
        self,
        principal: AccessPrincipal,
        *,
        study_id: str,
        candidate_id: str,
        revision: str | None = None,
        case_set_id: str | None = None,
    ) -> ToolResult:
        """Evaluate all coupling cases of a single candidate on a frozen revision (spec 16.2)."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            policies = self._load_policies_dict(store, scen)
            numerical = single_policy(policies, PolicyKind.NUMERICAL)
            source_pol = single_policy(policies, PolicyKind.SOURCE)
            if numerical is None or source_pol is None:
                return ToolResult(
                    status="not_established",
                    study_id=scen.study.study_id,
                    scenario_revision=rev.revision_id,
                    message="scenario requires both 'source' and 'numerical' policies",
                    synthetic=scen.study.synthetic,
                )

            upstream: dict[str, StageOutput] = {}
            for stg in (SiteEvidenceStage(), SourceEnvelopeStage(self._source_models)):
                upstream[stg.name] = stg.run(
                    StageContext(scen, rev.revision_id, policies, 0, upstream)
                )
            upstream["screen"] = ScreeningStage().run(
                StageContext(scen, rev.revision_id, policies, 0, upstream)
            )
            if upstream["screen"].status is not StageStatus.COMPLETED:
                raise InvalidRequestError(f"screening failed: {upstream['screen'].reason}")
            upstream["routes"] = RouteStage().run(
                StageContext(scen, rev.revision_id, policies, 0, upstream)
            )
            if upstream["routes"].status is not StageStatus.COMPLETED:
                raise InvalidRequestError(f"routes failed: {upstream['routes'].reason}")
            cand_out = CandidateStage().run(
                StageContext(scen, rev.revision_id, policies, 0, upstream)
            )
            cand_raw = next(
                (
                    c
                    for c in cand_out.payload.get("candidates", ())
                    if c.get("candidate_id") == candidate_id
                ),
                None,
            )
            if cand_raw is None:
                raise NotFoundError(f"candidate {candidate_id}")
            cand = Candidate.model_validate(cand_raw)
            cases_by_id = {
                c["coupling_case_id"]: c for c in cand_out.payload.get("coupling_cases", ())
            }
            target_case_ids = list(cand.coupling_case_ids) + list(cand.capacity_coupling_case_ids)
            if case_set_id is not None:
                target_case_ids = [
                    cid
                    for cid in target_case_ids
                    if cases_by_id[cid].get("operating_case_id") == case_set_id
                    or cid == case_set_id
                ]
                if not target_case_ids:
                    raise InvalidRequestError(
                        f"case_set_id {case_set_id!r} matches no case for {candidate_id!r}"
                    )

            from r3chain.domain.records.network import Route
            from r3chain.domain.records.plant import CouplingCase

            routes_map = {
                rid: Route.model_validate(rraw)
                for rid, rraw in cand_out.payload.get("routes", {}).items()
            }
            memo = MemoNetworkModel(self._network_model)
            outcomes: list[CaseOutcome] = []
            for cid in target_case_ids:
                cc = CouplingCase.model_validate(cases_by_id[cid])
                outcomes.append(
                    evaluate_case(
                        scen,
                        cc,
                        source_models=self._source_models,
                        network_model=memo,
                        source_policy=source_pol,
                        numerical_policy=numerical,
                        routes=routes_map,
                    )
                )

            checks: list[dict[str, Any]] = []
            missing: list[dict[str, Any]] = []
            summaries: dict[str, Any] = {}
            synthetic = scen.study.synthetic
            statuses: set[StageStatus] = set()
            for oc in outcomes:
                statuses.add(oc.status)
                summaries[oc.coupling_case_id] = coupling_case_summary(oc)
                missing.extend(m.to_json_dict() for m in oc.missing_inputs)
                if oc.result is not None:
                    synthetic = synthetic or oc.result.synthetic
                    checks.extend(c.to_json_dict() for c in oc.result.checks)

            fail_cls: FailureClass | None = None
            overall = "completed"
            if StageStatus.TOOL_FAILURE in statuses:
                overall = StageStatus.TOOL_FAILURE.value
                fail_cls = FailureClass.SERVICE_FAULT
            elif StageStatus.NON_CONVERGED in statuses:
                overall = StageStatus.NON_CONVERGED.value
                fail_cls = FailureClass.NUMERICAL_NON_CONVERGENCE
            elif StageStatus.PHYSICAL_LIMIT_EXCEEDED in statuses:
                overall = StageStatus.PHYSICAL_LIMIT_EXCEEDED.value
                fail_cls = FailureClass.PHYSICAL_CHECK_FAILED
            elif StageStatus.NOT_ESTABLISHED in statuses:
                overall = StageStatus.NOT_ESTABLISHED.value

            return ToolResult(
                status=overall,
                failure_class=fail_cls,
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                model_versions={
                    "network": self._network_model.capabilities().engine_version,
                },
                checks=tuple(checks),
                missing_inputs=tuple(missing),
                synthetic=synthetic,
                data={
                    "candidate_id": candidate_id,
                    "candidate": cand.to_json_dict(),
                    "cases": summaries,
                },
            )
        finally:
            store.close()

    # --- 8. evaluate_study ------------------------------------------------------------------

    def evaluate_study(
        self,
        principal: AccessPrincipal,
        *,
        study_id: str,
        revision: str | None = None,
        candidate_set_id: str | None = None,
        uncertainty_policy_id: str | None = None,
        policies_dir: Path | None = None,
        seed: int = 0,
        use_cache: bool = True,
        wait: bool = False,
    ) -> ToolResult:
        """Start (or run synchronously with wait=True) a full frozen evaluation job (spec 16.2)."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            pdir = self.policy_directory if policies_dir is None else policies_dir
            if pdir.exists():
                for pol in load_policies(pdir):
                    store.register_policy(pol)

            if uncertainty_policy_id is not None:
                active_u = next(
                    (
                        ref
                        for ref in scen.policies
                        if store.get_policy(ref.policy_id, ref.version).kind
                        is PolicyKind.UNCERTAINTY
                    ),
                    None,
                )
                if active_u is None or active_u.policy_id != uncertainty_policy_id:
                    found_u = [
                        p
                        for p in load_policies(pdir)
                        if p.policy_id == uncertainty_policy_id and p.kind is PolicyKind.UNCERTAINTY
                    ]
                    if not found_u:
                        raise InvalidRequestError(
                            f"unknown uncertainty policy {uncertainty_policy_id!r}"
                        )
                    target_pol = sorted(found_u, key=lambda p: p.version)[-1]
                    store.register_policy(target_pol)
                    new_refs = [
                        ref.to_json_dict()
                        for ref in scen.policies
                        if store.get_policy(ref.policy_id, ref.version).kind
                        is not PolicyKind.UNCERTAINTY
                    ]
                    new_refs.append(policy_ref(target_pol).to_json_dict())
                    patched = apply_patch(
                        scen.to_json_dict(), [PatchOperation("set", "policies", new_refs)]
                    )
                    new_scen, rep = parse_scenario(patched)
                    if new_scen is None or not rep.executable:
                        raise InvalidRequestError("could not switch uncertainty policy")
                    scen = new_scen
                    rev = store.add_revision(
                        scen,
                        author=principal.principal_id,
                        rationale=f"select uncertainty policy {uncertainty_policy_id}",
                        parent_revision_id=rev.revision_id,
                    )

            job = self._jobs.submit_run_job(
                store,
                study_id=scen.study.study_id,
                project_id=scen.study.project_id,
                revision_id=rev.revision_id,
                principal_id=principal.principal_id,
                access_scope=principal.cache_scope(),
                seed=seed,
                use_cache=use_cache,
                wait=wait,
            )
            run_data: dict[str, Any] = {"job": job.to_json_dict()}
            if candidate_set_id is not None:
                run_data["candidate_set_id"] = candidate_set_id
            run_obj = store.get_run(job.run_id) if job.run_id else None
            study_res = (
                next(
                    (r for r in store.get_results(job.run_id) if r.subject == "study"),
                    None,
                )
                if job.run_id
                else None
            )
            if run_obj is not None:
                run_data["run"] = run_obj.to_json_dict()
            if study_res is not None:
                run_data["study_result"] = study_res.to_json_dict()
            return ToolResult(
                status=job.status.value,
                failure_class=job.failure_class,
                message=job.error_message,
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                run_id=job.run_id,
                job_id=job.job_id,
                input_hash=scen.content_hash(),
                model_versions=run_obj.engine_versions if run_obj else {},
                synthetic=study_res.synthetic if study_res else scen.study.synthetic,
                data=run_data,
            )
        finally:
            store.close()

    # --- 9. get_job_status & cancel_job -----------------------------------------------------

    def get_job_status(
        self,
        principal: AccessPrincipal,
        *,
        job_id: str,
        project_id: str | None = None,
    ) -> ToolResult:
        """Authorise caller and return live job progress, stage, cancellation state, and refs."""
        store = self._open_store()
        try:
            job = self._jobs.get_job(store, job_id)
            if project_id is not None and job.project_id != project_id:
                raise NotFoundError(f"job {job_id} in project {project_id}")
            authorize_job(principal, store, job)
            return ToolResult(
                status=job.status.value,
                failure_class=job.failure_class,
                message=job.error_message,
                study_id=job.study_id,
                scenario_revision=job.scenario_revision,
                run_id=job.run_id,
                job_id=job.job_id,
                values={"progress_fraction": {"value": job.progress_fraction, "unit": "1"}},
                data={"job": job.to_json_dict(), "result_ids": list(job.result_ids)},
            )
        finally:
            store.close()

    def cancel_job(
        self,
        principal: AccessPrincipal,
        *,
        job_id: str,
        wait: bool = False,
    ) -> ToolResult:
        """Authorise caller and request cooperative cancellation of a running/queued job."""
        store = self._open_store()
        try:
            job = self._jobs.get_job(store, job_id)
            authorize_job(principal, store, job)
            updated = self._jobs.cancel_job(store, job_id)
            if wait:
                updated = self._jobs.wait_for_job(store, job_id, timeout_s=60.0)
            return ToolResult(
                status=updated.status.value,
                failure_class=updated.failure_class,
                study_id=updated.study_id,
                scenario_revision=updated.scenario_revision,
                run_id=updated.run_id,
                job_id=updated.job_id,
                values={"progress_fraction": {"value": updated.progress_fraction, "unit": "1"}},
                data={"job": updated.to_json_dict()},
            )
        finally:
            store.close()

    # --- 10. get_run_evidence ---------------------------------------------------------------

    def get_run_evidence(
        self,
        principal: AccessPrincipal,
        *,
        run_id: str,
        project_id: str | None = None,
        lang: Language = "en",
    ) -> ToolResult:
        """Retrieve authorised run evidence, stage outputs, and verified explanations (A16, V05)."""
        store = self._open_store()
        try:
            scenario = authorize_run(principal, store, run_id)
            if project_id is not None and scenario.study.project_id != project_id:
                raise NotFoundError(f"run {run_id} in project {project_id}")
            run = store.get_run(run_id)
            results = store.get_results(run_id)
            study_res = next(r for r in results if r.subject == "study")
            explanations = explain_study(store, run_id)
            unverified = [e.explanation_id for e in explanations if not e.all_claims_accepted]
            if unverified:
                raise ServiceFaultError(
                    f"claim check rejected generated explanation(s): {unverified}"
                )
            rendered = {
                rec.explanation_id: render_explanation(rec, lang=lang) for rec in explanations
            }
            return ToolResult(
                status="completed" if run.status is RunStatus.COMPLETED else run.status.value,
                failure_class=classify_result_failure(study_res),
                study_id=scenario.study.study_id,
                scenario_revision=run.scenario_revision,
                run_id=run.run_id,
                input_hash=run.result_hash,
                model_versions=run.engine_versions,
                assumptions_used=tuple(a.to_json_dict() for a in assumption_register(scenario)),
                missing_inputs=tuple(
                    m.to_json_dict() for out in study_res.stage_outputs for m in out.missing_inputs
                ),
                artifact_refs=study_res.artifact_refs,
                synthetic=study_res.synthetic,
                explanation={
                    "records": [e.to_json_dict() for e in explanations],
                    "rendered": rendered,
                    "lang": lang,
                },
                data={
                    "run": run.to_json_dict(),
                    "results": [r.to_json_dict() for r in results],
                    "datasets": [
                        d.to_json_dict() for d in filter_accessible_datasets(principal, scenario)
                    ],
                },
            )
        finally:
            store.close()

    # --- 11. compare_results ----------------------------------------------------------------

    def compare_results(
        self,
        principal: AccessPrincipal,
        *,
        result_ids: Sequence[str],
        metric_policy_id: str | None = None,
        lang: Language = "en",
    ) -> ToolResult:
        """Compare authorised results across metrics/boundaries and build explanations."""
        if not result_ids:
            raise InvalidRequestError("result_ids must contain at least one result identifier")

        store = self._open_store()
        try:
            loaded_results: list[Result] = [store.get_result(rid) for rid in result_ids]
            scenarios_by_run: dict[str, Scenario] = {}
            for res in loaded_results:
                if res.run_id not in scenarios_by_run:
                    scenarios_by_run[res.run_id] = authorize_run(principal, store, res.run_id)

            synthetic_flags = {r.synthetic for r in loaded_results}
            pilot_studies = {not s.study.synthetic for s in scenarios_by_run.values()}
            if len(synthetic_flags) > 1 or (True in synthetic_flags and True in pilot_studies):
                raise InvalidRequestError(
                    "boundary mismatch: synthetic engine results cannot be compared alongside "
                    "non-synthetic pilot results (W12)"
                )

            metric_id = "lcoh_geothermal_delivered_currency_per_j"
            if metric_policy_id is not None:
                found = [
                    p
                    for p in load_policies(self.policy_directory)
                    if p.policy_id == metric_policy_id
                ]
                if not found:
                    raise InvalidRequestError(f"unknown policy {metric_policy_id!r}")
                metric_id = str(found[0].parameters.get("decision_metric", metric_id))

            units: set[str] = set()
            boundaries: set[str] = set()
            rows: list[dict[str, Any]] = []
            exclusions: list[dict[str, Any]] = []
            for res in loaded_results:
                kpi = next((k for k in res.kpis if k.definition.kpi_id == metric_id), None)
                if kpi is not None:
                    units.add(kpi.definition.unit)
                    boundaries.add(kpi.definition.boundary)
                entry = {
                    "result_id": res.result_id,
                    "run_id": res.run_id,
                    "scenario_revision": res.scenario_revision,
                    "subject": res.subject,
                    "technical_status": res.technical_status.value,
                    "evaluation_status": res.evaluation_status.value,
                    "eligible": res.ranking_eligibility.eligible,
                    "conditional": res.ranking_eligibility.conditional,
                    "reasons": list(res.ranking_eligibility.reasons),
                    "metric_id": metric_id,
                    "metric_value": kpi.value if kpi else None,
                    "metric_unit": kpi.definition.unit if kpi else None,
                    "metric_boundary": kpi.definition.boundary if kpi else None,
                    "synthetic": res.synthetic,
                }
                if res.ranking_eligibility.eligible:
                    rows.append(entry)
                else:
                    exclusions.append(entry)

            if len(units) > 1 or len(boundaries) > 1:
                raise InvalidRequestError(
                    f"comparable KPI boundary/unit mismatch across results: "
                    f"boundaries={sorted(boundaries)}, units={sorted(units)}"
                )

            rows.sort(
                key=lambda item: (
                    item["metric_value"] is None,
                    float(item["metric_value"] or 0.0),
                    item["subject"],
                )
            )
            first_run_id = loaded_results[0].run_id
            comp_exp: ExplanationRecord | None = None
            same_run_cands = [
                r.subject
                for r in loaded_results
                if r.run_id == first_run_id and r.subject != "study"
            ]
            if same_run_cands and all(r.run_id == first_run_id for r in loaded_results):
                comp_exp = explain_comparison(store, first_run_id, same_run_cands)

            first_scen = scenarios_by_run[first_run_id]
            return ToolResult(
                status="completed",
                study_id=first_scen.study.study_id,
                scenario_revision=loaded_results[0].scenario_revision,
                run_id=first_run_id,
                synthetic=any(r.synthetic for r in loaded_results),
                explanation=(
                    {
                        "record": comp_exp.to_json_dict(),
                        "rendered": render_explanation(comp_exp, lang=lang),
                        "lang": lang,
                    }
                    if comp_exp is not None
                    else None
                ),
                data={
                    "metric_id": metric_id,
                    "eligible": rows,
                    "excluded": exclusions,
                    "boundary": next(iter(boundaries), None),
                    "unit": next(iter(units), None),
                },
            )
        finally:
            store.close()

    # --- 12. export_study_report ------------------------------------------------------------

    def export_study_report(
        self,
        principal: AccessPrincipal,
        *,
        run_id: str | None = None,
        result_ids: Sequence[str] = (),
        study_id: str | None = None,
        revision: str | None = None,
        fmt: str = "markdown",
        lang: Language = "en",
        output_path: Path | None = None,
    ) -> ToolResult:
        """Build, verify, store, and export an authorised evidence report (A16, A17)."""
        if fmt not in {"markdown", "json"}:
            raise InvalidRequestError(f"unsupported report format {fmt!r}")
        store = self._open_store()
        try:
            target_run_id = run_id
            if target_run_id is None and result_ids:
                first_res = store.get_result(result_ids[0])
                for rid in result_ids[1:]:
                    other = store.get_result(rid)
                    if other.run_id != first_res.run_id:
                        authorize_run(principal, store, other.run_id)
                target_run_id = first_res.run_id
            if target_run_id is None:
                raise InvalidRequestError("specify run_id or result_ids to export")

            scenario = authorize_run(principal, store, target_run_id)
            run = store.get_run(target_run_id)
            if study_id is not None and scenario.study.study_id != study_id:
                raise InvalidRequestError(
                    f"run {target_run_id!r} belongs to study {scenario.study.study_id!r}"
                )
            if revision is not None and run.scenario_revision != revision:
                raise InvalidRequestError(
                    f"run {target_run_id!r} belongs to revision {run.scenario_revision!r}"
                )

            explanations = explain_study(store, target_run_id)
            report: EvidenceReport = build_evidence_report(
                store,
                target_run_id,
                explanations=explanations,
            )
            rendered_text = (
                report.model_dump_json(indent=2)
                if fmt == "json"
                else render_markdown(report, lang=lang)
            )
            checksum = store.put_bytes(rendered_text.encode("utf-8"))
            if output_path is not None:
                output_path.write_text(rendered_text, encoding="utf-8")
            return ToolResult(
                status="completed",
                study_id=scenario.study.study_id,
                scenario_revision=run.scenario_revision,
                run_id=run.run_id,
                input_hash=run.result_hash,
                model_versions=run.engine_versions,
                artifact_refs=(checksum,),
                synthetic=report.synthetic,
                data={
                    "format": fmt,
                    "lang": lang,
                    "content": rendered_text,
                    "report": report.to_json_dict(),
                },
            )
        finally:
            store.close()

    # --- Replay / explain helpers -----------------------------------------------------------

    def replay_run(self, principal: AccessPrincipal, run_id: str) -> ToolResult:
        """Authorise caller and replay a stored run cleanly from its frozen revision (A17)."""
        store = self._open_store()
        try:
            scenario = authorize_run(principal, store, run_id)
            runner = PipelineRunner(
                store,
                self._stages_factory(),
                clock=self._clock,
                code_version_text=self._code_version,
                access_context=principal.cache_scope(),
            )
            outcome = runner.replay(run_id)
            replayed = store.get_run(outcome.replay_run_id)
            return ToolResult(
                status="completed" if outcome.identical else "service_fault",
                failure_class=None if outcome.identical else FailureClass.SERVICE_FAULT,
                study_id=scenario.study.study_id,
                scenario_revision=replayed.scenario_revision,
                run_id=replayed.run_id,
                input_hash=replayed.result_hash,
                model_versions=replayed.engine_versions,
                synthetic=scenario.study.synthetic,
                data={"replay": outcome.to_json_dict()},
            )
        finally:
            store.close()

    def explain_run_target(
        self,
        principal: AccessPrincipal,
        *,
        run_id: str,
        target: str = "study",
        kind: str = "result",
        lang: Language = "en",
    ) -> ToolResult:
        """Build, verify, and render a specific explanation record for ``run_id``."""
        store = self._open_store()
        try:
            scenario = authorize_run(principal, store, run_id)
            if kind == "rejection":
                rec = explain_rejection(store, run_id, target)
            elif kind == "comparison":
                rec = explain_comparison(store, run_id)
            else:
                rec = explain_result(store, run_id, target)
            verified = check_explanation(rec, store)
            if not verified.all_claims_accepted:
                raise ServiceFaultError("claim check failed for generated explanation")
            return ToolResult(
                status="completed",
                study_id=scenario.study.study_id,
                scenario_revision=verified.scenario_revision,
                run_id=verified.run_id,
                synthetic=verified.synthetic,
                explanation={
                    "record": verified.to_json_dict(),
                    "rendered": render_explanation(verified, lang=lang),
                    "lang": lang,
                },
            )
        finally:
            store.close()

    # --- Workspace UI support queries (Step 6.2) --------------------------------------------

    def list_studies(self, principal: AccessPrincipal) -> ToolResult:
        """List studies in the store that ``principal`` is authorised to inspect."""
        store = self._open_store()
        try:
            latest_by_study: dict[str, tuple[ScenarioRevision, Scenario]] = {}
            for rev in store.list_all_revisions():
                scen = store.get_scenario(rev.revision_id)
                latest_by_study[scen.study.study_id] = (rev, scen)
            items: list[dict[str, Any]] = []
            for study_id, (rev, scen) in latest_by_study.items():
                if (
                    "*" not in principal.allowed_projects
                    and scen.study.project_id not in principal.allowed_projects
                ):
                    continue
                if scen.study.synthetic and not principal.allow_synthetic:
                    continue
                if not all(
                    can_access_dataset(principal, ds, scen.study.project_id) for ds in scen.datasets
                ):
                    continue
                items.append(
                    {
                        "study_id": study_id,
                        "project_id": scen.study.project_id,
                        "title": scen.study.title,
                        "objective": scen.study.objective,
                        "baseline_id": scen.study.baseline_id,
                        "planning_date": scen.study.planning_date.isoformat(),
                        "crs": scen.study.study_area.crs,
                        "synthetic": scen.study.synthetic,
                        "latest_revision": rev.revision_id,
                        "runs_count": len(store.list_runs(rev.revision_id)),
                    }
                )
            return ToolResult(
                status="completed",
                synthetic=any(bool(item["synthetic"]) for item in items),
                data={"studies": items},
            )
        finally:
            store.close()

    def list_revisions(self, principal: AccessPrincipal, study_id: str) -> ToolResult:
        """List chronological scenario revisions and attached runs for an authorised study."""
        store = self._open_store()
        try:
            latest_rev, latest_scen = self._resolve_scenario(
                store, principal, study_id=study_id, enforce_all_datasets=True
            )
            matching: list[dict[str, Any]] = []
            for rev in store.list_all_revisions():
                scen = store.get_scenario(rev.revision_id)
                if scen.study.study_id != latest_scen.study.study_id:
                    continue
                authorize_scenario(principal, scen)
                runs = [
                    {
                        "run_id": r.run_id,
                        "status": r.status.value,
                        "created_at": r.created_at.isoformat(),
                        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                        "result_hash": r.result_hash,
                        "is_stale": rev.revision_id != latest_rev.revision_id,
                        "seed": r.seed,
                    }
                    for r in store.list_runs(rev.revision_id)
                ]
                matching.append(
                    {
                        **rev.to_json_dict(),
                        "is_latest": rev.revision_id == latest_rev.revision_id,
                        "runs": runs,
                    }
                )
            return ToolResult(
                status="completed",
                study_id=latest_scen.study.study_id,
                scenario_revision=latest_rev.revision_id,
                synthetic=latest_scen.study.synthetic,
                data={
                    "latest_revision": latest_rev.revision_id,
                    "revisions": matching,
                },
            )
        finally:
            store.close()

    def list_jobs(self, principal: AccessPrincipal, *, study_id: str | None = None) -> ToolResult:
        """List background jobs that ``principal`` is authorised to inspect."""
        store = self._open_store()
        try:
            visible: list[dict[str, Any]] = []
            for job in self._jobs.list_jobs(store, study_id=study_id):
                try:
                    authorize_job(principal, store, job)
                except PermissionError:
                    continue
                visible.append(job.to_json_dict())
            return ToolResult(status="completed", study_id=study_id, data={"jobs": visible})
        finally:
            store.close()

    def get_scenario_editor_bundle(
        self,
        principal: AccessPrincipal,
        study_id: str,
        *,
        revision: str | None = None,
    ) -> ToolResult:
        """Return authorised scenario JSON, assumptions, schema, and policy choices for editing."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            assumptions = assumption_register(scen)
            report = validate_scenario(scen)
            policies_out: list[dict[str, Any]] = []
            if self.policy_directory.exists():
                for pol in load_policies(self.policy_directory):
                    store.register_policy(pol)
                    policies_out.append(
                        {
                            "policy_id": pol.policy_id,
                            "version": pol.version,
                            "kind": pol.kind.value,
                            "status": pol.status.value,
                            "policy_hash": policy_ref(pol).policy_hash,
                            "parameters": pol.parameters,
                        }
                    )
            return ToolResult(
                status="completed" if report.executable else "invalid_request",
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                input_hash=scen.content_hash(),
                assumptions_used=tuple(a.to_json_dict() for a in assumptions),
                missing_inputs=tuple(m.to_json_dict() for m in report.missing_inputs),
                warnings=tuple(i.message for i in report.issues if i.severity.value == "warning"),
                synthetic=scen.study.synthetic or report.contains_synthetic_values,
                data={
                    "revision": rev.to_json_dict(),
                    "scenario": scen.to_json_dict(),
                    "validation": report.to_json_dict(),
                    "schema": Scenario.model_json_schema(),
                    "available_policies": policies_out,
                },
            )
        finally:
            store.close()

    def get_study_map(
        self,
        principal: AccessPrincipal,
        study_id: str,
        *,
        revision: str | None = None,
        run_id: str | None = None,
    ) -> ToolResult:
        """Build WGS84 GeoJSON map collections and text alternatives filtered by caller scope."""
        store = self._open_store()
        try:
            rev, scen = self._resolve_scenario(
                store, principal, study_id=study_id, revision=revision, enforce_all_datasets=True
            )
            outputs: dict[str, dict[str, Any]] = {}
            resolved_run_id = run_id
            if resolved_run_id is not None:
                run_scen = authorize_run(principal, store, resolved_run_id)
                if run_scen.study.study_id != scen.study.study_id:
                    raise InvalidRequestError(
                        f"run {resolved_run_id!r} belongs to study {run_scen.study.study_id!r}"
                    )
                study_res = next(
                    (r for r in store.get_results(resolved_run_id) if r.subject == "study"),
                    None,
                )
                if study_res is not None:
                    outputs = {o.stage: o.payload for o in study_res.stage_outputs}
            else:
                runs = [
                    r for r in store.list_runs(rev.revision_id) if r.status is RunStatus.COMPLETED
                ]
                if runs:
                    latest_run = runs[-1]
                    try:
                        authorize_run(principal, store, latest_run.run_id)
                        study_res = next(
                            (
                                r
                                for r in store.get_results(latest_run.run_id)
                                if r.subject == "study"
                            ),
                            None,
                        )
                        if study_res is not None:
                            outputs = {o.stage: o.payload for o in study_res.stage_outputs}
                            resolved_run_id = latest_run.run_id
                    except PermissionError:
                        outputs = {}

            accessible_ids = frozenset(
                ds.dataset_id for ds in filter_accessible_datasets(principal, scen)
            )
            map_bundle = build_map_layers(scen, outputs, allowed_dataset_ids=accessible_ids)
            return ToolResult(
                status="completed",
                study_id=scen.study.study_id,
                scenario_revision=rev.revision_id,
                run_id=resolved_run_id,
                input_hash=scen.content_hash(),
                synthetic=scen.study.synthetic,
                data=map_bundle,
            )
        finally:
            store.close()
