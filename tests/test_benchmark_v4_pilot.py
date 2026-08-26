"""The benchmark v4 pilot: does it contain what it claims to contain?

Every assertion here is about the *pilot*, never about a model. No provider
is constructed anywhere in this file and none could be: the fixtures build
records and run the deterministic core, and the one Stage-3 pass that appears
(in ``test_v4_stage4_integration.py``) is driven by a non-linguistic fake.

Two things this file deliberately does **not** assert. It does not assert that
the pilot is hard for a model -- no model has seen it. And it does not assert
that any arm's coverage is good or bad; that is
``tests/test_v4_baselines.py``'s job, and the numbers there are reported
rather than gated.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from finrecon.benchmark.generator_v4.config import (
    TARGET_ARCHETYPE_COUNTS,
    TOTAL_TARGET_CASES,
    V4_PILOT_SEED,
    V4_PILOT_SPLIT,
)
from finrecon.benchmark.generator_v4.dataset import build_v4_dataset
from finrecon.benchmark.generator_v4.families import (
    ARCHETYPES,
    COMPOSITION_NONE,
    FAMILIES,
    FAMILY_DECOY,
    FAMILY_MULTI_CANDIDATE,
    FAMILY_MULTI_FRAGMENT,
    FAMILY_TRUE_AMBIGUITY,
    archetype_spec,
)
from finrecon.benchmark.generator_v4.invariants import analyse_lexical
from finrecon.benchmark.generator_v4.plan import build_case_plan
from finrecon.benchmark.generator_v4.serialize import (
    dataset_file_names,
    write_v4_dataset,
)


@pytest.fixture(scope="session")
def v4_truth_rows(benchmark_dir):
    """The committed v4 ground truth, as raw dicts.

    Read from disk rather than regenerated: these tests are about the artifact
    that is committed, and a test that regenerated it would pass even if the
    committed files had drifted from the generator.
    """
    path = benchmark_dir / "ground_truth" / f"{V4_PILOT_SPLIT}.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


@pytest.fixture(scope="session")
def v4_regenerated(tmp_path_factory):
    """One clean-room rebuild from the committed seed, written to a temp tree.

    Session-scoped because building the pilot runs every case's exhaustive
    fragment enumeration twice -- once on a scratch factory before the case is
    accepted, once on the real one -- and that is the expensive half of the
    generator by a wide margin.
    """
    bundle = build_v4_dataset(V4_PILOT_SEED, TARGET_ARCHETYPE_COUNTS, V4_PILOT_SPLIT)
    directory = tmp_path_factory.mktemp("v4-regenerated")
    write_v4_dataset(bundle, directory)
    return bundle, directory


class TestDeterministicGeneration:
    def test_the_committed_pilot_is_reproducible_byte_for_byte(
        self, benchmark_dir, v4_regenerated
    ):
        """A clean-room rebuild from the seed must match what is committed.

        This is the pilot's whole reproducibility claim. It also stands in for
        a same-seed-twice test at a quarter of the cost: the committed files
        were produced by an earlier, independent run of the same code path, so
        agreeing with them *is* agreeing with a second run.
        """
        _bundle, directory = v4_regenerated
        for name in dataset_file_names():
            regenerated = (directory / "datasets" / V4_PILOT_SPLIT / name).read_bytes()
            committed = (benchmark_dir / "datasets" / V4_PILOT_SPLIT / name).read_bytes()
            assert regenerated == committed, name
        assert (
            directory / "ground_truth" / f"{V4_PILOT_SPLIT}.jsonl"
        ).read_bytes() == (
            benchmark_dir / "ground_truth" / f"{V4_PILOT_SPLIT}.jsonl"
        ).read_bytes()

    def test_the_case_plan_is_deterministic_in_the_seed(self):
        first = build_case_plan(V4_PILOT_SEED, V4_PILOT_SPLIT, TARGET_ARCHETYPE_COUNTS)
        second = build_case_plan(V4_PILOT_SEED, V4_PILOT_SPLIT, TARGET_ARCHETYPE_COUNTS)
        assert first == second

    def test_a_different_seed_produces_a_different_plan(self):
        """Determinism must not be constancy: the seed has to matter."""
        other = build_case_plan(V4_PILOT_SEED + 1, V4_PILOT_SPLIT, TARGET_ARCHETYPE_COUNTS)
        assert other != build_case_plan(
            V4_PILOT_SEED, V4_PILOT_SPLIT, TARGET_ARCHETYPE_COUNTS
        )

    def test_the_manifest_records_the_pilot_as_unfrozen(self, benchmark_dir):
        manifest = json.loads(
            (benchmark_dir / "manifests" / "v4-pilot.json").read_text(encoding="utf-8")
        )
        assert manifest["frozen"] is False
        assert manifest["case_count"] == TOTAL_TARGET_CASES
        assert manifest["seed"] == V4_PILOT_SEED
        assert "PILOT" in manifest["status"]


class TestCaseComposition:
    def test_the_pilot_has_the_declared_number_of_cases(self, v4_truth_rows):
        assert len(v4_truth_rows) == TOTAL_TARGET_CASES == 64

    def test_every_archetype_is_present_in_its_declared_count(self, v4_truth_rows):
        counts = Counter(row["archetype"] for row in v4_truth_rows)
        assert dict(counts) == TARGET_ARCHETYPE_COUNTS

    def test_no_archetype_dominates_the_pilot(self, v4_truth_rows):
        """Section 11: not ninety per cent of one family."""
        counts = Counter(row["archetype"] for row in v4_truth_rows)
        assert max(counts.values()) / len(v4_truth_rows) < 0.25

    def test_a_meaningful_share_of_cases_is_genuinely_ambiguous(self, v4_truth_rows):
        ambiguous = [row for row in v4_truth_rows if row["correct_relationship"] is None]
        assert len(ambiguous) == 16
        assert 0.15 <= len(ambiguous) / len(v4_truth_rows) <= 0.35

    def test_every_ambiguous_case_declares_no_composition(self, v4_truth_rows):
        for row in v4_truth_rows:
            if row["correct_relationship"] is None:
                assert row["required_composition"] == COMPOSITION_NONE, row["case_id"]
                assert row["required_outcome"] == "ESCALATE"

    def test_every_resolvable_case_declares_a_composition(self, v4_truth_rows):
        for row in v4_truth_rows:
            if row["correct_relationship"] is not None:
                assert row["required_composition"] != COMPOSITION_NONE, row["case_id"]
                assert row["required_outcome"] == "AUTO_RESOLVABLE"

    def test_every_family_tag_is_declared(self, v4_truth_rows):
        for row in v4_truth_rows:
            for family in row["families"]:
                assert family in FAMILIES, row["case_id"]

    def test_every_case_carries_more_than_two_candidates(self, v4_truth_rows):
        """Section 4.C: benchmark v3's T2 was always exactly two."""
        counts = Counter(row["expected_candidate_count"] for row in v4_truth_rows)
        assert min(counts) >= 3
        assert set(counts) == {3, 4, 5}
        assert dict(sorted(counts.items())) == {3: 40, 4: 12, 5: 12}

    def test_every_candidate_count_carries_both_outcomes(self, v4_truth_rows):
        """Candidate-set size must not predict whether a case is resolvable."""
        by_count: dict[int, set[str]] = {}
        for row in v4_truth_rows:
            by_count.setdefault(row["expected_candidate_count"], set()).add(
                row["required_outcome"]
            )
        for count, outcomes in sorted(by_count.items()):
            assert outcomes == {"AUTO_RESOLVABLE", "ESCALATE"}, count


