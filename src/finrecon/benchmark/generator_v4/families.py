"""The v4 family taxonomy, and the composition each archetype demands.

Two orthogonal vocabularies, deliberately kept apart.

**Families** are *descriptive* tags for offline analysis -- what a case
contains. A case may carry several (a wide conjunction case is
``multi_fragment`` *and* ``multi_candidate`` *and* ``decoy``), which is why
they are a set rather than a tier.

**Required composition** is a single *prescriptive* label -- the smallest
evidence combination that separates the true counterparty from the rest. It
is what turns the pilot's diagnostics into a capability statement: a case
labelled ``fragment_and_breakup_amount`` is unresolvable by any strategy
that only compares narration substrings against references, however
exhaustively.

Both live in hidden ground truth only. Nothing under ``src/finrecon``
outside this generator package may read either, and
``tests/test_benchmark_isolation.py`` asserts the reconciliation path never
imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Families (descriptive, many per case) --------------------------------

FAMILY_MULTI_FRAGMENT = "multi_fragment"
"""No single narration fragment reaches the true candidate alone (spec 4.A)."""

FAMILY_CONFLICT = "conflict"
"""A candidate carries positive evidence that another fact contradicts (4.B)."""

FAMILY_MULTI_CANDIDATE = "multi_candidate"
"""More than two plausible candidates (4.C)."""

FAMILY_CONTEXTUAL = "contextual"
"""The useful evidence is non-contiguous within the narration (4.D)."""

FAMILY_MULTI_HOP = "multi_hop"
"""Identification requires following a structured relation, not only a string (4.E)."""

FAMILY_AMOUNT_REFERENCE = "amount_reference"
"""Textual evidence must be combined with exact break-up arithmetic (4.F)."""

FAMILY_DECOY = "decoy"
"""The narration carries individually compelling but insufficient tokens (4.G)."""

FAMILY_TRUE_AMBIGUITY = "true_ambiguity"
"""No candidate is uniquely consistent with the evidence; escalation is correct (4.H)."""

FAMILY_SINGLE_FRAGMENT_CONTROL = "single_fragment_control"
"""Positive control: solvable by one fragment, exactly like benchmark v3 T2.

Not one of the eight families the spec asks for. It is here because a pilot
with no case the existing architecture can solve cannot distinguish "these
cases are hard" from "the harness is broken", and a diagnostic that cannot
fail that way is not a diagnostic.
"""

FAMILIES: tuple[str, ...] = (
    FAMILY_AMOUNT_REFERENCE,
    FAMILY_CONFLICT,
    FAMILY_CONTEXTUAL,
    FAMILY_DECOY,
    FAMILY_MULTI_CANDIDATE,
    FAMILY_MULTI_FRAGMENT,
    FAMILY_MULTI_HOP,
    FAMILY_SINGLE_FRAGMENT_CONTROL,
    FAMILY_TRUE_AMBIGUITY,
)
"""Every family id, alphabetically. Frozen once the pilot is generated."""


# --- Required composition (prescriptive, exactly one per case) -------------

COMPOSITION_SINGLE_FRAGMENT = "single_fragment"
"""One narration fragment, one declared reference relation. The v3 T2 shape."""

COMPOSITION_FRAGMENT_PAIR = "fragment_pair"
"""Two fragments whose reach sets intersect in exactly one candidate."""

COMPOSITION_FRAGMENT_TRIPLE = "fragment_triple"
"""Three fragments; every pair of them still leaves at least two candidates."""

COMPOSITION_FRAGMENT_AND_BREAKUP_AMOUNT = "fragment_and_breakup_amount"
"""A fragment's reach set intersected with "whose break-up carries this amount".

The second half is not a reference relation at all, so no amount of
substring enumeration reaches it.
"""

COMPOSITION_FRAGMENT_AND_VALUE_DATE = "fragment_and_value_date"
"""A fragment's reach set intersected with "whose settlement date is this date"."""

COMPOSITION_NONE = "none"
"""No composition identifies a unique candidate. The correct outcome is escalation."""

