"""Uncertainty and decision-rule settings read strictly from versioned policy records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from r3chain.domain import policies
from r3chain.domain.records.runs import Policy


@dataclass(frozen=True)
class UncertaintySettings:
    """Sampling, sample accounting, and resilience switches from ``PolicyKind.UNCERTAINTY``."""

    mode: Literal["uncertainty", "point_estimate", "progressive"]
    sampling_method: Literal["latin_hypercube", "monte_carlo", "point_estimate"]
    sample_count: int
    progressive_initial_count: int
    progressive_step_count: int
    progressive_max_count: int
    progressive_max_error_fraction: float
    max_unresolved_fraction: float
    max_sweep_count: int
    uncertainty_model_id: str | None
    resilience_cases: tuple[str, ...]
    source_outage_case_id: str | None
    adoption_fractions: tuple[float, ...]
    origin: str

    @classmethod
    def from_policy(cls, policy: Policy) -> UncertaintySettings:
        mode = policies.text(policy, "mode")
        if mode not in {"uncertainty", "point_estimate", "progressive"}:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: mode must be 'uncertainty', 'point_estimate', "
                "or 'progressive'"
            )
        method = policies.text(policy, "sampling_method")
        if method not in {"latin_hypercube", "monte_carlo", "point_estimate"}:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: sampling_method must be 'latin_hypercube', "
                "'monte_carlo', or 'point_estimate'"
            )
        sample_count = policies.count(policy, "sample_count")
        prog_init = policies.count(policy, "progressive_initial_count")
        prog_step = policies.count(policy, "progressive_step_count")
        prog_max = policies.count(policy, "progressive_max_count")
        max_sweep = policies.count(policy, "max_sweep_count")
        for label, count_val in (
            ("sample_count", sample_count),
            ("progressive_initial_count", prog_init),
            ("progressive_step_count", prog_step),
            ("progressive_max_count", prog_max),
            ("max_sweep_count", max_sweep),
        ):
            if count_val < 1:
                raise policies.PolicyError(f"{policies.origin(policy)}: {label} must be >= 1")
        if prog_max < prog_init:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: progressive_max_count must be >= "
                "progressive_initial_count"
            )
        prog_err = policies.number(policy, "progressive_max_error_fraction")
        max_unres = policies.number(policy, "max_unresolved_fraction")
        for label, frac in (
            ("progressive_max_error_fraction", prog_err),
            ("max_unresolved_fraction", max_unres),
        ):
            if not 0.0 <= frac <= 1.0:
                raise policies.PolicyError(f"{policies.origin(policy)}: {label} must lie in [0, 1]")
        resilience = policies.names(policy, "resilience_cases")
        valid_resilience = {
            "source_outage",
            "backup_loss",
            "base_load_loss",
            "heat_pump_electricity_loss",
        }
        unknown_res = [name for name in resilience if name not in valid_resilience]
        if unknown_res:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: unknown resilience case(s): {', '.join(unknown_res)}"
            )
        adoptions_raw = policies.names(policy, "adoption_fractions")
        adoptions: list[float] = []
        for token in adoptions_raw:
            try:
                frac = float(token)
            except ValueError as error:
                raise policies.PolicyError(
                    f"{policies.origin(policy)}: invalid adoption fraction {token!r}"
                ) from error
            if not 0.0 < frac <= 1.0:
                raise policies.PolicyError(
                    f"{policies.origin(policy)}: adoption_fractions must lie in (0, 1]"
                )
            adoptions.append(frac)
        return cls(
            mode=cast(Literal["uncertainty", "point_estimate", "progressive"], mode),
            sampling_method=cast(
                Literal["latin_hypercube", "monte_carlo", "point_estimate"], method
            ),
            sample_count=sample_count,
            progressive_initial_count=prog_init,
            progressive_step_count=prog_step,
            progressive_max_count=prog_max,
            progressive_max_error_fraction=prog_err,
            max_unresolved_fraction=max_unres,
            max_sweep_count=max_sweep,
            uncertainty_model_id=policies.optional_text(policy, "uncertainty_model_id"),
            resilience_cases=resilience,
            source_outage_case_id=policies.optional_text(policy, "source_outage_case_id"),
            adoption_fractions=tuple(adoptions),
            origin=policies.origin(policy),
        )


@dataclass(frozen=True)
class DecisionRuleSettings:
    """Decision rules and Pareto objectives read strictly from ``PolicyKind.RANKING``."""

    decision_metric: str
    decision_rule: Literal["expected", "median", "p95", "minimax_regret"]
    tie_tolerance_fraction: float
    preferable_cost_margin_fraction: float
    pareto_objectives: tuple[tuple[str, Literal["min", "max"]], ...]
    min_service_probability_fraction: float | None
    origin: str

    @classmethod
    def from_policy(cls, policy: Policy) -> DecisionRuleSettings:
        metric = policies.text(policy, "decision_metric")
        rule = policies.text(policy, "decision_rule")
        if rule not in {"expected", "median", "p95", "minimax_regret"}:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: decision_rule must be 'expected', 'median', 'p95', "
                "or 'minimax_regret'"
            )
        tie = policies.number(policy, "tie_tolerance_fraction")
        margin = policies.number(policy, "preferable_cost_margin_fraction")
        for label, frac in (
            ("tie_tolerance_fraction", tie),
            ("preferable_cost_margin_fraction", margin),
        ):
            if not 0.0 <= frac <= 1.0:
                raise policies.PolicyError(f"{policies.origin(policy)}: {label} must lie in [0, 1]")
        min_prob = policies.optional_non_negative_number(policy, "min_service_probability_fraction")
        if min_prob is not None and min_prob > 1.0:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: min_service_probability_fraction must lie in [0, 1]"
            )
        objectives: list[tuple[str, Literal["min", "max"]]] = []
        for token in policies.names(policy, "pareto_objectives"):
            if ":" not in token:
                raise policies.PolicyError(
                    f"{policies.origin(policy)}: pareto objective {token!r} must end with "
                    "':min' or ':max'"
                )
            obj_metric, direction = (part.strip() for part in token.split(":", 1))
            if not obj_metric or direction not in {"min", "max"}:
                raise policies.PolicyError(
                    f"{policies.origin(policy)}: invalid pareto objective {token!r}"
                )
            objectives.append((obj_metric, cast(Literal["min", "max"], direction)))
        if not objectives:
            raise policies.PolicyError(
                f"{policies.origin(policy)}: pareto_objectives cannot be empty"
            )
        return cls(
            decision_metric=metric,
            decision_rule=cast(Literal["expected", "median", "p95", "minimax_regret"], rule),
            tie_tolerance_fraction=tie,
            preferable_cost_margin_fraction=margin,
            pareto_objectives=tuple(objectives),
            min_service_probability_fraction=min_prob,
            origin=policies.origin(policy),
        )