class TestStageTwoBehaviour:
    """What the unmodified deterministic core does with the pilot."""

    def test_stage_two_resolves_nothing_and_refuses_for_ambiguity(self, v4_stage2):
        batch, _store = v4_stage2
        assert len(batch.decisions) == 64
        assert len(batch.snapshots) == 64
        rules = Counter(decision.rule_id for decision in batch.decisions)
        assert dict(rules) == {"unresolved.multiple_derived_candidates": 64}

    def test_every_candidate_came_from_exact_total_blocking(self, v4_stage2):
        """A widened ``date_window_only`` candidate can never auto-resolve.

        So a pilot carrying one would be quietly unsolvable for a reason that
        has nothing to do with its evidence (``notes/STAGE3-FINDINGS.md``
        section 6).
        """
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            for candidate in snapshot.candidates:
                assert candidate.blocking_rule == "exact_total_in_window"

    def test_the_true_settlement_is_in_every_resolvable_case_s_candidate_set(
        self, v4_stage2, v4_truth
    ):
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.correct_relationship is None:
                continue
            present = any(
                tuple(sorted(candidate.settlement_ids)) == entry.expected_settlement_ids
                for candidate in snapshot.candidates
            )
            assert present, snapshot.case_id

    def test_stage_two_builds_exactly_the_candidate_set_the_generator_intended(
        self, v4_stage2, v4_truth
    ):
        """No case's difficulty changed because another case's settlement drifted in."""
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            assert len(snapshot.candidates) == entry.expected_candidate_count, (
                snapshot.case_id
            )

    def test_no_candidate_set_shrank_between_generation_and_stage_two(
        self, v4_stage2, v4_truth_rows
    ):
        by_case = {row["case_id"]: row for row in v4_truth_rows}
        batch, _store = v4_stage2
        settlements_by_bank = {
            row["record_ids"]["bank_records"][0]: set(row["record_ids"]["settlements"])
            for row in by_case.values()
        }
        for snapshot in batch.snapshots:
            built = settlements_by_bank[snapshot.bank_record_id]
            offered = {sid for c in snapshot.candidates for sid in c.settlement_ids}
            assert built == offered, snapshot.case_id


