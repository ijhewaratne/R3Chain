"""Export and check the JSON Schemas of all versioned records.

One file per record and schema version is committed under ``schemas/``. Changing a record without
changing its schema version makes the committed file differ from the generated one, and the check
fails. That is how the schema evolution rules of the implementation plan are enforced.
"""

from __future__ import annotations

import json
from pathlib import Path

from r3chain.coupling.envelope import SourceEnvelope
from r3chain.coupling.loop import CouplingResult
from r3chain.domain.base import VersionedRecord
from r3chain.domain.records import VERSIONED_RECORDS as DOMAIN_RECORDS
from r3chain.explain.records import ExplanationRecord
from r3chain.geospatial.importers import LayerMapping
from r3chain.ports import CONTRACT_RECORDS
from r3chain.services.contracts import JobRecord, ToolResult

DEFAULT_SCHEMA_DIRECTORY = Path("schemas")

# Every record whose schema is exported and version checked.
VERSIONED_RECORDS: tuple[type[VersionedRecord], ...] = (
    *DOMAIN_RECORDS,
    *CONTRACT_RECORDS,
    SourceEnvelope,
    CouplingResult,
    LayerMapping,
    ExplanationRecord,
    JobRecord,
    ToolResult,
)


class SchemaChangedError(Exception):
    """A record changed while its schema version stayed the same."""


def schema_filename(record: type[VersionedRecord]) -> str:
    return f"{record.SCHEMA_NAME}-{record.current_schema_version()}.json"


def render_schema(record: type[VersionedRecord]) -> str:
    return json.dumps(record.model_json_schema(), indent=2, sort_keys=True) + "\n"


def export_schemas(directory: Path, *, force: bool = False) -> list[Path]:
    """Write every schema. An existing file is only replaced when ``force`` is set."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for record in VERSIONED_RECORDS:
        path = directory / schema_filename(record)
        text = render_schema(record)
        if path.exists():
            if path.read_text(encoding="utf-8") == text:
                continue
            if not force:
                raise SchemaChangedError(
                    f"{record.SCHEMA_NAME} changed but its schema_version is still "
                    f"{record.current_schema_version()}. Raise the version and add a migration"
                )
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def check_schemas(directory: Path) -> list[str]:
    """Return every difference between the committed schemas and the current records."""
    problems: list[str] = []
    for record in VERSIONED_RECORDS:
        path = directory / schema_filename(record)
        if not path.exists():
            problems.append(f"{path.name} is missing. Run 'r3chain schemas export'")
        elif path.read_text(encoding="utf-8") != render_schema(record):
            problems.append(
                f"{path.name} differs from the {record.__name__} record. Raise schema_version "
                "and add a migration"
            )
    return problems
