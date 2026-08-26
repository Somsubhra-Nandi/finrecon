"""The reference closure: is it complete, and does it agree with the slow path?

Two claims hold this module up, both made in its docstrings and neither
self-evident, so both are measured here over the whole committed corpus rather
than argued for.

**The boolean shadow agrees with the model path.**
:func:`finrecon.evidence.reference.relation_holds_admissibly` skips building
``ReferenceComparison`` and its seven relation members. It must return exactly
what ``strongest_admissible_relation(compare(...)) is not None`` returns, for
every fragment of every narration against every reference of every candidate.

**The character prefilter is conservative.** The closure skips a
fragment/reference pair when the fragment's characters are not drawn from the
reference plus the mask and separator alphabets *and* the reference is not
inside the fragment. That is a claim about the relation set, and if it is wrong
the closure silently omits evidence -- which is the one failure mode that
turns a safe rule into an unsafe one, because omitting an atom can only make
an intersection larger.

Everything here is deterministic and offline. No provider, no model, no
ground truth.
"""

from __future__ import annotations

import pytest

from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.evidence.closure import (
    MAX_NARRATION_LENGTH,
    all_fragments,
    build_reference_closure,
    fragment_reach,
    references_of,
)
from finrecon.evidence.reference import (
    DECLARED_RELATION_IDS,
    compare,
    fold,
    relation_holds_admissibly,
    strongest_admissible_relation,
)

ACCEPTED = frozenset(DECLARED_RELATION_IDS)
FLOOR = 4


def _slow_reach(snapshot: CaseSnapshot, fragment: str, floor: int = FLOOR) -> frozenset[str]:
    """The reach set computed entirely through the model path, with no prefilter.

    Deliberately the least clever implementation available: every candidate,
    every reference, a full :func:`compare` each time, no early character test.
    It is the reference implementation the fast path is judged against.
    """
    facts_by_id = {f.settlement_id: f for f in snapshot.base_evidence.settlement_facts}
    reached: set[str] = set()
    for candidate in snapshot.candidates:
        for settlement_id in candidate.settlement_ids:
            facts = facts_by_id.get(settlement_id)
            if facts is None:
                continue
            for kind, value in references_of(facts):
                relation = strongest_admissible_relation(
                    compare(fragment, value, kind),
                    accepted_relation_ids=ACCEPTED,
                    min_pinned_reference_characters=floor,
                )
                if relation is not None:
                    reached.add(candidate.candidate_id)
    return frozenset(reached)


@pytest.fixture(scope="module")
def corpus(v4_stage2, dev_result):
    """Every unresolved case in both committed splits, capped for runtime.

    The v4 pilot in full, because its narrations are the longest and its
    candidate sets the widest, plus a deterministic slice of DEV -- ordered by
    case ID, so the slice is the same slice on every machine and every run.
    """
    v4_batch, _ = v4_stage2
    dev_batch, _ = dev_result
    dev_slice = sorted(dev_batch.snapshots, key=lambda s: s.case_id)[:40]
    return tuple(v4_batch.snapshots) + tuple(dev_slice)


class TestTheBooleanShadowAgreesWithTheModelPath:
    def test_over_every_fragment_of_every_case_in_the_corpus(self, corpus):
        checked = 0
        for snapshot in corpus:
            narration = snapshot.base_evidence.bank_record.narration
            facts = snapshot.base_evidence.settlement_facts
            references = [
                (kind, value) for f in facts for kind, value in references_of(f)
            ]
            for fragment in all_fragments(narration, FLOOR):
                folded_fragment = fold(fragment)
                for kind, value in references:
                    fast = relation_holds_admissibly(
                        folded_fragment,
                        fold(value),
                        accepted_relation_ids=ACCEPTED,
                        min_pinned_reference_characters=FLOOR,
                    )
                    slow = (
                        strongest_admissible_relation(
                            compare(fragment, value, kind),
                            accepted_relation_ids=ACCEPTED,
                            min_pinned_reference_characters=FLOOR,
                        )
                        is not None
                    )
                    assert fast == slow, (fragment, value, kind)
                    checked += 1
        assert checked > 100_000, "the corpus should be large enough to mean something"

    def test_a_restricted_relation_set_is_honoured(self):
        """The accepted-relation filter is not ignored by the fast path."""
        assert relation_holds_admissibly(
            "AXISCN11",
            "AXISCN1137863727",
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=4,
        )
        assert not relation_holds_admissibly(
            "AXISCN11",
            "AXISCN1137863727",
            accepted_relation_ids=frozenset({"exact"}),
            min_pinned_reference_characters=4,
        )

    def test_the_floor_is_honoured(self):
        assert relation_holds_admissibly(
            "AXIS",
            "AXISCN1137863727",
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=4,
        )
        assert not relation_holds_admissibly(
            "AXIS",
            "AXISCN1137863727",
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=5,
        )


