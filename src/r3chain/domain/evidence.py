"""Provenance and the three states of an engineering input.

Every engineering input is one of:

Known
    A value with its provenance.
Unknown
    Not yet available. It names who is expected to resolve it.
Conflict
    Two or more sourced values disagree and a reviewer has not chosen between them.

A calculation that needs an input which is not known raises ``NotEstablished``. The pipeline
turns that into a ``not_established`` result. No default value is ever substituted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from r3chain.domain.base import Identifier, NonEmptyStr, Record


class ProvenanceType(StrEnum):
    MEASURED = "measured"
    IMPORTED = "imported"
    GUIDELINE = "guideline"
    USER_SUPPLIED = "user_supplied"
    ESTIMATED = "estimated"
    INFERRED = "inferred"
    SYNTHETIC = "synthetic"


_SOURCE_REQUIRED = frozenset(
    {
        ProvenanceType.MEASURED,
        ProvenanceType.IMPORTED,
        ProvenanceType.GUIDELINE,
        ProvenanceType.SYNTHETIC,
    }
)
_METHOD_REQUIRED = frozenset({ProvenanceType.ESTIMATED, ProvenanceType.INFERRED})


class Provenance(Record):
    """Where a value came from."""

    provenance_type: ProvenanceType
    source_id: Identifier | None = None
    locator: NonEmptyStr | None = None
    author: NonEmptyStr | None = None
    reviewer: NonEmptyStr | None = None
    method: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _has_required_origin(self) -> Provenance:
        kind = self.provenance_type
        if kind in _SOURCE_REQUIRED and self.source_id is None:
            raise ValueError(f"a {kind.value} value must name its source_id")
        if kind in _METHOD_REQUIRED and self.method is None:
            raise ValueError(f"an {kind.value} value must record its method of derivation")
        if kind is ProvenanceType.USER_SUPPLIED and self.author is None:
            raise ValueError("a user_supplied value must name its author")
        return self


class Known[T](Record):
    state: Literal["known"] = "known"
    value: T
    provenance: Provenance


class Unknown(Record):
    state: Literal["unknown"] = "unknown"
    reason: NonEmptyStr
    owner: NonEmptyStr


class Conflict[T](Record):
    state: Literal["conflict"] = "conflict"
    candidates: tuple[Known[T], ...] = Field(min_length=2)
    reviewer: NonEmptyStr | None = None
    note: str = ""


type Evidence[V] = Annotated[Known[V] | Unknown | Conflict[V], Field(discriminator="state")]


class MissingInput(Record):
    """An input that a calculation needed and that was not established."""

    field_path: str
    state: Literal["unknown", "conflict"]
    reason: str
    owner: str | None = None


class NotEstablished(Exception):
    """Raised when a calculation needs inputs that are unknown or in conflict."""

    def __init__(self, missing: tuple[MissingInput, ...]) -> None:
        self.missing = missing
        paths = ", ".join(item.field_path for item in missing)
        super().__init__(f"inputs not established: {paths}")


def missing_input[T](evidence: Unknown | Conflict[T], field_path: str) -> MissingInput:
    if isinstance(evidence, Unknown):
        return MissingInput(
            field_path=field_path, state="unknown", reason=evidence.reason, owner=evidence.owner
        )
    reason = evidence.note or f"{len(evidence.candidates)} sourced values disagree"
    return MissingInput(
        field_path=field_path, state="conflict", reason=reason, owner=evidence.reviewer
    )


def require[T](evidence: Known[T] | Unknown | Conflict[T], field_path: str) -> T:
    """Return the value of a known input, or raise ``NotEstablished``."""
    if isinstance(evidence, Known):
        return evidence.value
    raise NotEstablished((missing_input(evidence, field_path),))


class InputReader:
    """Collects every missing input of a calculation so they are reported together."""

    def __init__(self) -> None:
        self._missing: list[MissingInput] = []

    def get[T](self, evidence: Known[T] | Unknown | Conflict[T], field_path: str) -> T | None:
        if isinstance(evidence, Known):
            return evidence.value
        self._missing.append(missing_input(evidence, field_path))
        return None

    @property
    def missing(self) -> tuple[MissingInput, ...]:
        return tuple(self._missing)

    def raise_if_missing(self) -> None:
        if self._missing:
            raise NotEstablished(self.missing)
