"""Mechanical comparison between a narration fragment and a canonical reference.

This module is the heart of the Stage-3 evidence path, and its most
important property is what it refuses to do: **it never says which
candidate is correct.** It answers one narrow, purely lexical question --

    "In what declared, mechanical way, if any, is this fragment related to
    this reference string, and how many characters of the reference does
    that relation pin down?"

-- and returns the answer for *every* declared relation, holding or not.
There is no ``is_match`` field, no score, no rank, and no candidate
identity anywhere in the output.

Why the relations are stated here, once
---------------------------------------

The same seven relations are used by two callers with opposite authority:

* :mod:`finrecon.agent.tools` shows a model the comparison for **one**
  candidate it asked about. That output is evidence, not a decision.
* :mod:`finrecon.decide.validator` recomputes the comparison for **every**
  candidate in the immutable Stage-2 snapshot, and only then asks whether
  exactly one candidate is reachable.

Sharing the predicate is what makes those two views comparable; separating
the *authority* is what makes the design safe. A model may choose which
fragment to test -- that is the investigation -- but it cannot choose which
candidates the fragment is tested against.

Deliberately independent of the benchmark
-----------------------------------------

``benchmark/generator/t2_evidence.py`` holds a superficially similar set of
predicates. This module does **not** import it and must not: that one is
the generator's private answer to "could recovery work here", it is keyed
by a hidden degradation-category label, and importing it would put
benchmark internals on the production path. The relations below are
re-derived from the public DESIGN.md 5.2 degradation vocabulary and are
applied **without any knowledge of which category** (or tier) a case
belongs to -- all seven are always evaluated.

Permissiveness cuts the safe way
--------------------------------

Each relation is the widest test consistent with the transform it
corresponds to (an anagram test rather than a permutation solver, a prefix
test rather than a length-aware one). A wider relation makes it *easier*
for a second candidate to also satisfy it, and the validator requires
exactly one candidate to survive -- so erring wide costs coverage, never
precision.
"""

from __future__ import annotations

from typing import Final, Literal

from finrecon.normalize.provenance import FrozenModel

# --- Declared character alphabets ----------------------------------------

MASK_CHARACTERS: Final = frozenset("*#")
"""Characters treated as "one hidden character" by ``mask_consistent``.

Both are conventional statement-masking glyphs and neither can occur inside
a canonical identifier (the Stage-2 tokenizer's identifier class is
``[A-Za-z0-9_]``), so admitting them cannot make a real reference character
compare equal to a mask by accident.
"""

SEPARATOR_CHARACTERS: Final = frozenset("-/. ")
"""Characters stripped from a fragment by ``separator_normalized_equal``.

``_`` is deliberately **excluded**, mirroring
:mod:`finrecon.normalize.tokens`: an underscore is part of a canonical
identifier (``setl_dev_000123``), not a separator between references, and
stripping it would let an unrelated fragment normalize onto a real ID.
"""

REFERENCE_KINDS: Final = ("utr", "settlement_id")
ReferenceKind = Literal["utr", "settlement_id"]

RELATION_EXACT: Final = "exact"
RELATION_PREFIX: Final = "prefix_of_reference"
RELATION_SUFFIX: Final = "suffix_of_reference"
RELATION_CONTAINS: Final = "contains_reference"
RELATION_MASK: Final = "mask_consistent"
RELATION_SEPARATOR: Final = "separator_normalized_equal"
RELATION_MULTISET: Final = "character_multiset_equal"

DECLARED_RELATION_IDS: Final[tuple[str, ...]] = (
    RELATION_EXACT,
    RELATION_PREFIX,
    RELATION_SUFFIX,
    RELATION_CONTAINS,
    RELATION_MASK,
    RELATION_SEPARATOR,
    RELATION_MULTISET,
)
"""Every relation, in fixed order. Output ordering never depends on which held."""

RELATION_DESCRIPTIONS: Final[dict[str, str]] = {
    RELATION_EXACT: "fragment equals the reference after case folding",
    RELATION_PREFIX: "the reference starts with the fragment (right truncation)",
    RELATION_SUFFIX: "the reference ends with the fragment (left truncation)",
    RELATION_CONTAINS: "the fragment contains the whole reference (embedding in noise)",
    RELATION_MASK: "same length, and every unmasked fragment position equals the reference",
    RELATION_SEPARATOR: "removing declared separators from the fragment yields the reference",
    RELATION_MULTISET: "same length and same character multiset (chunk reordering)",
}