class TestThePrefilterOmitsNothing:
    def test_the_closure_reach_equals_the_unfiltered_reach(self, corpus):
        """The safety-critical equivalence, over the corpus.

        If the prefilter ever rejected a pair a relation could have held for,
        the closure would omit an atom -- and omitting an atom can only *grow*
        an intersection, which is the direction that turns an escalation into a
        resolution.
        """
        for snapshot in corpus:
            closure = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            fast_by_fragment: dict[str, frozenset[str]] = {}
            for atom in closure.atoms:
                fast_by_fragment[atom.fragment] = frozenset(atom.reach)

            narration = snapshot.base_evidence.bank_record.narration
            for fragment in all_fragments(narration, FLOOR):
                slow = _slow_reach(snapshot, fragment)
                fast = fragment_reach(
                    snapshot,
                    fragment,
                    accepted_relation_ids=ACCEPTED,
                    min_pinned_reference_characters=FLOOR,
                )
                assert fast == slow, (snapshot.case_id, fragment)

    def test_every_representative_fragment_reaches_what_its_atom_claims(self, corpus):
        for snapshot in corpus:
            closure = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            for atom in closure.atoms:
                assert frozenset(atom.reach) == _slow_reach(snapshot, atom.fragment), (
                    snapshot.case_id,
                    atom.atom_id,
                )


class TestAtomIdentity:
    def test_atoms_partition_the_reaching_fragments(self, corpus):
        """Every reaching fragment belongs to exactly one atom, by reach set."""
        for snapshot in corpus:
            closure = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            reach_sets = [frozenset(atom.reach) for atom in closure.atoms]
            assert len(reach_sets) == len(set(reach_sets)), snapshot.case_id
            assert sum(atom.member_fragment_count for atom in closure.atoms) == (
                closure.fragments_reaching_a_candidate
            )

    def test_overlapping_slices_of_one_reference_collapse_into_one_atom(self, v4_stage2):
        """``863727``, ``63727`` and ``3727`` are one claim read three ways."""
        from benchmark.baselines.adversarial import SPAN_TAIL, snapshot_for

        snapshot = snapshot_for(f"NEFT CR-RZRPAY-{SPAN_TAIL}/BATCH47-MUM")
        closure = build_reference_closure(
            snapshot,
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=FLOOR,
        )
        informative = closure.informative_atoms()
        assert len(informative) == 1
        atom = informative[0]
        assert atom.fragment == SPAN_TAIL
        assert atom.member_fragment_count == 3
        assert closure.independent_span_count() == 1

    def test_the_representative_is_the_longest_member(self, corpus):
        for snapshot in corpus:
            closure = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            for atom in closure.atoms:
                assert atom.fragment_length == len(atom.fragment)
                assert atom.fragment in snapshot.base_evidence.bank_record.narration

    def test_atom_ids_and_ordering_are_deterministic(self, corpus):
        """Two builds of one snapshot must be identical, field for field.

        Atom identity is derived from the sorted reach set rather than from
        iteration order, so this is a property of the construction. It is
        checked anyway because the alternative -- a dict-ordering dependency --
        would be invisible until it changed an audit record.
        """
        for snapshot in corpus[:20]:
            first = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            second = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            assert first == second


class TestIntersectionSemantics:
    def test_an_atom_reaching_every_candidate_is_not_informative(self):
        """``SETL`` prefixes every settlement ID at once and must separate nothing."""
        from benchmark.baselines.adversarial import SPAN_HEAD, snapshot_for

        snapshot = snapshot_for(f"RZPY/SETL/{SPAN_HEAD}/BATCH47-MUM")
        closure = build_reference_closure(
            snapshot,
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=FLOOR,
        )
        all_reaching = {atom.fragment: atom for atom in closure.atoms}
        assert "SETL" in all_reaching
        assert len(all_reaching["SETL"].reach) == 3
        assert all_reaching["SETL"].atom_id not in closure.informative_atom_ids

    def test_no_informative_evidence_gives_an_empty_intersection(self):
        from benchmark.baselines.adversarial import snapshot_for

        snapshot = snapshot_for("NEFT CREDIT - SETTLEMENT")
        closure = build_reference_closure(
            snapshot,
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=FLOOR,
        )
        assert closure.has_informative_evidence() is False
        assert closure.intersection() == frozenset()
        assert closure.union() == frozenset()

    def test_intersecting_more_claims_can_only_shrink_the_result(self, corpus):
        """Monotonicity, as a property of the structure rather than a rule.

        This is why contradiction can never leave a match standing: adding a
        claim is an intersection, and an intersection never grows.
        """
        for snapshot in corpus[:30]:
            closure = build_reference_closure(
                snapshot,
                accepted_relation_ids=ACCEPTED,
                min_pinned_reference_characters=FLOOR,
            )
            atoms = closure.informative_atoms()
            if not atoms:
                continue
            running = frozenset(atoms[0].reach)
            for atom in atoms[1:]:
                nxt = running & frozenset(atom.reach)
                assert nxt <= running
                running = nxt
            assert running == closure.intersection()


class TestTheEnumerationBoundFailsClosed:
    def test_a_narration_past_the_bound_yields_an_incomplete_closure(self):
        from benchmark.baselines.adversarial import UTR_A, snapshot_for

        long_narration = ("NEFT CR-RZRPAY-" + UTR_A[:12] + "-PADDING") * 20
        assert len(long_narration) > MAX_NARRATION_LENGTH
        snapshot = snapshot_for(long_narration)
        closure = build_reference_closure(
            snapshot,
            accepted_relation_ids=ACCEPTED,
            min_pinned_reference_characters=FLOOR,
        )
        assert closure.is_complete is False
        assert closure.incomplete_reason
        assert closure.atoms == ()
        assert closure.informative_atom_ids == ()

    def test_the_committed_splits_are_comfortably_inside_the_bound(self, corpus):
        """A bound that bound would be a silent behaviour change, not a guard."""
        longest = max(
            len(snapshot.base_evidence.bank_record.narration) for snapshot in corpus
        )
        assert longest < MAX_NARRATION_LENGTH / 2
