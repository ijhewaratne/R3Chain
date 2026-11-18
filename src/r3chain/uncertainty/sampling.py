"""Joint correlated sampling, scenario sweeps, point estimates, and progressive checks."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from typing import Literal

import numpy as np
from scipy.stats import norm, rankdata

from r3chain.domain.base import NonNegativeReal, Real, Record
from r3chain.domain.records.scenarios import (
    CorrelationGroupSpec,
    UncertaintyKind,
    UncertaintyModel,
    UncertainVariable,
)


class JointSampleSet(Record):
    """Realised parameter matrix stored alongside seed, method, and probability semantics."""

    uncertainty_model_id: str
    mode: str
    seed: int
    sample_count: int
    has_probabilities: bool
    probability_basis: str
    variable_paths: dict[str, str]
    values_by_variable: dict[str, tuple[Real, ...]]

    def realizations(self) -> list[dict[str, float]]:
        return [
            {vid: float(self.values_by_variable[vid][idx]) for vid in self.variable_paths}
            for idx in range(self.sample_count)
        ]


class ProgressiveAssessment(Record):
    """Verification of progressive sample count against the full sample batch (V04)."""

    accepted: bool
    chosen_count: int
    full_count: int
    relative_error_vs_full: NonNegativeReal
    max_error_fraction: NonNegativeReal
    reason: str


def _distribution_median(var: UncertainVariable) -> float:
    if var.kind is UncertaintyKind.SCENARIO_SET:
        ordered = sorted(float(v) for v in var.scenario_values)
        return ordered[len(ordered) // 2]
    assert var.distribution is not None
    return _inverse_cdf(var, 0.5)


def point_estimate_realization(model: UncertaintyModel) -> dict[str, float]:
    """Return one central realisation per variable for point-estimate baseline runs (V03)."""
    return {var.variable_id: _distribution_median(var) for var in model.variables}


def _inverse_cdf(var: UncertainVariable, u: float) -> float:
    u_clipped = float(np.clip(u, 1e-9, 1.0 - 1e-9))
    low_s, high_s = float(var.support.lower), float(var.support.upper)
    if var.kind is UncertaintyKind.SCENARIO_SET:
        vals = sorted(float(v) for v in var.scenario_values)
        index = min(int(u_clipped * len(vals)), len(vals) - 1)
        return vals[index]
    assert var.distribution is not None
    fam = var.distribution.family
    p = var.distribution.parameters
    if fam == "uniform":
        lo = max(float(p.get("low", low_s)), low_s)
        hi = min(float(p.get("high", high_s)), high_s)
        return lo + u_clipped * (hi - lo)
    if fam == "triangular":
        lo = max(float(p.get("low", low_s)), low_s)
        hi = min(float(p.get("high", high_s)), high_s)
        mode = float(np.clip(float(p["mode"]), lo, hi))
        fc = (mode - lo) / (hi - lo) if hi > lo else 0.5
        if u_clipped < fc:
            return lo + math.sqrt(u_clipped * (hi - lo) * (mode - lo))
        return hi - math.sqrt((1.0 - u_clipped) * (hi - lo) * (hi - mode))
    if fam == "normal":
        mean, std = float(p["mean"]), float(p["std"])
        fa = float(norm.cdf((low_s - mean) / std))
        fb = float(norm.cdf((high_s - mean) / std))
        u_trunc = float(np.clip(fa + u_clipped * max(fb - fa, 1e-12), 1e-12, 1.0 - 1e-12))
        val = mean + std * float(norm.ppf(u_trunc))
        return float(np.clip(val, low_s, high_s))
    if fam == "lognormal":
        if "mu" in p and "sigma" in p:
            mu, sigma = float(p["mu"]), float(p["sigma"])
        else:
            m, s = float(p["mean"]), float(p["std"])
            v = math.log1p((s * s) / (m * m))
            mu = math.log(m) - 0.5 * v
            sigma = math.sqrt(v)
        fa = float(norm.cdf((math.log(max(low_s, 1e-12)) - mu) / sigma))
        fb = float(norm.cdf((math.log(max(high_s, 1e-12)) - mu) / sigma))
        u_trunc = float(np.clip(fa + u_clipped * max(fb - fa, 1e-12), 1e-12, 1.0 - 1e-12))
        val = math.exp(mu + sigma * float(norm.ppf(u_trunc)))
        return float(np.clip(val, low_s, high_s))
    if fam == "bernoulli":
        prob = float(p["p"])
        lo = float(p.get("low", low_s))
        hi = float(p.get("high", high_s))
        return hi if u_clipped < prob else lo
    raise ValueError(f"unsupported distribution family {fam!r}")


def _latin_hypercube_unit(n: int, dim: int, rng: np.random.Generator) -> np.ndarray:
    u = np.empty((n, dim), dtype=float)
    for col in range(dim):
        perm = rng.permutation(n)
        u[:, col] = (perm + rng.random(n)) / n
    return u


def _apply_correlation_group(
    u_matrix: np.ndarray,
    var_to_col: dict[str, int],
    group: CorrelationGroupSpec,
) -> None:
    """Induce Spearman rank correlation using Iman-Conover or a Gaussian copula."""
    group_vars: list[str] = []
    for pair in group.pairs:
        for vid in (pair.variable_a, pair.variable_b):
            if vid not in group_vars:
                group_vars.append(vid)
    k = len(group_vars)
    if k < 2:
        return
    cols = [var_to_col[vid] for vid in group_vars]
    corr = np.eye(k, dtype=float)
    pos = {vid: idx for idx, vid in enumerate(group_vars)}
    for pair in group.pairs:
        i, j = pos[pair.variable_a], pos[pair.variable_b]
        # Convert Spearman rho to Pearson copula correlation r = 2 * sin(pi * rho / 6).
        r_pearson = 2.0 * math.sin(math.pi * pair.rank_correlation / 6.0)
        corr[i, j] = r_pearson
        corr[j, i] = r_pearson
    eigvals, eigvecs = np.linalg.eigh(corr)
    eigvals = np.maximum(eigvals, 1e-9)
    corr_psd = eigvecs @ np.diag(eigvals) @ eigvecs.T
    d = np.sqrt(np.diag(corr_psd))
    corr_psd = corr_psd / np.outer(d, d)
    chol = np.linalg.cholesky(corr_psd)

    n = u_matrix.shape[0]
    z = norm.ppf(np.clip(u_matrix[:, cols], 1e-9, 1.0 - 1e-9))
    if group.method == "gaussian_copula":
        correlated_z = z @ chol.T
        u_matrix[:, cols] = norm.cdf(correlated_z)
        return
    # Iman-Conover rank reordering preserves exact stratified LHS marginals per column.
    if n < 2:
        return
    target = z @ chol.T
    for idx_col, col in enumerate(cols):
        sorted_vals = np.sort(u_matrix[:, col])
        ranks = rankdata(target[:, idx_col], method="ordinal").astype(int) - 1
        u_matrix[:, col] = sorted_vals[ranks]


def generate_scenario_sweep(
    model: UncertaintyModel,
    *,
    max_sweep_count: int = 128,
) -> JointSampleSet:
    """Enumerate all coherent combinations across scenario-set variables (no probability claim)."""
    paths = {var.variable_id: var.field_path for var in model.variables}
    if not model.variables:
        return JointSampleSet(
            uncertainty_model_id=model.uncertainty_model_id,
            mode="scenario_sweep",
            seed=0,
            sample_count=1,
            has_probabilities=False,
            probability_basis="deterministic scenario sweep (no probability distributions defined)",
            variable_paths={},
            values_by_variable={},
        )
    lists = [[float(v) for v in var.scenario_values] for var in model.variables]
    total = math.prod(len(items) for items in lists)
    if total > max_sweep_count:
        raise ValueError(
            f"scenario sweep of {total} combinations exceeds max_sweep_count={max_sweep_count}; "
            "declare distributions or reduce scenario sets"
        )
    combos = list(itertools.product(*lists))
    by_var = {
        var.variable_id: tuple(combo[idx] for combo in combos)
        for idx, var in enumerate(model.variables)
    }
    return JointSampleSet(
        uncertainty_model_id=model.uncertainty_model_id,
        mode="scenario_sweep",
        seed=0,
        sample_count=len(combos),
        has_probabilities=False,
        probability_basis=(
            "deterministic scenario sweep across scenario_set variables (no probability claim)"
        ),
        variable_paths=paths,
        values_by_variable=by_var,
    )


def generate_joint_samples(
    model: UncertaintyModel,
    *,
    sample_count: int,
    seed: int,
    method: Literal["latin_hypercube", "monte_carlo", "point_estimate"] = "latin_hypercube",
    max_sweep_count: int = 128,
) -> JointSampleSet:
    """Draw correlated joint realisations across variables with an explicit seed (A14)."""
    paths = {var.variable_id: var.field_path for var in model.variables}
    if method == "point_estimate":
        pe = point_estimate_realization(model)
        return JointSampleSet(
            uncertainty_model_id=model.uncertainty_model_id,
            mode="point_estimate",
            seed=seed,
            sample_count=1,
            has_probabilities=False,
            probability_basis="point_estimate mode: central values only, no sample distribution",
            variable_paths=paths,
            values_by_variable={vid: (val,) for vid, val in pe.items()},
        )
    if model.variables and all(var.kind is UncertaintyKind.SCENARIO_SET for var in model.variables):
        return generate_scenario_sweep(model, max_sweep_count=max_sweep_count)
    if not model.variables:
        return JointSampleSet(
            uncertainty_model_id=model.uncertainty_model_id,
            mode="uncertainty",
            seed=seed,
            sample_count=sample_count,
            has_probabilities=True,
            probability_basis="trivial deterministic baseline (zero uncertain variables)",
            variable_paths={},
            values_by_variable={},
        )

    dim = len(model.variables)
    rng = np.random.default_rng(seed)
    if method == "latin_hypercube":
        u = _latin_hypercube_unit(sample_count, dim, rng)
    else:
        u = rng.random((sample_count, dim))
    var_to_col = {var.variable_id: idx for idx, var in enumerate(model.variables)}
    for group in model.correlation_groups:
        _apply_correlation_group(u, var_to_col, group)

    by_var: dict[str, tuple[Real, ...]] = {}
    for col, var in enumerate(model.variables):
        by_var[var.variable_id] = tuple(
            _inverse_cdf(var, float(u[row, col])) for row in range(sample_count)
        )
    return JointSampleSet(
        uncertainty_model_id=model.uncertainty_model_id,
        mode="uncertainty",
        seed=seed,
        sample_count=sample_count,
        has_probabilities=all(var.kind is UncertaintyKind.DISTRIBUTION for var in model.variables),
        probability_basis=(
            f"{method} with {sample_count} paired joint samples (seed={seed})"
            if all(var.kind is UncertaintyKind.DISTRIBUTION for var in model.variables)
            else "mixed distribution and scenario_set variables (treat scenario splits separately)"
        ),
        variable_paths=paths,
        values_by_variable=by_var,
    )


def run_progressive_allocation(
    *,
    total_count: int,
    initial_count: int,
    step_count: int,
    max_error_fraction: float,
    evaluate_prefix: Callable[[int], float],
) -> ProgressiveAssessment:
    """Accept an intermediate sample count only after error check against full batch (V04)."""
    full_val = evaluate_prefix(total_count)
    prev_val = evaluate_prefix(initial_count)
    chosen_candidate = initial_count
    for n in range(initial_count + step_count, total_count, step_count):
        curr_val = evaluate_prefix(n)
        step_err = abs(curr_val - prev_val) / max(abs(curr_val), 1e-12)
        prev_val = curr_val
        chosen_candidate = n
        if step_err <= 0.5 * max_error_fraction:
            break
    else:
        chosen_candidate = min(initial_count + step_count, total_count)

    prov_val = evaluate_prefix(chosen_candidate)
    rel_err = abs(prov_val - full_val) / max(abs(full_val), 1e-12)
    if rel_err <= max_error_fraction and chosen_candidate < total_count:
        return ProgressiveAssessment(
            accepted=True,
            chosen_count=chosen_candidate,
            full_count=total_count,
            relative_error_vs_full=rel_err,
            max_error_fraction=max_error_fraction,
            reason=(
                f"progressive prefix n={chosen_candidate}/{total_count} stays within error "
                f"tolerance ({rel_err:.4f} <= {max_error_fraction:.4f})"
            ),
        )
    return ProgressiveAssessment(
        accepted=False,
        chosen_count=total_count,
        full_count=total_count,
        relative_error_vs_full=rel_err,
        max_error_fraction=max_error_fraction,
        reason=(
            f"progressive prefix n={chosen_candidate}/{total_count} exceeds tolerance "
            f"({rel_err:.4f} > {max_error_fraction:.4f}); using full batch n={total_count}"
        ),
    )
