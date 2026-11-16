"""Base classes for all domain records.

Record
    An immutable value object. Unknown fields are rejected and NaN or infinity never validate.

VersionedRecord
    A top level record with a schema name and a schema version. Data written under an older
    version is upgraded through registered migrations when it is loaded. Data with a version that
    has no migration path is rejected instead of being read loosely.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, Strict, StringConstraints, model_validator

from r3chain.domain.hashing import content_hash

# Identifiers appear inside field paths such as ``sites[alpha].land_status``, so they must not
# contain path syntax.
Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]*$", max_length=120)
]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
# A real number. Integers are accepted. Strings, booleans, NaN and infinity are rejected, so a
# quoted number or a YAML 'yes' can never become an engineering value by coercion.
Real = Annotated[float, Strict()]
PositiveReal = Annotated[float, Strict(), Field(gt=0.0)]
NonNegativeReal = Annotated[float, Strict(), Field(ge=0.0)]

JsonDict = dict[str, Any]
Migration = Callable[[JsonDict], JsonDict]

_MIGRATIONS: dict[tuple[str, str], tuple[str, Migration]] = {}


def register_migration(
    schema_name: str, from_version: str, to_version: str
) -> Callable[[Migration], Migration]:
    """Register a function that upgrades stored data from one schema version to the next."""

    def decorator(function: Migration) -> Migration:
        key = (schema_name, from_version)
        if key in _MIGRATIONS:
            raise ValueError(f"a migration from {schema_name} {from_version} already exists")
        _MIGRATIONS[key] = (to_version, function)
        return function

    return decorator


class Record(BaseModel):
    """Immutable value object with strict field handling."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    def to_json_dict(self) -> JsonDict:
        return self.model_dump(mode="json")

    def content_hash(self) -> str:
        return content_hash(self.to_json_dict())


class VersionedRecord(Record):
    """Top level record that carries its schema version.

    Subclasses declare ``SCHEMA_NAME`` and a ``schema_version`` field whose type is the literal
    current version and whose default is that same version.
    """

    SCHEMA_NAME: ClassVar[str]
    schema_version: str

    @classmethod
    def current_schema_version(cls) -> str:
        default = cls.model_fields["schema_version"].default
        if not isinstance(default, str):
            raise TypeError(f"{cls.__name__} must declare a default schema_version")
        return default

    @model_validator(mode="before")
    @classmethod
    def _upgrade_schema_version(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        current = cls.current_schema_version()
        version = data.get("schema_version")
        if version is None:
            return {**data, "schema_version": current}
        upgraded = dict(data)
        visited: set[str] = set()
        while version != current:
            step = _MIGRATIONS.get((cls.SCHEMA_NAME, version))
            if step is None or version in visited:
                raise ValueError(
                    f"{cls.SCHEMA_NAME}: no migration from schema_version {version!r} "
                    f"to {current!r}"
                )
            visited.add(version)
            to_version, migrate = step
            upgraded = migrate(dict(upgraded))
            upgraded["schema_version"] = to_version
            version = to_version
        return upgraded