COMPOSITIONS: tuple[str, ...] = (
    COMPOSITION_SINGLE_FRAGMENT,
    COMPOSITION_FRAGMENT_PAIR,
    COMPOSITION_FRAGMENT_TRIPLE,
    COMPOSITION_FRAGMENT_AND_BREAKUP_AMOUNT,
    COMPOSITION_FRAGMENT_AND_VALUE_DATE,
    COMPOSITION_NONE,
)

LEXICAL_ONLY_COMPOSITIONS: frozenset[str] = frozenset(
    {
        COMPOSITION_SINGLE_FRAGMENT,
        COMPOSITION_FRAGMENT_PAIR,
        COMPOSITION_FRAGMENT_TRIPLE,
    }
)
"""Compositions a purely lexical strategy could in principle reach.

Everything outside this set needs a feature the declared reference relations
do not contain, which is the pilot's capability question stated as data.
"""


@dataclass(frozen=True)
class ArchetypeSpec:
    """One case archetype: what it builds and what it demands to be solved."""

    archetype: str
    families: tuple[str, ...]
    required_composition: str
    required_outcome: str
    """``AUTO_RESOLVABLE`` or ``ESCALATE``."""
    candidate_counts: tuple[int, ...]
    """Candidate-set sizes this archetype cycles through, in fixed order."""
    note: str


ARCHETYPES: tuple[ArchetypeSpec, ...] = (
    ArchetypeSpec(
        archetype="single_fragment_control",
        families=(FAMILY_SINGLE_FRAGMENT_CONTROL, FAMILY_DECOY, FAMILY_MULTI_CANDIDATE),
        required_composition=COMPOSITION_SINGLE_FRAGMENT,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(3,),
        note=(
            "Positive control. One long reference prefix reaches the true candidate "
            "alone; the narration also carries decoy tokens that are individually "
            "compelling but pin fewer characters than the declared evidence floor."
        ),
    ),
    ArchetypeSpec(
        archetype="conjunction_pair",
        families=(
            FAMILY_MULTI_FRAGMENT,
            FAMILY_CONTEXTUAL,
            FAMILY_DECOY,
            FAMILY_MULTI_CANDIDATE,
        ),
        required_composition=COMPOSITION_FRAGMENT_PAIR,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(3,),
        note=(
            "The reference survives as a truncated head in one narration field and a "
            "truncated tail in another. The head is shared with one decoy, the tail "
            "with a different decoy; only their conjunction is unique."
        ),
    ),
    ArchetypeSpec(
        archetype="conjunction_wide",
        families=(
            FAMILY_MULTI_FRAGMENT,
            FAMILY_MULTI_CANDIDATE,
            FAMILY_CONTEXTUAL,
            FAMILY_DECOY,
        ),
        required_composition=COMPOSITION_FRAGMENT_PAIR,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(4, 5),
        note="conjunction_pair widened to four and five candidates.",
    ),
    ArchetypeSpec(
        archetype="conjunction_triple",
        families=(FAMILY_MULTI_FRAGMENT, FAMILY_MULTI_CANDIDATE, FAMILY_CONTEXTUAL),
        required_composition=COMPOSITION_FRAGMENT_TRIPLE,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(5,),
        note=(
            "Head, tail and a chunk-reordered rendering of the same reference. Every "
            "pair of clues leaves two candidates; all three leave one. The arity probe "
            "for a pairwise composition baseline, and the most synthetic archetype here."
        ),
    ),
    ArchetypeSpec(
        archetype="amount_reference_hop",
        families=(
            FAMILY_AMOUNT_REFERENCE,
            FAMILY_MULTI_HOP,
            FAMILY_MULTI_CANDIDATE,
            FAMILY_DECOY,
        ),
        required_composition=COMPOSITION_FRAGMENT_AND_BREAKUP_AMOUNT,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(3,),
        note=(
            "A reference head reaches two candidates; a refund amount named in the "
            "narration appears as a break-up line in two candidates; one candidate is "
            "in both sets. The hop is narration -> amount -> break-up line -> refund "
            "record -> settlement."
        ),
    ),
    ArchetypeSpec(
        archetype="conflict_stale_reference",
        families=(
            FAMILY_CONFLICT,
            FAMILY_DECOY,
            FAMILY_MULTI_CANDIDATE,
            FAMILY_TRUE_AMBIGUITY,
        ),
        required_composition=COMPOSITION_NONE,
        required_outcome="ESCALATE",
        candidate_counts=(3,),
        note=(
            "A stale reference tail from a settlement that is not the counterparty "
            "reaches exactly one candidate, while the narration's value-date field "
            "agrees with a different one. No candidate is consistent with all the "
            "evidence, so escalation is the only correct outcome -- and a strategy "
            "that stops at the first discriminating fragment resolves it, wrongly."
        ),
    ),
    ArchetypeSpec(
        archetype="conflict_context_resolves",
        families=(
            FAMILY_CONFLICT,
            FAMILY_CONTEXTUAL,
            FAMILY_MULTI_CANDIDATE,
            FAMILY_DECOY,
        ),
        required_composition=COMPOSITION_FRAGMENT_AND_VALUE_DATE,
        required_outcome="AUTO_RESOLVABLE",
        candidate_counts=(3,),
        note=(
            "A reference head reaches two candidates dated a day apart; the "
            "narration's value-date field agrees with exactly one of them."
        ),
    ),
    ArchetypeSpec(
        archetype="ambiguity_no_discriminator",
        families=(FAMILY_TRUE_AMBIGUITY, FAMILY_MULTI_CANDIDATE),
        required_composition=COMPOSITION_NONE,
        required_outcome="ESCALATE",
        candidate_counts=(3, 4, 5),
        note=(
            "benchmark v3's T3 widened past two candidates: same amount, same date, a "
            "referenceless narration, nothing to recover."
        ),
    ),
    ArchetypeSpec(
        archetype="ambiguity_conjunction_incomplete",
        families=(
            FAMILY_TRUE_AMBIGUITY,
            FAMILY_MULTI_FRAGMENT,
            FAMILY_MULTI_CANDIDATE,
            FAMILY_CONTEXTUAL,
        ),
        required_composition=COMPOSITION_NONE,
        required_outcome="ESCALATE",
        candidate_counts=(4,),
        note=(
            "Shaped exactly like conjunction_pair, except the two clues intersect in "
            "two candidates rather than one. Defeats a composition strategy that "
            "assumes intersecting far enough must eventually yield a singleton."
        ),
    ),
)

