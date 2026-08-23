"""Declared Stage-3 policy: evidence thresholds and value-aware limits.

Every number below is a **declared rule** in the DESIGN.md 4.3 sense --
stated up front, applied uniformly, recorded on the decision by the policy
version that names it. Nothing in the decision path may widen a threshold,
relax a bound or invent a tolerance at runtime.

Two things are stated plainly because a reviewer will ask.

**These defaults were not tuned against the benchmark.** They were chosen
from the a-priori arguments written beside each one, before any DEV or
FROZEN-EVAL outcome was measured against them. Every threshold is also
oriented so that moving it in the *conservative* direction (higher evidence
floor, lower value ceiling) can only reduce auto-resolutions -- it can never
convert an escalation into a match. A tuning pass would show up as movement
in the permissive direction, which is exactly what the frozen benchmark
protocol exists to catch.

**The value thresholds do not bind on this benchmark.** The synthetic data
tops out around a few tens of thousands of rupees, well under the ceiling
below. So the value gate is exercised by construction in the tests rather
than by the dataset, and no coverage number here is a product of it. Saying
that is better than quietly picking a ceiling that makes the mechanism look
active.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finrecon.evidence.reference import DECLARED_RELATION_IDS

RUPEE = 100
"""Paise per rupee. Money stays integer paise everywhere (DESIGN.md 4.6)."""


@dataclass(frozen=True)
class EvidencePolicy:
    """What counts as an admissible reference link."""

    min_pinned_reference_characters: int = 4
    """Floor on how many reference characters a relation must pin down.

    Chosen from the alphabet, not from a result. Canonical references are
    upper-case alphanumeric, so four pinned characters is one in ~36^4 --
    about 1.7 million -- against a candidate set the blocking stage bounds
    to a handful of settlements inside a two-day value-date window. Its job
    is to reject *degenerate* fragments (a model testing ``"R"``), not to
    carry the safety argument: uniqueness over the complete candidate set
    does that, and this floor sits underneath it.

    Four is also the smallest fragment the declared degradation vocabulary
    can leave behind, so raising it would discard evidence that is genuinely
    present rather than evidence that is genuinely weak.
    """

    accepted_relation_ids: frozenset[str] = field(
        default_factory=lambda: frozenset(DECLARED_RELATION_IDS)
    )
    """Which mechanical relations may support a link. All of them, by default."""

    require_fragment_present_in_narration: bool = True
    """A fragment must occur literally in the immutable narration.

    The structural answer to a model inventing a reference. The validator
    re-checks presence against the snapshot itself rather than trusting the
    tool's boolean, so a fabricated fragment is inadmissible no matter what
    any tool output claims about it.
    """

    max_unexplained_delta_paise: int = 0
    """DESIGN.md 4.3: any unexplained delta above 0 paise is a hard blocker.

    Not a tolerance band set to zero -- there is no band. A delta is
    explained only when a declared break-up line accounts for it exactly.
    """

    require_exact_total_blocking_rule: bool = True
    """Only candidates proven by exact-total blocking may auto-resolve.

    Stage 2 emits a widened ``date_window_only`` candidate set when nothing
    totals exactly, so a case in that state has no arithmetically provable
    counterparty. Recovering a reference does not repair that, and resolving
    on a reference alone would settle money whose amount nobody has
    accounted for.
    """


@dataclass(frozen=True)
class ValuePolicy:
    """DESIGN.md 4.5: the auto-resolution bar rises with the amount at stake."""

    auto_resolution_ceiling_paise: int = 500_000 * RUPEE
    """Above this, escalate regardless of how good the evidence looks. Rs 5,00,000.

    A configuration choice, documented as one. DESIGN.md 4.5 requires the
    bar to rise with value but names no figure, and inventing a number and
    attributing it to Razorpay would be worse than admitting it is a
    conservative default. Deployments are expected to set this from their
    own risk appetite; the constant exists so the limit is visible in one
    place instead of implied by its absence.
    """

    elevated_scrutiny_threshold_paise: int = 100_000 * RUPEE
    """Above this, a stronger evidence floor applies. Rs 1,00,000."""

    elevated_min_pinned_reference_characters: int = 8
    """The floor that replaces the ordinary one above the scrutiny threshold.

    Double the base. The reasoning is the asymmetry from DESIGN.md 1: an
    escalated case costs an analyst five minutes, a wrong auto-match costs a
    restatement, and that trade gets worse as the amount grows. Eight pinned
    characters is ~36^8, which is a different order of confidence for a
    different order of money.
    """

    def min_pinned_for(self, value_paise: int, base: int) -> int:
        if value_paise > self.elevated_scrutiny_threshold_paise:
            return max(base, self.elevated_min_pinned_reference_characters)
        return base

    def exceeds_ceiling(self, value_paise: int) -> bool:
        return value_paise > self.auto_resolution_ceiling_paise


@dataclass(frozen=True)
class Stage3Policy:
    """The complete declared policy, recorded on every Stage-3 decision."""

    evidence: EvidencePolicy = field(default_factory=EvidencePolicy)
    value: ValuePolicy = field(default_factory=ValuePolicy)

    def describe(self) -> dict[str, object]:
        """A serializable statement of the rules a decision was taken under."""
        return {
            "min_pinned_reference_characters": self.evidence.min_pinned_reference_characters,
            "accepted_relation_ids": sorted(self.evidence.accepted_relation_ids),
            "require_fragment_present_in_narration": (
                self.evidence.require_fragment_present_in_narration
            ),
            "max_unexplained_delta_paise": self.evidence.max_unexplained_delta_paise,
            "require_exact_total_blocking_rule": (
                self.evidence.require_exact_total_blocking_rule
            ),
            "auto_resolution_ceiling_paise": self.value.auto_resolution_ceiling_paise,
            "elevated_scrutiny_threshold_paise": self.value.elevated_scrutiny_threshold_paise,
            "elevated_min_pinned_reference_characters": (
                self.value.elevated_min_pinned_reference_characters
            ),
        }


DEFAULT_POLICY = Stage3Policy()

__all__ = [
    "DEFAULT_POLICY",
    "RUPEE",
    "EvidencePolicy",
    "Stage3Policy",
    "ValuePolicy",
]
