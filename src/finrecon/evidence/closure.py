"""The deterministic reference-evidence closure of one immutable case snapshot.

Why this module exists
----------------------

The Stage-3 validator's original rule resolves a case when *some one* fragment
the agent tested reaches exactly one candidate. Allowing several fragments to
be *combined* is what benchmark v4 asks for, and the obvious way to do it is
unsafe:

.. code-block:: text

    f1 -> {A, B}        the agent tests f1 and f2
    f2 -> {A, C}        intersection = {A}, resolve A
    f3 -> {B, C}        ...but f3 is also in the narration, and says otherwise

Under an intersect-what-the-model-tested rule, the same narration proves A
(f1 and f2), B (f1 and f3) or C (f2 and f3) depending on which pair the model
happened to look at. The model would be choosing the winner by choosing where
not to look -- the omission channel DESIGN.md 4.1 and 11 exist to close,
reappearing one level up.

So the evidence set a conjunction is proved over is not the agent's selection.
It is the **closure**: every fragment of the immutable narration that stands in
a declared relation to any candidate's reference, whether the agent asked about
it or not. Under a closed evidence set, looking away cannot help, because
nothing can be left out.

What "closure" means precisely
------------------------------

Every contiguous substring of the snapshot's narration whose length is at least
the declared evidence floor is tested, against every reference of every
candidate in the snapshot, under the declared relations of
:mod:`finrecon.evidence.reference`. There is no length cap and no sampling: a
cap would be an omission the model could not exploit but the *narration* could,
and the whole point is that omission is impossible.

The one bound is a declared refusal, not a truncation.
:data:`MAX_NARRATION_LENGTH` is the longest narration this module will
enumerate; above it, :attr:`ReferenceClosure.is_complete` is ``False`` and the
caller must decline to identify anything. Failing closed on an input this
module cannot fully search is the only safe reading of a bound.

Atoms, and why they are grouped by reach set
--------------------------------------------

Overlapping slices of one reference must not become independent corroborating
evidence -- ``"ABC123"``, ``"BC12"`` and ``"C123"`` are one clue read three
ways, not three clues. Two things make that impossible here.

First, the rule is a set **intersection**, not a vote. Intersecting the same
reach set twice is the same as intersecting it once, so there is nothing for a
duplicate or an overlap to inflate. Duplicate-invariance and
overlap-invariance are properties of the operation rather than checks bolted
onto it.

Second, fragments are still grouped into :class:`EvidenceAtom` by their reach
set, so an auditor reading a resolution sees one atom per distinct claim rather
than four hundred substrings. The representative is the longest fragment in the
group, ties broken lexicographically -- deterministic, and the longest fragment
is the one a human would have quoted.

Atoms whose reach is the *whole* candidate set are excluded from the
intersection, and that is not a convenience. A fragment reaching every
candidate says nothing about which is right: canonical settlement IDs share a
prefix, so ``"SETL"`` stands in a declared relation to all of them at once
(``notes/STAGE3-FINDINGS.md`` section 4). Intersecting with the full set is a
no-op anyway; excluding it only keeps the atom count honest. Fragments reaching
*no* candidate are likewise excluded -- an empty reach is silence, not
contradiction, and admitting it would empty every intersection in existence.

This module decides nothing
---------------------------

It returns evidence. Whether that evidence identifies a candidate is
:mod:`finrecon.decide.validator`'s question, and whether money moves is
:mod:`finrecon.decide.policy`'s. Nothing here reads a tool output, an agent's
prose, a confidence, or hidden ground truth.
"""

from __future__ import annotations

from finrecon.candidates.snapshot import CaseSnapshot, SettlementFacts
from finrecon.evidence.reference import (
    MASK_CHARACTERS,
    REFERENCE_KINDS,
    SEPARATOR_CHARACTERS,
    ReferenceKind,
    compare,
    fold,
    relation_holds_admissibly,
    strongest_admissible_relation,
)
from finrecon.normalize.provenance import FrozenModel

MAX_NARRATION_LENGTH = 240
"""Longest narration this module will enumerate exhaustively.

A declared refusal rather than a cap. Above it the closure reports
``is_complete=False`` and the decision layer identifies nothing, because a
partially searched narration cannot support a claim about what the narration
does *not* contain.

240 characters is roughly three times the longest narration in any committed
split (70) and past what bank statement exports carry, and it bounds the
substring enumeration at about 29,000 fragments -- a tenth of a second.
"""


