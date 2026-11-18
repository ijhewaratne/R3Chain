"""R3CHAIN Model Context Protocol server exposing typed tools (spec sections 16.1-16.3)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import mcp.types as mcp_types
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from r3chain.explain.wording import Language
from r3chain.mcp.compat import register_interim_geo_tools
from r3chain.services.access import AccessDeniedError, AccessPrincipal
from r3chain.services.contracts import FailureClass, ToolResult
from r3chain.services.planning import InvalidRequestError, PlanningService, ServiceFaultError
from r3chain.store.store import NotFoundError

MCP_SDK_VERSION = "2.3.0"
MCP_PROTOCOL_VERSION = mcp_types.LATEST_PROTOCOL_VERSION

CORE_MCP_TOOLS: tuple[str, ...] = (
    "get_study_context",
    "search_project_evidence",
    "validate_scenario",
    "create_scenario_variant",
    "screen_candidates",
    "evaluate_source",
    "evaluate_candidate",
    "evaluate_study",
    "get_job_status",
    "get_run_evidence",
    "compare_results",
    "export_study_report",
)


def _error_call_result(status: str, failure_class: FailureClass, message: str) -> CallToolResult:
    envelope = ToolResult(status=status, failure_class=failure_class, message=message)
    text = envelope.model_dump_json()
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=envelope.to_json_dict(),
        is_error=True,
    )


def _execute_guarded(fn: Callable[[], ToolResult]) -> ToolResult | CallToolResult:
    """Separate invalid requests, denied access, and service faults from engineering results."""
    try:
        res = fn()
        if res.failure_class in {
            FailureClass.INVALID_REQUEST,
            FailureClass.DENIED_ACCESS,
            FailureClass.SERVICE_FAULT,
        }:
            return CallToolResult(
                content=[TextContent(type="text", text=res.model_dump_json())],
                structured_content=res.to_json_dict(),
                is_error=True,
            )
        return res
    except AccessDeniedError as error:
        return _error_call_result("denied_access", FailureClass.DENIED_ACCESS, str(error))
    except PermissionError as error:
        return _error_call_result("denied_access", FailureClass.DENIED_ACCESS, str(error))
    except (InvalidRequestError, NotFoundError, ValidationError, ValueError, KeyError) as error:
        return _error_call_result("invalid_request", FailureClass.INVALID_REQUEST, str(error))
    except ServiceFaultError as error:
        return _error_call_result("service_fault", FailureClass.SERVICE_FAULT, str(error))
    except Exception as error:
        return _error_call_result(
            "service_fault",
            FailureClass.SERVICE_FAULT,
            f"{type(error).__name__}: {error}"[:300],
        )


def create_mcp_server(
    service: PlanningService,
    *,
    principal: AccessPrincipal | None = None,
    include_interim_compat: bool = True,
    compat_name_overrides: Mapping[str, str] | None = None,
) -> MCPServer:
    """Create the single typed R3CHAIN MCP server with all 12 spec tools and compat wrappers."""
    caller = principal or AccessPrincipal.local_default()
    server = MCPServer(
        name="r3chain",
        version="0.1.0",
        instructions=(
            "R3CHAIN geothermal planning server. Every calculation runs on a frozen scenario "
            "revision through PlanningService; access checks and failure classes follow spec 16.3."
        ),
    )

    @server.tool(
        name="get_study_context",
        description="Authorised metadata, active assumptions, accessible sources, and open gaps.",
        structured_output=True,
    )
    def get_study_context(study_id: str, revision: str | None = None) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.get_study_context(caller, study_id, revision=revision)
        )

    @server.tool(
        name="search_project_evidence",
        description="Permission-filtered document and dataset evidence passages with locators.",
        structured_output=True,
    )
    def search_project_evidence(
        study_id: str,
        query: str,
        filters: dict[str, Any] | None = None,
        revision: str | None = None,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.search_project_evidence(
                caller,
                study_id,
                query,
                revision=revision,
                filters=filters,
            )
        )

    @server.tool(
        name="validate_scenario",
        description="Validate missing fields, units, contradictions, warnings, and executability.",
        structured_output=True,
    )
    def validate_scenario(
        study_id: str | None = None,
        revision: str | None = None,
        scenario: dict[str, Any] | None = None,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.validate_stored_scenario(
                caller,
                study_id=study_id,
                revision=revision,
                scenario_data=scenario,
            )
        )

    @server.tool(
        name="create_scenario_variant",
        description="Create an immutable scenario revision with field-level change records.",
        structured_output=True,
    )
    def create_scenario_variant(
        parent_revision: str,
        explicit_patch: list[dict[str, Any]],
        rationale: str,
        author: str = "mcp",
        study_id: str | None = None,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.create_scenario_variant(
                caller,
                parent_revision=parent_revision,
                explicit_patch=explicit_patch,
                rationale=rationale,
                author=author,
                study_id=study_id,
            )
        )

    @server.tool(
        name="screen_candidates",
        description="Site and route screening checks with evidence and conditional states.",
        structured_output=True,
    )
    def screen_candidates(
        study_id: str,
        revision: str | None = None,
        screening_policy_id: str | None = None,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.screen_candidates(
                caller,
                study_id=study_id,
                revision=revision,
                screening_policy_id=screening_policy_id,
            )
        )

    @server.tool(
        name="evaluate_source",
        description="Normalised geothermal source evaluation or classified failure state.",
        structured_output=True,
    )
    def evaluate_source(
        source_scenario_id: str,
        operating_point: dict[str, Any],
        sample_id: str = "deterministic",
        study_id: str | None = None,
        revision: str | None = None,
        seed: int = 0,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_source(
                caller,
                source_scenario_id=source_scenario_id,
                operating_point=operating_point,
                sample_id=sample_id,
                study_id=study_id,
                revision=revision,
                seed=seed,
            )
        )

    @server.tool(
        name="evaluate_candidate",
        description="Evaluate coupled operating and capacity cases for a single candidate.",
        structured_output=True,
    )
    def evaluate_candidate(
        study_id: str,
        revision: str,
        candidate_id: str,
        case_set_id: str | None = None,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_candidate(
                caller,
                study_id=study_id,
                revision=revision,
                candidate_id=candidate_id,
                case_set_id=case_set_id,
            )
        )

    @server.tool(
        name="evaluate_study",
        description="Start or run full frozen-revision evaluation and return job_id / results.",
        structured_output=True,
    )
    def evaluate_study(
        study_id: str,
        revision: str | None = None,
        candidate_set_id: str | None = None,
        uncertainty_policy_id: str | None = None,
        seed: int = 0,
        wait: bool = False,
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_study(
                caller,
                study_id=study_id,
                revision=revision,
                candidate_set_id=candidate_set_id,
                uncertainty_policy_id=uncertainty_policy_id,
                seed=seed,
                wait=wait,
            )
        )

    @server.tool(
        name="get_job_status",
        description="Authorised job progress, stage, cancellation state, and result references.",
        structured_output=True,
    )
    def get_job_status(project_id: str, job_id: str) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.get_job_status(caller, project_id=project_id, job_id=job_id)
        )

    @server.tool(
        name="cancel_job",
        description="Request cooperative cancellation of an authorised running or queued job.",
        structured_output=True,
    )
    def cancel_job(job_id: str, wait: bool = False) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.cancel_job(caller, job_id=job_id, wait=wait)
        )

    @server.tool(
        name="get_run_evidence",
        description="Authorised run inputs, versions, checks, diagnostics, and explanations.",
        structured_output=True,
    )
    def get_run_evidence(project_id: str, run_id: str, lang: Language = "en") -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.get_run_evidence(
                caller, project_id=project_id, run_id=run_id, lang=lang
            )
        )

    @server.tool(
        name="compare_results",
        description="Comparable KPI table, exclusions, boundaries, and comparison explanation.",
        structured_output=True,
    )
    def compare_results(
        result_ids: list[str],
        metric_policy_id: str | None = None,
        lang: Language = "en",
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.compare_results(
                caller,
                result_ids=result_ids,
                metric_policy_id=metric_policy_id,
                lang=lang,
            )
        )

    @server.tool(
        name="export_study_report",
        description="Authorised reproducible evidence report artifact with verified explanations.",
        structured_output=True,
    )
    def export_study_report(
        study_id: str,
        revision: str,
        result_ids: list[str],
        format: str = "markdown",
        lang: Language = "en",
    ) -> ToolResult:
        return _execute_guarded(  # type: ignore[return-value]
            lambda: service.export_study_report(
                caller,
                study_id=study_id,
                revision=revision,
                result_ids=result_ids,
                fmt=format,
                lang=lang,
            )
        )

    if include_interim_compat:
        register_interim_geo_tools(
            server,
            service,
            caller,
            _execute_guarded,
            name_overrides=compat_name_overrides,
        )

    return server
