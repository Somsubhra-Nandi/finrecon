"""Failure modes of the evaluator, each one loud and fail-closed.

Every error here exists so that a *partial* or *unverifiable* evaluation
stops rather than reporting a number. A harness that silently drops cases it
could not find would produce a smaller cohort and a better-looking
percentage, which is the single most dangerous thing an evaluator can do.
"""

from __future__ import annotations


class EvaluationError(RuntimeError):
    """Base class: an evaluation that must not be reported."""


class EvaluationInputError(EvaluationError):
    """An input artifact is missing, unreadable, or not what it claims to be.

    Raised instead of falling back to a live run. The evaluator has no
    provider to fall back to, and this is the error that keeps it that way.
    """


class CohortError(EvaluationError):
    """The evaluated cohort is not the cohort that was requested."""


class GroundTruthPolicyError(EvaluationError):
    """Ground-truth access was requested for a split the policy gates.

    DEV is the development split and is scorable freely. FROZEN-EVAL is the
    held-out artifact whose whole value is that outcomes are not consulted
    while iterating (DESIGN.md §5.1 step 7), so reading its truth takes an
    explicit, deliberate opt-in.
    """


class ReplayIntegrityError(EvaluationError):
    """Replay did not reproduce the cohort exactly, or reached for a provider.

    Includes the version-drift case: a recorded trajectory whose cache key no
    longer matches what the current code computes was produced under a
    different prompt, tool schema or policy, and scoring it as if it were
    current would misattribute the result.
    """


__all__ = [
    "CohortError",
    "EvaluationError",
    "EvaluationInputError",
    "GroundTruthPolicyError",
    "ReplayIntegrityError",
]