ARCHETYPE_BY_NAME: dict[str, ArchetypeSpec] = {spec.archetype: spec for spec in ARCHETYPES}

ARCHETYPE_NAMES: tuple[str, ...] = tuple(spec.archetype for spec in ARCHETYPES)


def archetype_spec(name: str) -> ArchetypeSpec:
    try:
        return ARCHETYPE_BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"unknown v4 archetype: {name!r}") from exc


__all__ = [
    "ARCHETYPES",
    "ARCHETYPE_BY_NAME",
    "ARCHETYPE_NAMES",
    "COMPOSITIONS",
    "COMPOSITION_FRAGMENT_AND_BREAKUP_AMOUNT",
    "COMPOSITION_FRAGMENT_AND_VALUE_DATE",
    "COMPOSITION_FRAGMENT_PAIR",
    "COMPOSITION_FRAGMENT_TRIPLE",
    "COMPOSITION_NONE",
    "COMPOSITION_SINGLE_FRAGMENT",
    "FAMILIES",
    "FAMILY_AMOUNT_REFERENCE",
    "FAMILY_CONFLICT",
    "FAMILY_CONTEXTUAL",
    "FAMILY_DECOY",
    "FAMILY_MULTI_CANDIDATE",
    "FAMILY_MULTI_FRAGMENT",
    "FAMILY_MULTI_HOP",
    "FAMILY_SINGLE_FRAGMENT_CONTROL",
    "FAMILY_TRUE_AMBIGUITY",
    "LEXICAL_ONLY_COMPOSITIONS",
    "ArchetypeSpec",
    "archetype_spec",
]