def fold(value: str) -> str:
    """Case-folding used on both sides of every comparison.

    Upper-casing only, matching :func:`finrecon.normalize.tokens.token_key`,
    so a Stage-3 comparison and a Stage-2 direct-key lookup agree about what
    "the same characters" means.
    """
    return value.upper()


def strip_separators(value: str) -> str:
    return "".join(ch for ch in value if ch not in SEPARATOR_CHARACTERS)


def count_masked(value: str) -> int:
    return sum(1 for ch in value if ch in MASK_CHARACTERS)


def count_separators(value: str) -> int:
    return sum(1 for ch in value if ch in SEPARATOR_CHARACTERS)


def count_alphanumeric(value: str) -> int:
    return sum(1 for ch in value if ch.isalnum())


# --- The relations -------------------------------------------------------
#
# Each returns (holds, pinned_reference_characters). "Pinned" is the number
# of reference characters the relation determines exactly, and it is the
# only strength signal anywhere in Stage 3 -- a count of facts, never a
# confidence. The policy layer applies a declared floor to it so a
# degenerate two-character fragment cannot carry a resolution.


def _relation_exact(f: str, r: str) -> tuple[bool, int]:
    return (bool(r) and f == r), len(r)


def _relation_prefix(f: str, r: str) -> tuple[bool, int]:
    return (bool(f) and f != r and r.startswith(f)), len(f)


def _relation_suffix(f: str, r: str) -> tuple[bool, int]:
    return (bool(f) and f != r and r.endswith(f)), len(f)


def _relation_contains(f: str, r: str) -> tuple[bool, int]:
    return (bool(r) and f != r and r in f), len(r)


def _relation_mask(f: str, r: str) -> tuple[bool, int]:
    masked = count_masked(f)
    if masked == 0 or len(f) != len(r):
        return False, 0
    pinned = 0
    for fragment_char, reference_char in zip(f, r):
        if fragment_char in MASK_CHARACTERS:
            continue
        if fragment_char != reference_char:
            return False, 0
        pinned += 1
    return True, pinned


def _relation_separator(f: str, r: str) -> tuple[bool, int]:
    if count_separators(f) == 0:
        return False, 0
    stripped = strip_separators(f)
    return (bool(r) and stripped == r), len(r)


def _relation_multiset(f: str, r: str) -> tuple[bool, int]:
    if not r or len(f) != len(r) or f == r:
        return False, 0
    return sorted(f) == sorted(r), len(r)


_RELATIONS = {
    RELATION_EXACT: _relation_exact,
    RELATION_PREFIX: _relation_prefix,
    RELATION_SUFFIX: _relation_suffix,
    RELATION_CONTAINS: _relation_contains,
    RELATION_MASK: _relation_mask,
    RELATION_SEPARATOR: _relation_separator,
    RELATION_MULTISET: _relation_multiset,
}


class ReferenceRelation(FrozenModel):
    """One declared relation, evaluated. Reported whether or not it holds."""

    relation_id: str
    holds: bool
    pinned_reference_characters: int
    """Reference characters this relation determines exactly. ``0`` when it does not hold."""


class ReferenceComparison(FrozenModel):
    """The complete mechanical comparison of one fragment against one reference.

    Every declared relation appears in :attr:`relations`, holding or not, so
    the shape of the output does not itself leak a conclusion. There is no
    verdict field by design (DESIGN.md 4.1): a comparison is evidence, and
    what it *means* is decided elsewhere, over the complete candidate set.
    """

    fragment: str
    fragment_folded: str
    fragment_length: int
    fragment_alphanumeric_characters: int
    fragment_mask_characters: int
    fragment_separator_characters: int
    reference_kind: ReferenceKind
    reference_value: str
    reference_folded: str
    reference_length: int
    relations: tuple[ReferenceRelation, ...]
    holding_relation_ids: tuple[str, ...]
    max_pinned_reference_characters: int
    """Largest ``pinned_reference_characters`` over relations that hold; ``0`` if none do."""


