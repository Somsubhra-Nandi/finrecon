"""Can anything that is not evidence predict the v4 pilot's answers?

A benchmark can be perfectly well formed and still be trivially solvable. If
the true counterparty is always the lowest settlement ID, or resolvable cases
always carry more candidates than ambiguous ones, then a rule stated purely
over visible *structure* scores well while reading no evidence at all -- and
every number the benchmark produces afterwards is about that rule instead of
about reconciliation.

So each test below states a shortcut and requires it to fail.

Where the line falls
--------------------

Narration *content* is evidence and is supposed to be informative: a line
carrying no reference at all should look different from one carrying a
truncated reference, because it *is* different, and escalating the first is
correct behaviour rather than a shortcut. What must not be informative is
anything the generator chose for reasons unrelated to the case -- identifier
ordinals, build order, candidate-set size, amounts, positions.

One correlation the pilot does carry is asserted here rather than hidden:
three narration shapes appear only in resolvable cases. See
``test_most_cases_live_in_a_narration_shape_that_carries_both_outcomes`` for
why that is a limitation to record and not a leak that scores anything.
"""

from __future__ import annotations

from collections import Counter

import pytest

from benchmark.baselines.report import leakage_audit, narration_shape


@pytest.fixture(scope="module")
def audit(v4_stage2, v4_truth, benchmark_dir):
    batch, _store = v4_stage2
    return leakage_audit(batch, v4_truth, benchmark_dir, "v4-pilot")


class TestCandidateOrderCarriesNoSignal:
    def test_the_truth_is_not_usually_the_lowest_settlement_id(self, audit):
        """"Pick the lowest ID" must score at chance, not above it.

        Chance is not one over the candidate count here, because the pilot
        mixes three-, four- and five-candidate cases. It is the mean of the
        reciprocals, which the test computes rather than hard-codes so that
        changing the candidate-count mix cannot silently change what "chance"
        means.
        """
        expected = _expected_extreme_share(audit)
        observed = audit["truth_is_lowest_settlement_id"] / audit["resolvable_cases_audited"]
        assert abs(observed - expected) < 0.12, (observed, expected)

    def test_the_truth_is_not_usually_the_highest_settlement_id(self, audit):
        expected = _expected_extreme_share(audit)
        observed = audit["truth_is_highest_settlement_id"] / audit["resolvable_cases_audited"]
        assert abs(observed - expected) < 0.12, (observed, expected)

    def test_every_candidate_position_holds_some_truths(self, audit):
        """No position is empty, and none holds a majority."""
        share = audit["truth_position_share"]
        assert set(share) >= {"0", "1", "2"}
        assert max(float(value) for value in share.values()) < 0.5

    def test_within_three_candidate_cases_the_truth_is_spread(self, audit):
        by_count = audit["truth_position_by_candidate_count"]
        three = by_count["3"]
        assert set(three) == {0, 1, 2}
        total = sum(three.values())
        for position, count in three.items():
            assert 0.15 < count / total < 0.55, (position, count, total)


class TestStructuralFactsDoNotDetermineTheOutcome:
    def test_candidate_count_does_not_determine_whether_a_case_is_resolvable(self, audit):
        for bucket, outcomes in audit["required_outcome_by_candidate_count"].items():
            assert set(outcomes) == {"AUTO_RESOLVABLE", "ESCALATE"}, bucket

    def test_narration_length_does_not_determine_whether_a_case_is_resolvable(self, audit):
        mixed = [
            bucket
            for bucket, outcomes in audit["required_outcome_by_narration_length"].items()
            if len(outcomes) == 2
        ]
        assert mixed, audit["required_outcome_by_narration_length"]

    def test_value_at_stake_ranges_overlap_between_resolvable_and_ambiguous(self, v4_truth):
        """Amount must say nothing about the answer. It is drawn from one band."""
        resolvable = [e.value_at_stake_paise for e in v4_truth.values() if e.is_uniquely_resolvable]
        ambiguous = [
            e.value_at_stake_paise for e in v4_truth.values() if not e.is_uniquely_resolvable
        ]
        assert min(resolvable) < max(ambiguous)
        assert min(ambiguous) < max(resolvable)

    def test_case_identifier_order_does_not_block_archetypes_together(self, audit):
        """A shuffled plan, verified as shuffled rather than assumed to be."""
        assert audit["longest_same_archetype_run_in_case_id_order"] <= 3

    def test_case_identifier_order_does_not_block_outcomes_together(self, audit, v4_truth):
        """A run no longer than chance would produce for this class balance.

        Bounded against the analytic expectation for the majority class rather
        than a hand-picked constant, so a future rebalance of the pilot cannot
        make the bound accidentally generous.
        """
        resolvable = sum(1 for e in v4_truth.values() if e.is_uniquely_resolvable)
        share = resolvable / len(v4_truth)
        import math

        expected = math.log(len(v4_truth)) / math.log(1 / share)
        assert audit["longest_same_outcome_run_in_case_id_order"] <= expected + 2


