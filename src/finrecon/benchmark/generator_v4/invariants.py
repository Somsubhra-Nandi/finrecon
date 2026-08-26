"""Independent verification of every v4 pilot case, per case and batch-wide.

Benchmark v1 declared a tier and trusted the declaration; Stage 2 then showed
the declaration was not backed by the data. v2 answered that by re-deriving
every T2 case from its own records. v4 keeps that discipline and widens it,
because v4 makes a stronger claim than v2 did. v2 claimed "one thing is
recoverable here". v4 claims "**no** single fragment identifies the true
counterparty, and this specific combination does" -- a claim about the whole
search space, which can only be settled by searching it.

So this module searches it. For every case it enumerates every admissible
narration fragment, computes the exact candidate set each one reaches under
the **real** declared relations, and then asks the questions the archetype
promised an answer to.

Why the fragment enumeration is provably complete
-------------------------------------------------

Fragments are enumerated from four characters (the declared evidence floor)
to twenty. That upper bound is not a sampling cap; it is exhaustive, given
two assertions this module makes separately for every case:

* no candidate's reference appears in the narration, either literally or
  after declared separators are stripped;
* the narration contains no mask character.

With those, ``contains_reference``, ``separator_normalized_equal``,
``mask_consistent`` and ``exact`` are all unreachable for any fragment of any
length. What remains is ``prefix_of_reference``, ``suffix_of_reference`` and
``character_multiset_equal``, and all three require the fragment to be no
longer than the reference -- at most nineteen characters, the length of a
generated settlement ID. Twenty is that bound plus one.

What is reimplemented and what is imported
------------------------------------------

The *plausibility* model -- which settlement groups a credit could be -- is
imported from :mod:`finrecon.benchmark.generator.t2_invariants`, which is
already the generator's own independent restatement of the declared Stage-2
blocking rules. Restating it a third time would add a third thing to keep in
sync, not a third opinion.

The *relations* are imported from :mod:`finrecon.evidence.reference`, and
that is deliberate in the opposite direction: here the question is not "do
two independent readings agree" but "what will the shipped validator actually
do with this narration". An independently written relation set would answer a
question nobody asked. Note the consequence, stated plainly rather than
buried: this makes the v4 generator's difficulty claims *relative to the
declared relation set*, exactly as ``notes/STAGE3-FINDINGS.md`` section 1
says of v3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations

from finrecon.benchmark.generator.t2_invariants import (
    PlausibilityInputs,
    plausible_settlement_groups,
)
from finrecon.benchmark.generator.token_contract import is_usable_direct_key
from finrecon.evidence.reference import (
    DECLARED_RELATION_IDS,
    MASK_CHARACTERS,
    REFERENCE_KINDS,
    compare,
    fold,
    strip_separators,
    strongest_admissible_relation,
)
from finrecon.models import BankRecord, Settlement

FRAGMENT_MIN_LENGTH = 4
"""The declared evidence floor. A shorter fragment can pin nothing admissible."""

FRAGMENT_MAX_LENGTH = 20
"""Exhaustive, not a sample. See the module docstring for why this is a bound."""

DEFAULT_FLOOR = 4
"""``EvidencePolicy.min_pinned_reference_characters``. The value ladder does not
bind on this pilot's amounts, so the ordinary floor is the only one in play."""

MAX_COMPOSITION_ARITY = 4
"""How many clue reach sets the analysis will intersect before giving up.

Four is one more than the highest arity any archetype claims to need, so an
archetype that accidentally became solvable at a *lower* arity than declared
fails its invariant, and one that became solvable at a higher arity than
declared is reported rather than missed.
"""

ACCEPTED_RELATIONS = frozenset(DECLARED_RELATION_IDS)


class V4ConstructError(AssertionError):
    """A generated v4 case does not satisfy the construct its archetype declares."""


# --- fragment / reach analysis --------------------------------------------


def narration_fragments(narration: str) -> tuple[str, ...]:
    """Every distinct contiguous substring within the admissible length band."""
    seen: set[str] = set()
    length = len(narration)
    for start in range(length):
        stop = min(length, start + FRAGMENT_MAX_LENGTH)
        for end in range(start + FRAGMENT_MIN_LENGTH, stop + 1):
            seen.add(narration[start:end])
    return tuple(sorted(seen))