class TestCompositionalStructure:
    """The claim that makes v4 different from v3, checked case by case."""

    def test_no_single_fragment_identifies_the_truth_in_a_multi_fragment_case(
        self, v4_stage2, v4_truth
    ):
        """Section 3, stated as a test over the whole lexical search space.

        Exhaustive: every admissible narration substring, against every
        candidate, under the real declared relations. If any one of them
        reached a candidate alone, the case would be a benchmark v3 T2 case
        wearing a v4 label.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if FAMILY_MULTI_FRAGMENT not in entry.families:
                continue
            analysis = _lexical_analysis(snapshot)
            assert analysis.single_fragment_identifications == frozenset(), (
                snapshot.case_id,
                sorted(analysis.single_fragment_identifications),
            )
            checked += 1
        assert checked == 32

    def test_the_control_family_is_solvable_by_one_fragment(self, v4_stage2, v4_truth):
        """The positive control. Without it, "nothing solved" is unreadable."""
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype != "single_fragment_control":
                continue
            analysis = _lexical_analysis(snapshot)
            assert len(analysis.single_fragment_identifications) == 1, snapshot.case_id
            checked += 1
        assert checked == 8

    def test_pair_and_triple_archetypes_need_exactly_the_arity_they_declare(
        self, v4_stage2, v4_truth
    ):
        expected = {
            "conjunction_pair": 2,
            "conjunction_wide": 2,
            "conjunction_triple": 3,
        }
        batch, _store = v4_stage2
        seen: Counter = Counter()
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype not in expected:
                continue
            analysis = _lexical_analysis(snapshot)
            assert analysis.minimal_arity == expected[entry.archetype], (
                snapshot.case_id,
                entry.archetype,
                analysis.minimal_arity,
            )
            seen[entry.archetype] += 1
        assert dict(seen) == {
            "conjunction_pair": 12,
            "conjunction_wide": 8,
            "conjunction_triple": 6,
        }

    def test_the_cross_modal_archetypes_are_out_of_lexical_reach_entirely(
        self, v4_stage2, v4_truth
    ):
        """No number of fragments composed together isolates a candidate.

        These are the cases whose second half is a break-up amount or a
        settlement date -- facts the declared relation set has no way to
        compare a substring against.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype not in (
                "amount_reference_hop",
                "conflict_context_resolves",
            ):
                continue
            analysis = _lexical_analysis(snapshot)
            assert analysis.minimal_arity is None, snapshot.case_id
            checked += 1
        assert checked == 14

    def test_ambiguous_cases_stay_ambiguous_under_exhaustive_composition(
        self, v4_stage2, v4_truth
    ):
        """Section 13.4. Escalation must be forced by the evidence, not by effort.

        ``conflict_stale_reference`` is excluded and separately asserted below:
        it *does* offer a discriminating fragment, and that is precisely the
        hazard it exists to expose.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if FAMILY_TRUE_AMBIGUITY not in entry.families:
                continue
            if entry.archetype == "conflict_stale_reference":
                continue
            analysis = _lexical_analysis(snapshot)
            assert analysis.single_fragment_identifications == frozenset(), snapshot.case_id
            assert analysis.minimal_arity is None, snapshot.case_id
            checked += 1
        assert checked == 12

    def test_the_stale_reference_archetype_offers_exactly_one_false_discriminator(
        self, v4_stage2, v4_truth
    ):
        """The unsafe-auto-match probe, asserted as the hazard it is.

        Exactly one candidate is reachable by a lone fragment, and the case has
        no correct answer at all -- so any strategy that resolves on the first
        fragment to separate the set produces a wrong auto-resolution here.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype != "conflict_stale_reference":
                continue
            assert entry.correct_relationship is None
            analysis = _lexical_analysis(snapshot)
            assert len(analysis.single_fragment_identifications) == 1, snapshot.case_id
            checked += 1
        assert checked == 4


