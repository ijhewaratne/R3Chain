"""Write the chronological 2026 time series of the synthetic study.

Every series uses UTC timestamps and canonical SI units (`W`, `1`, `currency/J`). Seasonal demand
matches the representative operating cases (`3.0 MW` winter peak, `~1.8 MW` shoulder average,
`0.6 MW` summer minimum). Waste heat base load availability has a 14-day summer planned overhaul,
and geothermal availability has a 7-day planned spring maintenance outage. Electricity and fuel
prices vary between winter and summer.

    .venv/bin/python examples/synthetic/series/make_series.py

Running it again writes the identical file. A change to the series is a new dataset version.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from r3chain.domain.units import currency_per_mwh_to_per_j

HERE = Path(__file__).resolve().parent
START_UTC = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
HOURS = 365 * 24


def _hourly_timestamps() -> list[str]:
    return [(START_UTC + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ") for h in range(HOURS)]


def _seasonal_demand_w() -> list[float]:
    # Peak in mid-January (~3.0 MW), minimum in mid-July (~0.6 MW), diurnal cycle +-10% clamped
    # within [600 kW, 3000 kW].
    values: list[float] = []
    for hour in range(HOURS):
        day = hour / 24.0
        seasonal = 0.5 * (1.0 + math.cos(2.0 * math.pi * (day - 15.0) / 365.0))
        diurnal = 0.08 * math.sin(2.0 * math.pi * ((hour % 24) - 6) / 24.0)
        load_mw = min(max(0.6 + 2.4 * seasonal + diurnal, 0.6), 3.0)
        values.append(round(load_mw * 1.0e6, 1))
    return values


def _waste_availability() -> list[float]:
    # 14-day planned maintenance shutdown in mid-July (hours 4600..4936).
    return [0.0 if 4600 <= hour < 4936 else 1.0 for hour in range(HOURS)]


def _geothermal_availability() -> list[float]:
    # 7-day planned inspection outage in mid-May (hours 3100..3268).
    return [0.0 if 3100 <= hour < 3268 else 1.0 for hour in range(HOURS)]


def _is_winter_hour(hour: int) -> bool:
    day = (START_UTC + timedelta(hours=hour)).date()
    return day.month in {1, 2, 3, 11, 12}


def _electricity_price_per_j() -> list[float]:
    winter = round(currency_per_mwh_to_per_j(120.0), 15)
    summer = round(currency_per_mwh_to_per_j(65.0), 15)
    return [winter if _is_winter_hour(hour) else summer for hour in range(HOURS)]


def _fuel_price_per_j() -> list[float]:
    winter = round(currency_per_mwh_to_per_j(85.0), 15)
    summer = round(currency_per_mwh_to_per_j(65.0), 15)
    return [winter if _is_winter_hour(hour) else summer for hour in range(HOURS)]


def _heat_tariff_per_j() -> list[float]:
    rate = round(currency_per_mwh_to_per_j(105.0), 15)
    return [rate] * HOURS


def main() -> None:
    starts = _hourly_timestamps()
    durations = [3600.0] * HOURS

    def entry(series_id: str, unit: str, values: list[float]) -> dict[str, object]:
        return {
            "series_id": series_id,
            "unit": unit,
            "kind": "interval_mean",
            "starts_utc": starts,
            "durations_s": durations,
            "values": values,
        }

    bundle = {
        "series": [
            entry("syn-demand", "W", _seasonal_demand_w()),
            entry("syn-waste-availability", "1", _waste_availability()),
            entry("syn-geo-availability", "1", _geothermal_availability()),
            entry("syn-electricity-price", "currency/J", _electricity_price_per_j()),
            entry("syn-fuel-price", "currency/J", _fuel_price_per_j()),
            entry("syn-heat-tariff", "currency/J", _heat_tariff_per_j()),
        ]
    }
    text = json.dumps(bundle, separators=(",", ":")) + "\n"
    (HERE / "annual_2026.json").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
