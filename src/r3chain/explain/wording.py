"""Fixed deterministic wording in German and English for typed claims (Plan v2 V07).

Zero language model involvement. Serves both the evidence view/report and the baseline
comparison for assistant explanations in later experiments.
"""

from __future__ import annotations

from typing import Literal

from r3chain.domain.units import JOULES_PER_MWH
from r3chain.explain.records import (
    CauseOfFailureClaim,
    ComparisonClaim,
    ExplanationClaim,
    ExplanationRecord,
    StatusClaim,
    ValueClaim,
)

Language = Literal["en", "de"]

_SUPPORTED_LANGS = frozenset({"en", "de"})

_TECH_STATUS_DE = {
    "passes_evaluated_cases": "besteht bewertete Faelle",
    "fails_evaluated_case": "verfehlt bewerteten Fall",
    "not_established": "nicht festgestellt",
}

_EVAL_STATUS_DE = {
    "valid": "gueltig",
    "unresolved_numerical": "numerisch nicht konvergiert",
    "invalid_input": "ungueltige Eingabe",
    "unsupported": "nicht unterstuetzt",
    "not_evaluated": "nicht bewertet",
    "cancelled": "abgebrochen",
    "tool_failure": "Werkzeugfehler",
}

_PREF_DE = {
    "clear_winner": "klarer Spitzenreiter",
    "tie_or_overlap": "Gleichstand oder Ueberlappung",
    "no_defensible_preference": "keine vertretbare Vorzugswahl",
    "insufficient_evidence": "unzureichende Nachweise",
    "ranked_eligible": "zulaessige Optionen gereiht",
    "no_eligible_candidate": "keine zulaessige Option",
}


def _validate_lang(lang: str) -> Language:
    if lang not in _SUPPORTED_LANGS:
        raise ValueError(f"unsupported explanation language {lang!r}; expected 'en' or 'de'")
    return "en" if lang == "en" else "de"


def _fmt_number(value: float, unit: str) -> str:
    if unit == "J":
        return f"{value / JOULES_PER_MWH:.2f} MWh ({value:.6g} J)"
    if unit == "currency/J":
        return f"{value * JOULES_PER_MWH:.2f} currency/MWh ({value:.6g} {unit})"
    if unit == "W":
        return f"{value / 1e6:.3f} MW ({value:.6g} W)"
    if unit == "Pa":
        return f"{value / 1e5:.3f} bar ({value:.6g} Pa)"
    return f"{value:.6g} {unit}".strip()


