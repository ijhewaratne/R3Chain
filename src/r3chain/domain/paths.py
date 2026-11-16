"""Field paths.

A field path addresses one input of a scenario, for example::

    sites[alpha].usable_pad_area_m2
    well_scenarios[source-alpha].reservoir_parameters[reservoir_temperature_k]

List elements are addressed by their identifier, never by position, so a path stays valid when a
list is reordered. The identifier of an element is its first field whose name ends in ``_id``.

The same paths are used to list assumptions, to report missing inputs, to apply a change and to
describe the difference between two revisions.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel

from r3chain.domain.evidence import Conflict, Known, Provenance, Unknown
from r3chain.domain.records.scenario import ChangeKind, FieldChange

EVIDENCE_STATES = frozenset({"known", "unknown", "conflict"})
EvidenceValue = Known[Any] | Unknown | Conflict[Any]


class Visit(NamedTuple):
    path: str
    value: Any
    parent: BaseModel | None
    field_name: str | None


def identity_of(item: Any) -> str | None:
    """Return the identifier of a record: its first field whose name ends in ``_id``."""
    if isinstance(item, BaseModel):
        for name in type(item).model_fields:
            if name.endswith("_id"):
                value = getattr(item, name)
                return value if isinstance(value, str) else None
    return None


def _walk(
    value: Any, path: str, parent: BaseModel | None, field_name: str | None, into_evidence: bool
) -> Iterator[Visit]:
    yield Visit(path, value, parent, field_name)
    if isinstance(value, Known | Unknown | Conflict) and not into_evidence:
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child_path = f"{path}.{name}" if path else name
            yield from _walk(getattr(value, name), child_path, value, name, into_evidence)
    elif isinstance(value, tuple | list):
        for index, item in enumerate(value):
            key = identity_of(item) or str(index)
            yield from _walk(item, f"{path}[{key}]", parent, field_name, into_evidence)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}[{key}]", parent, str(key), into_evidence)


def iter_evidence(root: BaseModel) -> Iterator[Visit]:
    """Yield every engineering input of a record tree with its field path."""
    for visit in _walk(root, "", None, None, into_evidence=False):
        if isinstance(visit.value, Known | Unknown | Conflict):
            yield visit


def iter_provenance(root: BaseModel) -> Iterator[tuple[str, Provenance]]:
    """Yield every provenance record of a record tree with its field path."""
    for visit in _walk(root, "", None, None, into_evidence=True):
        if isinstance(visit.value, Provenance):
            yield visit.path, visit.value


# --- addressing JSON data -------------------------------------------------------------------

_TOKEN = re.compile(r"\.?([A-Za-z_][A-Za-z0-9_]*)|\[([^\[\]]+)\]")
Token = tuple[Literal["field", "key"], str]


def parse_path(path: str) -> list[Token]:
    tokens: list[Token] = []
    position = 0
    while position < len(path):
        match = _TOKEN.match(path, position)
        if match is None or match.end() == position:
            raise ValueError(f"invalid field path {path!r} at position {position}")
        if match.group(1) is not None:
            tokens.append(("field", match.group(1)))
        else:
            tokens.append(("key", match.group(2)))
        position = match.end()
    if not tokens:
        raise ValueError("a field path cannot be empty")
    return tokens


def _json_identity(item: Any) -> str | None:
    if isinstance(item, dict):
        for name, value in item.items():
            if name.endswith("_id"):
                return value if isinstance(value, str) else None
    return None


def _locate(container: Any, token: Token, path: str) -> str | int:
    kind, name = token
    if isinstance(container, dict):
        if name in container:
            return name
        raise KeyError(f"{path!r}: no field {name!r}")
    if isinstance(container, list) and kind == "key":
        for index, item in enumerate(container):
            if _json_identity(item) == name:
                return index
        if name.isdigit() and int(name) < len(container):
            return int(name)
        raise KeyError(f"{path!r}: no element {name!r}")
    raise KeyError(f"{path!r}: cannot apply {name!r} to a {type(container).__name__}")


def get_at(data: Any, path: str) -> Any:
    current = data
    for token in parse_path(path):
        current = current[_locate(current, token, path)]
    return current


class PatchOperation(NamedTuple):
    """One explicit change. ``set`` replaces a value, ``add`` appends to a list, ``remove``
    deletes a list element or a dictionary key."""

    op: Literal["set", "add", "remove"]
    path: str
    value: Any = None


def apply_patch(data: dict[str, Any], operations: list[PatchOperation]) -> dict[str, Any]:
    """Return a changed copy of JSON data. The input is not modified."""
    result = copy.deepcopy(data)
    for operation in operations:
        tokens = parse_path(operation.path)
        if operation.op == "add":
            target: Any = result
            for token in tokens:
                target = target[_locate(target, token, operation.path)]
            if not isinstance(target, list):
                raise KeyError(f"{operation.path!r}: 'add' needs a list")
            target.append(copy.deepcopy(operation.value))
            continue
        parent: Any = result
        for token in tokens[:-1]:
            parent = parent[_locate(parent, token, operation.path)]
        last = tokens[-1]
        if operation.op == "set":
            if isinstance(parent, dict) and last[1] not in parent:
                # A field that is absent is created. Validation rejects unknown field names.
                parent[last[1]] = copy.deepcopy(operation.value)
            else:
                parent[_locate(parent, last, operation.path)] = copy.deepcopy(operation.value)
        elif operation.op == "remove":
            del parent[_locate(parent, last, operation.path)]
        else:
            raise ValueError(f"unknown patch operation {operation.op!r}")
    return result


def canonical_path(root: BaseModel, path: str) -> str:
    """Rewrite a path in the one form used everywhere: a dot before a record field, brackets
    around a list element or a mapping key.

    Plain JSON cannot tell a record from a mapping, so a path derived from JSON is resolved
    against the record tree. A path that does not resolve is returned unchanged.
    """
    current: Any = root
    result = ""
    for _, name in parse_path(path):
        if isinstance(current, BaseModel):
            if name not in type(current).model_fields:
                return path
            result += f".{name}" if result else name
            current = getattr(current, name)
        elif isinstance(current, dict):
            if name not in current:
                return path
            result += f"[{name}]"
            current = current[name]
        elif isinstance(current, tuple | list):
            match = next((item for item in current if identity_of(item) == name), None)
            if match is None and name.isdigit() and int(name) < len(current):
                match = current[int(name)]
            if match is None:
                return path
            result += f"[{name}]"
            current = match
        else:
            return path
    return result


# --- comparing two revisions ----------------------------------------------------------------


def _is_evidence(value: Any) -> bool:
    return isinstance(value, dict) and value.get("state") in EVIDENCE_STATES


def diff_json(before: Any, after: Any, path: str = "") -> list[FieldChange]:
    """Describe the field level differences between two JSON documents.

    An engineering input (a value with its provenance) is compared as one unit, so a change of
    value and a change of source are reported together.
    """
    if before == after:
        return []
    if _is_evidence(before) or _is_evidence(after):
        return [FieldChange(field_path=path, kind=ChangeKind.CHANGED, before=before, after=after)]
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[FieldChange] = []
        for key in [*before, *(key for key in after if key not in before)]:
            child = f"{path}.{key}" if path and _is_field_name(key) else _join(path, key)
            if key not in after:
                changes.append(
                    FieldChange(field_path=child, kind=ChangeKind.REMOVED, before=before[key])
                )
            elif key not in before:
                changes.append(
                    FieldChange(field_path=child, kind=ChangeKind.ADDED, after=after[key])
                )
            else:
                changes.extend(diff_json(before[key], after[key], child))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        return _diff_lists(before, after, path)
    return [FieldChange(field_path=path, kind=ChangeKind.CHANGED, before=before, after=after)]


def _is_field_name(key: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is not None


def _join(path: str, key: str) -> str:
    if not path:
        return key if _is_field_name(key) else f"[{key}]"
    return f"{path}[{key}]"


def _diff_lists(before: list[Any], after: list[Any], path: str) -> list[FieldChange]:
    before_ids = [_json_identity(item) for item in before]
    after_ids = [_json_identity(item) for item in after]
    if None not in before_ids and None not in after_ids:
        changes: list[FieldChange] = []
        after_by_id = dict(zip(after_ids, after, strict=True))
        before_by_id = dict(zip(before_ids, before, strict=True))
        for identifier, item in before_by_id.items():
            child = f"{path}[{identifier}]"
            if identifier not in after_by_id:
                changes.append(FieldChange(field_path=child, kind=ChangeKind.REMOVED, before=item))
            else:
                changes.extend(diff_json(item, after_by_id[identifier], child))
        for identifier, item in after_by_id.items():
            if identifier not in before_by_id:
                changes.append(
                    FieldChange(
                        field_path=f"{path}[{identifier}]", kind=ChangeKind.ADDED, after=item
                    )
                )
        if not changes and before_ids != after_ids:
            changes.append(
                FieldChange(
                    field_path=path, kind=ChangeKind.CHANGED, before=before_ids, after=after_ids
                )
            )
        return changes
    if len(before) == len(after):
        changes = []
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            changes.extend(diff_json(old, new, f"{path}[{index}]"))
        return changes
    return [FieldChange(field_path=path, kind=ChangeKind.CHANGED, before=before, after=after)]