class AtomMatch(FrozenModel):
    """The strongest declared relation by which one atom reaches one candidate."""

    candidate_id: str
    settlement_id: str
    reference_kind: str
    reference_value: str
    relation_id: str
    pinned_reference_characters: int


class EvidenceAtom(FrozenModel):
    """One distinct claim the narration makes about which candidates fit.

    An atom is a *class* of narration fragments sharing a reach set, not a
    single string. That is what makes overlapping slices of one reference a
    single piece of evidence rather than several.
    """

    atom_id: str
    """``atom:<n>``, assigned in the deterministic order atoms are emitted."""
    fragment: str
    """The representative: longest member, ties broken lexicographically."""
    fragment_length: int
    occurrences: tuple[int, ...]
    """Every start offset of the representative in the raw narration."""
    span: tuple[int, int]
    """Smallest ``(start, end)`` covering every occurrence of every member.

    The provenance a resolution is reported against, and what makes "these two
    atoms came from different parts of the line" a mechanical statement rather
    than an impression.
    """
    reach: tuple[str, ...]
    """Candidate IDs this claim is consistent with, sorted. Never empty."""
    member_fragment_count: int
    """How many distinct narration substrings collapsed into this one claim."""
    matches: tuple[AtomMatch, ...]
    """How the representative reaches each candidate it reaches. Audit only."""


class ReferenceClosure(FrozenModel):
    """Every distinct reference claim the immutable narration supports."""

    narration_length: int
    candidate_count: int
    is_complete: bool
    """False only when the narration exceeded :data:`MAX_NARRATION_LENGTH`."""
    incomplete_reason: str | None
    fragments_enumerated: int
    fragments_reaching_a_candidate: int
    atoms: tuple[EvidenceAtom, ...]
    """All atoms, informative or not, in deterministic order."""
    informative_atom_ids: tuple[str, ...]
    """Atoms whose reach is a strict subset of the candidate set."""
    floor_applied: int

    def informative_atoms(self) -> tuple[EvidenceAtom, ...]:
        wanted = set(self.informative_atom_ids)
        return tuple(atom for atom in self.atoms if atom.atom_id in wanted)

    def atom_for_reach(self, reach: frozenset[str]) -> EvidenceAtom | None:
        """The atom holding this exact reach set, if the narration supports one.

        How a fragment the agent tested is mapped onto the closure: compute
        that one fragment's reach with the same predicate, then ask which
        claim it is an instance of. Keyed on the reach set rather than on the
        string, because the string is one of many spellings of the same claim
        and the closure stores only the representative spelling.
        """
        key = tuple(sorted(reach))
        for atom in self.atoms:
            if atom.reach == key:
                return atom
        return None

    def intersection(self) -> frozenset[str]:
        """Candidates consistent with **every** informative claim.

        Empty when the claims contradict each other. Empty *also* when there
        are no informative claims at all, which the caller must distinguish --
        "the evidence rules everything out" and "there is no evidence" are
        different facts, and :meth:`has_informative_evidence` separates them.
        """
        atoms = self.informative_atoms()
        if not atoms:
            return frozenset()
        result: frozenset[str] = frozenset(atoms[0].reach)
        for atom in atoms[1:]:
            result = result & frozenset(atom.reach)
        return result

    def union(self) -> frozenset[str]:
        """Every candidate some informative claim is consistent with."""
        result: set[str] = set()
        for atom in self.informative_atoms():
            result.update(atom.reach)
        return frozenset(result)

    def has_informative_evidence(self) -> bool:
        return bool(self.informative_atom_ids)

    def independent_span_count(self) -> int:
        """How many disjoint stretches of the narration the informative atoms cover.

        Two atoms cut from the same run of characters are one place in the line
        read twice; two atoms from opposite ends are two places. Reported so a
        conjunctive resolution can be audited for whether its evidence actually
        came from more than one field -- without that ever becoming a threshold
        the rule depends on, because a threshold on evidence *count* is exactly
        the vote the intersection is designed not to be.
        """
        spans = sorted(atom.span for atom in self.informative_atoms())
        if not spans:
            return 0
        count = 1
        current_end = spans[0][1]
        for start, end in spans[1:]:
            if start >= current_end:
                count += 1
                current_end = end
            else:
                current_end = max(current_end, end)
        return count


def references_of(facts: SettlementFacts) -> tuple[tuple[ReferenceKind, str], ...]:
    """``(kind, value)`` for each reference, in the fixed declared order.

    Mirrors :func:`finrecon.decide.validator._references_of` exactly, including
    the rule that a settlement carrying no UTR contributes no ``utr`` entry
    rather than an empty one -- an empty string would be a reference that could
    accidentally satisfy a relation.
    """
    values: dict[str, str | None] = {"utr": facts.utr, "settlement_id": facts.settlement_id}
    return tuple(
        (kind, values[kind]) for kind in REFERENCE_KINDS if values[kind] is not None
    )  # type: ignore[misc]


def occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    found: list[int] = []
    start = haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return tuple(found)


def all_fragments(narration: str, floor: int) -> tuple[str, ...]:
    """Every distinct contiguous substring of length at least ``floor``.

    Exhaustive by construction and deduplicated, so a fragment occurring twice
    in a narration is one fragment with two offsets rather than two fragments.
    """
    seen: set[str] = set()
    length = len(narration)
    for start in range(length):
        for end in range(start + floor, length + 1):
            seen.add(narration[start:end])
    return tuple(sorted(seen))


class _PreparedReference:
    """One candidate reference, folded once, with its prefilter alphabet."""

    __slots__ = ("settlement_id", "kind", "value", "folded", "allowed")

    def __init__(self, settlement_id: str, kind: str, value: str) -> None:
        self.settlement_id = settlement_id
        self.kind = kind
        self.value = value
        self.folded = fold(value)
        self.allowed = frozenset(self.folded) | MASK_CHARACTERS | SEPARATOR_CHARACTERS


def _prepare(snapshot: CaseSnapshot) -> list[tuple[str, tuple[_PreparedReference, ...]]]:
    facts_by_id = {f.settlement_id: f for f in snapshot.base_evidence.settlement_facts}
    prepared: list[tuple[str, tuple[_PreparedReference, ...]]] = []
    for candidate in snapshot.candidates:
        references: list[_PreparedReference] = []
        for settlement_id in candidate.settlement_ids:
            facts = facts_by_id.get(settlement_id)
            if facts is None:
                continue
            for kind, value in references_of(facts):
                references.append(_PreparedReference(settlement_id, kind, value))
        prepared.append((candidate.candidate_id, tuple(references)))
    return prepared


def fragment_reach(
    snapshot: CaseSnapshot,
    fragment: str,
    *,
    accepted_relation_ids: frozenset[str],
    min_pinned_reference_characters: int,
) -> frozenset[str]:
    """The candidates one fragment reaches, over the complete snapshot.

    The single-fragment case of the closure, exposed so a fragment the agent
    tested can be located inside it without rebuilding the whole thing.
    """
    return _reach(
        _prepare(snapshot),
        fold(fragment),
        accepted_relation_ids=accepted_relation_ids,
        floor=min_pinned_reference_characters,
    )


def _reach(
    prepared: list[tuple[str, tuple[_PreparedReference, ...]]],
    folded_fragment: str,
    *,
    accepted_relation_ids: frozenset[str],
    floor: int,
) -> frozenset[str]:
    """Which candidates this folded fragment stands in a declared relation to.

    The character-set test is a *conservative prefilter*, not a rule: every
    relation except ``contains_reference`` requires the fragment's characters
    to be drawn from the reference plus the declared mask and separator
    alphabets, and ``contains_reference`` requires the whole reference to sit
    inside the fragment. A fragment failing both cannot satisfy any relation,
    so skipping it changes no result -- asserted against an unfiltered pass
    over the whole DEV and v4-pilot corpus in
    ``tests/test_evidence_closure.py`` rather than argued for here.
    """
    fragment_characters = frozenset(folded_fragment)
    reached: set[str] = set()
    for candidate_id, references in prepared:
        for reference in references:
            if (
                not fragment_characters <= reference.allowed
                and reference.folded not in folded_fragment
            ):
                continue
            if relation_holds_admissibly(
                folded_fragment,
                reference.folded,
                accepted_relation_ids=accepted_relation_ids,
                min_pinned_reference_characters=floor,
            ):
                reached.add(candidate_id)
                break
    return frozenset(reached)