def compare(
    fragment: str, reference_value: str, reference_kind: ReferenceKind
) -> ReferenceComparison:
    """Evaluate every declared relation between ``fragment`` and one reference."""
    folded_fragment = fold(fragment)
    folded_reference = fold(reference_value)

    relations: list[ReferenceRelation] = []
    for relation_id in DECLARED_RELATION_IDS:
        holds, pinned = _RELATIONS[relation_id](folded_fragment, folded_reference)
        relations.append(
            ReferenceRelation(
                relation_id=relation_id,
                holds=holds,
                pinned_reference_characters=pinned if holds else 0,
            )
        )

    holding = tuple(r.relation_id for r in relations if r.holds)
    max_pinned = max((r.pinned_reference_characters for r in relations if r.holds), default=0)

    return ReferenceComparison(
        fragment=fragment,
        fragment_folded=folded_fragment,
        fragment_length=len(fragment),
        fragment_alphanumeric_characters=count_alphanumeric(fragment),
        fragment_mask_characters=count_masked(fragment),
        fragment_separator_characters=count_separators(fragment),
        reference_kind=reference_kind,
        reference_value=reference_value,
        reference_folded=folded_reference,
        reference_length=len(reference_value),
        relations=tuple(relations),
        holding_relation_ids=holding,
        max_pinned_reference_characters=max_pinned,
    )


def relation_holds_admissibly(
    folded_fragment: str,
    folded_reference: str,
    *,
    accepted_relation_ids: frozenset[str],
    min_pinned_reference_characters: int,
) -> bool:
    """Does *some* accepted relation hold and clear the floor? A boolean shadow.

    Exactly equivalent to::

        strongest_admissible_relation(
            compare(fragment, reference, kind), ...
        ) is not None

    and deliberately not a second statement of the relations: it walks the same
    ``_RELATIONS`` table in the same declared order. What it skips is building
    :class:`ReferenceComparison` and its seven
    :class:`ReferenceRelation` members, which is the entire cost when the
    question is only "yes or no".

    That cost matters in exactly one place. :mod:`finrecon.evidence.closure`
    tests *every* narration substring against *every* candidate reference, and
    at that fan-out the model construction dominates by two orders of
    magnitude -- measured at 640 ms per case against 7 ms. The equivalence is
    asserted over the whole DEV and v4-pilot corpus in
    ``tests/test_evidence_closure.py`` rather than argued for here.

    **Both arguments must already be folded** (:func:`fold`). Folding is the
    caller's job because the closure folds each fragment once and reuses it
    across every reference, and a signature that folded internally would make
    that impossible to express.
    """
    for relation_id in DECLARED_RELATION_IDS:
        if relation_id not in accepted_relation_ids:
            continue
        holds, pinned = _RELATIONS[relation_id](folded_fragment, folded_reference)
        if holds and pinned >= min_pinned_reference_characters:
            return True
    return False


def strongest_admissible_relation(
    comparison: ReferenceComparison,
    *,
    accepted_relation_ids: frozenset[str],
    min_pinned_reference_characters: int,
) -> ReferenceRelation | None:
    """The highest-pinning relation that holds, is accepted, and clears the floor.

    Pure selection over already-computed facts: it introduces no new
    comparison and no notion of correctness. ``None`` means this fragment
    says nothing admissible about this reference.
    """
    admissible = [
        relation
        for relation in comparison.relations
        if relation.holds
        and relation.relation_id in accepted_relation_ids
        and relation.pinned_reference_characters >= min_pinned_reference_characters
    ]
    if not admissible:
        return None
    # Deterministic: most characters pinned, ties broken by declared order.
    order = {rid: i for i, rid in enumerate(DECLARED_RELATION_IDS)}
    admissible.sort(key=lambda r: (-r.pinned_reference_characters, order[r.relation_id]))
    return admissible[0]


__all__ = [
    "DECLARED_RELATION_IDS",
    "MASK_CHARACTERS",
    "REFERENCE_KINDS",
    "RELATION_CONTAINS",
    "RELATION_DESCRIPTIONS",
    "RELATION_EXACT",
    "RELATION_MASK",
    "RELATION_MULTISET",
    "RELATION_PREFIX",
    "RELATION_SEPARATOR",
    "RELATION_SUFFIX",
    "SEPARATOR_CHARACTERS",
    "ReferenceComparison",
    "ReferenceKind",
    "ReferenceRelation",
    "compare",
    "count_alphanumeric",
    "fold",
    "relation_holds_admissibly",
    "strip_separators",
    "strongest_admissible_relation",
]
