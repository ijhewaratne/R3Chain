"""Canonical JSON and content hashing.

Every frozen input, run and cache key is identified by the SHA-256 of its canonical JSON form.
The canonical form sorts keys, uses no insignificant whitespace and rejects NaN and infinity, so
equal content always produces the same hash regardless of how it was written.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

HASH_PREFIX = "sha256:"
_CHECKSUM_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    """Serialise a JSON compatible value deterministically."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(value: Any) -> str:
    """Hash a JSON compatible value by its canonical form."""
    return bytes_checksum(canonical_json(value).encode("utf-8"))


def bytes_checksum(data: bytes) -> str:
    return HASH_PREFIX + hashlib.sha256(data).hexdigest()


def file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return HASH_PREFIX + digest.hexdigest()


def is_checksum(text: str) -> bool:
    return bool(_CHECKSUM_PATTERN.match(text))


def short_hash(checksum: str, length: int = 16) -> str:
    """Return the first hex characters of a checksum, for readable identifiers."""
    return checksum.removeprefix(HASH_PREFIX)[:length]