def references_of(settlement: Settlement) -> tuple[tuple[str, str], ...]:
    """``(kind, value)`` for every reference this settlement carries.

    Mirrors :func:`finrecon.decide.validator._references_of` exactly, including
    the fixed ``REFERENCE_KINDS`` order and the rule that a settlement with no
    UTR contributes no ``utr`` entry rather than an empty one.
    """
    values: dict[str, str | None] = {
        "utr": settlement.utr,
        "settlement_id": settlement.settlement_id,
    }
    return tuple((kind, values[kind]) for kind in REFERENCE_KINDS if values[kind] is not None)  # type: ignore[misc]


def reach_of(
    fragment: str,
    settlements: tuple[Settlement, ...],
    *,
    floor: int = DEFAULT_FLOOR,
) -> frozenset[str]:
    """Every settlement this fragment stands in an admissible relation to."""
    reached: set[str] = set()
    for settlement in settlements:
        for kind, value in references_of(settlement):
            relation = strongest_admissible_relation(
                compare(fragment, value, kind),  # type: ignore[arg-type]
                accepted_relation_ids=ACCEPTED_RELATIONS,
                min_pinned_reference_characters=floor,
            )
            if relation is not None:
                reached.add(settlement.settlement_id)
                break
    return frozenset(reached)


@dataclass(frozen=True)
class LexicalAnalysis:
    """What every narration fragment proves about this case's candidates."""

    reach_by_fragment: dict[str, frozenset[str]]
    """Only fragments that reach at least one candidate."""
    distinct_reach_sets: tuple[frozenset[str], ...]
    single_fragment_identifications: frozenset[str]
    """Candidates some one fragment reaches alone. The v3-era resolution rule."""
    minimal_arity: int | None
    """Smallest number of reach sets whose intersection is a singleton, or ``None``."""
    arity_singletons: dict[int, frozenset[str]]
    """Arity -> every candidate reachable as a singleton intersection at that arity."""

    def fragments_reaching(self, settlement_ids: frozenset[str]) -> tuple[str, ...]:
        return tuple(
            sorted(f for f, reach in self.reach_by_fragment.items() if reach == settlement_ids)
        )


def analyse_lexical(
    narration: str,
    settlements: tuple[Settlement, ...],
    *,
    floor: int = DEFAULT_FLOOR,
    max_arity: int = MAX_COMPOSITION_ARITY,
) -> LexicalAnalysis:
    """Enumerate the whole lexical search space this narration offers."""
    reach_by_fragment: dict[str, frozenset[str]] = {}
    for fragment in narration_fragments(narration):
        reached = reach_of(fragment, settlements, floor=floor)
        if reached:
            reach_by_fragment[fragment] = reached

    distinct = tuple(sorted(set(reach_by_fragment.values()), key=lambda s: tuple(sorted(s))))
    singles = frozenset(
        next(iter(reach)) for reach in distinct if len(reach) == 1
    )

    arity_singletons: dict[int, frozenset[str]] = {}
    if singles:
        arity_singletons[1] = singles
    for arity in range(2, max_arity + 1):
        found: set[str] = set()
        for combo in combinations(distinct, arity):
            intersection: frozenset[str] = combo[0]
            for member in combo[1:]:
                intersection = intersection & member
            if len(intersection) == 1:
                found.add(next(iter(intersection)))
        if found:
            arity_singletons[arity] = frozenset(found)

    minimal = min(arity_singletons) if arity_singletons else None
    return LexicalAnalysis(
        reach_by_fragment=reach_by_fragment,
        distinct_reach_sets=distinct,
        single_fragment_identifications=singles,
        minimal_arity=minimal,
        arity_singletons=arity_singletons,
    )


# --- structural features ---------------------------------------------------


def settlements_with_breakup_amount(
    settlements: tuple[Settlement, ...], amount_paise: int
) -> frozenset[str]:
    """Settlements one of whose break-up lines has this absolute paise magnitude."""
    return frozenset(
        settlement.settlement_id
        for settlement in settlements
        if any(abs(int(line.amount)) == amount_paise for line in settlement.breakup)
    )


