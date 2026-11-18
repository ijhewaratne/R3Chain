"""FastAPI application exposing R3CHAIN services over HTTP (specification section 16.4)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from r3chain.explain.wording import Language
from r3chain.schemas import VERSIONED_RECORDS
from r3chain.services.access import AccessDeniedError, AccessPrincipal
from r3chain.services.contracts import FailureClass, ToolResult
from r3chain.services.planning import InvalidRequestError, PlanningService, ServiceFaultError
from r3chain.store.store import NotFoundError


class CreateStudyRequest(BaseModel):
    scenario: dict[str, Any]
    author: str = "api"
    rationale: str = "register study"


class CreateRevisionRequest(BaseModel):
    parent_revision: str
    explicit_patch: list[dict[str, Any]] = Field(min_length=1)
    rationale: str
    author: str = "api"


class EvaluateStudyRequest(BaseModel):
    revision: str | None = None
    candidate_set_id: str | None = None
    uncertainty_policy_id: str | None = None
    seed: int = 0
    use_cache: bool = True
    wait: bool = False


class SearchEvidenceRequest(BaseModel):
    query: str = ""
    revision: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ValidateScenarioRequest(BaseModel):
    scenario: dict[str, Any] | None = None
    study_id: str | None = None
    revision: str | None = None


class ScreenRequest(BaseModel):
    revision: str | None = None
    screening_policy_id: str | None = None


class SourceEvaluateRequest(BaseModel):
    source_scenario_id: str
    operating_point: dict[str, Any]
    sample_id: str = "deterministic"
    study_id: str | None = None
    revision: str | None = None
    seed: int = 0


class CandidateEvaluateRequest(BaseModel):
    revision: str | None = None
    case_set_id: str | None = None


class CompareRequest(BaseModel):
    result_ids: list[str] = Field(min_length=1)
    metric_policy_id: str | None = None
    lang: Language = "en"


class ExportRequest(BaseModel):
    run_id: str | None = None
    result_ids: list[str] = Field(default_factory=list)
    study_id: str | None = None
    revision: str | None = None
    format: Literal["markdown", "json"] = "markdown"
    lang: Language = "en"


def _parse_bool_header(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes"}


def _parse_csv_header(raw: str | None, *, default: tuple[str, ...]) -> tuple[str, ...]:
    if raw is None:
        return default
    parts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return parts or ()


def default_principal_resolver(
    x_r3chain_principal: Annotated[str | None, Header()] = None,
    x_r3chain_projects: Annotated[str | None, Header()] = None,
    x_r3chain_groups: Annotated[str | None, Header()] = None,
    x_r3chain_allow_restricted: Annotated[str | None, Header()] = None,
    x_r3chain_allow_synthetic: Annotated[str | None, Header()] = None,
) -> AccessPrincipal:
    """Resolve caller scope from request headers without allowing restricted escalation."""
    if (
        x_r3chain_principal is None
        and x_r3chain_projects is None
        and x_r3chain_groups is None
        and x_r3chain_allow_restricted is None
        and x_r3chain_allow_synthetic is None
    ):
        return AccessPrincipal.local_default()
    return AccessPrincipal(
        principal_id=(x_r3chain_principal or "http-caller").strip() or "http-caller",
        allowed_projects=_parse_csv_header(x_r3chain_projects, default=("*",)),
        access_groups=_parse_csv_header(x_r3chain_groups, default=("public", "synthetic")),
        allow_restricted=_parse_bool_header(x_r3chain_allow_restricted, default=False),
        allow_synthetic=_parse_bool_header(x_r3chain_allow_synthetic, default=True),
    )


PrincipalDep = Annotated[AccessPrincipal, Depends(default_principal_resolver)]


def _error_response(
    status_code: int, status_text: str, failure_class: FailureClass, message: str
) -> JSONResponse:
    envelope = ToolResult(
        status=status_text,
        failure_class=failure_class,
        message=message,
    )
    return JSONResponse(status_code=status_code, content=envelope.to_json_dict())


def _default_frontend_dir() -> Path | None:
    candidate = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    return candidate if (candidate / "index.html").is_file() else None


def create_app(
    store_root: Path,
    *,
    policy_directory: Path = Path("policies"),
    service: PlanningService | None = None,
    principal_dependency: Callable[..., AccessPrincipal] | None = None,
    frontend_dir: Path | None = None,
) -> FastAPI:
    """Create the FastAPI application backed by :class:`PlanningService`."""
    svc = service or PlanningService(store_root, policy_directory=policy_directory)
    owns_service = service is None

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owns_service:
                svc.close()

    app = FastAPI(
        title="R3CHAIN Geothermal Planning API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:4173",
            "http://localhost:4173",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if principal_dependency is not None:
        app.dependency_overrides[default_principal_resolver] = principal_dependency

    @app.exception_handler(RequestValidationError)
    async def _handle_req_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(422, "invalid_request", FailureClass.INVALID_REQUEST, str(exc))

    @app.exception_handler(AccessDeniedError)
    async def _handle_denied(_: Request, exc: AccessDeniedError) -> JSONResponse:
        return _error_response(403, "denied_access", FailureClass.DENIED_ACCESS, str(exc))

    @app.exception_handler(PermissionError)
    async def _handle_perm(_: Request, exc: PermissionError) -> JSONResponse:
        return _error_response(403, "denied_access", FailureClass.DENIED_ACCESS, str(exc))

    @app.exception_handler(NotFoundError)
    async def _handle_not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error_response(404, "invalid_request", FailureClass.INVALID_REQUEST, str(exc))

    @app.exception_handler(InvalidRequestError)
    async def _handle_invalid(_: Request, exc: InvalidRequestError) -> JSONResponse:
        return _error_response(422, "invalid_request", FailureClass.INVALID_REQUEST, str(exc))

    @app.exception_handler(ValidationError)
    async def _handle_pydantic(_: Request, exc: ValidationError) -> JSONResponse:
        return _error_response(422, "invalid_request", FailureClass.INVALID_REQUEST, str(exc))

    @app.exception_handler(ValueError)
    async def _handle_value(_: Request, exc: ValueError) -> JSONResponse:
        return _error_response(422, "invalid_request", FailureClass.INVALID_REQUEST, str(exc))

    @app.exception_handler(ServiceFaultError)
    async def _handle_fault(_: Request, exc: ServiceFaultError) -> JSONResponse:
        return _error_response(500, "service_fault", FailureClass.SERVICE_FAULT, str(exc))

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        return _error_response(
            500, "service_fault", FailureClass.SERVICE_FAULT, f"{type(exc).__name__}: {exc}"
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "r3chain-api"}

    @app.get("/schemas/{record_name}")
    def get_record_schema(record_name: str) -> dict[str, Any]:
        matches = [rec for rec in VERSIONED_RECORDS if record_name == rec.SCHEMA_NAME]
        if not matches:
            raise NotFoundError(f"schema {record_name}")
        latest = matches[-1]
        return {
            "schema_name": latest.SCHEMA_NAME,
            "schema_version": latest.current_schema_version(),
            "schema": latest.model_json_schema(),
        }

    @app.get("/studies")
    def list_studies(principal: PrincipalDep) -> dict[str, Any]:
        return svc.list_studies(principal).to_json_dict()

    @app.get("/jobs")
    def list_jobs(
        principal: PrincipalDep,
        study_id: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        return svc.list_jobs(principal, study_id=study_id).to_json_dict()

    @app.post("/validations")
    def validate_payload(
        body: ValidateScenarioRequest,
        principal: PrincipalDep,
    ) -> JSONResponse:
        res = svc.validate_stored_scenario(
            principal,
            study_id=body.study_id,
            revision=body.revision,
            scenario_data=body.scenario,
        )
        code = 200 if res.failure_class is None else 422
        return JSONResponse(status_code=code, content=res.to_json_dict())

    @app.post("/studies")
    def create_study(
        body: CreateStudyRequest,
        principal: PrincipalDep,
    ) -> JSONResponse:
        res = svc.register_study_document(
            principal,
            body.scenario,
            author=body.author,
            rationale=body.rationale,
        )
        code = 201 if res.failure_class is None else 422
        return JSONResponse(status_code=code, content=res.to_json_dict())

    @app.get("/studies/{study_id}")
    def get_study(
        study_id: str,
        principal: PrincipalDep,
        revision: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        return svc.get_study_context(principal, study_id, revision=revision).to_json_dict()

    @app.get("/studies/{study_id}/assumptions")
    def get_assumptions(
        study_id: str,
        principal: PrincipalDep,
        revision: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        ctx = svc.get_study_context(principal, study_id, revision=revision)
        return {
            "study_id": ctx.study_id,
            "scenario_revision": ctx.scenario_revision,
            "assumptions": list(ctx.assumptions_used),
            "missing_inputs": list(ctx.missing_inputs),
        }

    @app.post("/studies/{study_id}/evidence/search")
    def search_evidence(
        study_id: str,
        body: SearchEvidenceRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.search_project_evidence(
            principal,
            study_id,
            body.query,
            revision=body.revision,
            filters=body.filters,
        ).to_json_dict()

    @app.post("/studies/{study_id}/revisions")
    def create_revision(
        study_id: str,
        body: CreateRevisionRequest,
        principal: PrincipalDep,
    ) -> JSONResponse:
        res = svc.create_scenario_variant(
            principal,
            parent_revision=body.parent_revision,
            explicit_patch=body.explicit_patch,
            rationale=body.rationale,
            author=body.author,
            study_id=study_id,
        )
        code = 201 if res.failure_class is None else 422
        return JSONResponse(status_code=code, content=res.to_json_dict())

    @app.post("/studies/{study_id}/screen")
    def screen_study(
        study_id: str,
        body: ScreenRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.screen_candidates(
            principal,
            study_id=study_id,
            revision=body.revision,
            screening_policy_id=body.screening_policy_id,
        ).to_json_dict()

    @app.post("/studies/{study_id}/evaluations")
    def start_evaluation(
        study_id: str,
        body: EvaluateStudyRequest,
        principal: PrincipalDep,
    ) -> JSONResponse:
        res = svc.evaluate_study(
            principal,
            study_id=study_id,
            revision=body.revision,
            candidate_set_id=body.candidate_set_id,
            uncertainty_policy_id=body.uncertainty_policy_id,
            seed=body.seed,
            use_cache=body.use_cache,
            wait=body.wait,
        )
        code = 200 if body.wait else 202
        return JSONResponse(status_code=code, content=res.to_json_dict())

    @app.post("/sources/evaluate")
    def evaluate_source(
        body: SourceEvaluateRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.evaluate_source(
            principal,
            source_scenario_id=body.source_scenario_id,
            operating_point=body.operating_point,
            sample_id=body.sample_id,
            study_id=body.study_id,
            revision=body.revision,
            seed=body.seed,
        ).to_json_dict()

    @app.post("/studies/{study_id}/candidates/{candidate_id}/evaluate")
    def evaluate_single_candidate(
        study_id: str,
        candidate_id: str,
        body: CandidateEvaluateRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.evaluate_candidate(
            principal,
            study_id=study_id,
            candidate_id=candidate_id,
            revision=body.revision,
            case_set_id=body.case_set_id,
        ).to_json_dict()

    @app.get("/jobs/{job_id}")
    def get_job(
        job_id: str,
        principal: PrincipalDep,
        project_id: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        return svc.get_job_status(principal, job_id=job_id, project_id=project_id).to_json_dict()

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(
        job_id: str,
        principal: PrincipalDep,
        wait: Annotated[bool, Query()] = False,
    ) -> dict[str, Any]:
        return svc.cancel_job(principal, job_id=job_id, wait=wait).to_json_dict()

    @app.get("/runs/{run_id}")
    def get_run(
        run_id: str,
        principal: PrincipalDep,
        lang: Annotated[Language, Query()] = "en",
    ) -> dict[str, Any]:
        return svc.get_run_evidence(principal, run_id=run_id, lang=lang).to_json_dict()

    @app.get("/runs/{run_id}/evidence")
    def get_run_evidence(
        run_id: str,
        principal: PrincipalDep,
        project_id: Annotated[str | None, Query()] = None,
        lang: Annotated[Language, Query()] = "en",
    ) -> dict[str, Any]:
        return svc.get_run_evidence(
            principal, run_id=run_id, project_id=project_id, lang=lang
        ).to_json_dict()

    @app.post("/runs/{run_id}/replay")
    def replay_run(
        run_id: str,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.replay_run(principal, run_id).to_json_dict()

    @app.post("/comparisons")
    def compare_results(
        body: CompareRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.compare_results(
            principal,
            result_ids=body.result_ids,
            metric_policy_id=body.metric_policy_id,
            lang=body.lang,
        ).to_json_dict()

    @app.post("/exports")
    def export_report(
        body: ExportRequest,
        principal: PrincipalDep,
    ) -> dict[str, Any]:
        return svc.export_study_report(
            principal,
            run_id=body.run_id,
            result_ids=body.result_ids,
            study_id=body.study_id,
            revision=body.revision,
            fmt=body.format,
            lang=body.lang,
        ).to_json_dict()

    @app.get("/studies/{study_id}/revisions")
    def list_study_revisions(study_id: str, principal: PrincipalDep) -> dict[str, Any]:
        return svc.list_revisions(principal, study_id).to_json_dict()

    @app.get("/studies/{study_id}/editor")
    def get_study_editor(
        study_id: str,
        principal: PrincipalDep,
        revision: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        return svc.get_scenario_editor_bundle(principal, study_id, revision=revision).to_json_dict()

    @app.get("/studies/{study_id}/map")
    def get_study_map(
        study_id: str,
        principal: PrincipalDep,
        revision: Annotated[str | None, Query()] = None,
        run_id: Annotated[str | None, Query()] = None,
    ) -> dict[str, Any]:
        return svc.get_study_map(
            principal, study_id, revision=revision, run_id=run_id
        ).to_json_dict()

    resolved_ui = frontend_dir if frontend_dir is not None else _default_frontend_dir()
    if resolved_ui is not None and (resolved_ui / "index.html").is_file():
        assets_dir = resolved_ui / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @app.get("/", include_in_schema=False)
        def serve_workspace_index() -> FileResponse:
            return FileResponse(resolved_ui / "index.html")

    return app
