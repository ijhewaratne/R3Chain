"""Provenance, the three input states, and the rules that keep failure kinds apart."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from r3chain.domain.base import Real
from r3chain.domain.evidence import (
    Conflict,
    Evidence,
    InputReader,
    Known,
    NotEstablished,
    Provenance,
    ProvenanceType,
    Unknown,
    require,
)
from r3chain.domain.status import (
    AdapterState,
    EvaluationStatus,
    SiteEvidenceStatus,
    StageStatus,
    TechnicalStatus,
    ranking_eligibility,
    stage_status_from_adapter,
    summarise_stage_statuses,
)

SOURCED = Provenance(provenance_type=ProvenanceType.MEASURED, source_id="survey-1", locator="p 4")
NUMBER = TypeAdapter(Evidence[Real])


@pytest.mark.requirement("W25")
def test_sourced_value_must_name_its_source() -> None:
    for kind in ("measured", "imported", "guideline", "synthetic"):
        with pytest.raises(ValidationError, match="source_id"):
            Provenance.model_validate({"provenance_type": kind})
    # A derived value must say how it was derived.
    for kind in ("estimated", "inferred"):
        with pytest.raises(ValidationError, match="method"):
            Provenance.model_validate({"provenance_type": kind})
        Provenance.model_validate({"provenance_type": kind, "method": "scaled from pipe length"})
    with pytest.raises(ValidationError, match="author"):
        Provenance.model_validate({"provenance_type": "user_supplied"})
    # A known value cannot exist without provenance at all.
    with pytest.raises(ValidationError):
        NUMBER.validate_python({"state": "known", "value": 1.0})


@pytest.mark.requirement("A12")
def test_unknown_input_is_never_defaulted() -> None:
    known = Known[float](value=5200.0, provenance=SOURCED)
    unknown = Unknown(reason="not surveyed", owner="site specialist")
    conflict = Conflict[float](
        candidates=(known, Known[float](value=4800.0, provenance=SOURCED)), reviewer="planner"
    )
    assert require(known, "sites[a].usable_pad_area_m2") == 5200.0

    with pytest.raises(NotEstablished) as raised:
        require(unknown, "sites[b].usable_pad_area_m2")
    assert raised.value.missing[0].field_path == "sites[b].usable_pad_area_m2"
    assert raised.value.missing[0].owner == "site specialist"

    # A conflict is not resolved by picking one of the candidates.
    with pytest.raises(NotEstablished) as raised:
        require(conflict, "sites[c].usable_pad_area_m2")
    assert raised.value.missing[0].state == "conflict"

    # A calculation reports every missing input together.
    reader = InputReader()
    assert reader.get(known, "a") == 5200.0
    assert reader.get(unknown, "b") is None
    assert reader.get(conflict, "c") is None
    with pytest.raises(NotEstablished) as raised:
        reader.raise_if_missing()
    assert [item.field_path for item in raised.value.missing] == ["b", "c"]


def test_evidence_states_are_told_apart_by_their_tag() -> None:
    provenance = {"provenance_type": "measured", "source_id": "survey-1"}
    known = NUMBER.validate_python({"state": "known", "value": 3, "provenance": provenance})
    assert isinstance(known, Known) and known.value == 3.0
    unknown = NUMBER.validate_python({"state": "unknown", "reason": "r", "owner": "o"})
    assert isinstance(unknown, Unknown)
    with pytest.raises(ValidationError):
        NUMBER.validate_python({"state": "guessed", "value": 3})
    # A conflict needs at least two candidates.
    with pytest.raises(ValidationError):
        NUMBER.validate_python(
            {"state": "conflict", "candidates": [{"value": 1, "provenance": provenance}]}
        )
    # An unknown input must say why and who resolves it.
    with pytest.raises(ValidationError):
        NUMBER.validate_python({"state": "unknown"})


@pytest.mark.parametrize("bad", ["5200", True, float("nan"), float("inf"), None])
def test_numbers_are_never_coerced(bad: object) -> None:
    provenance = {"provenance_type": "measured", "source_id": "survey-1"}
    with pytest.raises(ValidationError):
        NUMBER.validate_python({"state": "known", "value": bad, "provenance": provenance})


@pytest.mark.requirement("A13")
@pytest.mark.requirement("W02")
def test_non_convergence_is_not_a_technical_failure() -> None:
    completed, not_run = StageStatus.COMPLETED, StageStatus.NOT_RUN

    # Non convergence is an unresolved numerical evaluation. It says nothing about the resource.
    assert summarise_stage_statuses([completed, StageStatus.NON_CONVERGED, not_run]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.UNRESOLVED_NUMERICAL,
    )
    # A physical limit that was exceeded is a valid evaluation of a failed case.
    assert summarise_stage_statuses([completed, StageStatus.PHYSICAL_LIMIT_EXCEEDED, not_run]) == (
        TechnicalStatus.FAILS_EVALUATED_CASE,
        EvaluationStatus.VALID,
    )
    assert summarise_stage_statuses([completed, completed]) == (
        TechnicalStatus.PASSES_EVALUATED_CASES,
        EvaluationStatus.VALID,
    )
    # A missing input is neither a pass nor a failure.
    assert summarise_stage_statuses([completed, StageStatus.NOT_ESTABLISHED, not_run]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.NOT_EVALUATED,
    )
    assert summarise_stage_statuses([StageStatus.UNSUPPORTED_MODEL]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.UNSUPPORTED,
    )
    # A tool failure outranks everything else and never becomes a result.
    assert summarise_stage_statuses([StageStatus.TOOL_FAILURE, StageStatus.NON_CONVERGED]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.TOOL_FAILURE,
    )
    assert summarise_stage_statuses([]) == (
        TechnicalStatus.NOT_ESTABLISHED,
        EvaluationStatus.NOT_EVALUATED,
    )


def test_every_adapter_state_is_a_stage_status() -> None:
    assert {state.value for state in AdapterState} == {
        "completed",
        "invalid_input",
        "unsupported_model",
        "non_converged",
        "physical_limit_exceeded",
        "cancelled",
        "tool_failure",
    }
    for state in AdapterState:
        assert stage_status_from_adapter(state).value == state.value


def test_ranking_needs_a_valid_passing_documented_candidate() -> None:
    passes, valid = TechnicalStatus.PASSES_EVALUATED_CASES, EvaluationStatus.VALID
    documented = SiteEvidenceStatus.DOCUMENTED

    eligible = ranking_eligibility(passes, documented, valid, comparison_fields_complete=True)
    assert eligible.eligible and not eligible.conditional and eligible.reasons == ()

    # Unknown site evidence can never become a positive check.
    for status in (SiteEvidenceStatus.UNKNOWN, SiteEvidenceStatus.CONFLICT):
        blocked = ranking_eligibility(passes, status, valid, comparison_fields_complete=True)
        assert not blocked.eligible
        assert f"site evidence is {status.value}" in blocked.reasons

    # A conditional candidate is ranked only if the policy says so, and it stays marked.
    conditional = SiteEvidenceStatus.CONDITIONAL
    assert not ranking_eligibility(
        passes, conditional, valid, comparison_fields_complete=True
    ).eligible
    allowed = ranking_eligibility(
        passes,
        conditional,
        valid,
        comparison_fields_complete=True,
        rankable_site_statuses=frozenset({documented, conditional}),
    )
    assert allowed.eligible and allowed.conditional

    # A numerical failure is not rankable and is not described as a technical failure.
    unresolved = ranking_eligibility(
        TechnicalStatus.NOT_ESTABLISHED,
        documented,
        EvaluationStatus.UNRESOLVED_NUMERICAL,
        comparison_fields_complete=False,
    )
    assert not unresolved.eligible
    assert unresolved.reasons == (
        "evaluation is unresolved_numerical",
        "technical status is not_established",
        "comparison fields are incomplete",
    )