def settlements_on_date(
    settlements: tuple[Settlement, ...], value_date: date
) -> frozenset[str]:
    return frozenset(
        settlement.settlement_id
        for settlement in settlements
        if settlement.created_at.date() == value_date
    )


# --- per-case verification -------------------------------------------------


@dataclass(frozen=True)
class CaseExpectation:
    """What one archetype promises about its own search space."""

    archetype: str
    expected_candidate_count: int
    true_settlement_id: str | None
    """``None`` for an archetype whose correct outcome is escalation."""
    single_fragment_identifications: frozenset[str]
    """Exactly which candidates one fragment may identify alone. Usually empty."""
    minimal_lexical_arity: int | None
    """The arity the archetype claims, or ``None`` when no lexical arity suffices."""
    structural_reach: frozenset[str] | None = None
    """Candidates the archetype's non-lexical feature selects, or ``None`` if it has none.

    Required to hold at least two candidates whenever it is present: a
    structural feature that already isolates one candidate on its own is not a
    composition, and a case built on one would be measuring a single feature
    while claiming to measure a conjunction.
    """
    structural_singletons: frozenset[str] = frozenset()
    """Candidates isolable by intersecting the structural reach with some lexical one.

    ``{truth}`` for the archetypes whose answer is a cross-modal conjunction;
    empty for ``conflict_stale_reference``, where the point is that *no*
    candidate is consistent with all the evidence.
    """


@dataclass(frozen=True)
class CaseVerification:
    """What the check observed, kept for diagnostics as well as enforcement."""

    case_id: str
    candidate_groups: tuple[tuple[str, ...], ...]
    candidate_settlement_ids: tuple[str, ...]
    lexical: LexicalAnalysis


