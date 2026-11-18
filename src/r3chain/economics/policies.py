"""Economic and KPI policy settings read strictly from versioned policy records."""

from __future__ import annotations

from dataclasses import dataclass

from r3chain.domain import policies
from r3chain.domain.records.runs import Policy


@dataclass(frozen=True)
class EconomicSettings:
    """Accounting rules and unit rates declared in a ``PolicyKind.ECONOMIC`` record."""

    residual_value_method: str
    evaluate_funded_case: bool
    route_lifetime_years: float
    route_fixed_opex_fraction: float
    route_pipe_cost_currency_per_m: float
    link_corridor_kind: str
    trench_rates: dict[str, float]
    crossing_costs: dict[str, float]
    additional_customer_connection_currency: float
    additional_customer_pipe_cost_currency_per_m: float
    origin: str
    economic_scenario_id: str | None = None

    @classmethod
    def from_policy(cls, policy: Policy) -> EconomicSettings:
        method = policies.text(policy, "residual_value_method")
        if method not in {"linear_remaining_life", "none"}:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: residual_value_method must be "
                "'linear_remaining_life' or 'none'"
            )
        route_life = policies.number(policy, "route_lifetime_years")
        if route_life <= 0.0 or not route_life.is_integer():
            raise policies.PolicyError(
                f"{policies.origin(policy)}: route_lifetime_years must be a positive whole number "
                "of years"
            )
        route_opex_frac = policies.number(policy, "route_fixed_opex_fraction")
        if not 0.0 <= route_opex_frac <= 1.0:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: route_fixed_opex_fraction must lie in [0, 1]"
            )
        route_pipe_rate = policies.number(policy, "route_pipe_cost_currency_per_m")
        cust_conn = policies.number(policy, "additional_customer_connection_currency")
        cust_pipe = policies.number(policy, "additional_customer_pipe_cost_currency_per_m")
        for label, value in (
            ("route_pipe_cost_currency_per_m", route_pipe_rate),
            ("additional_customer_connection_currency", cust_conn),
            ("additional_customer_pipe_cost_currency_per_m", cust_pipe),
        ):
            if value < 0.0:
                raise policies.PolicyError(f"{policies.origin(policy)}: {label} cannot be negative")
        return cls(
            residual_value_method=method,
            evaluate_funded_case=policies.flag(policy, "evaluate_funded_case"),
            route_lifetime_years=route_life,
            route_fixed_opex_fraction=route_opex_frac,
            route_pipe_cost_currency_per_m=route_pipe_rate,
            link_corridor_kind=policies.text(policy, "link_corridor_kind"),
            trench_rates=policies.numbers_by_key(policy, "trench_cost_", "_currency_per_m"),
            crossing_costs=policies.numbers_by_key(policy, "crossing_cost_", "_currency"),
            additional_customer_connection_currency=cust_conn,
            additional_customer_pipe_cost_currency_per_m=cust_pipe,
            origin=policies.origin(policy),
            economic_scenario_id=policies.optional_text(policy, "economic_scenario_id"),
        )
