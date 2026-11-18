"""Typed explanation records, deterministic claim checks, and fixed DE/EN wording."""

from r3chain.explain.builder import (
    explain_comparison,
    explain_rejection,
    explain_result,
    explain_study,
)
from r3chain.explain.checker import (
    VerificationContext,
    build_verification_context,
    check_claim,
    check_explanation,
    verify_claims_with_context,
)
from r3chain.explain.records import (
    CauseOfFailureClaim,
    ClaimCheckOutcome,
    ClaimType,
    ComparisonClaim,
    ComparisonEntry,
    ExplanationClaim,
    ExplanationRecord,
    OpenAssumptionClaim,
    StatusClaim,
    ValueClaim,
)
from r3chain.explain.wording import Language, render_claim, render_explanation

__all__ = [
    "CauseOfFailureClaim",
    "ClaimCheckOutcome",
    "ClaimType",
    "ComparisonClaim",
    "ComparisonEntry",
    "ExplanationClaim",
    "ExplanationRecord",
    "Language",
    "OpenAssumptionClaim",
    "StatusClaim",
    "ValueClaim",
    "VerificationContext",
    "build_verification_context",
    "check_claim",
    "check_explanation",
    "explain_comparison",
    "explain_rejection",
    "explain_result",
    "explain_study",
    "render_claim",
    "render_explanation",
    "verify_claims_with_context",
]
