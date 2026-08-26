"""validator.v2: conjunctive reference evidence, and the attacks it has to survive.

Every test here drives the **production** decision path -- the real
:func:`finrecon.decide.validator.validate_case` and the real
:func:`finrecon.decide.policy.decide`, through the real
``compare_reference_fragment`` tool. The experimental harness in
``benchmark/baselines/conjunction.py`` compared five candidate rules; this
module asserts that the one that shipped behaves as the shipped thing.

The safety properties, and where each is established
----------------------------------------------------

============================  ==================================================
Full snapshot                 ``TestTheModelNeverSuppliesTheCandidateAxis``
Order invariance              ``TestInvariance``
Duplicate invariance          ``TestInvariance``
Overlap / substring           ``TestInvariance``
Monotonic contradiction       ``TestContradictionIsMonotonic``
Fail closed                   ``TestFailsClosed``
Source provenance             ``TestProvenance``
============================  ==================================================

Two of them are properties of the *operation* rather than checks bolted onto
it -- intersecting a set twice is intersecting it once, and an intersection
never grows -- so the tests measure a claim already made structurally. They are
here because a future rule that reintroduced vote-counting would pass a review
and fail this file.

No provider is constructed anywhere. Nothing here is a model result.
"""

from __future__ import annotations

import itertools
import json

import pytest

from finrecon.agent.tools import TOOL_COMPARE_REFERENCE_FRAGMENT, ToolContext, execute
from finrecon.agent.version import POLICY_VERSION, VALIDATOR_VERSION
from finrecon.decide import policy as gate
from finrecon.decide.config import DEFAULT_POLICY
from finrecon.decide.policy import adjudicate
from finrecon.decide.validator import (
    FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION,
    REFERENCE_STATE_AMBIGUOUS,
    REFERENCE_STATE_CLOSURE_INCOMPLETE,
    REFERENCE_STATE_CONTRADICTORY,
    REFERENCE_STATE_IDENTIFIED,
    REFERENCE_STATE_NO_AGENT_EVIDENCE,
    REFERENCE_STATE_NO_EVIDENCE,
    RawToolEvidence,
    validate_case,
)
from benchmark.baselines.adversarial import (
    ADVERSARIAL_BY_NAME,
    ADVERSARIAL_CASES,
    CANDIDATE_A,
    CANDIDATE_B,
    FABRICATED,
    SPAN_HEAD,
    SPAN_HINGE,
    SPAN_LONG_A,
    SPAN_LONG_B,
    SPAN_TAIL,
    snapshot_for,
)
from tests.test_policy import trajectory_for


def evidence_for(snapshot, fragments):
    """Real tool outputs for the fragments an agent claims to have tested."""
    context = ToolContext(snapshot=snapshot)
    items = []
    for fragment in fragments:
        arguments, output = execute(
            context, TOOL_COMPARE_REFERENCE_FRAGMENT, json.dumps({"fragment": fragment})
        )
        items.append(
            RawToolEvidence(
                tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                arguments=arguments.model_dump(mode="json"),
                output=output.model_dump(mode="json"),
            )
        )
    return tuple(items)


def adjudicate_fragments(snapshot, fragments, **kwargs):
    """Validate and gate one case from a given agent fragment list."""
    trajectory = trajectory_for(
        snapshot, evidence=evidence_for(snapshot, fragments), **kwargs
    )
    return adjudicate(snapshot=snapshot, trajectory=trajectory, policy=DEFAULT_POLICY)


def resolved_candidate(snapshot, fragments, **kwargs):
    _result, decision = adjudicate_fragments(snapshot, fragments, **kwargs)
    return decision.resolved_candidate_id


