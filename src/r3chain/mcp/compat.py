"""Thin compatibility wrappers for the six interim ``geo_*`` MCP tools (Plan v2 section 2).

All wrappers delegate directly to :class:`r3chain.services.PlanningService` and use the unified
section 16.3 :class:`ToolResult` contract so existing demonstrations share the exact same backend,
validation, and access checks as the twelve canonical tools.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mcp.server import MCPServer
from mcp.types import CallToolResult

from r3chain.services.access import AccessPrincipal
from r3chain.services.contracts import ToolResult
from r3chain.services.planning import PlanningService

DEFAULT_INTERIM_TOOL_NAMES: tuple[str, ...] = (
    "geo_validate_scenario",
    "geo_evaluate_source",
    "geo_evaluate_candidate",
    "geo_run_study",
    "geo_get_run_artifacts",
    "geo_compare_cases",
)


def register_interim_geo_tools(
    server: MCPServer,
    service: PlanningService,
    principal: AccessPrincipal,
    execute_guarded: Callable[[Callable[[], ToolResult]], ToolResult | CallToolResult],
    *,
    name_overrides: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Register thin ``geo_*`` compatibility wrappers mapped onto :class:`PlanningService`."""
    aliases = {name: (name_overrides or {}).get(name, name) for name in DEFAULT_INTERIM_TOOL_NAMES}

    @server.tool(
        name=aliases["geo_validate_scenario"],
        description="Interim compatibility wrapper for validate_scenario.",
        structured_output=True,
    )
    def geo_validate_scenario(study_id: str, revision: str | None = None) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.validate_stored_scenario(
                principal, study_id=study_id, revision=revision
            )
        )

    @server.tool(
        name=aliases["geo_evaluate_source"],
        description="Interim compatibility wrapper for evaluate_source.",
        structured_output=True,
    )
    def geo_evaluate_source(
        source_scenario_id: str,
        operating_point: dict[str, Any],
        sample_id: str = "deterministic",
        study_id: str | None = None,
        revision: str | None = None,
    ) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_source(
                principal,
                source_scenario_id=source_scenario_id,
                operating_point=operating_point,
                sample_id=sample_id,
                study_id=study_id,
                revision=revision,
            )
        )

    @server.tool(
        name=aliases["geo_evaluate_candidate"],
        description="Interim compatibility wrapper for evaluate_candidate.",
        structured_output=True,
    )
    def geo_evaluate_candidate(
        study_id: str,
        revision: str,
        candidate_id: str,
        case_set_id: str | None = None,
    ) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_candidate(
                principal,
                study_id=study_id,
                revision=revision,
                candidate_id=candidate_id,
                case_set_id=case_set_id,
            )
        )

    @server.tool(
        name=aliases["geo_run_study"],
        description="Interim compatibility wrapper for deterministic or Monte Carlo study runs.",
        structured_output=True,
    )
    def geo_run_study(
        study_id: str,
        revision: str | None = None,
        uncertainty_policy_id: str | None = None,
        seed: int = 0,
        wait: bool = True,
    ) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.evaluate_study(
                principal,
                study_id=study_id,
                revision=revision,
                uncertainty_policy_id=uncertainty_policy_id,
                seed=seed,
                wait=wait,
            )
        )

    @server.tool(
        name=aliases["geo_get_run_artifacts"],
        description="Interim compatibility wrapper for get_run_evidence.",
        structured_output=True,
    )
    def geo_get_run_artifacts(project_id: str, run_id: str) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.get_run_evidence(principal, project_id=project_id, run_id=run_id)
        )

    @server.tool(
        name=aliases["geo_compare_cases"],
        description="Interim compatibility wrapper for compare_results.",
        structured_output=True,
    )
    def geo_compare_cases(
        result_ids: list[str],
        metric_policy_id: str | None = None,
    ) -> ToolResult:
        return execute_guarded(  # type: ignore[return-value]
            lambda: service.compare_results(
                principal,
                result_ids=result_ids,
                metric_policy_id=metric_policy_id,
            )
        )

    return tuple(aliases.values())
