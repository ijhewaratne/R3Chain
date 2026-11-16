"""The command line from end to end, and the committed JSON Schemas."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from tests.conftest import POLICY_DIRECTORY, REPO_ROOT, SYNTHETIC_SCENARIO

from r3chain.cli import main
from r3chain.schemas import (
    VERSIONED_RECORDS,
    SchemaChangedError,
    check_schemas,
    export_schemas,
    schema_filename,
)
from r3chain.store.store import Store


class Cli:
    """Runs commands against one temporary store and captures what they print."""

    def __init__(self, store: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.store = store
        self._capsys = capsys

    def __call__(self, *arguments: str) -> tuple[int, str]:
        code = main(["--store", str(self.store), *arguments])
        captured = self._capsys.readouterr()
        return code, captured.out + captured.err

    def run_synthetic(self) -> str:
        code, output = self("run", str(SYNTHETIC_SCENARIO), "--policies", str(POLICY_DIRECTORY))
        assert code == 0, output
        return output


@pytest.fixture
def cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Cli:
    return Cli(tmp_path / "store", capsys)


def _field(output: str, label: str) -> str:
    match = re.search(rf"^{label}\s+(\S+)", output, flags=re.MULTILINE)
    assert match is not None, output
    return match.group(1)


def test_validate_reports_gaps_and_succeeds(cli: Cli) -> None:
    code, output = cli("validate", str(SYNTHETIC_SCENARIO))
    assert code == 0
    assert "Status    executable" in output
    assert "Inputs not established (15)" in output
    assert "sites[delta].usable_pad_area_m2" in output

    code, output = cli("validate", str(SYNTHETIC_SCENARIO), "--json")
    report = json.loads(output)
    assert code == 0 and len(report["missing_inputs"]) == 15


def test_validate_fails_on_an_invalid_file(cli: Cli, tmp_path: Path) -> None:
    text = SYNTHETIC_SCENARIO.read_text(encoding="utf-8")
    # The GIS layers are named relative to the scenario file, so they travel with the copy.
    shutil.copytree(SYNTHETIC_SCENARIO.parent / "gis", tmp_path / "gis")
    broken = tmp_path / "broken.yaml"
    broken.write_text(text.replace("baseline_id: baseline-existing", "baseline_id: nothing"))
    code, output = cli("validate", str(broken))
    assert code == 1
    assert "NOT executable, 1 error(s)" in output
    assert "study.baseline_id  [unknown_reference]" in output
    # A scenario that does not validate is never executed.
    code, output = cli("run", str(broken), "--policies", str(POLICY_DIRECTORY))
    assert code == 1 and "Run " not in output


def test_assumptions_are_listed_without_being_asked_for_one_by_one(cli: Cli) -> None:
    code, output = cli("assumptions", str(SYNTHETIC_SCENARIO))
    assert code == 0
    assert "115 inputs: 100 active, 1 conflict, 14 unknown" in output
    assert "[synthetic from syn-definitions]" in output
    code, output = cli("assumptions", str(SYNTHETIC_SCENARIO), "--status", "unknown", "--json")
    rows = json.loads(output)
    assert code == 0 and len(rows) == 14
    assert all(row["status"] == "unknown" and row["owner"] for row in rows)


def test_run_show_and_replay(cli: Cli) -> None:
    output = cli.run_synthetic()
    run_id, revision_id = _field(output, "Run"), _field(output, "Revision")
    assert "site_evidence  completed        computed" in output
    assert "screen         completed        computed" in output
    assert "assess         completed        computed" in output
    assert "technical not_established, evaluation not_evaluated, synthetic" in output
    assert "Candidates (27)" in output and "among the 11 eligible of the 27" in output

    # The same scenario again is the same revision, and its stages are reused.
    second = cli.run_synthetic()
    assert _field(second, "Revision") == revision_id
    assert f"reused from {run_id}" in second

    code, shown = cli("show", run_id)
    assert code == 0
    assert "site delta: conflict" in shown and "site alpha: documented" in shown
    assert "source source-alpha: r3chain-synthetic-source 1.0.0, a synthetic test engine" in shown
    assert "24 of 40 grid points feasible" in shown

    code, shown = cli("show", revision_id)
    assert code == 0 and "Runs (2)" in shown and "Rationale initial import" in shown

    code, replayed = cli("replay", run_id)
    assert code == 0 and replayed.strip().endswith("Identical")

    code, missing = cli("show", "run-does-not-exist")
    assert code == 1 and "error:" in missing


@pytest.mark.requirement("A15")
@pytest.mark.requirement("W24")
def test_variant_creates_revision_with_change_record(cli: Cli) -> None:
    parent = _field(cli.run_synthetic(), "Revision")
    path = "economic_scenarios[econ-base].discount_rate_per_year"
    code, output = cli(
        "variant",
        parent,
        "--set-known",
        f"{path}=0.05",
        "--author",
        "reviewer-a",
        "--rationale",
        "Test a higher discount rate",
        "--run",
    )
    assert code == 0, output
    child = _field(output, "Revision")
    assert child != parent and _field(output, "Parent") == parent
    assert f"changed  {path}: 0.04 (synthetic)  ->  0.05 (user_supplied)" in output
    # The site stages do not depend on the discount rate, so they are validly reused.
    assert "site_evidence  completed        reused from" in output

    # The change is stored with the revision: what changed, who changed it and why.
    store = Store(cli.store)
    revision = store.get_revision(child)
    assert (revision.author, revision.rationale) == ("reviewer-a", "Test a higher discount rate")
    assert [change.field_path for change in revision.changes] == [path]
    assert revision.changes[0].after["provenance"]["author"] == "reviewer-a"
    # The parent revision still holds the old value.
    old = store.get_scenario(parent).economic_scenarios[0].discount_rate_per_year
    new = store.get_scenario(child).economic_scenarios[0].discount_rate_per_year
    assert (old.value, new.value) == (0.04, 0.05)  # type: ignore[union-attr]
    store.close()

    code, output = cli("assumptions", child, "--status", "active", "--json")
    row = next(item for item in json.loads(output) if item["field_path"] == path)
    assert (row["value"], row["provenance_type"], row["author"]) == (
        0.05,
        "user_supplied",
        "reviewer-a",
    )


def test_variant_rejects_an_invalid_change(cli: Cli) -> None:
    parent = _field(cli.run_synthetic(), "Revision")
    code, output = cli(
        "variant",
        parent,
        "--set-known",
        'sites[alpha].usable_pad_area_m2="large"',
        "--rationale",
        "bad value",
    )
    assert code == 1
    assert "sites[alpha].usable_pad_area_m2.value" in output
    code, output = cli("variant", parent, "--rationale", "nothing to do")
    assert code == 2
    code, output = cli("variant", parent, "--remove", "sites[omega]", "--rationale", "x")
    assert code == 1 and "error:" in output


def test_resolving_an_unknown_recomputes_what_depends_on_it(cli: Cli) -> None:
    parent = _field(cli.run_synthetic(), "Revision")
    code, output = cli(
        "variant",
        parent,
        "--set-known",
        "sites[delta].usable_pad_area_m2=5400",
        "--rationale",
        "Pad area from the setback survey",
        "--run",
    )
    assert code == 0
    assert "unknown (Depends on the setback survey)  ->  5400" in output
    assert "site_evidence  completed        computed" in output


def test_a_changed_policy_file_is_refused(cli: Cli, tmp_path: Path) -> None:
    cli.run_synthetic()
    policies = tmp_path / "policies"
    policies.mkdir()
    for source in POLICY_DIRECTORY.rglob("*.yaml"):
        text = source.read_text(encoding="utf-8")
        (policies / source.name).write_text(
            text.replace("min_usable_pad_area_m2: 5000", "min_usable_pad_area_m2: 100")
        )
    # The same policy version with a different threshold cannot be registered.
    code, output = cli("run", str(SYNTHETIC_SCENARIO), "--policies", str(policies))
    assert code == 1 and "already exists with different content" in output


def test_policies_are_listed_with_their_hashes(cli: Cli) -> None:
    code, output = cli("policies", str(POLICY_DIRECTORY))
    assert code == 0
    assert "numerical-coupling-default" in output and "screening-synthetic" in output
    assert output.count("sha256:") == len(list(POLICY_DIRECTORY.rglob("*.yaml"))) == 6


# --- schemas --------------------------------------------------------------------------------


def test_committed_schemas_are_current() -> None:
    """Fails when a record changed without a new schema version."""
    assert check_schemas(REPO_ROOT / "schemas") == []


def test_a_changed_record_needs_a_new_schema_version(tmp_path: Path) -> None:
    written = export_schemas(tmp_path)
    assert len(written) == len(VERSIONED_RECORDS)
    assert export_schemas(tmp_path) == []
    assert check_schemas(tmp_path) == []

    # Simulate a record that changed while its version stayed the same.
    stale = tmp_path / schema_filename(VERSIONED_RECORDS[0])
    stale.write_text(stale.read_text().replace('"title": "Study"', '"title": "Old study"'))
    assert len(check_schemas(tmp_path)) == 1
    with pytest.raises(SchemaChangedError, match="Raise the version"):
        export_schemas(tmp_path)
    assert export_schemas(tmp_path, force=True) == [stale]

    stale.unlink()
    assert "is missing" in check_schemas(tmp_path)[0]


def test_schema_commands(cli: Cli, tmp_path: Path) -> None:
    directory = str(tmp_path / "schemas")
    code, output = cli("schemas", "check", "--directory", directory)
    assert code == 1 and "is missing" in output
    code, output = cli("schemas", "export", "--directory", directory)
    assert code == 0 and output.count("wrote ") == len(VERSIONED_RECORDS)
    code, output = cli("schemas", "check", "--directory", directory)
    assert code == 0 and "schemas are current" in output