def _fmt_bound_value(value: float | str | None, unit: str | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return f"{value} {unit}".strip() if unit else value
    return _fmt_number(float(value), unit or "")


def _synthetic_tag(synthetic: bool, lang: Language) -> str:
    if not synthetic:
        return ""
    return "[SYNTHETIC] " if lang == "en" else "[SYNTHETISCH] "


def render_claim(claim: ExplanationClaim, *, lang: Language = "en") -> str:
    """Render one typed claim in fixed German or English wording without an LLM."""
    lang = _validate_lang(lang)
    tag = _synthetic_tag(claim.synthetic, lang)

    if isinstance(claim, ValueClaim):
        val_txt = _fmt_number(float(claim.value), claim.unit)
        ref = claim.kpi_id or claim.check_id or claim.field_path or "-"
        boundary = f", boundary={claim.boundary}" if claim.boundary else ""
        boundary_de = f", Systemgrenze={claim.boundary}" if claim.boundary else ""
        denom = (
            f" (samples={claim.sample_count}, unresolved={claim.unresolved_samples or 0})"
            if claim.sample_count is not None
            else ""
        )
        denom_de = (
            f" (Stichproben={claim.sample_count}, offen={claim.unresolved_samples or 0})"
            if claim.sample_count is not None
            else ""
        )
        if lang == "de":
            return (
                f"{tag}Wert {ref} fuer {claim.subject}{boundary_de}: {val_txt}{denom_de} "
                f"[Ergebnis {claim.result_id}, Revision {claim.scenario_revision}]."
            )
        return (
            f"{tag}Value {ref} for {claim.subject}{boundary}: {val_txt}{denom} "
            f"[result {claim.result_id}, revision {claim.scenario_revision}]."
        )

    if isinstance(claim, StatusClaim):
        if lang == "de":
            tech = _TECH_STATUS_DE.get(claim.technical_status.value, claim.technical_status.value)
            ev = _EVAL_STATUS_DE.get(claim.evaluation_status.value, claim.evaluation_status.value)
            elig = "zulaessig fuer Reihung" if claim.eligible else "nicht zulaessig fuer Reihung"
            cond = ", unter Vorbehalt" if claim.conditional else ""
            return (
                f"{tag}Status von {claim.subject}: technisch '{tech}', Auswertung '{ev}', "
                f"{elig}{cond} [Ergebnis {claim.result_id}, Revision {claim.scenario_revision}]."
            )
        cond = ", conditional" if claim.conditional else ""
        elig = "eligible for ranking" if claim.eligible else "not eligible for ranking"
        return (
            f"{tag}Status of {claim.subject}: technical '{claim.technical_status.value}', "
            f"evaluation '{claim.evaluation_status.value}', {elig}{cond} "
            f"[result {claim.result_id}, revision {claim.scenario_revision}]."
        )

    if isinstance(claim, CauseOfFailureClaim):
        obs_txt = _fmt_bound_value(claim.observed, claim.unit)
        lim_txt = _fmt_bound_value(claim.limit, claim.unit)
        ev_txt = ", ".join(claim.upstream_evidence) if claim.upstream_evidence else "-"
        if lang == "de":
            return (
                f"{tag}Ausschluss- oder Fehlergrund fuer {claim.subject} (Stufe {claim.stage}, "
                f"{claim.interval_or_sample}): Pruefung '{claim.check_id}' verletzt — "
                f"beobachtet {obs_txt}, Grenzwert {lim_txt}; Nachweis: {ev_txt}."
            )
        return (
            f"{tag}Failure cause for {claim.subject} (stage {claim.stage}, "
            f"{claim.interval_or_sample}): check '{claim.check_id}' failed — "
            f"observed {obs_txt}, limit {lim_txt}; evidence: {ev_txt}."
        )

    if isinstance(claim, ComparisonClaim):
        denom = (
            f" (n={claim.sample_count}, unresolved={claim.unresolved_samples or 0})"
            if claim.sample_count is not None
            else ""
        )
        if lang == "de":
            pref = _PREF_DE.get(claim.preference_outcome, claim.preference_outcome)
            win = (
                f"Spitzenreiter={claim.winner_id}"
                if claim.winner_id is not None
                else "kein eindeutiger Spitzenreiter"
            )
            return (
                f"{tag}Vergleich nach '{claim.metric_id}' (Grenze={claim.boundary}, "
                f"Richtlinie={claim.ranking_policy_id}): Ergebnis '{pref}' ({win}), "
                f"{len(claim.ranked_candidate_ids)} zulaessige von {len(claim.candidate_ids)} "
                f"Kandidaten{denom} [Revision {claim.scenario_revision}]."
            )
        win = f"winner={claim.winner_id}" if claim.winner_id is not None else "no single winner"
        return (
            f"{tag}Comparison on '{claim.metric_id}' (boundary={claim.boundary}, "
            f"policy={claim.ranking_policy_id}): outcome '{claim.preference_outcome}' ({win}), "
            f"{len(claim.ranked_candidate_ids)} eligible of {len(claim.candidate_ids)} "
            f"candidates{denom} [revision {claim.scenario_revision}]."
        )

    # OpenAssumptionClaim
    owner = claim.owner or ("unbenannt" if lang == "de" else "unassigned")
    reason = f" ({claim.reason})" if claim.reason else ""
    if lang == "de":
        return (
            f"{tag}Offene Annahme '{claim.field_path}' [{claim.state}, Zust.: {owner}]{reason} "
            f"[Revision {claim.scenario_revision}]."
        )
    return (
        f"{tag}Open assumption '{claim.field_path}' [{claim.state}, owner: {owner}]{reason} "
        f"[revision {claim.scenario_revision}]."
    )


def render_explanation(record: ExplanationRecord, *, lang: Language = "en") -> str:
    """Render an entire :class:`ExplanationRecord` as deterministic multi-line text."""
    lang = _validate_lang(lang)
    tag = _synthetic_tag(record.synthetic, lang)
    if lang == "de":
        header = (
            f"{tag}Erklaerung '{record.explanation_id}' ({record.scope}: {record.subject}, "
            f"Lauf {record.run_id}, Revision {record.scenario_revision}, "
            f"Pruefung={'bestanden' if record.all_claims_accepted else 'abgelehnt'})"
        )
    else:
        header = (
            f"{tag}Explanation '{record.explanation_id}' ({record.scope}: {record.subject}, "
            f"run {record.run_id}, revision {record.scenario_revision}, "
            f"claim_check={'accepted' if record.all_claims_accepted else 'rejected'})"
        )
    lines = [header, *(f"- {render_claim(claim, lang=lang)}" for claim in record.claims)]
    return "\n".join(lines)
