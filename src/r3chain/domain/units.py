"""Units, references and explicit conversions.

Conventions (specification section 7.1):

* Internal values are SI: metres, seconds, kilograms, kelvin, pascals, watts, joules.
* A field name carries its unit as a suffix, for example ``return_temperature_k``.
* A temperature difference is not a temperature. Differences use ``_delta_k``.
* A pressure always states whether it is absolute or gauge.
* A depth always states whether it is vertical depth or measured well length.

Display units such as degrees Celsius, bar and MWh exist only at the edges and are converted
through the functions in this module.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from pydantic import Field, model_validator

from r3chain.domain.base import Real, Record

STANDARD_GRAVITY_M_S2 = 9.80665
STANDARD_ATMOSPHERE_PA = 101_325.0
ZERO_CELSIUS_K = 273.15
SECONDS_PER_HOUR = 3600.0
JOULES_PER_KWH = 3.6e6
JOULES_PER_MWH = 3.6e9
PASCALS_PER_BAR = 1.0e5


class UnitError(ValueError):
    """A value has a missing, unknown, ambiguous or inconsistent unit or reference."""


# --- explicit conversions -----------------------------------------------------------------


def celsius_to_kelvin(temperature_c: float) -> float:
    temperature_k = temperature_c + ZERO_CELSIUS_K
    if temperature_k < 0.0:
        raise UnitError(f"{temperature_c} degC is below absolute zero")
    return temperature_k


def kelvin_to_celsius(temperature_k: float) -> float:
    if temperature_k < 0.0:
        raise UnitError(f"{temperature_k} K is below absolute zero")
    return temperature_k - ZERO_CELSIUS_K


def bar_to_pa(pressure_bar: float) -> float:
    return pressure_bar * PASCALS_PER_BAR


def pa_to_bar(pressure_pa: float) -> float:
    return pressure_pa / PASCALS_PER_BAR


def mwh_to_joule(energy_mwh: float) -> float:
    return energy_mwh * JOULES_PER_MWH


def joule_to_mwh(energy_j: float) -> float:
    return energy_j / JOULES_PER_MWH


def kw_to_w(power_kw: float) -> float:
    return power_kw * 1.0e3


def w_to_kw(power_w: float) -> float:
    return power_w / 1.0e3


def currency_per_mwh_to_per_j(price_per_mwh: float) -> float:
    return price_per_mwh / JOULES_PER_MWH


def currency_per_j_to_per_mwh(price_per_j: float) -> float:
    return price_per_j * JOULES_PER_MWH


# --- pressure and depth carry their reference ---------------------------------------------


class PressureReference(StrEnum):
    ABSOLUTE = "absolute"
    GAUGE = "gauge"


class Pressure(Record):
    """A pressure together with its reference."""

    value_pa: Real
    reference: PressureReference

    @model_validator(mode="after")
    def _absolute_is_not_negative(self) -> Pressure:
        if self.reference is PressureReference.ABSOLUTE and self.value_pa < 0.0:
            raise ValueError("an absolute pressure cannot be negative")
        return self

    def to_absolute(self, ambient_pa: float) -> Pressure:
        """Convert to absolute pressure using an explicit ambient pressure."""
        if self.reference is PressureReference.ABSOLUTE:
            return self
        return Pressure(value_pa=self.value_pa + ambient_pa, reference=PressureReference.ABSOLUTE)

    def to_gauge(self, ambient_pa: float) -> Pressure:
        """Convert to gauge pressure using an explicit ambient pressure."""
        if self.reference is PressureReference.GAUGE:
            return self
        return Pressure(value_pa=self.value_pa - ambient_pa, reference=PressureReference.GAUGE)


def require_same_reference(*pressures: Pressure) -> PressureReference:
    """Return the shared reference, or fail if absolute and gauge values are mixed."""
    references = {pressure.reference for pressure in pressures}
    if len(references) != 1:
        raise UnitError(
            "inconsistent pressure references: absolute and gauge values cannot be combined "
            "without an explicit ambient pressure"
        )
    return next(iter(references))


class DepthKind(StrEnum):
    VERTICAL = "vertical"
    MEASURED = "measured"


class Depth(Record):
    """A depth together with its kind: true vertical depth or measured length along the well."""

    value_m: Real = Field(ge=0.0)
    kind: DepthKind


# --- field name suffixes ------------------------------------------------------------------

# Suffix, canonical unit. Matching always prefers the longest suffix.
_SUFFIX_UNITS: tuple[tuple[str, str], ...] = (
    ("_delta_k", "delta_K"),
    ("_currency_per_j", "currency/J"),
    ("_currency_per_m", "currency/m"),
    ("_currency", "currency"),
    ("_w_m2_k", "W/(m2 K)"),
    ("_j_kg_k", "J/(kg K)"),
    ("_w_m_k", "W/(m K)"),
    ("_kg_m3", "kg/m3"),
    ("_per_year", "1/a"),
    ("_fraction", "1"),
    ("_ratio", "1"),
    ("_kg_s", "kg/s"),
    ("_j_kg", "J/kg"),
    ("_pa_s", "Pa s"),
    ("_years", "a"),
    ("_count", "count"),
    ("_w_k", "W/K"),
    ("_m_s", "m/s"),
    ("_m2", "m2"),
    ("_m3", "m3"),
    ("_pa", "Pa"),
    ("_k", "K"),
    ("_w", "W"),
    ("_j", "J"),
    ("_m", "m"),
    ("_s", "s"),
)
UNIT_SUFFIXES: tuple[tuple[str, str], ...] = tuple(
    sorted(_SUFFIX_UNITS, key=lambda item: len(item[0]), reverse=True)
)
CANONICAL_UNITS: frozenset[str] = frozenset(unit for _, unit in _SUFFIX_UNITS) | {"currency"}


def unit_for_field(name: str) -> str | None:
    """Return the canonical unit implied by a field name, or None if it carries no unit."""
    for suffix, unit in UNIT_SUFFIXES:
        if name.endswith(suffix):
            return unit
    return None


def require_unit_suffix(name: str) -> str:
    unit = unit_for_field(name)
    if unit is None:
        raise UnitError(
            f"{name!r} has no unit suffix. Name numeric inputs with their unit, "
            "for example 'reservoir_temperature_k' or 'net_to_gross_fraction'."
        )
    return unit


# --- display units to canonical units -----------------------------------------------------


class CanonicalQuantity(NamedTuple):
    value: float
    unit: str
    pressure_reference: PressureReference | None = None


_LINEAR_UNITS: dict[str, tuple[str, float]] = {
    "K": ("K", 1.0),
    "delta_K": ("delta_K", 1.0),
    "delta_degC": ("delta_K", 1.0),
    "W": ("W", 1.0),
    "kW": ("W", 1.0e3),
    "MW": ("W", 1.0e6),
    "GW": ("W", 1.0e9),
    "J": ("J", 1.0),
    "kJ": ("J", 1.0e3),
    "MJ": ("J", 1.0e6),
    "GJ": ("J", 1.0e9),
    "Wh": ("J", 3.6e3),
    "kWh": ("J", JOULES_PER_KWH),
    "MWh": ("J", JOULES_PER_MWH),
    "GWh": ("J", 3.6e12),
    "kg/s": ("kg/s", 1.0),
    "t/h": ("kg/s", 1000.0 / SECONDS_PER_HOUR),
    "m": ("m", 1.0),
    "mm": ("m", 1.0e-3),
    "km": ("m", 1.0e3),
    "m2": ("m2", 1.0),
    "ha": ("m2", 1.0e4),
    "m3": ("m3", 1.0),
    "s": ("s", 1.0),
    "min": ("s", 60.0),
    "h": ("s", SECONDS_PER_HOUR),
    "m/s": ("m/s", 1.0),
    "1": ("1", 1.0),
    "%": ("1", 0.01),
    "a": ("a", 1.0),
    "1/a": ("1/a", 1.0),
    "%/a": ("1/a", 0.01),
    "currency": ("currency", 1.0),
    "currency/m": ("currency/m", 1.0),
    "currency/J": ("currency/J", 1.0),
    "currency/kWh": ("currency/J", 1.0 / JOULES_PER_KWH),
    "currency/MWh": ("currency/J", 1.0 / JOULES_PER_MWH),
}
_PRESSURE_UNITS: dict[str, float] = {
    "Pa": 1.0,
    "mbar": 100.0,
    "kPa": 1.0e3,
    "bar": PASCALS_PER_BAR,
    "MPa": 1.0e6,
}
_AMBIGUOUS_UNITS: dict[str, str] = {
    "C": "use 'degC' for a temperature or 'delta_K' for a temperature difference",
    "deg": "use 'degC' for a temperature or 'delta_K' for a temperature difference",
    "m3/h": "a volumetric flow needs a density. Convert it to kg/s explicitly",
    "l/s": "a volumetric flow needs a density. Convert it to kg/s explicitly",
    "bara": "use 'bar' with pressure_reference='absolute'",
    "barg": "use 'bar' with pressure_reference='gauge'",
}


def to_canonical(
    value: float, unit: str, *, pressure_reference: PressureReference | None = None
) -> CanonicalQuantity:
    """Convert a value in a display unit to its canonical SI form.

    Unknown and ambiguous units are rejected. A pressure needs an explicit reference.
    """
    if unit in _AMBIGUOUS_UNITS:
        raise UnitError(f"ambiguous unit {unit!r}: {_AMBIGUOUS_UNITS[unit]}")
    if unit in _PRESSURE_UNITS:
        if pressure_reference is None:
            raise UnitError(f"a pressure in {unit!r} must state an absolute or gauge reference")
        pressure = Pressure(value_pa=value * _PRESSURE_UNITS[unit], reference=pressure_reference)
        return CanonicalQuantity(pressure.value_pa, "Pa", pressure.reference)
    if pressure_reference is not None:
        raise UnitError(f"a pressure reference was given for the non pressure unit {unit!r}")
    if unit == "degC":
        return CanonicalQuantity(celsius_to_kelvin(value), "K")
    if unit in _LINEAR_UNITS:
        canonical_unit, factor = _LINEAR_UNITS[unit]
        return CanonicalQuantity(value * factor, canonical_unit)
    raise UnitError(f"unknown unit {unit!r}")