class TestNoBenchmarkMetadataIsVisible:
    def test_no_family_or_archetype_label_appears_in_the_visible_files(self, audit):
        assert audit["benchmark_labels_found_in_visible_files"] == []

    def test_settlement_identifiers_are_a_single_uninterrupted_sequence(self, v4_stage2):
        """Identifier ordinals must encode build order and nothing else.

        A gap in the sequence would mean some case consumed identifiers and was
        then discarded -- which is exactly what the generator's bounded redraw
        would do if it materialized before verifying instead of after. The
        absence of gaps is the observable consequence of that ordering.
        """
        batch, _store = v4_stage2
        ordinals = sorted(
            int(settlement.settlement_id.rsplit("_", 1)[1])
            for settlement in batch.batch.settlements
        )
        assert ordinals == list(range(1, len(ordinals) + 1))

    def test_the_true_settlement_ordinal_is_not_a_fixed_offset_within_its_case(
        self, v4_stage2, v4_truth
    ):
        """"The truth is the Nth settlement built in its case" must not hold."""
        batch, _store = v4_stage2
        offsets: Counter = Counter()
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.correct_relationship is None:
                continue
            ordinals = sorted(
                int(sid.rsplit("_", 1)[1])
                for candidate in snapshot.candidates
                for sid in candidate.settlement_ids
            )
            truth_ordinal = int(entry.expected_settlement_ids[0].rsplit("_", 1)[1])
            offsets[ordinals.index(truth_ordinal)] += 1
        assert len(offsets) >= 3
        assert max(offsets.values()) / sum(offsets.values()) < 0.5


class TestKnownCorrelationsAreRecordedNotHidden:
    def test_most_cases_live_in_a_narration_shape_that_carries_both_outcomes(self, audit):
        """The pilot's one real correlation, bounded and stated.

        Three narration shapes -- the refund-amount field, the long truncated
        reference, and the reordered rendering -- appear only in resolvable
        cases, because only resolvable archetypes were built with them. So
        "this line has an RFND field, therefore this case has an answer" is a
        true rule over this pilot.

        It is recorded rather than removed for two reasons. It scores nothing:
        every metric requires naming a settlement, and a shape label names
        none. And removing it means building ambiguous variants of those three
        archetypes, which is full-v4 work rather than pilot work -- it is
        listed as such in ``benchmark/V4-PILOT.md``.

        What the test does enforce is that the correlation stays a minority
        property, so the pilot cannot drift into being mostly shape-readable.
        """
        pure = audit["cases_in_outcome_pure_narration_shapes"]
        total = sum(
            sum(counter.values())
            for counter in audit["required_outcome_by_narration_shape"].values()
        )
        assert pure / total < 0.5, audit["required_outcome_by_narration_shape"]

    def test_the_two_conflict_archetypes_are_indistinguishable_by_shape(
        self, v4_stage2, v4_truth
    ):
        """The sharpest pair in the pilot: same narration shape, opposite answers.

        ``conflict_context_resolves`` resolves and ``conflict_stale_reference``
        escalates, and both render a reference field, a batch marker and a
        value date. Nothing but the evidence separates them.
        """
        batch, _store = v4_stage2
        shapes: dict[str, set[str]] = {}
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype not in (
                "conflict_context_resolves",
                "conflict_stale_reference",
            ):
                continue
            shape = narration_shape(snapshot.base_evidence.bank_record.narration)
            shapes.setdefault(shape, set()).add(entry.required_outcome)
        assert len(shapes) == 1
        assert next(iter(shapes.values())) == {"AUTO_RESOLVABLE", "ESCALATE"}

    def test_the_conjunction_and_incomplete_conjunction_archetypes_share_a_shape(
        self, v4_stage2, v4_truth
    ):
        """A split-reference line resolves in one archetype and escalates in another."""
        batch, _store = v4_stage2
        outcomes: set[str] = set()
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype not in (
                "conjunction_pair",
                "conjunction_wide",
                "ambiguity_conjunction_incomplete",
            ):
                continue
            assert (
                narration_shape(snapshot.base_evidence.bank_record.narration)
                == "reference_split_across_fields"
            ), snapshot.case_id
            outcomes.add(entry.required_outcome)
        assert outcomes == {"AUTO_RESOLVABLE", "ESCALATE"}


def _expected_extreme_share(audit: dict) -> float:
    """Chance rate of the truth landing on one declared end of the ID order."""
    total = 0
    weighted = 0.0
    for bucket, positions in audit["truth_position_by_candidate_count"].items():
        count = sum(positions.values())
        total += count
        weighted += count / int(bucket)
    return weighted / total
