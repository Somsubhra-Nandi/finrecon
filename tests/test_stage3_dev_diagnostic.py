"""DEV engineering diagnostics for Stage 3. Not a benchmark result.

Read the header of every assertion below with two qualifications attached.

**DEV only.** DESIGN.md §5.1 step 7 says build against DEV and report
against FROZEN. Nothing here reads a FROZEN-EVAL outcome.

**Fake provider.** These runs are driven by
:class:`tests.stage3_fakes.MechanicalInvestigator`, a deterministic
non-linguistic stand-in. It brute-forces narration fragments because it
cannot read one, and it never fabricates because it cannot invent one. So
its resolution count measures the *architecture* -- loop, tools, validator,
policy gate, ledger -- and says nothing whatever about how a model would do.
The one number here that would mean the same thing under any provider is
the count of **wrong** auto-resolutions, because the deterministic gate is
what produces it.

What these tests are for: a regression in the Stage-3 predicates fails a
test instead of quietly producing confident wrong answers.
"""

from __future__ import annotations

import json
from collections import Counter

from finrecon.decide import policy as gate
from finrecon.matchers.result import DecisionStatus


class TestStageTwoBaselineIsUnchanged:
    """Stage 3 must not have moved the Stage-2 line it stands on."""

    def test_stage_two_still_leaves_two_hundred_t2_cases_unresolved(
        self, dev_result, dev_ground_truth
    ):
        result, _ = dev_result
        by_case = {d.case_id: d for d in result.decisions}
        t2 = [c for c, e in dev_ground_truth.items() if e["tier"] == "T2"]
        assert len(t2) == 200
        assert all(by_case[c].status is DecisionStatus.UNRESOLVED for c in t2)

    def test_every_t2_case_reaches_stage_three_with_exactly_two_candidates(
        self, dev_result, dev_ground_truth
    ):
        result, _ = dev_result
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T2":
                continue
            assert len(result.candidates_by_case[case_id]) == 2, case_id

    def test_stage_three_investigates_exactly_the_stage_two_residual(
        self, dev_stage3_result
    ):
        result, batch, _ = dev_stage3_result
        assert len(result.outcomes) == len(batch.snapshots) == 240


class TestNoUnsafeAutoMatch:
    """The metric DESIGN.md §1 says matters most, on the DEV split."""

    def test_no_dev_case_is_auto_resolved_incorrectly(
        self, dev_stage3_result, dev_ground_truth
    ):
        result, _, _ = dev_stage3_result
        wrong = []
        for outcome in result.resolved():
            expected = dev_ground_truth[outcome.case_id]["correct_relationship"]
            if expected is None:
                wrong.append((outcome.case_id, "resolved a case with no correct answer"))
                continue
            if tuple(sorted(expected["settlement_ids"])) != outcome.decision.resolved_settlement_ids:
                wrong.append((outcome.case_id, "resolved to the wrong settlement"))
        assert wrong == []

    def test_every_resolution_reconciles_to_the_exact_paise(self, dev_stage3_result):
        for outcome in dev_stage3_result[0].resolved():
            assessment = next(
                a
                for a in outcome.validator_result.financial_assessments
                if a.candidate_id == outcome.decision.resolved_candidate_id
            )
            assert assessment.group_unexplained_delta_paise == 0
            assert assessment.every_breakup_is_exact
            assert assessment.breakup_references_are_sound

    def test_every_resolution_rests_on_a_fragment_from_the_real_narration(
        self, dev_stage3_result
    ):
        for outcome in dev_stage3_result[0].resolved():
            narration = outcome.snapshot.base_evidence.bank_record.narration
            assert outcome.validator_result.discriminating_fragments
            for fragment in outcome.validator_result.admissible_fragments:
                assert fragment in narration


