"""Reading typed values from a policy record.

A calculation names the values it needs. A value that is absent or of the wrong type makes the
policy unusable for that calculation, and the error names the policy and the value. No default
is ever filled in.
"""

from __future__ import annotations

from r3chain.domain.records.runs import Policy


class PolicyError(Exception):
    """A policy lacks a value that a calculation needs, or holds it with the wrong type."""


def _where(policy: Policy) -> str:
    return f"policy {policy.policy_id} version {policy.version}"


def number(policy: Policy, name: str) -> float:
    value = policy.parameters.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PolicyError(f"{_where(policy)} lacks the number {name}")
    return float(value)


def count(policy: Policy, name: str) -> int:
    value = number(policy, name)
    if not value.is_integer() or value < 0:
        raise PolicyError(f"{_where(policy)}: {name} must be a whole number")
    return int(value)


def text(policy: Policy, name: str) -> str:
    value = policy.parameters.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{_where(policy)} lacks the text {name}")
    return value.strip()


def optional_text(policy: Policy, name: str) -> str | None:
    """Return a non-empty string parameter when present, or ``None`` if omitted."""
    value = policy.parameters.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{_where(policy)}: {name} must be a non-empty string")
    return value.strip()


def optional_non_negative_number(policy: Policy, name: str) -> float | None:
    """Return a non-negative finite number parameter when present, or ``None`` if omitted."""
    value = policy.parameters.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or float(value) < 0.0:
        raise PolicyError(f"{_where(policy)}: {name} must be a non-negative number")
    return float(value)


def flag(policy: Policy, name: str) -> bool:
    value = policy.parameters.get(name)
    if not isinstance(value, bool):
        raise PolicyError(f"{_where(policy)} lacks the yes or no value {name}")
    return value


def names(policy: Policy, name: str) -> tuple[str, ...]:
    """A comma separated list of identifiers."""
    return tuple(item.strip() for item in text(policy, name).split(",") if item.strip())


def numbers_by_key(policy: Policy, prefix: str, suffix: str) -> dict[str, float]:
    """Every number named prefix + key + suffix, by key. For example setback_housing_m."""
    found: dict[str, float] = {}
    for name, value in policy.parameters.items():
        if name.startswith(prefix) and name.endswith(suffix) and len(name) > len(prefix + suffix):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise PolicyError(f"{_where(policy)}: {name} must be a number")
            found[name[len(prefix) : len(name) - len(suffix)]] = float(value)
    return found


def origin(policy: Policy) -> str:
    return _where(policy)