class TestDecoyConstruction:
    def test_every_decoy_family_case_carries_admissible_but_useless_evidence(
        self, v4_stage2, v4_truth
    ):
        """Section 4.G: a decoy has to be *reached*, not merely present.

        A token no declared relation touches is noise, not a decoy. What makes
        a decoy a decoy is that it clears the evidence floor and still says
        nothing about which candidate is right.

        ``conflict_stale_reference`` is the one archetype excluded, and its
        exclusion is the finding rather than an exemption: its decoy is a
        *discriminating* one, which is why it is asserted separately above and
        why it is the only archetype here that can produce an unsafe
        auto-match.
        """
        batch, _store = v4_stage2
        checked = 0
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if FAMILY_DECOY not in entry.families:
                continue
            if entry.archetype == "conflict_stale_reference":
                continue
            analysis = _lexical_analysis(snapshot)
            non_discriminating = [
                reach for reach in analysis.distinct_reach_sets if len(reach) > 1
            ]
            assert non_discriminating, snapshot.case_id
            checked += 1
        assert checked == 42

    def test_the_control_family_s_partial_reference_decoy_is_below_the_floor(
        self, v4_stage2, v4_truth
    ):
        """A three-character partial cannot pin four characters, by arithmetic.

        Asserted anyway, because the decoy's whole job is to look convincing
        to a reader while being inadmissible to the gate, and a construction
        that quietly made it four characters long would change the archetype
        without changing its name.
        """
        batch, _store = v4_stage2
        for snapshot in batch.snapshots:
            entry = v4_truth[snapshot.case_id]
            if entry.archetype != "single_fragment_control":
                continue
            narration = snapshot.base_evidence.bank_record.narration
            assert "REV" in narration, snapshot.case_id
            marker = narration.index("REV")
            partial = narration[marker + 3 : marker + 6]
            assert partial.isdigit() and len(partial) == 3, snapshot.case_id
            analysis = _lexical_analysis(snapshot)
            assert partial not in analysis.reach_by_fragment


class TestGroundTruthIsNotVisible:
    def test_no_family_archetype_or_composition_label_appears_in_visible_data(
        self, benchmark_dir, v4_truth_rows
    ):
        labels = {row["archetype"] for row in v4_truth_rows}
        labels |= {row["required_composition"] for row in v4_truth_rows}
        for row in v4_truth_rows:
            labels.update(row["families"])
        directory = benchmark_dir / "datasets" / V4_PILOT_SPLIT
        for path in sorted(directory.glob("*.jsonl")):
            text = path.read_text(encoding="utf-8")
            for label in labels:
                assert label not in text, (path.name, label)

    def test_the_case_snapshot_carries_no_benchmark_metadata(self, v4_stage2):
        """What Stage 3 is handed must say nothing about what the case is for."""
        batch, _store = v4_stage2
        for snapshot in batch.snapshots[:20]:
            serialized = json.dumps(snapshot.model_dump(mode="json"))
            for leak in (
                "archetype",
                "families",
                "required_composition",
                "required_outcome",
                "conjunction",
                "ambiguity",
                "decoy",
                "true_reference",
                "distractor",
            ):
                assert leak not in serialized, (snapshot.case_id, leak)

    def test_the_agent_briefing_carries_no_benchmark_metadata(self, v4_stage2):
        from finrecon.agent.prompt import case_briefing

        batch, _store = v4_stage2
        for snapshot in batch.snapshots[:20]:
            briefing = case_briefing(snapshot)
            for leak in (
                "archetype",
                "required_composition",
                "conjunction",
                "multi_fragment",
                "decoy",
                "true_reference",
            ):
                assert leak not in briefing, (snapshot.case_id, leak)


class TestArchetypeDeclarations:
    def test_every_declared_archetype_is_generated(self):
        assert set(TARGET_ARCHETYPE_COUNTS) == {spec.archetype for spec in ARCHETYPES}

    def test_every_archetype_carries_the_multi_candidate_family(self):
        """v4's premise is that two candidates was never enough."""
        for spec in ARCHETYPES:
            assert FAMILY_MULTI_CANDIDATE in spec.families, spec.archetype

    def test_ground_truth_families_match_the_archetype_declaration(self, v4_truth_rows):
        for row in v4_truth_rows:
            spec = archetype_spec(row["archetype"])
            assert tuple(row["families"]) == spec.families, row["case_id"]


def _lexical_analysis(snapshot):
    """Run the generator's own exhaustive analysis over a Stage-2 snapshot.

    Reconstructs the settlement records the analysis needs from the snapshot's
    own facts, so the test measures what the *pipeline* produced rather than
    re-deriving it from the generator's in-memory objects.
    """
    from finrecon.models import Settlement
    from finrecon.models.money import Paise

    settlements = tuple(
        Settlement(
            settlement_id=facts.settlement_id,
            utr=facts.utr,
            amount=Paise(facts.amount_paise),
            created_at=facts.created_at_utc,
            breakup=(),
        )
        for facts in snapshot.base_evidence.settlement_facts
    )
    return analyse_lexical(
        snapshot.base_evidence.bank_record.narration, settlements
    )