class TestT3RemainsAmbiguous:
    def test_every_t3_case_escalates(self, dev_stage3_result, dev_ground_truth):
        result, _, _ = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        t3 = [c for c, e in dev_ground_truth.items() if e["tier"] == "T3"]
        assert len(t3) == 40
        for case_id in t3:
            assert by_case[case_id].decision.outcome == "ESCALATE", case_id

    def test_t3_escalations_are_blocked_on_absent_evidence_not_on_an_error(
        self, dev_stage3_result, dev_ground_truth
    ):
        """Two candidates is not permission to choose one; it is a reason to stop."""
        result, _, _ = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T3":
                continue
            outcome = by_case[case_id]
            assert gate.BLOCKER_NO_REFERENCE_LINK in outcome.decision.blockers, case_id
            assert outcome.trajectory.completed_normally, case_id
            assert not outcome.trajectory.had_validation_failure, case_id

    def test_every_t3_case_still_offered_both_candidates(
        self, dev_stage3_result, dev_ground_truth
    ):
        """Escalation must mean "not chosen", never "not offered"."""
        result, _, _ = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T3":
                continue
            assert len(by_case[case_id].validator_result.complete_candidate_ids) == 2


class TestProductionCodeIsTierBlind:
    def test_no_stage_three_decision_carries_a_tier_or_an_archetype(
        self, dev_stage3_result
    ):
        result, _, _ = dev_stage3_result
        for outcome in result.outcomes[:50]:
            serialized = json.dumps(outcome.decision.model_dump(mode="json"))
            for leak in ("tier", "archetype", "required_outcome", "true_reference", "T2", "T3"):
                assert leak not in serialized, outcome.case_id

    def test_no_trajectory_carries_a_tier_label(self, dev_stage3_result):
        result, _, _ = dev_stage3_result
        for outcome in result.outcomes[:50]:
            serialized = json.dumps(outcome.trajectory.model_dump(mode="json"))
            for leak in ("archetype", "required_outcome", "true_reference", "distractor"):
                assert leak not in serialized, outcome.case_id

    def test_the_same_predicates_are_applied_to_every_case(
        self, dev_stage3_result, dev_ground_truth
    ):
        """T2 and T3 differ in outcome only because the evidence differs."""
        result, _, _ = dev_stage3_result
        declarations = {
            json.dumps(o.decision.policy_declaration, sort_keys=True) for o in result.outcomes
        }
        assert len(declarations) == 1, "one declared policy, applied uniformly"
        floors = {o.decision.min_pinned_reference_characters_applied for o in result.outcomes}
        assert floors == {4}, "no case got a bespoke evidence bar"


class TestArchitectureDiagnostic:
    """Plumbing statistics from the fake provider. Not a capability measure."""

    def test_the_loop_stays_inside_its_budget_on_every_case(self, dev_stage3_result):
        result, _, _ = dev_stage3_result
        for outcome in result.outcomes:
            assert outcome.trajectory.step_count <= outcome.trajectory.max_steps

    def test_every_case_terminated_in_a_declared_state(self, dev_stage3_result):
        result, _, _ = dev_stage3_result
        reasons = Counter(o.trajectory.termination_reason for o in result.outcomes)
        assert set(reasons) == {"investigation_complete"}

    def test_the_end_to_end_path_resolves_a_substantial_share_of_t2(
        self, dev_stage3_result, dev_ground_truth
    ):
        """A floor, not a target. It exists so a broken path fails loudly.

        The fake investigator reaches roughly 85% of DEV T2 by brute force;
        this asserts only that the architecture carries cases end to end at
        all. Do not read it as a model result, and do not tune anything to
        raise it.
        """
        result, _, _ = dev_stage3_result
        t2_resolved = sum(
            1
            for o in result.resolved()
            if dev_ground_truth[o.case_id]["tier"] == "T2"
        )
        assert t2_resolved >= 100

    def test_escalations_always_name_at_least_one_blocker(self, dev_stage3_result):
        result, _, _ = dev_stage3_result
        for outcome in result.escalated():
            assert outcome.decision.blockers, outcome.case_id
            for blocker in outcome.decision.blockers:
                assert blocker in gate.HARD_BLOCKERS

    def test_no_escalation_is_caused_by_a_broken_tool_call(self, dev_stage3_result):
        """If the plumbing were failing, this is where it would show."""
        result, _, _ = dev_stage3_result
        assert not [
            o for o in result.escalated() if gate.BLOCKER_TOOL_VALIDATION in o.decision.blockers
        ]
        assert not [
            o for o in result.escalated() if gate.BLOCKER_PROVIDER_FAILURE in o.decision.blockers
        ]

    def test_every_investigated_case_has_a_persisted_decision(self, dev_stage3_result):
        result, batch, store = dev_stage3_result
        counts = store.stage3_outcome_counts(batch.batch_id)
        assert counts.get("RESOLVE", 0) == len(result.resolved())
        assert counts.get("ESCALATE", 0) == len(result.escalated())
        assert store.count("stage3_investigations") == len(result.outcomes)


