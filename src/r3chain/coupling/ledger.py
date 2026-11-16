"""Energy ledger from reservoir extraction to customer delivery (specification 10.5).

Every energy flow of a coupled operating point is one entry between two named boundaries. The
ledger then balances every boundary that only passes energy on: what enters it must leave it.

An ideal boundary in the network model fixes the pressure and feeds in whatever heat the network
takes. It is needed for the calculation, and it could hide a source that is too small. The slack
audit therefore assigns the heat of every ideal boundary to the finite sources that deliver it.
Heat that no finite source delivers is listed as unassigned. It is never dropped.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from r3chain.domain.base import Identifier, NonEmptyStr, NonNegativeReal, Real, Record

# Boundaries that only pass energy on. Each must balance.
GEOTHERMAL_CIRCUIT = "geothermal_circuit"
PLANT = "plant"
NETWORK = "network"
PASS_THROUGH_BOUNDARIES = (GEOTHERMAL_CIRCUIT, PLANT, NETWORK)

# Boundaries where energy enters or leaves the system.
RESERVOIR = "reservoir"
ELECTRICITY_GRID = "electricity_grid"
CONSUMERS = "consumers"
GROUND = "ground"
SURROUNDINGS = "surroundings"
UNASSIGNED_SLACK = "unassigned_slack"
NETWORK_ACCOUNTING = "network_engine_accounting"


class LedgerQuantity(StrEnum):
    HEAT = "heat"
    ELECTRICITY = "electricity"
    # Mechanical power given to a fluid. The electricity it needs depends on a pump efficiency.
    HYDRAULIC_POWER = "hydraulic_power"


class LedgerEntry(Record):
    entry_id: Identifier
    description: NonEmptyStr
    quantity: LedgerQuantity
    from_boundary: NonEmptyStr
    to_boundary: NonEmptyStr
    power_w: Real
    # The model or record that produced the number.
    basis: NonEmptyStr


class LedgerBalance(Record):
    boundary: NonEmptyStr
    inflow_w: Real
    outflow_w: Real
    residual_w: Real
    tolerance_w: NonNegativeReal
    closed: bool


class SlackExchange(Record):
    """What one ideal boundary exchanged with the network, and who really delivers it."""

    boundary_id: Identifier
    description: NonEmptyStr
    heat_supplied_w: Real
    # Finite source, heat it delivers.
    assigned_heat_w: dict[str, Real]
    unassigned_heat_w: Real
    # A circulating boundary takes out the mass it feeds in.
    net_mass_flow_kg_s: Real
    intended_dispatch: NonEmptyStr
    tolerance_w: NonNegativeReal
    within_intended_dispatch: bool


class EnergyLedger(Record):
    entries: tuple[LedgerEntry, ...]
    balances: tuple[LedgerBalance, ...]
    slack_exchanges: tuple[SlackExchange, ...] = ()

    def entry(self, entry_id: str) -> LedgerEntry:
        return next(item for item in self.entries if item.entry_id == entry_id)

    @property
    def closed(self) -> bool:
        return all(balance.closed for balance in self.balances)


# Hydraulic power is a property of a flow. It is not part of the heat and electricity balance.
_BALANCED = frozenset({LedgerQuantity.HEAT, LedgerQuantity.ELECTRICITY})


def build_ledger(
    entries: Sequence[LedgerEntry],
    *,
    tolerance_w: float,
    slack_exchanges: Sequence[SlackExchange] = (),
) -> EnergyLedger:
    """Assemble a ledger and balance every pass through boundary."""
    balances: list[LedgerBalance] = []
    for boundary in PASS_THROUGH_BOUNDARIES:
        counted = [entry for entry in entries if entry.quantity in _BALANCED]
        inflow = sum(entry.power_w for entry in counted if entry.to_boundary == boundary)
        outflow = sum(entry.power_w for entry in counted if entry.from_boundary == boundary)
        residual = inflow - outflow
        balances.append(
            LedgerBalance(
                boundary=boundary,
                inflow_w=inflow,
                outflow_w=outflow,
                residual_w=residual,
                tolerance_w=tolerance_w,
                closed=abs(residual) <= tolerance_w,
            )
        )
    return EnergyLedger(
        entries=tuple(entries), balances=tuple(balances), slack_exchanges=tuple(slack_exchanges)
    )
