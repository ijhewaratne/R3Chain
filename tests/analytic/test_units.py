"""Units, references and conversions."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from r3chain.domain.units import (
    STANDARD_ATMOSPHERE_PA,
    Depth,
    DepthKind,
    Pressure,
    PressureReference,
    UnitError,
    bar_to_pa,
    celsius_to_kelvin,
    joule_to_mwh,
    kelvin_to_celsius,
    mwh_to_joule,
    pa_to_bar,
    require_same_reference,
    require_unit_suffix,
    to_canonical,
    unit_for_field,
)

finite = st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False)


@given(st.floats(min_value=-273.15, max_value=2000.0, allow_nan=False))
def test_celsius_kelvin_round_trip(temperature_c: float) -> None:
    assert kelvin_to_celsius(celsius_to_kelvin(temperature_c)) == pytest.approx(
        temperature_c, abs=1e-9
    )


@given(finite)
def test_pressure_and_energy_round_trips(value: float) -> None:
    assert pa_to_bar(bar_to_pa(value)) == pytest.approx(value, rel=1e-12, abs=1e-12)
    assert joule_to_mwh(mwh_to_joule(value)) == pytest.approx(value, rel=1e-12, abs=1e-12)


def test_known_conversion_values() -> None:
    assert celsius_to_kelvin(0.0) == 273.15
    assert celsius_to_kelvin(80.0) == pytest.approx(353.15)
    assert bar_to_pa(1.0) == 100_000.0
    assert mwh_to_joule(1.0) == 3.6e9
    with pytest.raises(UnitError):
        celsius_to_kelvin(-300.0)
    with pytest.raises(UnitError):
        kelvin_to_celsius(-1.0)


def test_display_units_convert_to_canonical_units() -> None:
    assert to_canonical(80.0, "degC") == (pytest.approx(353.15), "K", None)
    assert to_canonical(5.0, "delta_K") == (5.0, "delta_K", None)
    assert to_canonical(2.5, "MW") == (2.5e6, "W", None)
    assert to_canonical(1.0, "MWh") == (3.6e9, "J", None)
    assert to_canonical(36.0, "t/h") == (pytest.approx(10.0), "kg/s", None)
    assert to_canonical(4.0, "%/a") == (pytest.approx(0.04), "1/a", None)
    gauge = to_canonical(4.0, "bar", pressure_reference=PressureReference.GAUGE)
    assert gauge == (400_000.0, "Pa", PressureReference.GAUGE)


def test_ambiguous_and_unknown_units_are_rejected() -> None:
    # A temperature of 5 degrees and a temperature difference of 5 degrees are different things.
    with pytest.raises(UnitError, match="ambiguous"):
        to_canonical(5.0, "C")
    # A volumetric flow cannot become a mass flow without a density.
    with pytest.raises(UnitError, match="density"):
        to_canonical(100.0, "m3/h")
    with pytest.raises(UnitError, match="unknown unit"):
        to_canonical(1.0, "furlong")
    # A pressure without its reference is ambiguous.
    with pytest.raises(UnitError, match="absolute or gauge"):
        to_canonical(4.0, "bar")
    with pytest.raises(UnitError, match="non pressure"):
        to_canonical(4.0, "kW", pressure_reference=PressureReference.GAUGE)


def test_pressure_keeps_its_reference() -> None:
    gauge = Pressure(value_pa=400_000.0, reference=PressureReference.GAUGE)
    absolute = gauge.to_absolute(STANDARD_ATMOSPHERE_PA)
    assert absolute.reference is PressureReference.ABSOLUTE
    assert absolute.value_pa == pytest.approx(501_325.0)
    assert absolute.to_gauge(STANDARD_ATMOSPHERE_PA) == gauge
    assert absolute.to_absolute(STANDARD_ATMOSPHERE_PA) is absolute
    # Mixing the two references without a conversion is refused.
    with pytest.raises(UnitError, match="inconsistent pressure references"):
        require_same_reference(gauge, absolute)
    assert require_same_reference(gauge, gauge) is PressureReference.GAUGE
    # A vacuum is a negative gauge pressure. An absolute pressure cannot be negative.
    Pressure(value_pa=-20_000.0, reference=PressureReference.GAUGE)
    with pytest.raises(ValidationError):
        Pressure(value_pa=-1.0, reference=PressureReference.ABSOLUTE)


def test_depth_states_its_kind() -> None:
    assert Depth(value_m=3000.0, kind=DepthKind.VERTICAL).kind is DepthKind.VERTICAL
    with pytest.raises(ValidationError):
        Depth(value_m=-1.0, kind=DepthKind.MEASURED)
    with pytest.raises(ValidationError):
        Depth.model_validate({"value_m": 3000.0})


def test_field_names_carry_their_unit() -> None:
    assert unit_for_field("return_temperature_k") == "K"
    assert unit_for_field("min_approach_delta_k") == "delta_K"
    assert unit_for_field("heat_transfer_coefficient_w_m2_k") == "W/(m2 K)"
    assert unit_for_field("max_mass_flow_kg_s") == "kg/s"
    assert unit_for_field("dynamic_viscosity_pa_s") == "Pa s"
    assert unit_for_field("usable_pad_area_m2") == "m2"
    assert unit_for_field("net_to_gross_fraction") == "1"
    assert unit_for_field("discount_rate_per_year") == "1/a"
    assert unit_for_field("name") is None
    with pytest.raises(UnitError, match="no unit suffix"):
        require_unit_suffix("reservoir_temperature")