# --- Adversarial probes: what a maximally aggressive agent could achieve ---
#
# The mechanical investigator above tests six fragments. These two probes
# test *every* contiguous narration substring of length >= 4, against every
# candidate and every reference kind -- far more than any bounded agent could
# do inside the step budget. They answer two different questions.


def _exhaustive_identifications(narration, references_by_candidate, floor=4):
    """Candidates a fragment could uniquely identify, over all substrings.

    An upper bound on what any purely lexical strategy can extract from this
    narration under the declared relations.
    """
    from finrecon.evidence.reference import (
        DECLARED_RELATION_IDS,
        compare,
        strongest_admissible_relation,
    )

    accepted = frozenset(DECLARED_RELATION_IDS)
    fragments = {
        narration[start:end]
        for start in range(len(narration))
        for end in range(start + 4, min(len(narration), start + 40) + 1)
    }
    identified = set()
    for fragment in fragments:
        reached = set()
        for candidate_id, references in references_by_candidate.items():
            for kind, value in references:
                if value and strongest_admissible_relation(
                    compare(fragment, value, kind),
                    accepted_relation_ids=accepted,
                    min_pinned_reference_characters=floor,
                ):
                    reached.add(candidate_id)
        if len(reached) == 1:
            identified |= reached
    return identified


def _references(snapshot):
    facts = {f.settlement_id: f for f in snapshot.base_evidence.settlement_facts}
    return {
        candidate.candidate_id: [
            (kind, value)
            for sid in candidate.settlement_ids
            for kind, value in (
                ("utr", facts[sid].utr),
                ("settlement_id", facts[sid].settlement_id),
            )
        ]
        for candidate in snapshot.candidates
    }


class TestAdversarialRefusal:
    """Can an unboundedly aggressive agent manufacture a link where none exists?

    This is the strongest form of the abstention claim (DESIGN.md §4.4). The
    mechanical investigator refusing all 40 T3 cases could just mean it did
    not try hard enough. This probe tries as hard as the declared relations
    permit.
    """

    def test_no_t3_case_yields_a_unique_link_under_exhaustive_enumeration(
        self, dev_stage3_result, dev_ground_truth
    ):
        result, _, _ = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        probed = 0
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T3":
                continue
            snapshot = by_case[case_id].snapshot
            identified = _exhaustive_identifications(
                snapshot.base_evidence.bank_record.narration, _references(snapshot)
            )
            assert identified == set(), f"{case_id} manufactured a link: {identified}"
            probed += 1
        assert probed == 40


class TestReferenceRecoveryIsNotModelExclusive:
    """A finding, recorded as a test so it cannot quietly stop being true.

    Exhaustive substring enumeration reaches **every** DEV T2 case correctly
    and picks no wrong one. Reference recovery on this benchmark is therefore
    within reach of a purely lexical, model-free strategy, and the C-vs-D
    ablation (DESIGN.md §5.5) has little room to move on T2 as a result.

    That is an experimental result about the benchmark, not a defect to fix.
    Weakening the validator's relation set to leave the model something to do
    would trade correctly reconciled money for a better-looking ablation,
    which DESIGN.md §1 rules out. See ``notes/STAGE3-FINDINGS.md`` §1.
    """

    def test_exhaustive_enumeration_reaches_every_t2_case_and_misidentifies_none(
        self, dev_stage3_result, dev_ground_truth
    ):
        result, _, _ = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        tally = Counter()
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T2":
                continue
            snapshot = by_case[case_id].snapshot
            identified = _exhaustive_identifications(
                snapshot.base_evidence.bank_record.narration, _references(snapshot)
            )
            truth = {
                c.candidate_id
                for c in snapshot.candidates
                if set(c.settlement_ids)
                == set(entry["correct_relationship"]["settlement_ids"])
            }
            if identified == truth:
                tally["unique_correct"] += 1
            elif not identified:
                tally["none"] += 1
            elif len(identified) == 1:
                tally["unique_wrong"] += 1
            else:
                tally["ambiguous"] += 1

        assert tally["unique_wrong"] == 0, "the relations never point at the wrong candidate"
        assert tally["unique_correct"] == 200, (
            "recorded as a finding: DEV T2 needs no model under these relations"
        )
