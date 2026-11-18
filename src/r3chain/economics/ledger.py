"""Cost ledger assembly, boundary scoping and upstream reconciliation (specification 13.1-13.2)."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from r3chain.domain.base import Identifier, NonEmptyStr, Real, Record
from r3chain.domain.evidence import InputReader, MissingInput, NotEstablished, missing_input
from r3chain.domain.records.network import Route
from r3chain.domain.records.scenarios import AmountBasis, CostCategory, CostItem, EconomicScenario
from r3chain.economics.policies import EconomicSettings
from r3chain.geospatial.routes import GeneratedRoute
from r3chain.ports.dispatch import DispatchTotals


class CostBoundary(StrEnum):
    PLANT = "plant"
    GEOTHERMAL_DELIVERED = "geothermal_delivered"
    SYSTEM_DELIVERED = "system_delivered"


_BOUNDARY_LOCATIONS: dict[CostBoundary, frozenset[str]] = {
    CostBoundary.PLANT: frozenset({"source", "plant"}),
    CostBoundary.GEOTHERMAL_DELIVERED: frozenset(
        {"source", "plant", "route", "connection", "customer"}
    ),
    CostBoundary.SYSTEM_DELIVERED: frozenset(
        {
            "source",
            "plant",
            "route",
            "connection",
            "customer",
            "network",
            "existing",
            "baseline",
            "system",
        }
    ),
}


class DuplicateCostOwnershipError(ValueError):
    """Two included cost rows claim the same asset and cost category."""


class LedgerRow(Record):
    """One line of a candidate or baseline cost ledger, included or excluded."""

    item_id: Identifier
    asset_id: Identifier
    category: CostCategory
    cost_owner: NonEmptyStr
    location: NonEmptyStr
    included: bool
    exclusion_reason: str = ""
    amount_currency: Real
    amount_basis: AmountBasis
    lifetime_years: Real | None = None
    origin: NonEmptyStr


class ReconciliationTable(Record):
    """Every candidate cost item, excluded upstream duplicates and conflict diagnostics."""

    subject_id: NonEmptyStr
    rows: tuple[LedgerRow, ...]
    duplicate_ownership_errors: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.duplicate_ownership_errors

    def rows_for_boundary(self, boundary: CostBoundary) -> tuple[LedgerRow, ...]:
        allowed = _BOUNDARY_LOCATIONS[boundary]
        return tuple(row for row in self.rows if row.included and row.location in allowed)


def _matches_candidate(
    item: CostItem,
    *,
    site_id: str,
    plant_id: str,
    route_id: str,
    connection_id: str,
    candidate_id: str,
    alternative_id: str | None,
) -> bool:
    scope = item.applies_to
    if scope is None:
        return True
    if (
        scope.applies_to_baseline
        and not scope.site_ids
        and not scope.plant_ids
        and not scope.route_ids
        and not scope.connection_ids
        and not scope.candidate_ids
        and not scope.alternative_ids
    ):
        return False
    checks = (
        (scope.site_ids, site_id),
        (scope.plant_ids, plant_id),
        (scope.route_ids, route_id),
        (scope.connection_ids, connection_id),
        (scope.candidate_ids, candidate_id),
    )
    for allowed, actual in checks:
        if allowed and actual not in allowed:
            return False
    return not (
        scope.alternative_ids
        and (alternative_id is None or alternative_id not in scope.alternative_ids)
    )


def _matches_baseline(item: CostItem, *, alternative_id: str | None) -> bool:
    scope = item.applies_to
    if scope is None:
        return item.location in {"existing", "baseline", "network", "system"}
    if scope.applies_to_baseline:
        return True
    return bool(
        alternative_id is not None
        and scope.alternative_ids
        and alternative_id in scope.alternative_ids
    )


def _resolve_items_to_rows(
    items: Sequence[CostItem], *, prefix: str, missing: list[MissingInput]
) -> list[LedgerRow]:
    rows: list[LedgerRow] = []
    for item in items:
        item_path = f"{prefix}.cost_items[{item.item_id}]"
        if not item.included:
            # Excluded items still appear in the reconciliation table with their declared reason or
            # value (0 when unknown, since excluded items never enter sums).
            reader = InputReader()
            val = reader.get(item.amount, f"{item_path}.amount")
            life = (
                reader.get(item.lifetime_years, f"{item_path}.lifetime_years")
                if item.lifetime_years is not None
                else None
            )
            rows.append(
                LedgerRow(
                    item_id=item.item_id,
                    asset_id=item.asset_id,
                    category=item.category,
                    cost_owner=item.cost_owner,
                    location=item.location,
                    included=False,
                    exclusion_reason=(
                        f"marked included=false (cost_owner={item.cost_owner}): "
                        "excluded to avoid double counting"
                    ),
                    amount_currency=0.0 if val is None else val,
                    amount_basis=item.amount_basis,
                    lifetime_years=life,
                    origin=_item_origin(item),
                )
            )
            continue

        amount = item.amount
        if amount.state != "known":
            missing.append(missing_input(amount, f"{item_path}.amount"))
            continue
        item_life: float | None = None
        if item.lifetime_years is not None:
            if item.lifetime_years.state != "known":
                missing.append(missing_input(item.lifetime_years, f"{item_path}.lifetime_years"))
                continue
            item_life = float(item.lifetime_years.value)
        rows.append(
            LedgerRow(
                item_id=item.item_id,
                asset_id=item.asset_id,
                category=item.category,
                cost_owner=item.cost_owner,
                location=item.location,
                included=True,
                amount_currency=float(amount.value),
                amount_basis=item.amount_basis,
                lifetime_years=item_life,
                origin=_item_origin(item),
            )
        )
    return rows


def _item_origin(item: CostItem) -> str:
    if item.amount.state == "known":
        p = item.amount.provenance
        suffix = f" ({p.locator})" if p.locator else ""
        return f"{p.provenance_type.value}: {p.source_id or p.author or 'declared'}{suffix}"
    return f"cost_owner {item.cost_owner}"


def route_capex_currency(
    generated: GeneratedRoute | None,
    route: Route,
    settings: EconomicSettings,
    *,
    include_additional_customers: bool = False,
) -> tuple[float, float, str]:
    """Compute route CAPEX (trench + supply/return pipes + crossings) and optional customer CAPEX.

    Returns ``(route_capex, customer_capex, description)``. Both supply and return pipe lengths are
    always costed separately from trench length so costs are never omitted or double counted.
    """
    trench_cost = 0.0
    if generated is not None and generated.lengths_by_kind_m:
        for kind, length_m in generated.lengths_by_kind_m.items():
            if kind == "link" or (kind.startswith("link (") and kind.endswith(")")):
                link_kind = kind[6:-1] if kind.startswith("link (") else settings.link_corridor_kind
                rate = settings.trench_rates.get(link_kind)
            else:
                rate = settings.trench_rates.get(kind)
            if rate is None:
                raise NotEstablished(
                    (
                        MissingInput(
                            field_path=f"policies[{settings.origin}].trench_cost_{kind}_currency_per_m",
                            state="unknown",
                            reason=f"no trench cost rate for corridor kind {kind!r}",
                            owner="economics specialist",
                        ),
                    )
                )
            trench_cost += float(length_m) * rate
    else:
        fallback_rate = settings.trench_rates.get(settings.link_corridor_kind)
        if fallback_rate is None:
            raise NotEstablished(
                (
                    MissingInput(
                        field_path=(
                            f"policies[{settings.origin}].trench_cost_"
                            f"{settings.link_corridor_kind}_currency_per_m"
                        ),
                        state="unknown",
                        reason=(
                            f"no trench cost rate for corridor kind {settings.link_corridor_kind!r}"
                        ),
                        owner="economics specialist",
                    ),
                )
            )
        trench_cost = route.corridor_length_m * fallback_rate

    pipe_length_m = route.supply_pipe_length_m + route.return_pipe_length_m
    pipe_cost = pipe_length_m * settings.route_pipe_cost_currency_per_m
    crossing_cost = 0.0
    for crossing in route.crossings:
        c_rate = settings.crossing_costs.get(crossing.kind)
        if c_rate is None:
            raise NotEstablished(
                (
                    MissingInput(
                        field_path=f"policies[{settings.origin}].crossing_cost_{crossing.kind}_currency",
                        state="unknown",
                        reason=f"no crossing cost for barrier kind {crossing.kind!r}",
                        owner="economics specialist",
                    ),
                )
            )
        crossing_cost += c_rate

    base_capex = trench_cost + pipe_cost + crossing_cost
    customer_capex = 0.0
    if include_additional_customers and generated is not None:
        for customer in generated.linked_customers:
            customer_capex += (
                settings.additional_customer_connection_currency
                + customer.distance_m * settings.additional_customer_pipe_cost_currency_per_m
            )
    basis = (
        f"{route.corridor_length_m:.1f} m trench + "
        f"{route.supply_pipe_length_m:.1f} m supply + "
        f"{route.return_pipe_length_m:.1f} m return pipe"
        + (f" + {len(route.crossings)} crossing(s)" if route.crossings else "")
    )
    return base_capex, customer_capex, basis


def reconcile_ledger(subject_id: str, rows: Sequence[LedgerRow]) -> ReconciliationTable:
    """Detect any duplicate `(asset_id, category)` claimed by multiple included rows."""
    seen: dict[tuple[str, CostCategory], LedgerRow] = {}
    duplicates: list[str] = []
    for row in rows:
        if not row.included:
            continue
        key = (row.asset_id, row.category)
        if key in seen:
            first = seen[key]
            duplicates.append(
                f"duplicate cost ownership for {row.category.value} of asset {row.asset_id!r}: "
                f"included by both {first.item_id!r} (owner={first.cost_owner}) and "
                f"{row.item_id!r} (owner={row.cost_owner})"
            )
        else:
            seen[key] = row
    return ReconciliationTable(
        subject_id=subject_id, rows=tuple(rows), duplicate_ownership_errors=tuple(duplicates)
    )


def assemble_candidate_ledger(
    economic: EconomicScenario,
    *,
    subject_id: str,
    site_id: str,
    plant_id: str,
    route: Route,
    generated_route: GeneratedRoute | None,
    connection_id: str,
    candidate_id: str,
    alternative_id: str | None,
    dispatch_totals: DispatchTotals,
    economic_settings: EconomicSettings | None = None,
    upstream_items: Sequence[CostItem] = (),
    include_additional_customers: bool = False,
) -> ReconciliationTable:
    """Assemble and reconcile every static and operational cost row for one candidate."""
    e_base = f"economic_scenarios[{economic.economic_scenario_id}]"
    applicable = [
        item
        for item in economic.cost_items
        if _matches_candidate(
            item,
            site_id=site_id,
            plant_id=plant_id,
            route_id=route.route_id,
            connection_id=connection_id,
            candidate_id=candidate_id,
            alternative_id=alternative_id,
        )
    ]
    missing: list[MissingInput] = []
    rows = _resolve_items_to_rows(applicable, prefix=e_base, missing=missing)
    rows.extend(_resolve_items_to_rows(upstream_items, prefix="upstream_source", missing=missing))
    if missing:
        raise NotEstablished(tuple(missing))

    # Derive route and additional customer CAPEX from policy if the scenario has not explicitly
    # declared a route CAPEX item (an explicit included item, or an excluded one reconciled
    # upstream, takes precedence; an explicit unknown item would have raised NotEstablished above).
    declared_route_capex = any(
        row.asset_id == route.route_id and row.category is CostCategory.CAPEX for row in rows
    )
    if not declared_route_capex and economic_settings is not None:
        base_capex, cust_capex, route_desc = route_capex_currency(
            generated_route,
            route,
            economic_settings,
            include_additional_customers=include_additional_customers,
        )
        rows.append(
            LedgerRow(
                item_id=f"{route.route_id}-capex",
                asset_id=route.route_id,
                category=CostCategory.CAPEX,
                cost_owner="system",
                location="route",
                included=True,
                amount_currency=base_capex,
                amount_basis=AmountBasis.TOTAL,
                lifetime_years=economic_settings.route_lifetime_years,
                origin=f"{economic_settings.origin} ({route_desc})",
            )
        )
        if economic_settings.route_fixed_opex_fraction > 0.0:
            rows.append(
                LedgerRow(
                    item_id=f"{route.route_id}-fixed-opex",
                    asset_id=route.route_id,
                    category=CostCategory.FIXED_OPEX,
                    cost_owner="system",
                    location="route",
                    included=True,
                    amount_currency=base_capex * economic_settings.route_fixed_opex_fraction,
                    amount_basis=AmountBasis.PER_YEAR,
                    origin=f"{economic_settings.origin} (route fixed OPEX)",
                )
            )
        if cust_capex > 0.0:
            rows.append(
                LedgerRow(
                    item_id=f"{route.route_id}-customers-capex",
                    asset_id=f"{route.route_id}-customers",
                    category=CostCategory.CAPEX,
                    cost_owner="system",
                    location="customer",
                    included=True,
                    amount_currency=cust_capex,
                    amount_basis=AmountBasis.TOTAL,
                    lifetime_years=economic_settings.route_lifetime_years,
                    origin=f"{economic_settings.origin} (linked customer service connections)",
                )
            )

    # Operational annual energy costs from chronological dispatch.
    if dispatch_totals.geothermal_electricity_cost_currency != 0.0:
        rows.append(
            LedgerRow(
                item_id=f"{candidate_id}-geothermal-electricity",
                asset_id=f"{candidate_id}-geothermal-electricity",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="plant",
                included=True,
                amount_currency=dispatch_totals.geothermal_electricity_cost_currency,
                amount_basis=AmountBasis.PER_YEAR,
                origin="chronological dispatch (well pump + compressor electricity)",
            )
        )
    if dispatch_totals.backup_fuel_and_om_cost_currency != 0.0:
        rows.append(
            LedgerRow(
                item_id=f"{candidate_id}-backup-energy",
                asset_id=f"{candidate_id}-backup-energy",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="existing",
                included=True,
                amount_currency=dispatch_totals.backup_fuel_and_om_cost_currency,
                amount_basis=AmountBasis.PER_YEAR,
                origin="chronological dispatch (backup fuel and variable O&M)",
            )
        )
    if dispatch_totals.base_load_fuel_and_om_cost_currency != 0.0:
        rows.append(
            LedgerRow(
                item_id=f"{candidate_id}-baseload-energy",
                asset_id=f"{candidate_id}-baseload-energy",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="existing",
                included=True,
                amount_currency=dispatch_totals.base_load_fuel_and_om_cost_currency,
                amount_basis=AmountBasis.PER_YEAR,
                origin="chronological dispatch (existing base load variable cost)",
            )
        )

    return reconcile_ledger(subject_id, rows)


def assemble_baseline_ledger(
    economic: EconomicScenario,
    *,
    baseline_id: str,
    dispatch_totals: DispatchTotals,
) -> ReconciliationTable:
    """Assemble and reconcile costs for the baseline alternative without geothermal."""
    e_base = f"economic_scenarios[{economic.economic_scenario_id}]"
    applicable = [
        item for item in economic.cost_items if _matches_baseline(item, alternative_id=baseline_id)
    ]
    missing: list[MissingInput] = []
    rows = _resolve_items_to_rows(applicable, prefix=e_base, missing=missing)
    if missing:
        raise NotEstablished(tuple(missing))
    if dispatch_totals.backup_fuel_and_om_cost_currency != 0.0:
        rows.append(
            LedgerRow(
                item_id=f"{baseline_id}-backup-energy",
                asset_id=f"{baseline_id}-backup-energy",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="existing",
                included=True,
                amount_currency=dispatch_totals.backup_fuel_and_om_cost_currency,
                amount_basis=AmountBasis.PER_YEAR,
                origin="baseline chronological dispatch (backup fuel and variable O&M)",
            )
        )
    if dispatch_totals.base_load_fuel_and_om_cost_currency != 0.0:
        rows.append(
            LedgerRow(
                item_id=f"{baseline_id}-baseload-energy",
                asset_id=f"{baseline_id}-baseload-energy",
                category=CostCategory.VARIABLE_COST,
                cost_owner="operation",
                location="existing",
                included=True,
                amount_currency=dispatch_totals.base_load_fuel_and_om_cost_currency,
                amount_basis=AmountBasis.PER_YEAR,
                origin="baseline chronological dispatch (existing base load variable cost)",
            )
        )
    return reconcile_ledger(baseline_id, rows)
