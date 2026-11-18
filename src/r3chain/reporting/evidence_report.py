"""Structured study evidence report built strictly from stored runs and revisions (A17)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from r3chain.domain.assumptions import assumption_register
from r3chain.domain.base import NonEmptyStr, Record
from r3chain.domain.units import JOULES_PER_MWH
from r3chain.explain.records import ExplanationRecord
from r3chain.explain.wording import Language, render_explanation
from r3chain.store.store import Store


class EvidenceReport(Record):
    """Complete reproducible evidence summary derived purely from frozen store records."""

    run_id: NonEmptyStr
    scenario_id: NonEmptyStr
    revision_id: NonEmptyStr
    result_hash: NonEmptyStr
    code_version: NonEmptyStr
    seed: int
    synthetic: bool
    policy_refs: tuple[dict[str, str], ...]
    missing_inputs: tuple[dict[str, Any], ...]
    assessments: tuple[dict[str, Any], ...]
    economics: dict[str, Any]
    compare: dict[str, Any]
    not_modelled: tuple[str, ...]
    explanations: tuple[dict[str, Any], ...] = ()


def build_evidence_report(
    store: Store,
    run_id: str,
    *,
    explanations: Sequence[ExplanationRecord] = (),
) -> EvidenceReport:
    """Assemble an :class:`EvidenceReport` from a completed run without rerunning models."""
    run = store.get_run(run_id)
    scenario = store.get_scenario(run.scenario_revision)
    results = store.get_results(run_id)
    study_res = next(res for res in results if res.subject == "study")
    stages = {out.stage: out.payload for out in study_res.stage_outputs}
    assumptions = assumption_register(scenario)
    missing = [
        {"field_path": a.field_path, "status": a.status.value, "owner": a.owner, "note": a.note}
        for a in assumptions
        if a.status.value in {"unknown", "conflict"}
    ]
    operate_payload = stages.get("operate", {})
    cost_payload = stages.get("cost", {})
    compare_payload = stages.get("compare", {})
    assess_payload = stages.get("assess", {})
    not_modelled = tuple(
        dict.fromkeys(
            [
                *operate_payload.get("not_modelled", ()),
                *cost_payload.get("not_modelled", ()),
            ]
        )
    )
    return EvidenceReport(
        run_id=run.run_id,
        scenario_id=scenario.scenario_id,
        revision_id=run.scenario_revision,
        result_hash=run.result_hash,
        code_version=run.code_version,
        seed=run.seed,
        synthetic=study_res.synthetic,
        policy_refs=tuple(
            {
                "policy_id": p.policy_id,
                "version": p.version,
                "policy_hash": p.policy_hash,
            }
            for p in scenario.policies
        ),
        missing_inputs=tuple(missing),
        assessments=tuple(assess_payload.get("assessments", ())),
        economics={
            "baseline": cost_payload.get("baseline", {}),
            "candidates": cost_payload.get("candidates", {}),
        },
        compare=compare_payload,
        not_modelled=not_modelled,
        explanations=tuple(rec.to_json_dict() for rec in explanations),
    )


def render_markdown(report: EvidenceReport, *, lang: Language = "en") -> str:
    """Format an :class:`EvidenceReport` deterministically in Markdown."""
    lines: list[str] = [
        f"# R3CHAIN Evidence Report — {report.scenario_id}",
        "",
        f"- **Run ID:** `{report.run_id}`",
        f"- **Scenario revision:** `{report.revision_id}`",
        f"- **Result hash:** `{report.result_hash}`",
        f"- **Code version:** `{report.code_version}` | **Seed:** `{report.seed}`",
        (
            "- **Synthetic notice:** Synthetic models or values are present; results cannot enter "
            "a reviewed pilot ranking."
            if report.synthetic
            else "- **Data status:** Non-synthetic inputs."
        ),
        "",
        "## Policies",
        "",
        "| Policy | Version | Hash |",
        "|---|---|---|",
    ]
    for p in report.policy_refs:
        lines.append(f"| `{p['policy_id']}` | `{p['version']}` | `{p['policy_hash'][:18]}...` |")

    lines.extend(["", f"## Inputs Not Established ({len(report.missing_inputs)})", ""])
    if report.missing_inputs:
        for item in report.missing_inputs:
            lines.append(
                f"- `{item['field_path']}` ({item['status']}, owner: {item.get('owner') or '-'}"
                f"): {item.get('note', '')}"
            )
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Candidate Eligibility & Technical Separation",
            "",
            "| Candidate | Technical | Screening | Eligible | Limits / Reasons |",
            "|---|---|---|---|---|",
        ]
    )
    labels: dict[str, str] = {}
    for cand in report.assessments:
        cid = str(cand["candidate_id"])
        labels[cid] = str(cand["label"])
        elig = cand.get("ranking_eligibility", {})
        reasons = ", ".join(elig.get("reasons", ())) or ", ".join(
            cand.get("limiting_conditions", ())
        )
        lines.append(
            f"| {cand['label']} | {cand['technical_status']} | {cand['screening_status']} | "
            f"{'yes' if elig.get('eligible') else 'no'} | {reasons or '-'} |"
        )

    cmp = report.compare
    if cmp:
        comp_block = cmp.get("comparison", {})
        lines.extend(
            [
                "",
                "## Uncertainty, Regret & Decision Analysis",
                "",
                f"- **Mode:** `{cmp.get('mode', '-')}`",
                f"- **Probability basis:** {cmp.get('probability_basis', '-')}",
                f"- **Paired sample count:** `{cmp.get('sample_count', 0)}`",
                f"- **Success event established (O05):** `{cmp.get('success_event_established')}`",
                f"- **Preference conclusion:** `{comp_block.get('preference_outcome', '-')}` — "
                f"{comp_block.get('preference_reason', '')}",
                "",
                "| Contender | Eval / Total | Feasible | "
                "Conditional LCOH p05 / p50 / p95 (EUR/MWh) | Max Regret | First-Rank Share "
                "| Pareto |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        stability = {r["candidate_id"]: r for r in comp_block.get("ranking_stability", [])}
        cand_map = comp_block.get("candidates", {})
        ordered = comp_block.get("ordered_candidate_ids") or list(cand_map.keys())
        for cid in ordered:
            row = cand_map[cid]
            acc = row["sample_account"]
            q = row.get("lcoh_quantiles")
            if q:
                q_str = (
                    f"{q['p05'] * JOULES_PER_MWH:.2f} / "
                    f"{q['p50'] * JOULES_PER_MWH:.2f} / "
                    f"{q['p95'] * JOULES_PER_MWH:.2f}"
                )
            else:
                q_str = "-"
            reg = row.get("max_regret_currency")
            reg_str = f"{reg:,.0f}" if reg is not None else "-"
            stab = stability.get(cid)
            share_str = (
                f"{stab['first_rank_count']}/{stab['denominator']} ({stab['first_rank_share']:.0%})"
                if stab
                else "-"
            )
            pareto_str = "yes" if row.get("pareto_optimal") else "no"
            lines.append(
                f"| {labels.get(cid, cid)} | {acc['evaluated_samples']}/{acc['total_samples']} | "
                f"{acc['feasible_samples']} | {q_str} | {reg_str} | {share_str} | {pareto_str} |"
            )

        drivers = comp_block.get("decision_drivers", ())
        if drivers:
            lines.extend(["", "### Decision Drivers & Ranking Stability (V01)", ""])
            for drv in drivers:
                lines.append(
                    f"- `{drv['variable_id']}`: rho={drv['spearman_rank_correlation']:.3f}, "
                    f"switches_winner={drv['switches_winner']} — {drv['effect_summary']}"
                )

        resilience = cmp.get("resilience", {})
        if resilience:
            lines.extend(["", "### Deterministic Resilience Stress Cases (V02)", ""])
            lines.append(
                "| Candidate | Case | Unserved Heat (MWh) | Unserved Hours | Largest Deficit (MW) |"
            )
            lines.append("|---|---|---|---|---|")
            for cid, cases in resilience.items():
                for case in cases:
                    lines.append(
                        f"| {labels.get(cid, cid)} | {case['case_id']} | "
                        f"{case['unserved_heat_j'] / JOULES_PER_MWH:.2f} | "
                        f"{case['unserved_hours']:.1f} | "
                        f"{case['largest_deficit_w'] / 1e6:.2f} |"
                    )

        adoption = cmp.get("adoption_thresholds", {})
        if adoption:
            lines.extend(["", "### Connection Adoption Stages & Thresholds (W18)", ""])
            for cid, item in adoption.items():
                lines.append(f"- `{labels.get(cid, cid)}`: {item.get('note', '')}")

    if report.not_modelled:
        lines.extend(["", "## Disclosed Unmodelled Effects", ""])
        for note in report.not_modelled:
            lines.append(f"- {note}")

    if report.explanations:
        heading = (
            "## Gepruefte Erklaerungen (V05-V07)"
            if lang == "de"
            else "## Verified Explanations (V05-V07)"
        )
        lines.extend(["", heading, ""])
        for raw in report.explanations:
            rec = ExplanationRecord.model_validate(raw)
            lines.append(render_explanation(rec, lang=lang))
            lines.append("")

    return "\n".join(lines) + "\n"
