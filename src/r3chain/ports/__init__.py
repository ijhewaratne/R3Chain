"""Interfaces between the core and the engines behind it."""

from r3chain.domain.base import VersionedRecord
from r3chain.ports.dispatch import DispatchRequest, DispatchResult
from r3chain.ports.network import NetworkCase, NetworkResult
from r3chain.ports.source import SourceRequest, SourceResult

# Contract records whose schemas are exported and version checked.
CONTRACT_RECORDS: tuple[type[VersionedRecord], ...] = (
    SourceRequest,
    SourceResult,
    NetworkCase,
    NetworkResult,
    DispatchRequest,
    DispatchResult,
)

__all__ = [
    "CONTRACT_RECORDS",
    "DispatchRequest",
    "DispatchResult",
    "NetworkCase",
    "NetworkResult",
    "SourceRequest",
    "SourceResult",
]