class TestVersionIdentity:
    def test_the_validator_declares_v2(self):
        assert VALIDATOR_VERSION == "validator.v2"

    def test_the_policy_gate_is_unchanged(self):
        """v2 was constrained to need no new blocker. This is that constraint."""
        assert POLICY_VERSION == "policy.v1"

    def test_the_blocker_vocabulary_is_unchanged(self):
        """A contradiction arrives as ``ambiguous_reference_link``, not a new id."""
        assert gate.HARD_BLOCKERS == (
            gate.BLOCKER_SNAPSHOT_INTEGRITY,
            gate.BLOCKER_TOOL_VALIDATION,
            gate.BLOCKER_STEP_BUDGET_EXHAUSTED,
            gate.BLOCKER_PROVIDER_FAILURE,
            gate.BLOCKER_INVESTIGATION_INCOMPLETE,
            gate.BLOCKER_NO_REFERENCE_LINK,
            gate.BLOCKER_AMBIGUOUS_REFERENCE_LINK,
            gate.BLOCKER_NO_SURVIVING_CANDIDATE,
            gate.BLOCKER_MULTIPLE_SURVIVING_CANDIDATES,
            gate.BLOCKER_UNEXPLAINED_DELTA,
            gate.BLOCKER_FINANCIAL_MISMATCH,
            gate.BLOCKER_COUNTERPARTY_ALREADY_RESOLVED,
            gate.BLOCKER_VALUE_ABOVE_CEILING,
        )