def build_reference_closure(
    snapshot: CaseSnapshot,
    *,
    accepted_relation_ids: frozenset[str],
    min_pinned_reference_characters: int,
) -> ReferenceClosure:
    """Enumerate every reference claim the snapshot's narration supports.

    Pure in the snapshot and the two declared policy parameters. Reads no tool
    output, so an agent cannot change what this returns -- which is the entire
    reason it exists.
    """
    narration = snapshot.base_evidence.bank_record.narration
    candidate_count = len(snapshot.candidates)
    floor = min_pinned_reference_characters

    if len(narration) > MAX_NARRATION_LENGTH:
        return ReferenceClosure(
            narration_length=len(narration),
            candidate_count=candidate_count,
            is_complete=False,
            incomplete_reason=(
                f"narration is {len(narration)} characters, above the declared "
                f"exhaustive-enumeration bound of {MAX_NARRATION_LENGTH}; the closure "
                "would be partial, and a partial closure cannot support a claim about "
                "what the narration does not contain"
            ),
            fragments_enumerated=0,
            fragments_reaching_a_candidate=0,
            atoms=(),
            informative_atom_ids=(),
            floor_applied=floor,
        )

    prepared = _prepare(snapshot)
    fragments = all_fragments(narration, floor)

    reach_by_fragment: dict[str, frozenset[str]] = {}
    for fragment in fragments:
        reached = _reach(
            prepared,
            fold(fragment),
            accepted_relation_ids=accepted_relation_ids,
            floor=floor,
        )
        if reached:
            reach_by_fragment[fragment] = reached

    # Group by reach set, ordered by the sorted reach tuple, so atom identities
    # depend on the narration and the candidate set alone -- never on dict
    # iteration order or on which fragment happened to be examined first.
    grouped: dict[tuple[str, ...], list[str]] = {}
    for fragment, reach in reach_by_fragment.items():
        grouped.setdefault(tuple(sorted(reach)), []).append(fragment)

    atoms: list[EvidenceAtom] = []
    informative: list[str] = []
    for index, reach_key in enumerate(sorted(grouped)):
        members = sorted(grouped[reach_key])
        # Longest member, ties broken lexicographically smallest.
        representative = min(members, key=lambda value: (-len(value), value))
        starts: list[int] = []
        ends: list[int] = []
        for member in members:
            for offset in occurrences(narration, member):
                starts.append(offset)
                ends.append(offset + len(member))
        atom_id = f"atom:{index}"
        atoms.append(
            EvidenceAtom(
                atom_id=atom_id,
                fragment=representative,
                fragment_length=len(representative),
                occurrences=occurrences(narration, representative),
                span=(min(starts), max(ends)),
                reach=reach_key,
                member_fragment_count=len(members),
                matches=_matches_for(
                    representative,
                    reach_key,
                    prepared,
                    accepted_relation_ids=accepted_relation_ids,
                    floor=floor,
                ),
            )
        )
        if len(reach_key) < candidate_count:
            informative.append(atom_id)

    return ReferenceClosure(
        narration_length=len(narration),
        candidate_count=candidate_count,
        is_complete=True,
        incomplete_reason=None,
        fragments_enumerated=len(fragments),
        fragments_reaching_a_candidate=len(reach_by_fragment),
        atoms=tuple(atoms),
        informative_atom_ids=tuple(informative),
        floor_applied=floor,
    )


def _matches_for(
    fragment: str,
    reach: tuple[str, ...],
    prepared: list[tuple[str, tuple[_PreparedReference, ...]]],
    *,
    accepted_relation_ids: frozenset[str],
    floor: int,
) -> tuple[AtomMatch, ...]:
    """The strongest relation by which ``fragment`` reaches each candidate.

    Computed through the full :func:`finrecon.evidence.reference.compare` path
    rather than the boolean shadow, because this is the audit record and it has
    to name a relation and a pinned-character count. It runs once per atom
    rather than once per fragment, so the cost the closure avoids in bulk is
    paid exactly where the detail is needed.
    """
    wanted = set(reach)
    matches: list[AtomMatch] = []
    for candidate_id, references in prepared:
        if candidate_id not in wanted:
            continue
        best: AtomMatch | None = None
        for reference in references:
            relation = strongest_admissible_relation(
                compare(fragment, reference.value, reference.kind),  # type: ignore[arg-type]
                accepted_relation_ids=accepted_relation_ids,
                min_pinned_reference_characters=floor,
            )
            if relation is None:
                continue
            contender = AtomMatch(
                candidate_id=candidate_id,
                settlement_id=reference.settlement_id,
                reference_kind=reference.kind,
                reference_value=reference.value,
                relation_id=relation.relation_id,
                pinned_reference_characters=relation.pinned_reference_characters,
            )
            if (
                best is None
                or contender.pinned_reference_characters > best.pinned_reference_characters
            ):
                best = contender
        if best is not None:
            matches.append(best)
    return tuple(matches)


__all__ = [
    "MAX_NARRATION_LENGTH",
    "AtomMatch",
    "EvidenceAtom",
    "ReferenceClosure",
    "all_fragments",
    "build_reference_closure",
    "fragment_reach",
    "occurrences",
    "references_of",
]
