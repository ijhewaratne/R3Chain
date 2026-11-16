"""The assumption register.

The register lists every engineering input of a scenario with its value, unit, provenance and
state. It is derived from the scenario, so it is always complete and always current. A user does
not have to know which questions to ask to discover an assumption (workshop point W24).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel

from r3chain.domain.evidence import Conflict, Known, MissingInput, Unknown, missing_input
from r3chain.domain.hashing import content_hash, short_hash
from r3chain.domain.paths import Visit, iter_evidence
from r3chain.domain.records.common import Elevation
from r3chain.domain.records.runs import Assumption, AssumptionStatus
from r3chain.domain.records.scenarios import CostItem
from r3chain.domain.units import Depth, Pressure, unit_for_field


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _unit(visit: Visit, value: Any) -> str | None:
    if isinstance(value, Pressure):
        return f"Pa ({value.reference.value})"
    if isinstance(value, Depth):
        return f"m ({value.kind.value})"
    if isinstance(value, Elevation):
        return f"m ({value.vertical_datum})"
    if isinstance(visit.parent, CostItem) and visit.field_name == "amount":
        item = visit.parent
        return (
            f"{item.currency} {item.price_year} {item.price_convention.value}, "
            f"{item.amount_basis.value}"
        )
    if visit.field_name is None:
        return None
    return unit_for_field(visit.field_name)


def _assumption_id(field_path: str) -> str:
    return "asm-" + short_hash(content_hash(field_path), 12)


def _row(visit: Visit) -> Assumption:
    evidence = visit.value
    identifier = _assumption_id(visit.path)
    if isinstance(evidence, Known):
        provenance = evidence.provenance
        return Assumption(
            assumption_id=identifier,
            field_path=visit.path,
            status=AssumptionStatus.ACTIVE,
            value=_plain(evidence.value),
            unit=_unit(visit, evidence.value),
            provenance_type=provenance.provenance_type,
            source_id=provenance.source_id,
            locator=provenance.locator,
            author=provenance.author,
            reviewer=provenance.reviewer,
            method=provenance.method,
        )
    if isinstance(evidence, Unknown):
        return Assumption(
            assumption_id=identifier,
            field_path=visit.path,
            status=AssumptionStatus.UNKNOWN,
            unit=_unit(visit, None),
            note=evidence.reason,
            owner=evidence.owner,
        )
    assert isinstance(evidence, Conflict)
    first = evidence.candidates[0].value
    return Assumption(
        assumption_id=identifier,
        field_path=visit.path,
        status=AssumptionStatus.CONFLICT,
        value=[_plain(candidate.value) for candidate in evidence.candidates],
        unit=_unit(visit, first),
        reviewer=evidence.reviewer,
        note=evidence.note,
        owner=evidence.reviewer,
    )


def assumption_register(root: BaseModel) -> tuple[Assumption, ...]:
    """List every engineering input below a record, in document order."""
    return tuple(_row(visit) for visit in iter_evidence(root))


def missing_inputs(root: BaseModel, prefix: str = "") -> tuple[MissingInput, ...]:
    """List every input below a record that is unknown or in conflict."""
    return tuple(
        missing_input(visit.value, prefix + visit.path)
        for visit in iter_evidence(root)
        if not isinstance(visit.value, Known)
    )
