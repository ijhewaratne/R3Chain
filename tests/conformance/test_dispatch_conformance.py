"""Conformance suite of the dispatch strategy port (extension point X8)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

import pytest

from r3chain.domain.evidence import NotEstablished
from r3chain.domain.status import AdapterState
from r3chain.operation.dispatch import RuleBasedDispatchStrategy
from r3chain.ports.dispatch import (
    DispatchGeothermalSpec,
    DispatchRequest,
    DispatchStrategy,
    DispatchUnit,
    ExistingUnitRole,
)


class StrategyFactory(NamedTuple):
    name: str
    make_strategy: Callable[[], DispatchStrategy]
    make_request: Callable[[], DispatchRequest]


def _default_request() -> DispatchRequest:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    starts = tuple(start + timedelta(hours=h) for h in range(4))
    durations = (3600.0, 3600.0, 3600.0, 3600.0)
    return DispatchRequest(
        request_id="conf-req",
        operating_scenario_id="conf-op",
        starts_utc=starts,
        durations_s=durations,
        demand_heat_w=(2_000_000.0, 500_000.0, 1_500_000.0, 3_500_000.0),
        existing_units=(
            DispatchUnit(
                unit_id="base",
                label="Waste heat base load",
                role=ExistingUnitRole.BASE_LOAD,
                priority=10,
                max_heat_w=600_000.0,
                min_output_w=100_000.0,
            ),
            DispatchUnit(
                unit_id="backup",
                label="Peak backup boiler",
                role=ExistingUnitRole.PEAK_BACKUP,
                priority=100,
                max_heat_w=2_000_000.0,
                min_output_w=0.0,
                thermal_efficiency_fraction=0.9,
            ),
        ),
        geothermal=DispatchGeothermalSpec(
            unit_id="geo",
            max_delivered_heat_w=1_200_000.0,
            max_plant_handover_heat_w=1_250_000.0,
            route_loss_when_active_w=50_000.0,
            min_output_fraction=0.2,
            availability_fraction=(1.0, 1.0, 0.0, 1.0),
        ),
        geothermal_priority=50,
    )


DISPATCH_STRATEGIES = [
    StrategyFactory("rule-based", RuleBasedDispatchStrategy, _default_request),
]


@pytest.fixture(params=DISPATCH_STRATEGIES, ids=lambda item: item.name)
def strategy_case(request: pytest.FixtureRequest) -> StrategyFactory:
    return request.param  # type: ignore[no-any-return]


def test_capabilities_are_declared(strategy_case: StrategyFactory) -> None:
    caps = strategy_case.make_strategy().capabilities()
    assert caps.strategy and caps.strategy_version
    assert caps.supports_existing_base_load


@pytest.mark.requirement("W08")
def test_dispatch_conserves_energy_and_separates_curtailment(
    strategy_case: StrategyFactory,
) -> None:
    strategy = strategy_case.make_strategy()
    request = strategy_case.make_request()
    result = strategy.dispatch(request)
    assert result.state is AdapterState.COMPLETED and result.totals is not None

    totals = result.totals
    assert math.isclose(
        totals.geothermal_delivered_heat_j + totals.base_load_heat_j + totals.backup_heat_j,
        totals.demand_heat_j - totals.unserved_heat_j,
        rel_tol=1e-9,
    )
    # Interval 1 demand 0.5 MW < base load max 0.6 MW -> geothermal earns 0 and curtails 1.2 MWh.
    assert result.geothermal_w[1] == 0.0
    assert result.curtailed_w[1] == pytest.approx(1_200_000.0)
    # Interval 2 geothermal is on planned outage (availability 0) -> geo=0, curtailed=0.
    assert result.geothermal_w[2] == 0.0 and result.curtailed_w[2] == 0.0
    # Interval 3 demand 3.5 MW: base 0.6 + geo 1.2 + backup 1.7 = 3.5 MW.
    assert result.by_unit_w["backup"][3] == pytest.approx(1_700_000.0)
    assert totals.unserved_heat_j == 0.0
    # Interval 0 operates geothermal at 1.2 MW and loses full 50 kW (1.25 MW plant handover).
    assert result.geothermal_plant_w[0] == pytest.approx(1_250_000.0)

    # Part load: residual demand 0.6 MW (50% of 1.2 MW geo) still loses full 50 kW route loss.
    part = request.model_copy(
        update={"demand_heat_w": (1_200_000.0, 500_000.0, 1_500_000.0, 3_500_000.0)}
    )
    part_res = strategy.dispatch(part)
    assert part_res.geothermal_w[0] == pytest.approx(600_000.0)
    assert part_res.geothermal_plant_w[0] == pytest.approx(650_000.0)


def test_missing_electricity_tariff_raises_not_established(
    strategy_case: StrategyFactory,
) -> None:
    strategy = strategy_case.make_strategy()
    request = strategy_case.make_request()
    assert request.geothermal is not None
    elec_req = request.model_copy(
        update={
            "geothermal": request.geothermal.model_copy(
                update={"source_electricity_per_plant_heat_ratio": 0.1}
            ),
            "electricity_price_currency_per_j": (),
        }
    )
    with pytest.raises(NotEstablished):
        strategy.dispatch(elec_req)


@pytest.mark.requirement("W08")
def test_dispatch_respects_maintenance_and_min_output(strategy_case: StrategyFactory) -> None:
    strategy = strategy_case.make_strategy()
    base_req = strategy_case.make_request()
    # Residual demand 150 kW is below geothermal min_output (20% of 1.2 MW = 240 kW): stays off.
    tight = base_req.model_copy(
        update={
            "demand_heat_w": (750_000.0, 500_000.0, 1_500_000.0, 3_500_000.0),
        }
    )
    result = strategy.dispatch(tight)
    assert result.state is AdapterState.COMPLETED and result.geothermal_w[0] == 0.0
    assert result.curtailed_w[0] == pytest.approx(1_200_000.0)


def test_dispatch_rejects_unsupported_storage_and_repeats_deterministically(
    strategy_case: StrategyFactory,
) -> None:
    strategy = strategy_case.make_strategy()
    request = strategy_case.make_request()
    first = strategy.dispatch(request)
    second = strategy.dispatch(request)
    assert first.to_json_dict() == second.to_json_dict()

    unsupported = strategy.dispatch(request.model_copy(update={"requires_storage": True}))
    assert unsupported.state is AdapterState.UNSUPPORTED_MODEL and unsupported.totals is None
