"""Time basis fixtures (acceptance test A07 at fixture level).

Interval integration, leap years and daylight saving transitions in Europe/Berlin.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from r3chain.domain.timeseries import (
    IntervalSeries,
    SeriesKind,
    hours_in_local_year,
    local_year_bounds_utc,
    localize_wallclock,
    same_interval_set,
)
from r3chain.domain.units import JOULES_PER_MWH, UnitError

BERLIN = ZoneInfo("Europe/Berlin")


def constant_power(year: int, power_w: float, step_s: float = 3600.0) -> IntervalSeries:
    start, end = local_year_bounds_utc(year)
    count = round((end - start).total_seconds() / step_s)
    return IntervalSeries.regular(
        series_id=f"power-{year}",
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        start_utc=start,
        step_s=step_s,
        values=[power_w] * count,
    )


@pytest.mark.requirement("A07")
def test_annual_energy_uses_actual_intervals() -> None:
    # A constant 1 MW delivers 1 MWh every hour. 2026 has 365 days and 2028 has 366 days, so
    # the yearly energy is 8760 MWh and 8784 MWh. Nothing may assume 8760 hours.
    assert hours_in_local_year(2026) == 8760
    assert hours_in_local_year(2028) == 8784
    assert constant_power(2026, 1.0e6).energy_j() == pytest.approx(8760 * JOULES_PER_MWH)
    assert constant_power(2028, 1.0e6).energy_j() == pytest.approx(8784 * JOULES_PER_MWH)
    # The local year starts at midnight in Berlin, which is 23:00 UTC on the day before.
    start, end = local_year_bounds_utc(2026)
    assert start == datetime(2025, 12, 31, 23, tzinfo=UTC)
    assert end == datetime(2026, 12, 31, 23, tzinfo=UTC)


def test_power_and_energy_semantics_agree() -> None:
    # The same physical quantity given as quarter hourly mean power and as quarter hourly energy.
    start = datetime(2026, 1, 1, tzinfo=UTC)
    power = IntervalSeries.regular(
        series_id="p",
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        start_utc=start,
        step_s=900.0,
        values=[2.0e6, 4.0e6, 0.0, 2.0e6],
    )
    energy = IntervalSeries.regular(
        series_id="e",
        unit="J",
        kind=SeriesKind.INTERVAL_TOTAL,
        start_utc=start,
        step_s=900.0,
        values=[value * 900.0 for value in power.values],
    )
    # (2 + 4 + 0 + 2) MW for a quarter of an hour each is 2 MWh.
    assert power.energy_j() == pytest.approx(2.0 * JOULES_PER_MWH)
    assert energy.energy_j() == pytest.approx(power.energy_j())
    assert power.time_weighted_mean() == pytest.approx(2.0e6)
    assert same_interval_set(power, energy)


def test_a_power_series_labelled_as_energy_is_not_integrated() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    wrong = IntervalSeries.regular(
        series_id="x",
        unit="W",
        kind=SeriesKind.INTERVAL_TOTAL,
        start_utc=start,
        step_s=3600.0,
        values=[1.0],
    )
    with pytest.raises(UnitError):
        wrong.energy_j()
    temperature = IntervalSeries.regular(
        series_id="t",
        unit="K",
        kind=SeriesKind.INTERVAL_MEAN,
        start_utc=start,
        step_s=3600.0,
        values=[350.0],
    )
    with pytest.raises(UnitError):
        temperature.energy_j()


@pytest.mark.requirement("A07")
def test_daylight_saving_days_have_23_and_25_hours() -> None:
    # In 2026 the clocks go forward on 29 March and back on 25 October.
    def hours_of_local_day(month: int, day: int) -> float:
        start = datetime(2026, month, day, tzinfo=BERLIN).astimezone(UTC)
        end = (datetime(2026, month, day, tzinfo=BERLIN) + timedelta(days=1)).replace(tzinfo=BERLIN)
        return (end.astimezone(UTC) - start).total_seconds() / 3600.0

    assert hours_of_local_day(3, 29) == 23
    assert hours_of_local_day(10, 25) == 25
    assert hours_of_local_day(6, 15) == 24

    # A constant 1 MW on the short day delivers 23 MWh and on the long day 25 MWh.
    year = constant_power(2026, 1.0e6)
    for month, day, expected in ((3, 29, 23.0), (10, 25, 25.0)):
        start = datetime(2026, month, day, tzinfo=BERLIN).astimezone(UTC)
        end = start + timedelta(hours=expected)
        assert year.between(start, end).energy_j() == pytest.approx(expected * JOULES_PER_MWH)


@pytest.mark.requirement("A07")
def test_repeated_local_hour_is_resolved() -> None:
    # On 25 October 2026 the local hour from 02:00 to 03:00 occurs twice. Six hourly wall clock
    # readings from midnight therefore span six real hours, without a gap or an overlap.
    wall_clock = [
        datetime(2026, 10, 25, 0),
        datetime(2026, 10, 25, 1),
        datetime(2026, 10, 25, 2),
        datetime(2026, 10, 25, 2),
        datetime(2026, 10, 25, 3),
        datetime(2026, 10, 25, 4),
    ]
    converted = localize_wallclock(wall_clock)
    assert converted[0] == datetime(2026, 10, 24, 22, tzinfo=UTC)
    assert [later - earlier for earlier, later in pairwise(converted)] == [timedelta(hours=1)] * 5


def test_nonexistent_local_time_is_rejected() -> None:
    # On 29 March 2026 the local clock jumps from 02:00 straight to 03:00.
    with pytest.raises(ValueError, match="does not exist"):
        localize_wallclock([datetime(2026, 3, 29, 2, 30)])
    with pytest.raises(ValueError, match="naive"):
        localize_wallclock([datetime(2026, 3, 29, 12, tzinfo=UTC)])
    with pytest.raises(ValueError, match="not after"):
        localize_wallclock([datetime(2026, 6, 1, 12), datetime(2026, 6, 1, 11)])


def test_gap_is_detected() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    series = IntervalSeries(
        series_id="gappy",
        unit="W",
        kind=SeriesKind.INTERVAL_MEAN,
        starts_utc=(start, start + timedelta(hours=1), start + timedelta(hours=3)),
        durations_s=(3600.0, 3600.0, 3600.0),
        values=(1.0, 1.0, 1.0),
    )
    assert series.gaps() == ((start + timedelta(hours=2), start + timedelta(hours=3)),)
    assert not series.is_contiguous
    assert not series.covers(start, start + timedelta(hours=4))
    complete = constant_power(2026, 1.0)
    assert complete.is_contiguous
    assert complete.covers(*local_year_bounds_utc(2026))
    assert not same_interval_set(series, complete)


def test_naive_and_non_utc_times_are_rejected() -> None:
    common = {"series_id": "x", "unit": "W", "kind": "interval_mean"}
    with pytest.raises(ValidationError, match="timezone"):
        IntervalSeries(
            **common, starts_utc=(datetime(2026, 1, 1),), durations_s=(3600.0,), values=(1.0,)
        )
    berlin_time = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    with pytest.raises(ValidationError, match="UTC"):
        IntervalSeries(**common, starts_utc=(berlin_time,), durations_s=(3600.0,), values=(1.0,))


def test_invalid_intervals_are_rejected() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    common = {"series_id": "x", "unit": "W", "kind": "interval_mean"}
    with pytest.raises(ValidationError, match="positive duration"):
        IntervalSeries(**common, starts_utc=(start,), durations_s=(0.0,), values=(1.0,))
    with pytest.raises(ValidationError, match="overlaps"):
        IntervalSeries(
            **common,
            starts_utc=(start, start + timedelta(minutes=30)),
            durations_s=(3600.0, 3600.0),
            values=(1.0, 1.0),
        )
    with pytest.raises(ValidationError, match="strictly increasing"):
        IntervalSeries(
            **common, starts_utc=(start, start), durations_s=(3600.0, 3600.0), values=(1.0, 1.0)
        )
    with pytest.raises(ValidationError, match="same length"):
        IntervalSeries(**common, starts_utc=(start,), durations_s=(3600.0,), values=(1.0, 2.0))
    with pytest.raises(ValidationError, match="canonical unit"):
        IntervalSeries(
            series_id="x",
            unit="MW",
            kind="interval_mean",
            starts_utc=(start,),
            durations_s=(3600.0,),
            values=(1.0,),
        )
    with pytest.raises(ValidationError):
        IntervalSeries(**common, starts_utc=(start,), durations_s=(3600.0,), values=(float("nan"),))
