"""Acceptance tests A15, A16, and A17 at service, HTTP API, MCP, and CLI level (Gate 6.1)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from r3chain.api import create_app
from r3chain.cli import main
from r3chain.domain.hashing import bytes_checksum
from r3chain.domain.records.scenario import Scenario
from r3chain.explain import check_claim, explain_result
from r3chain.mcp import create_mcp_server
from r3chain.services import AccessDeniedError, AccessPrincipal, FailureClass, PlanningService
from r3chain.store.store import Store
from tests.conftest import POLICY_DIRECTORY, SYNTHETIC_SCENARIO, FakeClock


def _build_restricted_scenario(
    base_data: dict[str, Any],
    doc_dir: Path,
) -> tuple[dict[str, Any], Path, Path]:
    """Create a mixed public + restricted document scenario for access-control verification."""
    pub_file = doc_dir / "public-note.txt"
    pub_file.write_text("Public summary: synthetic planning baseline OK.", encoding="utf-8")
    sec_file = doc_dir / "restricted-wsw.txt"
    sec_file.write_text(
        "CONFIDENTIAL SECRET-WSW-42: private customer load tariff 99 EUR/MWh.",
        encoding="utf-8",
    )

    data = json.loads(json.dumps(base_data))
    now = datetime(2026, 11, 18, 9, 0, tzinfo=UTC).isoformat()
    data["datasets"].extend(
        [
            {
                "schema_version": "0.1.0",
                "dataset_id": "ds-public-note",
                "version": "1",
                "kind": "document",
                "title": "Public planning overview",
                "source_uri": pub_file.name,
                "checksum": bytes_checksum(pub_file.read_bytes()),
                "owner": "public-team",
                "acquired_at": now,
                "allowed_use": "public",
                "access_group": "public",
                "quality": "reviewed",
                "synthetic": True,
            },
            {
                "schema_version": "0.1.0",
                "dataset_id": "ds-restricted-pilot",
                "version": "1",
                "kind": "document",
                "title": "Restricted WSW commercial annex",
                "source_uri": sec_file.name,
                "checksum": bytes_checksum(sec_file.read_bytes()),
                "owner": "wsw-confidential",
                "acquired_at": now,
                "allowed_use": "restricted",
                "access_group": "wsw-confidential",
                "quality": "reviewed",
                "synthetic": True,
            },
        ]
    )
    return data, pub_file, sec_file


@pytest.mark.requirement("A15")
def test_a15_scenario_correction_invalidates_stale_results_and_explanations(
    tmp_path: Path,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> None:
    """A15: changing discount rate invalidates cost & compare and rejects stale claims."""
    svc = PlanningService(
        tmp_path / "store",
        policy_directory=POLICY_DIRECTORY,
        clock=FakeClock(),
        network_model=network_model,
    )
    admin = AccessPrincipal.local_default()
    scen = Scenario.model_validate(json.loads(json.dumps(_synthetic_data)))
    rev1, _ = svc.register_inputs_and_scenario(
        admin, scen, base_dir=SYNTHETIC_SCENARIO.parent, policies_dir=POLICY_DIRECTORY
    )
    run1_res = svc.evaluate_study(
        admin, study_id=scen.study.study_id, revision=rev1.revision_id, wait=True
    )
    run1_id = run1_res.run_id
    assert run1_id is not None

    # Create revision 2 via API changing discount rate from 4% to 7%.
    app = create_app(tmp_path / "store", policy_directory=POLICY_DIRECTORY, service=svc)
    with TestClient(app) as client:
        rev2_resp = client.post(
            f"/studies/{scen.study.study_id}/revisions",
            json={
                "parent_revision": rev1.revision_id,
                "explicit_patch": [
                    {
                        "op": "set_known",
                        "path": "economic_scenarios[econ-base].discount_rate_per_year",
                        "value": 0.07,
                    }
                ],
                "rationale": "discount rate sensitivity 7%",
                "author": "reviewer",
            },
        )
        assert rev2_resp.status_code == 201
        rev2_id = rev2_resp.json()["scenario_revision"]
        assert any(
            c["field_path"] == "economic_scenarios[econ-base].discount_rate_per_year"
            for c in rev2_resp.json()["data"]["changes"]
        )

        run2_resp = client.post(
            f"/studies/{scen.study.study_id}/evaluations",
            json={"revision": rev2_id, "wait": True},
        )
        assert run2_resp.status_code == 200
        run2 = run2_resp.json()["data"]["run"]
        hits = {s["stage"]: s["cache_hit"] for s in run2["stages"]}
        assert hits["operate"] is True
        assert hits["cost"] is False
        assert hits["compare"] is False

    # Stale claim citing rev1 checked against run2 context is deterministically rejected.
    store = Store(tmp_path / "store")
    try:
        from r3chain.explain import build_verification_context

        exp_old = explain_result(store, run1_id, "study")
        ctx_new = build_verification_context(store, run2["run_id"])
        stale_outcome = check_claim(exp_old.claims[0], ctx_new)
        assert not stale_outcome.accepted
        assert "run_id_mismatch" in stale_outcome.error_codes
        stale_rev_only = exp_old.claims[0].model_copy(update={"run_id": run2["run_id"]})
        assert "stale_revision" in check_claim(stale_rev_only, ctx_new).error_codes
    finally:
        store.close()
        svc.close()


@pytest.mark.requirement("A16")
def test_a16_access_and_export_block_unauthorised_evidence(
    tmp_path: Path,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> None:
    """A16: unauthorised callers cannot retrieve evidence via search, runs, cache, or export."""
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    restricted_doc, pub_file, sec_file = _build_restricted_scenario(_synthetic_data, docs_dir)
    # Also ensure base GIS URIs resolve cleanly via registering base datasets first.
    svc = PlanningService(
        tmp_path / "store",
        policy_directory=POLICY_DIRECTORY,
        clock=FakeClock(),
        network_model=network_model,
    )
    admin = AccessPrincipal.full_admin("lead-admin")
    unauthorised = AccessPrincipal(
        principal_id="external-guest",
        allowed_projects=("r3chain",),
        access_groups=("public",),
        allow_restricted=False,
        allow_synthetic=True,
    )
    other_project = AccessPrincipal(
        principal_id="other-proj",
        allowed_projects=("unrelated-project",),
        access_groups=("*",),
        allow_restricted=True,
        allow_synthetic=True,
    )

    scen = Scenario.model_validate(restricted_doc)
    # Register public & restricted text files manually + rest of scenario datasets.
    store = Store(tmp_path / "store", clock=FakeClock())
    for ds in scen.datasets:
        if ds.dataset_id == "ds-public-note":
            store.register_dataset(ds, pub_file)
        elif ds.dataset_id == "ds-restricted-pilot":
            store.register_dataset(ds, sec_file)
        else:
            cand = SYNTHETIC_SCENARIO.parent / ds.source_uri
            store.register_dataset(ds, cand if cand.is_file() else None)
    store.close()

    rev, _ = svc.register_inputs_and_scenario(
        admin, scen, base_dir=SYNTHETIC_SCENARIO.parent, policies_dir=POLICY_DIRECTORY
    )
    admin_eval = svc.evaluate_study(
        admin, study_id=scen.study.study_id, revision=rev.revision_id, wait=True
    )
    assert admin_eval.status == "completed"
    admin_run_id = admin_eval.run_id
    admin_job_id = admin_eval.job_id
    assert admin_run_id is not None and admin_job_id is not None
    admin_study_res_id = admin_eval.data["study_result"]["result_id"]

    # 1. Search filters out restricted documents and blocks explicit restricted group filter.
    pub_search = svc.search_project_evidence(
        unauthorised, scen.study.study_id, "SECRET-WSW-42", revision=rev.revision_id
    )
    assert "ds-restricted-pilot" not in pub_search.data["accessible_dataset_ids"]
    assert all("SECRET-WSW-42" not in p["excerpt"] for p in pub_search.data["passages"])
    with pytest.raises(PermissionError):
        svc.search_project_evidence(
            unauthorised,
            scen.study.study_id,
            "tariff",
            revision=rev.revision_id,
            filters={"dataset_id": "ds-restricted-pilot"},
        )

    # Admin sees restricted content cleanly.
    admin_search = svc.search_project_evidence(
        admin, scen.study.study_id, "SECRET-WSW-42", revision=rev.revision_id
    )
    assert any("SECRET-WSW-42" in p["excerpt"] for p in admin_search.data["passages"])

    # 2. Unauthorised principal cannot execute, inspect run/job/evidence, compare, or export.
    for denied_caller in (unauthorised, other_project):
        with pytest.raises(AccessDeniedError):
            svc.evaluate_study(
                denied_caller, study_id=scen.study.study_id, revision=rev.revision_id, wait=True
            )
        with pytest.raises(AccessDeniedError):
            svc.get_job_status(denied_caller, job_id=admin_job_id)
        with pytest.raises(AccessDeniedError):
            svc.get_run_evidence(denied_caller, run_id=admin_run_id)
        with pytest.raises(AccessDeniedError):
            svc.compare_results(denied_caller, result_ids=[admin_study_res_id])
        with pytest.raises(AccessDeniedError):
            svc.export_study_report(denied_caller, run_id=admin_run_id)

    # 3. Same denial enforced via HTTP API & MCP server.
    app = create_app(tmp_path / "store", policy_directory=POLICY_DIRECTORY, service=svc)
    denied_headers = {
        "X-R3CHAIN-Principal": "external-guest",
        "X-R3CHAIN-Projects": "r3chain",
        "X-R3CHAIN-Groups": "public",
        "X-R3CHAIN-Allow-Restricted": "false",
    }
    with TestClient(app) as client:
        assert (
            client.get(f"/runs/{admin_run_id}/evidence", headers=denied_headers).status_code == 403
        )
        assert client.get(f"/jobs/{admin_job_id}", headers=denied_headers).status_code == 403
        exp = client.post("/exports", json={"run_id": admin_run_id}, headers=denied_headers)
        assert exp.status_code == 403
        assert exp.json()["failure_class"] == FailureClass.DENIED_ACCESS.value

    mcp_server = create_mcp_server(svc, principal=unauthorised)

    async def _check_mcp_denied() -> None:
        async with Client(mcp_server) as client:
            r_ev = await client.call_tool(
                "get_run_evidence",
                {"project_id": scen.study.project_id, "run_id": admin_run_id},
            )
            assert r_ev.is_error and r_ev.structured_content is not None
            assert r_ev.structured_content["failure_class"] == FailureClass.DENIED_ACCESS.value

            r_exp = await client.call_tool(
                "export_study_report",
                {
                    "study_id": scen.study.study_id,
                    "revision": rev.revision_id,
                    "result_ids": [admin_study_res_id],
                },
            )
            assert r_exp.is_error and r_exp.structured_content is not None
            assert r_exp.structured_content["failure_class"] == FailureClass.DENIED_ACCESS.value

    asyncio.run(_check_mcp_denied())

    # 4. Cache key isolation: same non-restricted scenario revision evaluated by two distinct access
    # scopes never hits the other scope's cached stage outputs (section 19.3).
    pub_only = json.loads(json.dumps(_synthetic_data))
    scen_pub = Scenario.model_validate(pub_only)
    scope_a = AccessPrincipal(
        principal_id="org-a",
        allowed_projects=("r3chain",),
        access_groups=("public", "synthetic", "group-a"),
    )
    scope_b = AccessPrincipal(
        principal_id="org-b",
        allowed_projects=("r3chain",),
        access_groups=("public", "synthetic", "group-b"),
    )
    rev_pub, _ = svc.register_inputs_and_scenario(
        scope_a, scen_pub, base_dir=SYNTHETIC_SCENARIO.parent, policies_dir=POLICY_DIRECTORY
    )
    run_a = svc.evaluate_study(
        scope_a, study_id=scen_pub.study.study_id, revision=rev_pub.revision_id, wait=True
    )
    run_b = svc.evaluate_study(
        scope_b, study_id=scen_pub.study.study_id, revision=rev_pub.revision_id, wait=True
    )
    assert all(not stg["cache_hit"] for stg in run_b.data["run"]["stages"])
    assert run_a.data["run"]["run_id"] not in run_b.data["run"]["parent_run_ids"]
    svc.close()


@pytest.mark.requirement("A17")
def test_a17_report_inputs_replay_identically_without_assistant_across_interfaces(
    tmp_path: Path,
    network_model: Any,
    _synthetic_data: dict[str, Any],
) -> None:
    """A17: exported report inputs replay to identical result_hash via service, API, and CLI."""
    svc = PlanningService(
        tmp_path / "store",
        policy_directory=POLICY_DIRECTORY,
        clock=FakeClock(),
        network_model=network_model,
    )
    principal = AccessPrincipal.local_default()
    scen = Scenario.model_validate(json.loads(json.dumps(_synthetic_data)))
    rev, _ = svc.register_inputs_and_scenario(
        principal, scen, base_dir=SYNTHETIC_SCENARIO.parent, policies_dir=POLICY_DIRECTORY
    )
    eval_res = svc.evaluate_study(
        principal, study_id=scen.study.study_id, revision=rev.revision_id, wait=True
    )
    run_id = eval_res.run_id
    assert run_id is not None

    exported = svc.export_study_report(principal, run_id=run_id, fmt="json", lang="en")
    report_json = json.loads(exported.data["content"])
    assert report_json["run_id"] == run_id
    assert report_json["explanations"]
    assert all(exp["all_claims_accepted"] for exp in report_json["explanations"])

    # Replay through HTTP API and CLI against the same frozen store.
    app = create_app(tmp_path / "store", policy_directory=POLICY_DIRECTORY, service=svc)
    with TestClient(app) as client:
        rep_resp = client.post(f"/runs/{run_id}/replay")
        assert rep_resp.status_code == 200
        replay = rep_resp.json()["data"]["replay"]
        assert replay["identical"] is True
        assert replay["original_hash"] == report_json["result_hash"]

    assert main(["--store", str(tmp_path / "store"), "replay", run_id]) == 0
    svc.close()
