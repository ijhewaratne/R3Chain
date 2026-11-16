"""The requirements register stays complete and tied to real tests.

Every workshop requirement, open decision and acceptance test of the specification has exactly
one entry. An entry that claims to be at least partly built must name tests that exist.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.conftest import REPO_ROOT

REGISTER = REPO_ROOT / "requirements" / "register.yaml"
TESTS = REPO_ROOT / "tests"

EXPECTED_IDS = (
    [f"W{number:02d}" for number in range(1, 37)]
    + [f"O{number:02d}" for number in range(1, 15)]
    + [f"A{number:02d}" for number in range(1, 19)]
)
MILESTONES = {f"M{number}" for number in range(10)}
# The modules of the implementation plan, section 3.2.
PLANNED_MODULES = {
    "domain",
    "ports",
    "store",
    "pipeline",
    "ingestion",
    "geospatial",
    "adapters",
    "coupling",
    "operation",
    "economics",
    "uncertainty",
    "search",
    "services",
    "api",
    "mcp",
    "assistant",
    "reporting",
    "reference",
    "policies",
    "benchmarks",
    "frontend",
    "docs",
}
BUILD_STATUSES = {"planned", "partial", "built", "verified", "superseded"}
DECISION_STATUSES = {"open", "resolved"}
CLAIMS_TESTS = {"partial", "built", "verified"}


@pytest.fixture(scope="module")
def entries() -> list[dict[str, Any]]:
    loaded = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    assert isinstance(loaded["entries"], list)
    return loaded["entries"]


@pytest.fixture(scope="module")
def test_sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TESTS.rglob("test_*.py")))


def test_register_covers_every_requirement(entries: list[dict[str, Any]]) -> None:
    identifiers = [entry["id"] for entry in entries]
    assert len(identifiers) == len(set(identifiers)), "an identifier appears twice"
    assert sorted(identifiers) == sorted(EXPECTED_IDS)


def test_entries_are_well_formed(entries: list[dict[str, Any]]) -> None:
    kinds = {"W": "workshop", "O": "open_decision", "A": "acceptance"}
    for entry in entries:
        identifier = entry["id"]
        assert entry["kind"] == kinds[identifier[0]], identifier
        assert entry["statement"].strip(), identifier
        assert entry["milestone"] in MILESTONES, identifier
        if entry["kind"] == "open_decision":
            assert entry["status"] in DECISION_STATUSES, identifier
            assert entry["reviewer"].strip(), identifier
            continue
        assert entry["status"] in BUILD_STATUSES, identifier
        assert set(entry["lands_in"]) <= PLANNED_MODULES, identifier
        assert entry["lands_in"], identifier
        if entry["kind"] == "workshop":
            assert entry["source"].strip(), identifier


def test_claimed_progress_is_backed_by_existing_tests(
    entries: list[dict[str, Any]], test_sources: str
) -> None:
    defined = set(re.findall(r"^def (test_\w+)\(", test_sources, flags=re.MULTILINE))
    for entry in entries:
        if entry["kind"] == "open_decision":
            continue
        tests = entry["tests"]
        if entry["status"] in CLAIMS_TESTS:
            assert tests, f"{entry['id']} is {entry['status']} but names no test"
        else:
            assert not tests, f"{entry['id']} is {entry['status']} but lists tests"
        missing = [name for name in tests if name not in defined]
        assert not missing, f"{entry['id']} names tests that do not exist: {missing}"


def test_requirement_markers_point_at_register_entries(
    entries: list[dict[str, Any]], test_sources: str
) -> None:
    """A test marked with a requirement must be listed by that requirement, and vice versa."""
    by_id = {entry["id"]: entry for entry in entries}
    marked: dict[str, set[str]] = {}
    pattern = re.compile(r'((?:@pytest\.mark\.requirement\("\w+"\)\n)+)def (test_\w+)\(')
    for markers, name in pattern.findall(test_sources):
        for identifier in re.findall(r'requirement\("(\w+)"\)', markers):
            assert identifier in by_id, f"{name} is marked with unknown requirement {identifier}"
            marked.setdefault(identifier, set()).add(name)
    for identifier, names in marked.items():
        listed = set(by_id[identifier].get("tests", []))
        assert names <= listed, f"{identifier} does not list {sorted(names - listed)}"


# The milestone the repository has reached. Raise it when a milestone gate passes.
CURRENT_MILESTONE = 2


def test_no_acceptance_test_is_claimed_complete_before_its_milestone(
    entries: list[dict[str, Any]],
) -> None:
    """An acceptance test can be marked built only once its own milestone is reached."""
    early = [
        entry["id"]
        for entry in entries
        if entry["kind"] == "acceptance"
        and entry["status"] in {"built", "verified"}
        and int(entry["milestone"][1:]) > CURRENT_MILESTONE
    ]
    assert early == []
    # Milestone M1 completes elevation and pressure, and zero or reversed flow. Milestone M2
    # completes the heat balance, the exchanger terminal limit, the heat pump balance, the
    # finite source capacity and the failure categories.
    built = sorted(
        entry["id"]
        for entry in entries
        if entry["kind"] == "acceptance" and entry["status"] in {"built", "verified"}
    )
    assert built == ["A01", "A02", "A03", "A04", "A05", "A06", "A13"]


def test_register_file_is_where_the_plan_says(repo_root: Path) -> None:
    assert REGISTER.is_file()
    assert (repo_root / "docs" / "implementation-plan.md").is_file()
