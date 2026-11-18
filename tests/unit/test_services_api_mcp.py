"""Tests for PlanningService, FastAPI HTTP endpoints, and the typed MCP server (Step 6.1)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import Client
from tests.conftest import POLICY_DIRECTORY, SYNTHETIC_SCENARIO, FakeClock, known

from r3chain.adapters.pandapipes_network import PandapipesNetworkModel
from r3chain.cli import main
from r3chain.domain.records.scenario import Scenario
from r3chain.domain.status import AdapterState, StageStatus
from r3chain.mcp import (
    CORE_MCP_TOOLS,
    DEFAULT_INTERIM_TOOL_NAMES,
    MCP_PROTOCOL_VERSION,
    MCP_SDK_VERSION,
    create_mcp_server,
)
from r3chain.pipeline.runner import StageContext
from r3chain.pipeline.stages import default_stages
from r3chain.ports.source import SourceCapabilities, SourceRequest, SourceResult
from r3chain.services import AccessPrincipal, FailureClass, PlanningService
from r3chain.store.store import Store


def _make_seeded_service(
    root: Path,
    _synthetic_data: dict[str, Any],
    *,
    network_model: Any | None = None,
    source_models: Any | None = None,
    stages_factory: Any | None = None,
) -> tuple[PlanningService, str, str]:
    store_dir = root / "store"
    svc = PlanningService(
        store_dir,
        policy_directory=POLICY_DIRECTORY,
        clock=FakeClock(),
        code_version_text="m6-test",
        network_model=network_model or PandapipesNetworkModel(),
        source_models=source_models,
        stages_factory=stages_factory,
    )
    scen = Scenario.model_validate(json.loads(json.dumps(_synthetic_data)))
    principal = AccessPrincipal.local_default()
    rev, _ = svc.register_inputs_and_scenario(
        principal,
        scen,
        base_dir=SYNTHETIC_SCENARIO.parent,
        policies_dir=POLICY_DIRECTORY,
        author="m6-test",
        rationale="seed study",
    )
    return svc, scen.study.study_id, rev.revision_id


@pytest.mark.requirement("W04")
@pytest.mark.requirement("W22")
@pytest.mark.requirement("W24")
@pytest.mark.requirement("W25")
def test_http_api_and_mcp_server_end_to_end_and_cli_equivalence(
    tmp_path: Path,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> None:
    from r3chain.api import create_app

    # 1. Separate stores for CLI vs API vs MCP proving identical revision and input_hashes.
    cli_store = tmp_path / "cli_store"
    assert (
        main(
            [
                "--store",
                str(cli_store),
                "run",
                str(SYNTHETIC_SCENARIO),
                "--policies",
                str(POLICY_DIRECTORY),
                "--author",
                "planner",
                "--rationale",
                "baseline",
                "--seed",
                "7",
            ]
        )
        == 0
    )
    cli_db = Store(cli_store)
    cli_runs = cli_db.list_runs()
    assert len(cli_runs) == 1
    cli_base_run = cli_runs[0]
    assert (
        main(
            [
                "--store",
                str(cli_store),
                "variant",
                cli_base_run.scenario_revision,
                "--set-known",
                "well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]=388.15",
                "--author",
                "planner",
                "--rationale",
                "warmer variant",
                "--run",
            ]
        )
        == 0
    )
    cli_runs_after = cli_db.list_runs()
    cli_var_run = cli_runs_after[-1]
    cli_db.close()

    # 2. HTTP API workflow covering all §16.4 endpoints.
    api_svc, study_id, base_rev = _make_seeded_service(
        tmp_path / "api", _synthetic_data, network_model=network_model
    )
    app = create_app(tmp_path / "api" / "store", policy_directory=POLICY_DIRECTORY, service=api_svc)
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"

        val = client.post("/validations", json={"scenario": _synthetic_data})
        assert val.status_code == 200 and val.json()["data"]["executable"] is True

        ctx = client.get(f"/studies/{study_id}")
        assert ctx.status_code == 200
        assert ctx.json()["scenario_revision"] == base_rev
        assert len(ctx.json()["assumptions_used"]) > 50

        assumptions = client.get(f"/studies/{study_id}/assumptions")
        assert assumptions.status_code == 200 and assumptions.json()["assumptions"]

        ev_search = client.post(
            f"/studies/{study_id}/evidence/search",
            json={"query": "reservoir"},
        )
        assert ev_search.status_code == 200 and ev_search.json()["data"]["passages"]

        scr = client.post(f"/studies/{study_id}/screen", json={"revision": base_rev})
        assert scr.status_code == 200 and scr.json()["status"] == "completed"
        cand_list = scr.json()["data"]["candidates"]
        cand_id = cand_list[0]["candidate_id"]

        # Standalone source evaluation: passing + physical limit exceeded.
        src_ok = client.post(
            "/sources/evaluate",
            json={
                "source_scenario_id": "source-alpha",
                "study_id": study_id,
                "revision": base_rev,
                "operating_point": {
                    "requested_mass_flow_kg_s": 10.0,
                    "injection_temperature_k": 313.15,
                },
            },
        )
        assert src_ok.status_code == 200 and src_ok.json()["status"] == "completed"

        src_phys = client.post(
            "/sources/evaluate",
            json={
                "source_scenario_id": "source-alpha",
                "study_id": study_id,
                "revision": base_rev,
                "operating_point": {
                    "requested_mass_flow_kg_s": 500.0,
                    "injection_temperature_k": 313.15,
                },
            },
        )
        assert src_phys.status_code == 200
        assert src_phys.json()["failure_class"] == FailureClass.PHYSICAL_CHECK_FAILED.value

        cand_eval = client.post(
            f"/studies/{study_id}/candidates/{cand_id}/evaluate",
            json={"revision": base_rev},
        )
        assert cand_eval.status_code == 200

        # Evaluate base study synchronously via API.
        run_base_resp = client.post(
            f"/studies/{study_id}/evaluations",
            json={"revision": base_rev, "seed": 7, "wait": True},
        )
        assert run_base_resp.status_code == 200
        api_base_run = run_base_resp.json()["data"]["run"]
        assert api_base_run["scenario_revision"] == cli_base_run.scenario_revision
        assert api_base_run["input_hashes"] == cli_base_run.input_hashes

        # Run details + verified explanations in English and German.
        run_id = api_base_run["run_id"]
        ev_en = client.get(f"/runs/{run_id}/evidence?lang=en")
        ev_de = client.get(f"/runs/{run_id}/evidence?lang=de")
        assert ev_en.status_code == 200 and ev_de.status_code == 200
        assert "[SYNTHETIC]" in next(iter(ev_en.json()["explanation"]["rendered"].values()))
        assert "[SYNTHETISCH]" in next(iter(ev_de.json()["explanation"]["rendered"].values()))

        # Variant matching CLI --set-known yields identical revision and input_hashes.
        var_resp = client.post(
            f"/studies/{study_id}/revisions",
            json={
                "parent_revision": base_rev,
                "explicit_patch": [
                    {
                        "op": "set_known",
                        "path": (
                            "well_scenarios[source-alpha]"
                            ".reservoir_parameters[reservoir_temperature_k]"
                        ),
                        "value": 388.15,
                    }
                ],
                "rationale": "warmer variant",
                "author": "planner",
            },
        )
        assert var_resp.status_code == 201
        api_var_rev = var_resp.json()["scenario_revision"]
        assert api_var_rev == cli_var_run.scenario_revision

        var_run_resp = client.post(
            f"/studies/{study_id}/evaluations",
            json={"revision": api_var_rev, "wait": True},
        )
        assert var_run_resp.status_code == 200
        api_var_run = var_run_resp.json()["data"]["run"]
        assert api_var_run["input_hashes"] == cli_var_run.input_hashes
        assert api_var_run["result_hash"] == cli_var_run.result_hash

        # Compare and Export endpoints.
        cand_result_ids = [
            r["result_id"] for r in ev_en.json()["data"]["results"] if r["subject"] != "study"
        ][:4]
        cmp_resp = client.post("/comparisons", json={"result_ids": cand_result_ids})
        assert cmp_resp.status_code == 200
        assert cmp_resp.json()["explanation"]["record"]["all_claims_accepted"] is True

        exp_resp = client.post(
            "/exports",
            json={
                "run_id": run_id,
                "result_ids": cand_result_ids,
                "format": "markdown",
                "lang": "de",
            },
        )
        assert exp_resp.status_code == 200
        assert "## Gepruefte Erklaerungen (V05-V07)" in exp_resp.json()["data"]["content"]
        assert exp_resp.json()["artifact_refs"][0].startswith("sha256:")
    api_svc.close()

    # 3. Real MCP Client-Server session checking protocol pin, 12+6 tools, schemas, and inputs.
    assert MCP_SDK_VERSION == "2.3.0"
    assert len(CORE_MCP_TOOLS) == 12
    assert len(DEFAULT_INTERIM_TOOL_NAMES) == 6
    mcp_svc, m_study_id, m_rev = _make_seeded_service(
        tmp_path / "mcp", _synthetic_data, network_model=network_model
    )
    server = create_mcp_server(mcp_svc)

    async def _run_mcp_checks() -> None:
        async with Client(server) as mcp_client:
            assert mcp_client.protocol_version is not None
            assert MCP_PROTOCOL_VERSION
            listed = await mcp_client.list_tools()
            names = {t.name for t in listed.tools}
            assert set(CORE_MCP_TOOLS) <= names
            assert set(DEFAULT_INTERIM_TOOL_NAMES) <= names
            assert "cancel_job" in names
            for t in listed.tools:
                assert t.output_schema is not None, f"{t.name} missing output_schema"

            # Apply identical patch & evaluation via MCP -> identical revision & input_hashes.
            m_var = await mcp_client.call_tool(
                "create_scenario_variant",
                {
                    "parent_revision": m_rev,
                    "explicit_patch": [
                        {
                            "op": "set_known",
                            "path": (
                                "well_scenarios[source-alpha]"
                                ".reservoir_parameters[reservoir_temperature_k]"
                            ),
                            "value": 388.15,
                        }
                    ],
                    "rationale": "warmer variant",
                    "author": "planner",
                },
            )
            assert not m_var.is_error
            assert m_var.structured_content is not None
            assert m_var.structured_content["scenario_revision"] == cli_var_run.scenario_revision
            # Backwards-compatible JSON text block also included.
            assert json.loads(m_var.content[0].text)["scenario_revision"] == (  # type: ignore[union-attr]
                cli_var_run.scenario_revision
            )

            m_eval = await mcp_client.call_tool(
                "evaluate_study",
                {
                    "study_id": m_study_id,
                    "revision": m_var.structured_content["scenario_revision"],
                    "wait": True,
                },
            )
            assert not m_eval.is_error and m_eval.structured_content is not None
            mcp_run = m_eval.structured_content["data"]["run"]
            assert mcp_run["input_hashes"] == cli_var_run.input_hashes

            # Interim geo_* compat wrapper works against same backend.
            compat_res = await mcp_client.call_tool(
                "geo_validate_scenario", {"study_id": m_study_id, "revision": m_rev}
            )
            assert not compat_res.is_error
            assert compat_res.structured_content["data"]["executable"] is True  # type: ignore[index]

    try:
        asyncio.run(_run_mcp_checks())
    finally:
        mcp_svc.close()


def test_tool_contract_separates_all_five_failure_classes(
    tmp_path: Path,
    _synthetic_data: dict[str, Any],
) -> None:
    """§16.3: invalid_request, denied_access, physical_check_failed, numerical, service_fault."""
    from r3chain.api import create_app

    class NonConvergingOrCrashingSource:
        def __init__(self, *, crash: bool) -> None:
            self._crash = crash

        def capabilities(self) -> SourceCapabilities:
            return SourceCapabilities(
                engine="stub-source",
                engine_version="0.0.1",
                model_families=("synthetic-test-model",),
                well_configurations=("doublet",),
                required_reservoir_parameters={"synthetic-test-model": ()},
                fidelity="test",
                synthetic=True,
            )

        def evaluate(self, request: SourceRequest) -> SourceResult:
            if self._crash:
                raise RuntimeError("boom in external solver binary")
            return SourceResult(
                engine="stub-source",
                engine_version="0.0.1",
                fidelity="test",
                model_family=request.model_family,
                source_scenario_id=request.source_scenario_id,
                input_hash=request.input_hash(),
                sample_id=request.sample_id,
                state=AdapterState.NON_CONVERGED,
                reason="forced non convergence",
                synthetic=True,
            )

    # 1. Physical check failure via synthetic source limit + invalid/denied via HTTP API.
    svc_real, study_id, rev = _make_seeded_service(tmp_path / "five", _synthetic_data)
    app = create_app(
        tmp_path / "five" / "store", policy_directory=POLICY_DIRECTORY, service=svc_real
    )
    with TestClient(app) as client:
        # invalid_request (422)
        bad = client.post(
            f"/studies/{study_id}/revisions",
            json={"parent_revision": rev, "explicit_patch": [], "rationale": "bad"},
        )
        assert bad.status_code == 422

        bad_patch = client.post(
            f"/studies/{study_id}/revisions",
            json={
                "parent_revision": rev,
                "explicit_patch": [{"op": "bogus", "path": "x"}],
                "rationale": "bad op",
            },
        )
        assert bad_patch.status_code == 422
        assert bad_patch.json()["failure_class"] == FailureClass.INVALID_REQUEST.value

        # denied_access (403)
        denied = client.get(
            f"/studies/{study_id}",
            headers={"X-R3CHAIN-Principal": "outsider", "X-R3CHAIN-Projects": "other-project"},
        )
        assert denied.status_code == 403
        assert denied.json()["failure_class"] == FailureClass.DENIED_ACCESS.value

        # physical_check_failed
        phys = client.post(
            "/sources/evaluate",
            json={
                "source_scenario_id": "source-alpha",
                "study_id": study_id,
                "revision": rev,
                "operating_point": {
                    "requested_mass_flow_kg_s": 500.0,
                    "injection_temperature_k": 313.15,
                },
            },
        )
        assert phys.json()["failure_class"] == FailureClass.PHYSICAL_CHECK_FAILED.value
    svc_real.close()

    # 2. numerical_non_convergence and service_fault via MCP protocol.
    svc_nc, s_id2, rev2 = _make_seeded_service(
        tmp_path / "nc",
        _synthetic_data,
        source_models=(NonConvergingOrCrashingSource(crash=False),),
    )
    server_nc = create_mcp_server(svc_nc)

    svc_err, s_id3, rev3 = _make_seeded_service(
        tmp_path / "err",
        _synthetic_data,
        source_models=(NonConvergingOrCrashingSource(crash=True),),
    )
    server_err = create_mcp_server(svc_err)

    async def _check_mcp_failures() -> None:
        async with Client(server_nc) as c_nc:
            res = await c_nc.call_tool(
                "evaluate_source",
                {
                    "source_scenario_id": "source-alpha",
                    "study_id": s_id2,
                    "revision": rev2,
                    "operating_point": {
                        "requested_mass_flow_kg_s": 10.0,
                        "injection_temperature_k": 313.15,
                    },
                },
            )
            assert not res.is_error and res.structured_content is not None
            assert (
                res.structured_content["failure_class"]
                == FailureClass.NUMERICAL_NON_CONVERGENCE.value
            )

            # Denied access & invalid request in MCP carry is_error=True + structured failure_class.
            bad_req = await c_nc.call_tool("get_study_context", {"study_id": "missing-study"})
            assert bad_req.is_error and bad_req.structured_content is not None
            assert bad_req.structured_content["failure_class"] == FailureClass.INVALID_REQUEST.value

        async with Client(server_err) as c_err:
            fault = await c_err.call_tool(
                "evaluate_source",
                {
                    "source_scenario_id": "source-alpha",
                    "study_id": s_id3,
                    "revision": rev3,
                    "operating_point": {
                        "requested_mass_flow_kg_s": 10.0,
                        "injection_temperature_k": 313.15,
                    },
                },
            )
            assert fault.is_error and fault.structured_content is not None
            assert fault.structured_content["failure_class"] == FailureClass.SERVICE_FAULT.value

    try:
        asyncio.run(_check_mcp_failures())
    finally:
        svc_nc.close()
        svc_err.close()


def test_async_job_reports_progress_and_supports_cooperative_cancellation(
    tmp_path: Path,
    _synthetic_data: dict[str, Any],
) -> None:
    """Async evaluation jobs track live stage progress and stop cleanly when cancelled."""
    from r3chain.api import create_app

    stage1_entered = threading.Event()
    proceed = threading.Event()

    class _BarrierStage:
        name = "site_evidence"
        version = "1.0.0"
        requires: tuple[str, ...] = ()
        stochastic = False

        def input_fingerprint(self, context: StageContext) -> Any:
            return {"study_id": context.scenario.study.study_id}

        def run(self, context: StageContext) -> Any:
            from r3chain.domain.records.runs import StageOutput

            stage1_entered.set()
            proceed.wait(timeout=5.0)
            return StageOutput(
                stage=self.name, stage_version=self.version, status=StageStatus.COMPLETED
            )

    def _custom_stages() -> tuple[Any, ...]:
        base = default_stages()
        return (_BarrierStage(), *base[1:])

    svc, study_id, rev = _make_seeded_service(
        tmp_path / "cancel", _synthetic_data, stages_factory=_custom_stages
    )
    app = create_app(tmp_path / "cancel" / "store", policy_directory=POLICY_DIRECTORY, service=svc)
    with TestClient(app) as client:
        started = client.post(
            f"/studies/{study_id}/evaluations",
            json={"revision": rev, "wait": False},
        )
        assert started.status_code == 202
        job_id = started.json()["job_id"]
        assert stage1_entered.wait(timeout=5.0)

        cancelled_req = client.post(f"/jobs/{job_id}/cancel")
        assert cancelled_req.status_code == 200
        assert cancelled_req.json()["data"]["job"]["cancel_requested"] is True
        proceed.set()

        # Poll briefly until background worker finishes finalization.
        final_job: dict[str, Any] = {}
        for _ in range(50):
            polled = client.get(f"/jobs/{job_id}")
            assert polled.status_code == 200
            final_job = polled.json()["data"]["job"]
            if final_job["status"] == "cancelled":
                break
            time.sleep(0.05)
        assert final_job["status"] == "cancelled"
        assert final_job["progress_fraction"] < 1.0
        assert final_job["run_id"] is not None

        run_ev = client.get(f"/runs/{final_job['run_id']}/evidence")
        assert run_ev.status_code == 200
        assert run_ev.json()["data"]["run"]["status"] == "cancelled"
    svc.close()


def test_create_study_endpoint_handles_valid_and_invalid_documents(
    tmp_path: Path,
    _synthetic_data: dict[str, Any],
) -> None:
    """POST /studies validates and registers executable scenarios cleanly."""
    from r3chain.api import create_app

    store_root = tmp_path / "post_study" / "store"
    scen = json.loads(json.dumps(_synthetic_data))
    # Remove file-backed layers so POST works with inline payload cleanly without file paths.
    scen["datasets"] = [known_ds for known_ds in scen["datasets"] if known_ds["kind"] != "gis"]
    scen["spatial_layers"] = []

    app = create_app(store_root, policy_directory=POLICY_DIRECTORY)
    with TestClient(app) as client:
        bad = client.post("/studies", json={"scenario": {"study": {}}})
        assert bad.status_code == 422
        assert bad.json()["failure_class"] == FailureClass.INVALID_REQUEST.value

        # Inject a known synthetic parameter into plain minimal scenario using existing fixtures:
        minimal = json.loads(json.dumps(_synthetic_data))
        res = client.post("/studies", json={"scenario": minimal})
        assert res.status_code == 201
        assert res.json()["study_id"] == minimal["study"]["study_id"]
        assert known(1)["state"] == "known"


@pytest.mark.requirement("W22")
@pytest.mark.requirement("W24")
@pytest.mark.requirement("W25")
@pytest.mark.requirement("W26")
@pytest.mark.requirement("A16")
def test_workspace_ui_endpoints_schemas_map_and_static_bundle(
    tmp_path: Path,
    _synthetic_data: dict[str, Any],
) -> None:
    """Step 6.2 workspace routes expose map layers, schema editor bundle, access checks, and UI."""
    from r3chain.api import create_app

    svc, study_id, rev = _make_seeded_service(tmp_path / "ui_api", _synthetic_data)
    dist_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    app = create_app(
        tmp_path / "ui_api" / "store",
        policy_directory=POLICY_DIRECTORY,
        service=svc,
        frontend_dir=dist_dir,
    )
    outsider = {"X-R3CHAIN-Principal": "outsider", "X-R3CHAIN-Projects": "other-project"}
    try:
        with TestClient(app) as client:
            if (dist_dir / "index.html").is_file():
                root_resp = client.get("/")
                assert root_resp.status_code == 200
                assert "R3CHAIN Planner Workspace" in root_resp.text

            studies = client.get("/studies")
            assert studies.status_code == 200
            assert any(s["study_id"] == study_id for s in studies.json()["data"]["studies"])

            outsider_studies = client.get("/studies", headers=outsider)
            assert outsider_studies.status_code == 200
            assert outsider_studies.json()["data"]["studies"] == []

            schema_resp = client.get("/schemas/scenario")
            assert schema_resp.status_code == 200
            assert schema_resp.json()["schema_name"] == "scenario"
            assert "$defs" in schema_resp.json()["schema"]

            editor = client.get(f"/studies/{study_id}/editor", params={"revision": rev})
            assert editor.status_code == 200
            ed_data = editor.json()["data"]
            assert ed_data["schema"]["title"] == "Scenario"
            assert ed_data["scenario"]["study"]["study_id"] == study_id
            assert len(ed_data["available_policies"]) >= 1
            assert client.get(f"/studies/{study_id}/editor", headers=outsider).status_code == 403

            map_resp = client.get(f"/studies/{study_id}/map", params={"revision": rev})
            assert map_resp.status_code == 200
            map_data = map_resp.json()["data"]
            assert map_data["bbox"] is not None
            assert len(map_data["layers"]["sites"]["features"]) >= 1
            assert len(map_data["layers"]["targets"]["features"]) >= 1
            assert len(map_data["text_summary"]) >= 1
            assert client.get(f"/studies/{study_id}/map", headers=outsider).status_code == 403

            revs = client.get(f"/studies/{study_id}/revisions")
            assert revs.status_code == 200
            assert revs.json()["data"]["latest_revision"] == rev
            assert client.get("/jobs").status_code == 200
    finally:
        svc.close()