def verify_case(
    *,
    case_id: str,
    bank_record: BankRecord,
    pool: PlausibilityInputs,
    expectation: CaseExpectation,
    floor: int = DEFAULT_FLOOR,
) -> CaseVerification:
    """Enforce every v4 invariant for one case. Raises :class:`V4ConstructError`.

    ``pool`` is whichever record universe the caller wants the case checked
    against -- its own records at build time, the whole split afterwards. A
    wider pool can only add candidates, so both passes are run and the second
    is the one that matters.
    """
    narration = bank_record.narration
    by_id = {s.settlement_id: s for s in pool.settlements}

    groups = plausible_settlement_groups(bank_record, pool)
    candidate_ids = tuple(sorted({sid for group in groups for sid in group}))
    candidates = tuple(by_id[sid] for sid in candidate_ids)

    # (1) Nothing in this narration is a usable direct join key: a v4 case that
    #     Stage 2's direct-key matcher could settle is not a v4 case.
    for settlement in pool.settlements:
        for identifier in (settlement.settlement_id, settlement.utr):
            if identifier and is_usable_direct_key(narration, identifier):
                raise V4ConstructError(
                    f"v4 case {case_id!r}: narration {narration!r} carries {identifier!r} "
                    "as a whole token, which is a direct key"
                )

    # (2) The candidate set is exactly the size the archetype built for. A
    #     silently widened set is a silently different difficulty.
    if len(groups) != expectation.expected_candidate_count:
        raise V4ConstructError(
            f"v4 case {case_id!r} ({expectation.archetype}): structured evidence leaves "
            f"{len(groups)} plausible candidate group(s) {groups}, but the archetype "
            f"built {expectation.expected_candidate_count}"
        )
    if any(len(group) != 1 for group in groups):
        raise V4ConstructError(
            f"v4 case {case_id!r}: a candidate group is not a single settlement {groups}; "
            "v4 keeps every candidate a singleton so candidate count means candidate count"
        )

    # (3) The true counterparty, when there is one, is among the candidates.
    if expectation.true_settlement_id is not None:
        if (expectation.true_settlement_id,) not in groups:
            raise V4ConstructError(
                f"v4 case {case_id!r}: true settlement "
                f"{expectation.true_settlement_id!r} is not among the plausible "
                f"candidate groups {groups}"
            )

    # (4) The enumeration bound is a bound. Both halves of the argument in the
    #     module docstring are asserted here rather than assumed.
    if any(character in narration for character in MASK_CHARACTERS):
        raise V4ConstructError(
            f"v4 case {case_id!r}: narration {narration!r} contains a mask character"
        )
    folded_narration = fold(narration)
    stripped_narration = strip_separators(folded_narration)
    for settlement in candidates:
        for _kind, value in references_of(settlement):
            folded_value = fold(value)
            if folded_value in folded_narration or folded_value in stripped_narration:
                raise V4ConstructError(
                    f"v4 case {case_id!r}: reference {value!r} appears in the narration "
                    "(literally or after separator stripping), which would make a "
                    "contains/separator relation reachable and break the fragment bound"
                )

    lexical = analyse_lexical(narration, candidates, floor=floor)

    # (5) Exactly the single-fragment identifications the archetype declares --
    #     usually none at all, which is the whole point of v4.
    if lexical.single_fragment_identifications != expectation.single_fragment_identifications:
        raise V4ConstructError(
            f"v4 case {case_id!r} ({expectation.archetype}): exhaustive single-fragment "
            f"enumeration identifies {sorted(lexical.single_fragment_identifications)}, "
            f"but the archetype declares {sorted(expectation.single_fragment_identifications)}"
        )

    # (6) The lexical composition arity is exactly what the archetype claims.
    if lexical.minimal_arity != expectation.minimal_lexical_arity:
        raise V4ConstructError(
            f"v4 case {case_id!r} ({expectation.archetype}): the smallest lexical "
            f"composition that isolates a candidate has arity {lexical.minimal_arity}, "
            f"but the archetype declares {expectation.minimal_lexical_arity} "
            f"(reach sets: {[sorted(s) for s in lexical.distinct_reach_sets]})"
        )
    if expectation.minimal_lexical_arity is not None:
        reached = lexical.arity_singletons[expectation.minimal_lexical_arity]
        expected_target = (
            frozenset({expectation.true_settlement_id})
            if expectation.true_settlement_id is not None
            else frozenset()
        )
        if expectation.archetype != "conflict_stale_reference" and reached != expected_target:
            raise V4ConstructError(
                f"v4 case {case_id!r} ({expectation.archetype}): composition at arity "
                f"{expectation.minimal_lexical_arity} isolates {sorted(reached)}, "
                f"expected {sorted(expected_target)}"
            )

    # (7) When the archetype needs a non-lexical feature: that feature must not
    #     isolate a candidate by itself, and intersecting it with the lexical
    #     evidence must isolate exactly what the archetype declares.
    if expectation.structural_reach is not None:
        structural = expectation.structural_reach
        if len(structural) < 2:
            raise V4ConstructError(
                f"v4 case {case_id!r} ({expectation.archetype}): the structural feature "
                f"already selects {sorted(structural)} on its own. A feature that "
                "isolates a candidate unaided is not half of a composition."
            )
        isolated = {
            next(iter(reach & structural))
            for reach in lexical.distinct_reach_sets
            if len(reach & structural) == 1
        }
        if frozenset(isolated) != expectation.structural_singletons:
            raise V4ConstructError(
                f"v4 case {case_id!r} ({expectation.archetype}): intersecting the "
                f"structural feature {sorted(structural)} with the lexical reach sets "
                f"{[sorted(s) for s in lexical.distinct_reach_sets]} isolates "
                f"{sorted(isolated)}, but the archetype declares "
                f"{sorted(expectation.structural_singletons)}"
            )

    return CaseVerification(
        case_id=case_id,
        candidate_groups=groups,
        candidate_settlement_ids=candidate_ids,
        lexical=lexical,
    )


__all__ = [
    "ACCEPTED_RELATIONS",
    "DEFAULT_FLOOR",
    "FRAGMENT_MAX_LENGTH",
    "FRAGMENT_MIN_LENGTH",
    "MAX_COMPOSITION_ARITY",
    "CaseExpectation",
    "CaseVerification",
    "LexicalAnalysis",
    "PlausibilityInputs",
    "V4ConstructError",
    "analyse_lexical",
    "narration_fragments",
    "reach_of",
    "references_of",
    "settlements_on_date",
    "settlements_with_breakup_amount",
    "verify_case",
]
