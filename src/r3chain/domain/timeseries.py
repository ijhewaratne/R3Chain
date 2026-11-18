"""Interval time series.

Specification section 7.1: timestamps are timezone aware and stored in UTC, every interval has an
explicit duration, and a series states whether a value is the mean over its interval (for example
power) or the total within it (for example energy). Annual totals are computed from the actual
intervals. Nothing assumes that a year has 8760 hours.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, AwareDatetime, Field, model_validator

from r3chain.domain.base import Identifier, Real, Record
from r3chain.domain.units import CANONICAL_UNITS, UnitError

DEFAULT_PRESENTATION_TIMEZONE = "Europe/Berlin"


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps are stored in UTC. Convert local times before storing them")
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


def timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"unknown timezone {name!r}") from error


class SeriesKind(StrEnum):
    # The value is the mean over the interval, for example power in W or a temperature in K.
    INTERVAL_MEAN = "interval_mean"
    # The value is the total within the interval, for example energy in J.
    INTERVAL_TOTAL = "interval_total"


class IntervalSeries(Record):
    """Values on explicit, non overlapping UTC intervals."""

    series_id: Identifier
    unit: str
    kind: SeriesKind
    starts_utc: tuple[UtcDatetime, ...]
    durations_s: tuple[Real, ...]
    values: tuple[Real, ...] = Field(json_schema_extra={"unit": "see unit"})

    @model_validator(mode="after")
    def _intervals_are_valid(self) -> IntervalSeries:
        if self.unit not in CANONICAL_UNITS:
            raise ValueError(f"series unit {self.unit!r} is not a canonical unit")
        count = len(self.starts_utc)
        if count == 0:
            raise ValueError("a series needs at least one interval")
        if len(self.durations_s) != count or len(self.values) != count:
            raise ValueError("starts, durations and values must have the same length")
        if any(duration <= 0.0 for duration in self.durations_s):
            raise ValueError("every interval needs a positive duration")
        ends = self.ends_utc
        for index in range(1, count):
            if self.starts_utc[index] <= self.starts_utc[index - 1]:
                raise ValueError("interval starts must be strictly increasing")
            if self.starts_utc[index] < ends[index - 1]:
                raise ValueError(f"interval {index} overlaps the previous interval")
        return self

    @classmethod
    def regular(
        cls,
        *,
        series_id: str,
        unit: str,
        kind: SeriesKind,
        start_utc: datetime,
        step_s: float,
        values: Sequence[float],
    ) -> IntervalSeries:
        """Build a series of equal intervals that follow one another without gaps."""
        starts = tuple(
            start_utc + timedelta(seconds=step_s * index) for index in range(len(values))
        )
        return cls(
            series_id=series_id,
            unit=unit,
            kind=kind,
            starts_utc=starts,
            durations_s=tuple(step_s for _ in values),
            values=tuple(values),
        )

    @property
    def ends_utc(self) -> tuple[datetime, ...]:
        return tuple(
            start + timedelta(seconds=duration)
            for start, duration in zip(self.starts_utc, self.durations_s, strict=True)
        )

    @property
    def start_utc(self) -> datetime:
        return self.starts_utc[0]

    @property
    def end_utc(self) -> datetime:
        return self.ends_utc[-1]

    @property
    def total_duration_s(self) -> float:
        return math.fsum(self.durations_s)

    def gaps(self) -> tuple[tuple[datetime, datetime], ...]:
        """Return every stretch of time between the first and last interval that has no value."""
        ends = self.ends_utc
        return tuple(
            (ends[index - 1], self.starts_utc[index])
            for index in range(1, len(self.starts_utc))
            if self.starts_utc[index] > ends[index - 1]
        )

    @property
    def is_contiguous(self) -> bool:
        return not self.gaps()

    def covers(self, start_utc: datetime, end_utc: datetime) -> bool:
        """True when the series has a value for every instant of the requested period."""
        return self.is_contiguous and self.start_utc <= start_utc and self.end_utc >= end_utc

    def interval_set(self) -> tuple[tuple[datetime, float], ...]:
        return tuple(zip(self.starts_utc, self.durations_s, strict=True))

    def between(self, start_utc: datetime, end_utc: datetime) -> IntervalSeries:
        """Return the intervals that lie completely inside the half open period."""
        selected = [
            index
            for index, (start, end) in enumerate(zip(self.starts_utc, self.ends_utc, strict=True))
            if start >= start_utc and end <= end_utc
        ]
        if not selected:
            raise ValueError("no interval lies inside the requested period")
        return IntervalSeries(
            series_id=self.series_id,
            unit=self.unit,
            kind=self.kind,
            starts_utc=tuple(self.starts_utc[index] for index in selected),
            durations_s=tuple(self.durations_s[index] for index in selected),
            values=tuple(self.values[index] for index in selected),
        )

    def energy_j(self) -> float:
        """Integrate to energy. Valid for mean power in W and for interval energy in J."""
        if self.kind is SeriesKind.INTERVAL_MEAN and self.unit == "W":
            return math.fsum(
                value * duration
                for value, duration in zip(self.values, self.durations_s, strict=True)
            )
        if self.kind is SeriesKind.INTERVAL_TOTAL and self.unit == "J":
            return math.fsum(self.values)
        raise UnitError(
            f"cannot integrate a {self.kind.value} series in {self.unit!r} to energy. "
            "Use mean power in W or interval energy in J"
        )

    def time_weighted_mean(self) -> float:
        if self.kind is not SeriesKind.INTERVAL_MEAN:
            raise UnitError("a time weighted mean needs an interval_mean series")
        weighted = math.fsum(
            value * duration for value, duration in zip(self.values, self.durations_s, strict=True)
        )
        return weighted / self.total_duration_s


def same_interval_set(*series: IntervalSeries) -> bool:
    """True when all series are defined on exactly the same intervals."""
    if len(series) < 2:
        return True
    reference = series[0].interval_set()
    return all(item.interval_set() == reference for item in series[1:])


def local_year_bounds_utc(
    year: int, timezone_name: str = DEFAULT_PRESENTATION_TIMEZONE
) -> tuple[datetime, datetime]:
    """Return the UTC instants at which a local calendar year starts and ends."""
    zone = timezone(timezone_name)
    start = datetime(year, 1, 1, tzinfo=zone).astimezone(UTC)
    end = datetime(year + 1, 1, 1, tzinfo=zone).astimezone(UTC)
    return start, end


def hours_in_local_year(year: int, timezone_name: str = DEFAULT_PRESENTATION_TIMEZONE) -> float:
    start, end = local_year_bounds_utc(year, timezone_name)
    return (end - start).total_seconds() / 3600.0


def localize_wallclock(
    local_times: Sequence[datetime], timezone_name: str = DEFAULT_PRESENTATION_TIMEZONE
) -> tuple[datetime, ...]:
    """Convert naive local wall clock times, in chronological order, to UTC.

    * A wall clock time that does not exist (the hour skipped in spring) is rejected.
    * A wall clock time that occurs twice (the hour repeated in autumn) is read as the first
      occurrence, unless that would not move time forward, in which case it is the second.
    """
    zone = timezone(timezone_name)
    result: list[datetime] = []
    for local in local_times:
        if local.tzinfo is not None:
            raise ValueError("localize_wallclock expects naive local times")
        first = local.replace(tzinfo=zone, fold=0)
        second = local.replace(tzinfo=zone, fold=1)
        first_utc = first.astimezone(UTC)
        if first_utc.astimezone(zone).replace(tzinfo=None) != local:
            raise ValueError(f"local time {local.isoformat()} does not exist in {timezone_name}")
        chosen = first_utc
        ambiguous = first.utcoffset() != second.utcoffset()
        if ambiguous and result and first_utc <= result[-1]:
            chosen = second.astimezone(UTC)
        if result and chosen <= result[-1]:
            raise ValueError(f"local time {local.isoformat()} is not after the previous timestamp")
        result.append(chosen)
    return tuple(result)