class TestTheCapability:
    """What v2 exists to do: compose two individually inconclusive clues."""

    def test_two_ambiguous_clues_jointly_resolve(self):
        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "RESOLVE"
        assert decision.resolved_candidate_id == CANDIDATE_A
        assert result.reference_evidence_state == REFERENCE_STATE_IDENTIFIED
        assert result.resolved_conjunctively is True
        assert result.informative_atom_count == 2

    def test_neither_clue_alone_resolves_it(self):
        """The conjunction is load-bearing, not decoration."""
        snapshot = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"].snapshot()
        for fragment in (SPAN_HEAD, SPAN_TAIL):
            _result, decision = adjudicate_fragments(snapshot, (fragment,))
            assert decision.outcome == "RESOLVE", (
                "the closure still sees the whole narration, so one seed is enough "
                "to reach the same proof"
            )
        # ...but under the pre-v2 rule neither fragment discriminated, which is
        # what the reach sets say:
        result = validate_case(
            snapshot=snapshot, evidence=evidence_for(snapshot, (SPAN_HEAD,))
        )
        assert result.discriminating_fragments == ()

    def test_a_single_clue_resolution_is_not_reported_as_conjunctive(self):
        case = ADVERSARIAL_BY_NAME["two_candidate_clean_resolution"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "RESOLVE"
        assert result.reference_evidence_state == REFERENCE_STATE_IDENTIFIED
        assert result.discriminating_fragments, "v1 would have resolved this too"
        assert result.resolved_conjunctively is False, (
            "a clue that isolates a candidate on its own is not a conjunction, "
            "even when other non-contradicting clues are present"
        )


class TestEveryAdversarialFixture:
    """The whole attack suite, through the production gate."""

    @pytest.mark.parametrize(
        "case", ADVERSARIAL_CASES, ids=lambda c: c.name
    )
    def test_the_shipped_gate_matches_the_declared_safe_outcome(self, case):
        _result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.resolved_candidate_id == case.must_resolve_to, case.why

    def test_the_cherry_picking_attack_is_refused_from_every_angle(self):
        """One narration, three false proofs, all three refused.

        The attack that ruled out intersecting the agent's own selection: the
        same line yields ``{A}``, ``{B}`` or ``{C}`` depending on which pair of
        clues is tested, so a rule reading only the agent's pair would let the
        model pick the winner by picking where not to look.
        """
        for name in (
            "cherry_picking",
            "cherry_picking_toward_b",
            "cherry_picking_toward_c",
        ):
            case = ADVERSARIAL_BY_NAME[name]
            result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
            assert decision.outcome == "ESCALATE", name
            assert result.reference_evidence_state == REFERENCE_STATE_CONTRADICTORY
            assert gate.BLOCKER_AMBIGUOUS_REFERENCE_LINK in decision.blockers

    def test_all_three_cherry_picking_angles_reach_the_same_decision(self):
        """The decision cannot depend on which pair the agent happened to test."""
        decisions = {
            name: resolved_candidate(
                ADVERSARIAL_BY_NAME[name].snapshot(),
                ADVERSARIAL_BY_NAME[name].model_fragments,
            )
            for name in (
                "cherry_picking",
                "cherry_picking_toward_b",
                "cherry_picking_toward_c",
            )
        }
        assert set(decisions.values()) == {None}, decisions

    def test_a_stale_strong_reference_no_longer_resolves(self):
        """The v1 safety hole, closed. The one fixture v1 got wrong and v2 does not.

        A twelve-character prefix of A is conclusive on its own; the same
        narration also carries a clue consistent only with B and C. v1 set the
        second aside as non-discriminating and resolved A.
        """
        case = ADVERSARIAL_BY_NAME["stale_strong_reference_plus_hinge"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_CONTRADICTORY
        # v1's own input is still recorded, and still says A -- which is exactly
        # how the difference between the two rules stays legible in a stored
        # result rather than becoming invisible.
        assert result.discriminating_fragments == (SPAN_LONG_A,)
        assert result.reference_identified_candidate_ids != (CANDIDATE_A,)


class TestInvariance:
    def test_evidence_order_changes_nothing(self):
        """Every ordering of every fixture's fragments reaches one decision."""
        for case in ADVERSARIAL_CASES:
            snapshot = case.snapshot()
            baseline = resolved_candidate(snapshot, case.model_fragments)
            for permutation in itertools.permutations(case.model_fragments):
                assert resolved_candidate(snapshot, permutation) == baseline, (
                    case.name,
                    permutation,
                )

    def test_repeating_evidence_changes_nothing(self):
        for case in ADVERSARIAL_CASES:
            snapshot = case.snapshot()
            baseline = resolved_candidate(snapshot, case.model_fragments)
            for multiplier in (2, 3):
                repeated = case.model_fragments * multiplier
                assert resolved_candidate(snapshot, repeated) == baseline, case.name

    def test_a_repeated_ambiguous_clue_never_becomes_conclusive(self):
        """Three offerings of one two-candidate clue are still one clue."""
        case = ADVERSARIAL_BY_NAME["duplicate_cannot_strengthen"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_AMBIGUOUS
        assert result.informative_atom_count == 1
        assert len(result.reference_intersection_candidate_ids) == 2

    def test_overlapping_slices_of_one_span_are_one_claim(self):
        """``863727``, ``63727``, ``3727`` must not become three votes."""
        case = ADVERSARIAL_BY_NAME["overlapping_slices_of_one_span"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert result.informative_atom_count == 1
        assert result.reference_closure.independent_span_count() == 1
        atom = result.reference_closure.informative_atoms()[0]
        assert atom.member_fragment_count == 3
        assert len(result.agent_surfaced_atom_ids) == 1

    def test_a_generic_wrapper_is_not_independent_corroboration(self):
        """``SETL`` reaches every candidate, so it corroborates nothing."""
        case = ADVERSARIAL_BY_NAME["generic_wrapper_plus_specific"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert result.informative_atom_count == 1
        reaching = {atom.fragment: atom for atom in result.reference_closure.atoms}
        assert "SETL" in reaching
        assert reaching["SETL"].atom_id not in result.informative_atom_ids


class TestContradictionIsMonotonic:
    def test_adding_a_contradicting_clue_destroys_the_match(self):
        before = ADVERSARIAL_BY_NAME["contradiction_before"]
        after = ADVERSARIAL_BY_NAME["contradiction_after"]

        before_result, before_decision = adjudicate_fragments(
            before.snapshot(), before.model_fragments
        )
        assert before_decision.outcome == "RESOLVE"
        assert before_decision.resolved_candidate_id == CANDIDATE_A
        assert before_result.reference_intersection_candidate_ids == (CANDIDATE_A,)

        after_result, after_decision = adjudicate_fragments(
            after.snapshot(), after.model_fragments
        )
        assert after_decision.outcome == "ESCALATE"
        assert after_result.reference_evidence_state == REFERENCE_STATE_CONTRADICTORY
        assert after_result.reference_intersection_candidate_ids == ()
        assert set(after_result.reference_union_candidate_ids) == {
            CANDIDATE_A,
            CANDIDATE_B,
        }

    def test_a_contradiction_the_agent_never_tested_still_destroys_the_match(self):
        """The omission case. The contradiction is in the narration either way."""
        untested = ADVERSARIAL_BY_NAME["contradiction_after_untested"]
        result, decision = adjudicate_fragments(
            untested.snapshot(), untested.model_fragments
        )
        assert untested.model_fragments == (SPAN_LONG_A,)
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_CONTRADICTORY
        assert SPAN_LONG_B not in result.admissible_fragments

    def test_tested_and_untested_contradictions_are_indistinguishable(self):
        tested = ADVERSARIAL_BY_NAME["contradiction_after"]
        untested = ADVERSARIAL_BY_NAME["contradiction_after_untested"]
        assert tested.narration == untested.narration
        first = validate_case(
            snapshot=tested.snapshot(),
            evidence=evidence_for(tested.snapshot(), tested.model_fragments),
        )
        second = validate_case(
            snapshot=untested.snapshot(),
            evidence=evidence_for(untested.snapshot(), untested.model_fragments),
        )
        assert (
            first.reference_identified_candidate_ids
            == second.reference_identified_candidate_ids
        )
        assert first.reference_closure == second.reference_closure


class TestFailsClosed:
    def test_a_contradiction_escalates(self):
        case = ADVERSARIAL_BY_NAME["cherry_picking"]
        _result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"

    def test_several_survivors_escalate(self):
        case = ADVERSARIAL_BY_NAME["duplicate_cannot_strengthen"]
        _result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_AMBIGUOUS_REFERENCE_LINK in decision.blockers

    def test_fabricated_evidence_cannot_participate(self):
        case = ADVERSARIAL_BY_NAME["fabricated_only"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert [item.fragment for item in result.inadmissible_fragments] == [FABRICATED]
        assert result.inadmissible_fragments[0].reason == (
            FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION
        )
        assert result.admissible_fragments == ()
        assert result.reference_evidence_state == REFERENCE_STATE_NO_AGENT_EVIDENCE

    def test_a_fabricated_discriminator_alongside_real_evidence_is_ignored(self):
        case = ADVERSARIAL_BY_NAME["fabricated_plus_real"]
        result, decision = adjudicate_fragments(case.snapshot(), case.model_fragments)
        assert decision.outcome == "ESCALATE"
        assert FABRICATED in [item.fragment for item in result.inadmissible_fragments]
        assert SPAN_HEAD in result.admissible_fragments
        # The fabrication would have isolated A had it been believed. What the
        # narration actually supports reaches two candidates, so nothing is
        # isolated -- and the fabricated string appears in no closure atom.
        assert result.reference_evidence_state == REFERENCE_STATE_AMBIGUOUS
        assert len(result.reference_intersection_candidate_ids) == 2
        assert all(
            FABRICATED != atom.fragment for atom in result.reference_closure.atoms
        )

    def test_a_case_the_investigation_gathered_nothing_for_escalates(self):
        """The invariant that decided the rule. Asserted on the production path.

        A closure-only rule would resolve this: the narration does isolate a
        candidate. It must not, because money moving on a case whose audit
        trail shows the investigation contributing nothing is a worse artifact
        than an escalation.
        """
        snapshot = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"].snapshot()
        result, decision = adjudicate_fragments(snapshot, ())
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_NO_AGENT_EVIDENCE
        assert gate.BLOCKER_NO_REFERENCE_LINK in decision.blockers
        # ...and the closure it declined to use would have identified one.
        assert len(result.reference_closure.intersection()) == 1

    def test_an_uninformative_seed_does_not_unlock_the_closure(self):
        """A fragment reaching every candidate is not evidence of anything.

        ``SETL`` is admissible and does reach references, so it seeds the path;
        what it cannot do is separate candidates. The case still escalates, on
        the closure's own reading rather than on the seed's.
        """
        snapshot = snapshot_for(f"RZPY/SETL/{SPAN_HEAD}/BATCH47-MUM")
        result, decision = adjudicate_fragments(snapshot, ("SETL",))
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_AMBIGUOUS

    def test_a_narration_with_nothing_to_recover_escalates(self):
        snapshot = snapshot_for("NEFT CREDIT - SETTLEMENT")
        result, decision = adjudicate_fragments(snapshot, ("NEFT", "CREDIT"))
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_NO_AGENT_EVIDENCE

    def test_a_narration_past_the_enumeration_bound_escalates(self):
        """A partial closure cannot support a claim about what is absent."""
        from finrecon.evidence.closure import MAX_NARRATION_LENGTH

        narration = (f"NEFT CR-RZRPAY-{SPAN_LONG_A}-PAD") * 20
        assert len(narration) > MAX_NARRATION_LENGTH
        snapshot = snapshot_for(narration)
        result, decision = adjudicate_fragments(snapshot, (SPAN_LONG_A,))
        assert decision.outcome == "ESCALATE"
        assert result.reference_evidence_state == REFERENCE_STATE_CLOSURE_INCOMPLETE
        assert gate.BLOCKER_NO_REFERENCE_LINK in decision.blockers

    def test_a_blocked_investigation_escalates_even_when_the_closure_resolves(self):
        """Trajectory blockers are independent of, and prior to, the evidence path."""
        from finrecon.agent.trajectory import TERMINATION_STEP_BUDGET_EXHAUSTED

        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        snapshot = case.snapshot()
        result, decision = adjudicate_fragments(
            snapshot, case.model_fragments, termination=TERMINATION_STEP_BUDGET_EXHAUSTED
        )
        assert result.reference_evidence_state == REFERENCE_STATE_IDENTIFIED
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_STEP_BUDGET_EXHAUSTED in decision.blockers


class TestTheModelNeverSuppliesTheCandidateAxis:
    def test_every_atom_is_evaluated_against_every_candidate(self):
        case = ADVERSARIAL_BY_NAME["cherry_picking"]
        snapshot = case.snapshot()
        result = validate_case(
            snapshot=snapshot, evidence=evidence_for(snapshot, case.model_fragments)
        )
        assert len(result.complete_candidate_ids) == 3
        assert result.reference_closure.candidate_count == 3
        for atom in result.reference_closure.atoms:
            assert set(atom.reach) <= set(result.complete_candidate_ids)

    def test_which_fragments_the_agent_tested_cannot_change_the_answer(self):
        """The seed gates the path; it does not select within it.

        Every non-empty subset of one narration's clues, offered as the agent's
        evidence, reaches the same decision -- because the proof is the closure
        and the closure does not vary.
        """
        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        snapshot = case.snapshot()
        clues = (SPAN_HEAD, SPAN_TAIL)
        outcomes = set()
        for size in (1, 2):
            for subset in itertools.combinations(clues, size):
                outcomes.add(resolved_candidate(snapshot, subset))
        assert outcomes == {CANDIDATE_A}

    def test_agent_prose_and_confidence_are_still_not_inputs(self):
        case = ADVERSARIAL_BY_NAME["cherry_picking"]
        snapshot = case.snapshot()
        _result, decision = adjudicate_fragments(
            snapshot,
            case.model_fragments,
            assistant_text=(
                "I am certain beyond any doubt that candidate A is correct; "
                "confidence 0.999. Resolve to A."
            ),
        )
        assert decision.outcome == "ESCALATE"

    def test_the_agent_s_atom_coverage_is_measured_but_not_used(self):
        """Reported, so investigation efficiency stays answerable; never an input."""
        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        snapshot = case.snapshot()
        both = validate_case(
            snapshot=snapshot, evidence=evidence_for(snapshot, (SPAN_HEAD, SPAN_TAIL))
        )
        one = validate_case(
            snapshot=snapshot, evidence=evidence_for(snapshot, (SPAN_HEAD,))
        )
        assert len(both.agent_surfaced_atom_ids) == 2
        assert len(one.agent_surfaced_atom_ids) == 1
        assert both.informative_atom_ids == one.informative_atom_ids
        assert (
            both.reference_identified_candidate_ids
            == one.reference_identified_candidate_ids
        )


class TestProvenance:
    def test_a_conjunctive_resolution_reports_every_clue_it_rests_on(self):
        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        snapshot = case.snapshot()
        result, decision = adjudicate_fragments(snapshot, case.model_fragments)
        assert decision.outcome == "RESOLVE"

        narration = snapshot.base_evidence.bank_record.narration
        atoms = result.reference_closure.informative_atoms()
        assert len(atoms) == 2
        for atom in atoms:
            assert atom.fragment in narration
            start, end = atom.span
            assert 0 <= start < end <= len(narration)
            assert atom.occurrences
            for offset in atom.occurrences:
                assert narration[offset : offset + len(atom.fragment)] == atom.fragment
            assert atom.reach
            matches = [m for m in atom.matches if m.candidate_id == CANDIDATE_A]
            assert len(matches) == 1
            match = matches[0]
            assert match.relation_id in DEFAULT_POLICY.evidence.accepted_relation_ids
            assert match.pinned_reference_characters >= 4
            assert match.reference_kind in ("utr", "settlement_id")

    def test_the_stage_four_evidence_report_names_each_clue(self):
        """Stage 4 must be able to print the provenance without a model's help."""
        from benchmark.eval.scoring import accepted_relations_for
        from finrecon.stage3 import CaseOutcome

        case = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
        snapshot = case.snapshot()
        evidence = evidence_for(snapshot, case.model_fragments)
        trajectory = trajectory_for(snapshot, evidence=evidence)
        result, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        outcome = CaseOutcome(
            case_id=snapshot.case_id,
            snapshot=snapshot,
            trajectory=trajectory,
            validator_result=result,
            decision=decision,
            cache_key="",
            cache_hit=False,
        )
        relations = accepted_relations_for(outcome)
        assert len(relations) == 2
        for row in relations:
            assert row["fragment"] in snapshot.base_evidence.bank_record.narration
            assert row["atom_id"].startswith("atom:")
            assert len(row["narration_span"]) == 2
            assert row["candidates_reached"] >= 2, (
                "each clue is individually inconclusive; that is the point"
            )
            assert row["pinned_reference_characters"] >= 4

    def test_an_escalation_records_which_state_it_ended_in(self):
        for name, expected in (
            ("cherry_picking", REFERENCE_STATE_CONTRADICTORY),
            ("duplicate_cannot_strengthen", REFERENCE_STATE_AMBIGUOUS),
            ("fabricated_only", REFERENCE_STATE_NO_AGENT_EVIDENCE),
        ):
            case = ADVERSARIAL_BY_NAME[name]
            result, _decision = adjudicate_fragments(
                case.snapshot(), case.model_fragments
            )
            assert result.reference_evidence_state == expected, name

    def test_a_narration_with_no_reference_at_all_reports_no_informative_evidence(self):
        snapshot = snapshot_for(f"NEFT CR-RZRPAY-{SPAN_HINGE}-MUM")
        result = validate_case(
            snapshot=snapshot, evidence=evidence_for(snapshot, (SPAN_HINGE,))
        )
        assert result.reference_evidence_state in (
            REFERENCE_STATE_AMBIGUOUS,
            REFERENCE_STATE_NO_EVIDENCE,
        )


class TestBenchmarkV3IsNotRegressed:
    """The 200 DEV T2 cases are the coverage v2 must not cost."""

    def test_v2_resolves_the_same_dev_cases_v1_did_and_no_others_wrongly(
        self, dev_stage3_result, dev_ground_truth
    ):
        result, _batch, _store = dev_stage3_result
        wrong = []
        for outcome in result.resolved():
            expected = dev_ground_truth[outcome.case_id]["correct_relationship"]
            if expected is None:
                wrong.append((outcome.case_id, "no correct answer"))
            elif (
                tuple(sorted(expected["settlement_ids"]))
                != outcome.decision.resolved_settlement_ids
            ):
                wrong.append((outcome.case_id, "wrong settlement"))
        assert wrong == []

    def test_every_t3_case_still_escalates(self, dev_stage3_result, dev_ground_truth):
        result, _batch, _store = dev_stage3_result
        by_case = {o.case_id: o for o in result.outcomes}
        t3 = [c for c, e in dev_ground_truth.items() if e["tier"] == "T3"]
        assert len(t3) == 40
        for case_id in t3:
            assert by_case[case_id].decision.outcome == "ESCALATE", case_id
            assert (
                by_case[case_id].validator_result.reference_evidence_state
                == REFERENCE_STATE_NO_AGENT_EVIDENCE
            ), case_id

    def test_t2_resolutions_are_single_claim_not_conjunctive(
        self, dev_stage3_result, dev_ground_truth
    ):
        """v3 T2 was built to turn on one recovered reference, and still does.

        A v2 that had quietly started resolving T2 *conjunctively* would mean
        the closure had found corroboration the tier's construction never put
        there -- worth knowing, and it has not happened.
        """
        result, _batch, _store = dev_stage3_result
        conjunctive = [
            o.case_id
            for o in result.resolved()
            if dev_ground_truth[o.case_id]["tier"] == "T2"
            and o.validator_result.resolved_conjunctively
        ]
        assert conjunctive == []
