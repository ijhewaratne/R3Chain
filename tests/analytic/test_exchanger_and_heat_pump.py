"""Analytic fixtures for fluids, the counterflow exchanger and the heat pump.

Expected values are derived by hand in the comments, or come from a second calculation that
does not share code with the function under test.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from hypothesis import given
from hypothesis import strategies as st

from r3chain.coupling.exchanger import (
    ExchangerLimit,
    Stream,
    counterflow_effectiveness,
    pressure_loss_pa,
    rate_counterflow,
    terminal_checks,
)
from r3chain.coupling.fluids import (
    ConstantHeatCapacity,
    TabulatedHeatCapacity,
    mean_heat_capacity_j_kg_k,
    mean_heat_capacity_of_stream,
)
from r3chain.coupling.heat_pump import (
    CarnotScreeningHeatPump,
    HeatPumpLimit,
    HeatPumpPerformance,
    MapHeatPump,
    OutsideMapDomain,
    operate_heat_pump,
)
from r3chain.domain.records.plant import HeatPumpMap
from r3chain.domain.records.runs import CheckOutcome

# An illustrative liquid with a round heat capacity, so that the arithmetic can be followed.
LIQUID = ConstantHeatCapacity(4000.0, "illustrative value for hand calculations")

# Heat capacity of water in J/(kg K) at a few temperatures in K.
WATER_TABLE = (
    (283.0, 4195.5),
    (293.0, 4184.4),
    (303.0, 4180.1),
    (313.0, 4179.6),
    (323.0, 4181.5),
    (333.0, 4185.1),
    (343.0, 4190.2),
    (353.0, 4196.9),
    (363.0, 4205.3),
)
WATER = TabulatedHeatCapacity(
    temperatures_k=tuple(row[0] for row in WATER_TABLE),
    heat_capacities_j_kg_k=tuple(row[1] for row in WATER_TABLE),
    basis="test table",
)


def hot(mass_flow_kg_s: float, temperature_k: float = 370.0) -> Stream:
    return Stream(mass_flow_kg_s, temperature_k, LIQUID)


def cold(mass_flow_kg_s: float, temperature_k: float = 320.0) -> Stream:
    return Stream(mass_flow_kg_s, temperature_k, LIQUID)


# --- fluids ---------------------------------------------------------------------------------


def _interpolated(temperature_k: float) -> float:
    """Linear interpolation in the water table, written independently of the model."""
    for (t0, c0), (t1, c1) in pairwise(WATER_TABLE):
        if t0 <= temperature_k <= t1:
            return c0 + (c1 - c0) * (temperature_k - t0) / (t1 - t0)
    raise AssertionError("temperature outside the test table")


@pytest.mark.requirement("A01")
def test_enthalpy_of_tabulated_heat_capacity_matches_numerical_integration() -> None:
    # Independent calculation: midpoint rule over 30 000 steps of 1 mK.
    low, high, steps = 323.15, 353.15, 30_000
    width = (high - low) / steps
    integral = sum(_interpolated(low + (index + 0.5) * width) for index in range(steps)) * width

    difference = WATER.enthalpy_j_kg(high) - WATER.enthalpy_j_kg(low)
    assert difference == pytest.approx(integral, rel=1e-9)
    # A constant heat capacity of 4180 J/(kg K) would be 0.2 percent off over this range. That
    # is more than the heat tolerance of the numerical policy, which is why it needs a review.
    assert abs(4180.0 * (high - low) - integral) / integral > 0.0015
    assert mean_heat_capacity_j_kg_k(WATER, low, high) == pytest.approx(integral / (high - low))


def test_tabulated_enthalpy_can_be_inverted_and_is_never_extrapolated() -> None:
    for temperature in (283.0, 290.5, 313.0, 347.77, 363.0):
        enthalpy = WATER.enthalpy_j_kg(temperature)
        assert WATER.temperature_k(enthalpy) == pytest.approx(temperature, abs=1e-9)
    with pytest.raises(ValueError, match="not extrapolated"):
        WATER.enthalpy_j_kg(370.0)
    with pytest.raises(ValueError, match="not extrapolated"):
        WATER.temperature_k(-1.0)
    with pytest.raises(ValueError, match="increase strictly"):
        TabulatedHeatCapacity((300.0, 300.0), (4180.0, 4180.0), "t")


def test_a_constant_heat_capacity_must_state_its_basis() -> None:
    with pytest.raises(ValueError, match="basis"):
        ConstantHeatCapacity(4180.0, " ")
    with pytest.raises(ValueError, match="positive"):
        ConstantHeatCapacity(0.0, "t")
    fluid = ConstantHeatCapacity(4180.0, "t")
    assert fluid.temperature_k(fluid.enthalpy_j_kg(351.2)) == pytest.approx(351.2)


def test_mean_heat_capacity_of_a_stream_reproduces_the_reported_heat() -> None:
    # An engine reports 1 000 000 W for 8 kg/s cooled from 360 K to 330 K.
    #   cp = 1 000 000 / (8 * 30) = 4166.67 J/(kg K)
    fluid = mean_heat_capacity_of_stream(
        heat_w=1.0e6,
        mass_flow_kg_s=8.0,
        hot_temperature_k=360.0,
        cold_temperature_k=330.0,
        reported_by="an engine",
    )
    assert fluid.heat_capacity_j_kg_k == pytest.approx(4166.6667, rel=1e-6)
    assert "an engine" in fluid.basis
    with pytest.raises(ValueError):
        mean_heat_capacity_of_stream(
            heat_w=1.0e6,
            mass_flow_kg_s=8.0,
            hot_temperature_k=330.0,
            cold_temperature_k=330.0,
            reported_by="an engine",
        )


# --- effectiveness --------------------------------------------------------------------------


def test_counterflow_effectiveness_matches_the_closed_forms() -> None:
    # Equal heat capacity rates: effectiveness = NTU / (1 + NTU) = 2 / 3.
    assert counterflow_effectiveness(2.0, 1.0) == pytest.approx(2.0 / 3.0)
    # One stream with a far larger rate: effectiveness = 1 - exp(-NTU).
    assert counterflow_effectiveness(1.0, 0.0) == pytest.approx(1.0 - math.exp(-1.0))
    # NTU 1.5 and ratio 0.5:
    #   decay = exp(-1.5 * 0.5) = 0.472367
    #   effectiveness = (1 - 0.472367) / (1 - 0.5 * 0.472367) = 0.690785
    assert counterflow_effectiveness(1.5, 0.5) == pytest.approx(0.690785, abs=1e-6)
    # The two forms join without a jump.
    assert counterflow_effectiveness(2.0, 1.0 - 1e-10) == pytest.approx(2.0 / 3.0, abs=1e-8)
    assert counterflow_effectiveness(0.0, 0.3) == 0.0
    with pytest.raises(ValueError):
        counterflow_effectiveness(1.0, 1.5)


# --- exchanger limits -----------------------------------------------------------------------


def test_exchanger_rating_matches_the_hand_calculation() -> None:
    # Hot stream 10 kg/s at 370 K, heat capacity rate 40 000 W/K.
    # Cold stream 20 kg/s at 320 K, heat capacity rate 80 000 W/K.
    # UA = 60 000 W/K: NTU = 60 000 / 40 000 = 1.5, ratio = 0.5, effectiveness = 0.690785.
    #   Q = 0.690785 * 40 000 * (370 - 320) = 1 381 570 W
    #   hot outlet  = 370 - 1 381 570 / 40 000 = 335.461 K
    #   cold outlet = 320 + 1 381 570 / 80 000 = 337.270 K
    point = rate_counterflow(hot(10.0), cold(20.0), ua_w_k=60_000.0, min_approach_delta_k=5.0)
    assert point.binding_limit is ExchangerLimit.RATING
    assert point.heat_w == pytest.approx(1_381_570.0, rel=1e-6)
    assert point.hot_outlet_temperature_k == pytest.approx(335.461, abs=1e-3)
    assert point.cold_outlet_temperature_k == pytest.approx(337.270, abs=1e-3)
    assert point.hot_end_approach_delta_k == pytest.approx(370.0 - 337.270, abs=1e-3)
    assert point.cold_end_approach_delta_k == pytest.approx(335.461 - 320.0, abs=1e-3)
    assert point.effectiveness_fraction == pytest.approx(0.690785, abs=1e-6)
    assert abs(point.balance_residual_w) < 1e-6


def test_exchanger_heats_the_cold_stream_to_its_target_and_no_further() -> None:
    # Target 330 K: Q = 80 000 * 10 = 800 000 W, well inside the rating of 1 381 570 W.
    point = rate_counterflow(
        hot(10.0),
        cold(20.0),
        ua_w_k=60_000.0,
        min_approach_delta_k=5.0,
        cold_outlet_target_k=330.0,
    )
    assert point.binding_limit is ExchangerLimit.DEMAND
    assert point.heat_w == pytest.approx(800_000.0)
    assert point.hot_outlet_temperature_k == pytest.approx(350.0)
    assert point.rating_heat_w == pytest.approx(1_381_570.0, rel=1e-6)


def test_each_exchanger_limit_binds_where_the_hand_calculation_says() -> None:
    large = 1.0e9  # a surface so large that the rating never binds first
    # Hot end: cold stream 5 kg/s (20 000 W/K) may reach 370 - 5 = 365 K.
    #   Q = 20 000 * (365 - 320) = 900 000 W
    point = rate_counterflow(hot(10.0), cold(5.0), ua_w_k=large, min_approach_delta_k=5.0)
    assert (point.binding_limit, point.heat_w) == (
        ExchangerLimit.HOT_END_APPROACH,
        pytest.approx(900_000.0),
    )
    assert point.hot_end_approach_delta_k == pytest.approx(5.0)

    # Cold end: hot stream 5 kg/s (20 000 W/K) may cool to 320 + 5 = 325 K.
    #   Q = 20 000 * (370 - 325) = 900 000 W
    point = rate_counterflow(hot(5.0), cold(20.0), ua_w_k=large, min_approach_delta_k=5.0)
    assert (point.binding_limit, point.heat_w) == (
        ExchangerLimit.COLD_END_APPROACH,
        pytest.approx(900_000.0),
    )
    assert point.cold_end_approach_delta_k == pytest.approx(5.0)

    # Minimum hot outlet 340 K: Q = 40 000 * (370 - 340) = 1 200 000 W.
    point = rate_counterflow(
        hot(10.0),
        cold(20.0),
        ua_w_k=large,
        min_approach_delta_k=5.0,
        min_hot_outlet_temperature_k=340.0,
    )
    assert (point.binding_limit, point.heat_w) == (
        ExchangerLimit.MIN_HOT_OUTLET,
        pytest.approx(1_200_000.0),
    )

    # A hot stream that is colder than the cold stream transfers nothing.
    point = rate_counterflow(
        hot(10.0, 310.0), cold(20.0), ua_w_k=60_000.0, min_approach_delta_k=5.0
    )
    assert point.heat_w == 0.0
    with pytest.raises(ValueError, match="positive mass flow"):
        rate_counterflow(hot(0.0), cold(20.0), ua_w_k=60_000.0, min_approach_delta_k=5.0)


@pytest.mark.requirement("A01")
def test_exchanger_balance_closes_on_enthalpy_with_a_temperature_dependent_fluid() -> None:
    source = Stream(12.0, 360.0, WATER)
    sink = Stream(18.0, 300.0, WATER)
    point = rate_counterflow(source, sink, ua_w_k=90_000.0, min_approach_delta_k=3.0)
    # Independent balance: integrate the heat capacity over each stream with the midpoint rule.

    def heat(mass_flow: float, low: float, high: float) -> float:
        steps = 20_000
        width = (high - low) / steps
        total = sum(_interpolated(low + (index + 0.5) * width) for index in range(steps))
        return mass_flow * total * width

    released = heat(12.0, point.hot_outlet_temperature_k, 360.0)
    absorbed = heat(18.0, 300.0, point.cold_outlet_temperature_k)
    assert released == pytest.approx(point.heat_w, rel=1e-8)
    assert absorbed == pytest.approx(point.heat_w, rel=1e-8)
    assert abs(point.balance_residual_w) < 1e-6 * point.heat_w


@given(
    hot_flow=st.floats(0.5, 60.0),
    cold_flow=st.floats(0.5, 60.0),
    hot_inlet=st.floats(300.0, 420.0),
    cold_inlet=st.floats(290.0, 360.0),
    conductance=st.floats(1.0e3, 5.0e6),
    approach=st.floats(0.0, 15.0),
    target_rise=st.floats(0.0, 80.0),
)
def test_exchanger_never_breaks_a_limit(
    hot_flow: float,
    cold_flow: float,
    hot_inlet: float,
    cold_inlet: float,
    conductance: float,
    approach: float,
    target_rise: float,
) -> None:
    point = rate_counterflow(
        hot(hot_flow, hot_inlet),
        cold(cold_flow, cold_inlet),
        ua_w_k=conductance,
        min_approach_delta_k=approach,
        cold_outlet_target_k=cold_inlet + target_rise,
    )
    slack = 1e-6
    assert point.heat_w >= 0.0
    assert point.heat_w <= point.rating_heat_w + slack
    assert abs(point.balance_residual_w) <= 1e-6 * max(point.heat_w, 1.0)
    assert point.cold_outlet_temperature_k <= cold_inlet + target_rise + slack
    if point.heat_w > 0.0:
        assert point.hot_end_approach_delta_k >= approach - slack
        assert point.cold_end_approach_delta_k >= approach - slack


@given(
    flow=st.floats(1.0, 40.0),
    more=st.floats(1.0, 3.0),
    hot_inlet=st.floats(330.0, 420.0),
    conductance=st.floats(1.0e4, 2.0e6),
)
def test_more_hot_flow_never_transfers_less_heat(
    flow: float, more: float, hot_inlet: float, conductance: float
) -> None:
    # The search for the geothermal flow relies on this.
    def heat(mass_flow: float) -> float:
        return rate_counterflow(
            hot(mass_flow, hot_inlet),
            cold(15.0),
            ua_w_k=conductance,
            min_approach_delta_k=5.0,
            cold_outlet_target_k=350.0,
        ).heat_w

    assert heat(flow * more) >= heat(flow) * (1.0 - 1e-12)


# --- terminal checks ------------------------------------------------------------------------


@pytest.mark.requirement("A02")
def test_terminal_checks_name_the_failing_end() -> None:
    # Hot end. The source enters at 345 K and the network needs 353.15 K with a 5 K approach.
    #   hot end difference = 345 - 353.15 = -8.15 K, margin = -8.15 - 5 = -13.15 K
    # The cold end is checked for the duty the hot end permits (340 K at the cold outlet):
    #   Q = 80 000 * (340 - 320) = 1 600 000 W, hot outlet = 345 - 1 600 000 / 160 000 = 335 K
    #   cold end difference = 335 - 320 = 15 K, margin = 10 K
    hot_end, cold_end, permitted = terminal_checks(
        hot(40.0, 345.0),
        cold(20.0),
        required_cold_outlet_k=353.15,
        min_approach_delta_k=5.0,
        threshold_origin="test",
    )
    assert hot_end.check_id == "exchanger_hot_end_approach"
    assert (hot_end.outcome, hot_end.observed) == (CheckOutcome.FAIL, pytest.approx(-8.15))
    assert hot_end.margin == pytest.approx(-13.15)
    assert (cold_end.outcome, cold_end.margin) == (CheckOutcome.PASS, pytest.approx(10.0))
    assert permitted == pytest.approx(1_600_000.0)

    # Cold end. The source is hot enough at 370 K, but 2 kg/s carry too little heat.
    #   required Q = 80 000 * (353.15 - 320) = 2 652 000 W
    #   hot outlet = 370 - 2 652 000 / 8 000 = 38.5 K, far below the cold inlet
    hot_end, cold_end, permitted = terminal_checks(
        hot(2.0),
        cold(20.0),
        required_cold_outlet_k=353.15,
        min_approach_delta_k=5.0,
        threshold_origin="test",
    )
    assert hot_end.outcome is CheckOutcome.PASS
    assert hot_end.margin == pytest.approx(370.0 - 353.15 - 5.0)
    assert cold_end.check_id == "exchanger_cold_end_approach"
    assert (cold_end.outcome, cold_end.observed) == (CheckOutcome.FAIL, pytest.approx(38.5 - 320.0))
    # Both terminals permit only 8 000 * (370 - 325) = 360 000 W.
    assert permitted == pytest.approx(360_000.0)
    assert (hot_end.unit, hot_end.limit, hot_end.threshold_origin) == (
        "K (difference)",
        5.0,
        "test",
    )


def test_pressure_loss_scales_with_the_square_of_the_flow() -> None:
    # 60 000 Pa at 15 kg/s. At 7.5 kg/s: 60 000 * 0.5^2 = 15 000 Pa.
    assert pressure_loss_pa(60_000.0, 15.0, 7.5) == pytest.approx(15_000.0)
    assert pressure_loss_pa(60_000.0, 15.0, 30.0) == pytest.approx(240_000.0)
    with pytest.raises(ValueError):
        pressure_loss_pa(60_000.0, 0.0, 7.5)


# --- heat pump ------------------------------------------------------------------------------

MAP = HeatPumpMap(
    map_id="test-map",
    description="invented table for tests",
    source_inlet_temperatures_k=(318.15, 333.15),
    sink_outlet_temperatures_k=(343.15, 353.15),
    heating_cop_ratio=((4.9, 4.1), (6.2, 5.0)),
    max_condenser_heat_w=((1_150_000.0, 1_100_000.0), (1_350_000.0, 1_300_000.0)),
)


class FixedHeatPump:
    """A heat pump with one coefficient of performance, for hand calculations."""

    basis = "performance_map"
    description = "fixed test heat pump"

    def __init__(self, cop: float, capacity_w: float | None = None) -> None:
        self._performance = HeatPumpPerformance(cop, capacity_w)

    def performance(
        self, source_inlet_temperature_k: float, sink_outlet_temperature_k: float
    ) -> HeatPumpPerformance:
        return self._performance


def test_performance_map_is_interpolated_and_never_extrapolated() -> None:
    pump = MapHeatPump(MAP)
    assert pump.basis == "performance_map" and "test-map" in pump.description
    assert pump.performance(318.15, 343.15).heating_cop_ratio == pytest.approx(4.9)
    assert pump.performance(333.15, 353.15).max_condenser_heat_w == pytest.approx(1_300_000.0)
    # Centre of the cell: the mean of the four corners.
    #   COP      = (4.9 + 4.1 + 6.2 + 5.0) / 4 = 5.05
    #   capacity = (1150 + 1100 + 1350 + 1300) / 4 = 1225 kW
    centre = pump.performance(325.65, 348.15)
    assert centre.heating_cop_ratio == pytest.approx(5.05)
    assert centre.max_condenser_heat_w == pytest.approx(1_225_000.0)

    with pytest.raises(OutsideMapDomain, match="source inlet temperature") as low:
        pump.performance(310.0, 348.15)
    assert (low.value.value, low.value.bound) == (310.0, 318.15)
    with pytest.raises(OutsideMapDomain, match="sink outlet temperature") as high:
        pump.performance(325.0, 360.0)
    assert (high.value.value, high.value.bound) == (360.0, 353.15)


def test_a_performance_map_must_be_a_complete_table() -> None:
    data = MAP.to_json_dict()
    with pytest.raises(ValueError, match="2 rows of 2 values"):
        HeatPumpMap.model_validate({**data, "heating_cop_ratio": [[4.9, 4.1]]})
    with pytest.raises(ValueError, match="increase strictly"):
        HeatPumpMap.model_validate({**data, "sink_outlet_temperatures_k": [353.15, 343.15]})
    with pytest.raises(ValueError, match="above 1"):
        HeatPumpMap.model_validate({**data, "heating_cop_ratio": [[4.9, 0.9], [6.2, 5.0]]})


def test_screening_model_is_a_labelled_fraction_of_carnot() -> None:
    # Source inlet 330 K, sink outlet 350 K, differences 10 K and 5 K, efficiency 0.5.
    #   condensing = 355 K, evaporating = 320 K, lift = 35 K
    #   COP = 0.5 * 355 / 35 = 5.0714
    pump = CarnotScreeningHeatPump(0.5, 10.0, 5.0)
    assert pump.basis == "screening_model"
    assert "Not equipment performance" in pump.description
    performance = pump.performance(330.0, 350.0)
    assert performance.heating_cop_ratio == pytest.approx(5.0714286, rel=1e-7)
    assert performance.max_condenser_heat_w is None
    with pytest.raises(OutsideMapDomain, match="coefficient of performance"):
        CarnotScreeningHeatPump(0.05, 10.0, 5.0).performance(330.0, 350.0)
    with pytest.raises(OutsideMapDomain, match="condensing temperature above"):
        pump.performance(400.0, 350.0)
    with pytest.raises(ValueError, match="carnot_efficiency_fraction"):
        CarnotScreeningHeatPump(1.2, 10.0, 5.0)


@pytest.mark.requirement("A03")
def test_heat_pump_balance_closes_under_the_stated_loss_model() -> None:
    # COP 4, required condenser heat 1 000 000 W, 10 percent of the compressor electricity lost.
    #   W = 1 000 000 / 4 = 250 000 W
    #   loss = 0.1 * 250 000 = 25 000 W
    #   Q_evaporator = 1 000 000 - 0.9 * 250 000 = 775 000 W
    point = operate_heat_pump(
        FixedHeatPump(4.0),
        source_inlet_temperature_k=330.0,
        sink_outlet_temperature_k=350.0,
        required_condenser_heat_w=1_000_000.0,
        max_evaporator_heat_w=2_000_000.0,
        compressor_heat_loss_fraction=0.1,
    )
    assert point.binding_limit is HeatPumpLimit.DEMAND
    assert point.compressor_electricity_w == pytest.approx(250_000.0)
    assert point.compressor_heat_loss_w == pytest.approx(25_000.0)
    assert point.evaporator_heat_w == pytest.approx(775_000.0)
    # Condenser heat equals evaporator heat plus the compressor input that is not lost.
    assert point.condenser_heat_w == pytest.approx(
        point.evaporator_heat_w + (1.0 - 0.1) * point.compressor_electricity_w
    )
    assert abs(point.balance_residual_w) < 1e-6

    # Without a loss, condenser heat is evaporator heat plus all of the compressor input.
    tight = operate_heat_pump(
        FixedHeatPump(4.0),
        source_inlet_temperature_k=330.0,
        sink_outlet_temperature_k=350.0,
        required_condenser_heat_w=1_000_000.0,
        max_evaporator_heat_w=2_000_000.0,
        compressor_heat_loss_fraction=0.0,
    )
    assert tight.evaporator_heat_w == pytest.approx(750_000.0)
    assert tight.condenser_heat_w == pytest.approx(
        tight.evaporator_heat_w + tight.compressor_electricity_w
    )


def test_heat_pump_is_limited_by_its_capacity_and_by_its_source() -> None:
    def operate(pump: FixedHeatPump, source_heat_w: float) -> tuple[HeatPumpLimit, float]:
        point = operate_heat_pump(
            pump,
            source_inlet_temperature_k=330.0,
            sink_outlet_temperature_k=350.0,
            required_condenser_heat_w=1_000_000.0,
            max_evaporator_heat_w=source_heat_w,
            compressor_heat_loss_fraction=0.1,
        )
        assert abs(point.balance_residual_w) < 1e-6
        return point.binding_limit, point.condenser_heat_w

    # A capacity of 800 000 W is below the requirement.
    assert operate(FixedHeatPump(4.0, 800_000.0), 2.0e6) == (
        HeatPumpLimit.CAPACITY,
        pytest.approx(800_000.0),
    )
    # The source can give 387 500 W. The evaporator share of the condenser heat is
    # 1 - 0.9 / 4 = 0.775, so the condenser delivers 387 500 / 0.775 = 500 000 W.
    assert operate(FixedHeatPump(4.0), 387_500.0) == (
        HeatPumpLimit.SOURCE_HEAT,
        pytest.approx(500_000.0),
    )
    with pytest.raises(ValueError, match="compressor_heat_loss_fraction"):
        operate_heat_pump(
            FixedHeatPump(4.0),
            source_inlet_temperature_k=330.0,
            sink_outlet_temperature_k=350.0,
            required_condenser_heat_w=1.0,
            max_evaporator_heat_w=1.0,
            compressor_heat_loss_fraction=1.0,
        )


@pytest.mark.requirement("A03")
@given(
    cop=st.floats(1.2, 12.0),
    required=st.floats(0.0, 5.0e6),
    source_heat=st.floats(0.0, 5.0e6),
    loss=st.floats(0.0, 0.3),
    capacity=st.one_of(st.none(), st.floats(1.0e4, 5.0e6)),
)
def test_heat_pump_balance_closes_for_any_operating_point(
    cop: float, required: float, source_heat: float, loss: float, capacity: float | None
) -> None:
    point = operate_heat_pump(
        FixedHeatPump(cop, capacity),
        source_inlet_temperature_k=330.0,
        sink_outlet_temperature_k=350.0,
        required_condenser_heat_w=required,
        max_evaporator_heat_w=source_heat,
        compressor_heat_loss_fraction=loss,
    )
    scale = max(point.condenser_heat_w, 1.0)
    assert abs(point.balance_residual_w) <= 1e-9 * scale
    assert point.condenser_heat_w <= required + 1e-9 * scale
    assert point.evaporator_heat_w <= source_heat + 1e-9 * scale
    assert point.evaporator_heat_w <= point.condenser_heat_w
    if capacity is not None:
        assert point.condenser_heat_w <= capacity + 1e-9 * scale
