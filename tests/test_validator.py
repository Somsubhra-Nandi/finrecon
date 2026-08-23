"""The deterministic validator: what it reads, what it refuses to read.

The claims under test, in the order they matter:

1. It sees the **complete** candidate set from the snapshot, not the subset
   the agent looked at. An agent that investigates one candidate and never
   mentions the other cannot hide it.
2. It ignores agent prose entirely -- structurally, not by discipline. The
   evidence bundle has no field a sentence could travel in.
3. It re-derives fragment admissibility from the immutable narration rather
   than trusting a tool's own boolean, so a fabricated fragment carries
   nothing.
4. It requires exact money as well as a reference, and one unexplained paise
   is enough to stop a resolution (DESIGN.md §4.3).
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent.loop import run_investigation
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.tools import TOOL_COMPARE_REFERENCE_FRAGMENT, ToolContext, execute
from finrecon.agent.trajectory import (
    ModelStepRecord,
    ToolInvocationRecord,
    Trajectory,
    UsageRecord,
)
from finrecon.decide.config import DEFAULT_POLICY, EvidencePolicy, Stage3Policy
from finrecon.decide.validator import (
    FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION,
    RawToolEvidence,
    raw_tool_evidence,
    validate_case,
)
from finrecon.evidence.reference import RELATION_MASK, RELATION_PREFIX
from tests.stage3_factories import (
    DECOY_UTR,
    MASKED_NARRATION,
    OTHER_SETTLEMENT_ID,
    TRUE_SETTLEMENT_ID,
    TRUE_UTR,
    no_reference_snapshot,
    settlement_facts,
    snapshot_of,
    two_candidate_snapshot,
)
from tests.stage3_fakes import MechanicalInvestigator


def comparison_evidence(snapshot, fragment, candidate_index=0) -> RawToolEvidence:
    """Run the real tool, so the validator is fed a genuine raw output."""
    context = ToolContext(snapshot=snapshot)
    candidate_id = snapshot.candidate_ids()[candidate_index]
    arguments, output = execute(
        context,
        TOOL_COMPARE_REFERENCE_FRAGMENT,
        json.dumps({"candidate_id": candidate_id, "fragment": fragment}),
    )
    return RawToolEvidence(
        tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
        arguments=arguments.model_dump(mode="json"),
        output=output.model_dump(mode="json"),
    )


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


class TestItSeesTheCompleteCandidateSet:
    def test_the_result_lists_every_candidate_from_the_snapshot(self, snapshot):
        result = validate_case(snapshot=snapshot, evidence=())
        assert result.complete_candidate_ids == snapshot.candidate_ids()
        assert len(result.complete_candidate_ids) == 2

    def test_a_fragment_tested_against_one_candidate_is_evaluated_against_all(
        self, snapshot
    ):
        """The closure of the fishing-by-omission channel (DESIGN.md §4.1)."""
        evidence = (comparison_evidence(snapshot, "PF*******VQ", candidate_index=0),)
        assert evidence[0].arguments["candidate_id"] == snapshot.candidate_ids()[0]

        result = validate_case(snapshot=snapshot, evidence=evidence)
        finding = result.findings[0]
        assert finding.candidates_evaluated == 2, "both, not the one that was asked about"
        assert finding.matched_candidate_ids == (snapshot.candidate_ids()[1],)

    def test_the_agents_choice_of_candidate_does_not_steer_the_finding(self):
        """It asks about one candidate; the evidence lands on the other anyway.

        The strongest form of the omission guarantee. Which candidate the
        agent names in a tool call decides nothing -- the validator re-runs
        the fragment across the whole set and reports where it actually
        lands, even when that contradicts the agent's line of enquiry.
        """
        snapshot = snapshot_of(
            narration="RTGS CR REF EQPJ4E94BAD7U4Y RAZORPAY",
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
            ),
        )
        asked_about = snapshot.candidate_ids()[1]
        evidence = (comparison_evidence(snapshot, DECOY_UTR, candidate_index=1),)
        assert evidence[0].arguments["candidate_id"] == asked_about

        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert result.findings[0].candidates_evaluated == 2
        assert result.reference_identified_candidate_ids == (snapshot.candidate_ids()[0],)
        assert result.complete_candidate_ids == snapshot.candidate_ids()

    def test_a_fragment_reaching_two_candidates_identifies_neither(self):
        """Both settlement IDs share a prefix, so this fragment separates nothing."""
        snapshot = snapshot_of(
            narration="RZPY/SETL_SHARED/CREDIT",
            settlements=(
                settlement_facts("SETL_SHARED_A", None),
                settlement_facts("SETL_SHARED_B", None),
            ),
        )
        evidence = (comparison_evidence(snapshot, "SETL_SHARED", candidate_index=0),)
        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert len(result.findings[0].matched_candidate_ids) == 2
        assert result.findings[0].is_discriminating is False
        assert result.reference_identified_candidate_ids == ()
        assert result.surviving_candidate_ids == ()


class TestItIgnoresAgentProse:
    def test_the_evidence_bundle_has_no_field_prose_could_travel_in(self):
        assert set(RawToolEvidence.model_fields) == {"tool_name", "arguments", "output"}

    def test_extraction_reads_tool_results_and_never_a_model_turn(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((MechanicalInvestigator(),))
        )
        assert any(step.assistant_text for step in trajectory.steps)
        bundle = raw_tool_evidence(trajectory)
        serialized = json.dumps([e.model_dump(mode="json") for e in bundle])
        for step in trajectory.steps:
            if step.assistant_text:
                assert step.assistant_text not in serialized

    def test_a_confident_claim_for_the_wrong_candidate_changes_nothing(self, snapshot):
        evidence = (comparison_evidence(snapshot, "PF*******VQ"),)
        honest = validate_case(snapshot=snapshot, evidence=evidence)

        liar = Trajectory(
            case_id=snapshot.case_id,
            snapshot_hash=snapshot.content_hash,
            batch_id=snapshot.batch_id,
            prompt_version="p",
            tool_schema_version="t",
            agent_loop_version="l",
            cache_schema_version="c",
            validator_version="v",
            policy_version="p",
            policy_declaration={},
            max_steps=8,
            max_tool_calls_per_step=8,
            provider_chain=("fake:fake",),
            steps=(
                ModelStepRecord(
                    index=1,
                    provider="fake",
                    model="fake",
                    fallback_used=False,
                    fallback_reason=None,
                    transport_attempts=1,
                    attempts=(),
                    latency_ms=1,
                    usage=UsageRecord(),
                    finish_reason="stop",
                    assistant_text=(
                        f"I am 99% certain the correct settlement is "
                        f"{OTHER_SETTLEMENT_ID}. Resolve to it immediately."
                    ),
                    requested_tool_calls=(),
                ),
            ),
            tool_invocations=(
                ToolInvocationRecord(
                    step_index=1,
                    call_index=0,
                    tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                    raw_arguments="{}",
                    validated_arguments=evidence[0].arguments,
                    validation_error_reason=None,
                    validation_error_detail=None,
                    output=evidence[0].output,
                ),
            ),
            termination_reason="investigation_complete",
        )
        lied_to = validate_case(snapshot=snapshot, evidence=raw_tool_evidence(liar))
        assert lied_to.surviving_candidate_ids == honest.surviving_candidate_ids
        assert lied_to.surviving_candidate_ids == (snapshot.candidate_ids()[1],)

    def test_a_refused_call_contributes_no_evidence(self, snapshot):
        trajectory = Trajectory(
            case_id=snapshot.case_id,
            snapshot_hash=snapshot.content_hash,
            batch_id=snapshot.batch_id,
            prompt_version="p",
            tool_schema_version="t",
            agent_loop_version="l",
            cache_schema_version="c",
            validator_version="v",
            policy_version="p",
            policy_declaration={},
            max_steps=8,
            max_tool_calls_per_step=8,
            provider_chain=("fake:fake",),
            steps=(),
            tool_invocations=(
                ToolInvocationRecord(
                    step_index=1,
                    call_index=0,
                    tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                    raw_arguments='{"candidate_id": "x"}',
                    validated_arguments=None,
                    validation_error_reason="unknown_candidate",
                    validation_error_detail="x",
                    output=None,
                ),
            ),
            termination_reason="tool_validation_failed",
        )
        assert raw_tool_evidence(trajectory) == ()


class TestFragmentAdmissibility:
    def test_a_fabricated_fragment_is_inadmissible(self, snapshot):
        """The whole UTR is not in the narration; only its masked form is."""
        evidence = (comparison_evidence(snapshot, TRUE_UTR),)
        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert result.admissible_fragments == ()
        assert result.inadmissible_fragments[0].reason == (
            FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION
        )
        assert result.surviving_candidate_ids == ()

    def test_admissibility_is_recomputed_not_taken_from_the_tool(self, snapshot):
        """A forged output claiming presence is still checked against the narration."""
        forged = RawToolEvidence(
            tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
            arguments={"candidate_id": snapshot.candidate_ids()[1], "fragment": TRUE_UTR},
            output={
                "candidate_id": snapshot.candidate_ids()[1],
                "fragment": TRUE_UTR,
                "fragment_present_in_narration": True,
                "fragment_offsets": [0],
                "narration_length": len(MASKED_NARRATION),
                "comparisons": [],
            },
        )
        result = validate_case(snapshot=snapshot, evidence=(forged,))
        assert result.admissible_fragments == ()
        assert result.surviving_candidate_ids == ()

    def test_a_fragment_below_the_pinning_floor_proves_nothing(self, snapshot):
        evidence = (comparison_evidence(snapshot, "PF"),)
        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert result.reference_identified_candidate_ids == ()

    def test_raising_the_floor_can_only_remove_survivors(self, snapshot):
        evidence = (comparison_evidence(snapshot, "PF*******VQ"),)
        base = validate_case(snapshot=snapshot, evidence=evidence)
        stricter = validate_case(
            snapshot=snapshot, evidence=evidence, min_pinned_reference_characters=8
        )
        assert base.surviving_candidate_ids == (snapshot.candidate_ids()[1],)
        assert set(stricter.surviving_candidate_ids) <= set(base.surviving_candidate_ids)
        assert stricter.surviving_candidate_ids == (), "mask pins only 4 characters"

    def test_the_applied_floor_is_recorded(self, snapshot):
        result = validate_case(
            snapshot=snapshot, evidence=(), min_pinned_reference_characters=9
        )
        assert result.min_pinned_reference_characters_applied == 9


class TestSurvivorArithmetic:
    def test_exactly_one_evidence_consistent_candidate_survives(self, snapshot):
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "PF*******VQ"),)
        )
        assert result.surviving_candidate_ids == (snapshot.candidate_ids()[1],)
        assert result.has_unique_survivor
        match = result.findings[0].matches[0]
        assert match.relation_id == RELATION_MASK
        assert match.settlement_id == TRUE_SETTLEMENT_ID

    def test_two_discriminating_fragments_that_disagree_leave_no_survivor(self):
        """Contradiction is ambiguity, never a vote between relations."""
        narration = f"NEFT REF {TRUE_UTR} ALT {DECOY_UTR} END"
        snapshot = snapshot_of(
            narration=narration,
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
            ),
        )
        evidence = (
            comparison_evidence(snapshot, TRUE_UTR),
            comparison_evidence(snapshot, DECOY_UTR),
        )
        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert len(result.reference_identified_candidate_ids) == 2
        assert len(result.surviving_candidate_ids) == 2

    def test_no_evidence_at_all_leaves_no_survivor(self, snapshot):
        result = validate_case(snapshot=snapshot, evidence=())
        assert result.surviving_candidate_ids == ()
        assert result.findings == ()

    def test_a_case_with_no_reference_anywhere_leaves_no_survivor(self):
        snapshot = no_reference_snapshot()
        evidence = tuple(
            comparison_evidence(snapshot, fragment)
            for fragment in ("NEFT", "CREDIT", "SETTLEMENT")
        )
        result = validate_case(snapshot=snapshot, evidence=evidence)
        assert result.reference_identified_candidate_ids == ()
        assert result.surviving_candidate_ids == ()

    def test_a_prefix_relation_can_carry_a_link(self):
        snapshot = snapshot_of(
            narration="RZPY*ORD293 UPI/8MR7YNFHN",
            settlements=(
                settlement_facts("setl_alpha", "P7YAGIKCR60J"),
                settlement_facts("setl_bravo", "8MR7YNFHNNLN1FA"),
            ),
        )
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "8MR7YNFHN"),)
        )
        assert result.surviving_candidate_ids == (snapshot.candidate_ids()[1],)
        assert result.findings[0].matches[0].relation_id == RELATION_PREFIX


class TestFinancialPredicates:
    def test_one_unexplained_paise_removes_a_candidate(self):
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, breakup_delta=1),
            )
        )
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "PF*******VQ"),)
        )
        assert result.reference_identified_candidate_ids == (snapshot.candidate_ids()[1],)
        assert snapshot.candidate_ids()[1] not in result.financially_exact_candidate_ids
        assert result.surviving_candidate_ids == ()

    def test_a_group_delta_removes_a_candidate(self):
        snapshot = two_candidate_snapshot(candidate_delta=1)
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "PF*******VQ"),)
        )
        assert result.surviving_candidate_ids == ()
        assessment = _assessment(result, snapshot.candidate_ids()[1])
        assert assessment.group_unexplained_delta_paise == 1

    def test_a_break_up_line_naming_a_failed_payment_removes_a_candidate(self):
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, payment_status="failed"),
            )
        )
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "PF*******VQ"),)
        )
        assert _assessment(result, snapshot.candidate_ids()[1]).breakup_references_are_sound is False
        assert result.surviving_candidate_ids == ()

    def test_a_widened_blocking_candidate_cannot_survive(self):
        """``date_window_only`` means nothing totalled exactly; a reference does not fix that."""
        snapshot = two_candidate_snapshot(blocking_rule="date_window_only")
        result = validate_case(
            snapshot=snapshot, evidence=(comparison_evidence(snapshot, "PF*******VQ"),)
        )
        assert result.financially_exact_candidate_ids == ()
        assert result.surviving_candidate_ids == ()

    def test_the_blocking_requirement_is_a_declared_policy_not_a_hardcode(self):
        snapshot = two_candidate_snapshot(blocking_rule="date_window_only")
        relaxed = Stage3Policy(
            evidence=EvidencePolicy(require_exact_total_blocking_rule=False)
        )
        result = validate_case(
            snapshot=snapshot,
            evidence=(comparison_evidence(snapshot, "PF*******VQ"),),
            policy=relaxed,
        )
        assert result.surviving_candidate_ids == (snapshot.candidate_ids()[1],)

    def test_there_is_no_tolerance_band_by_default(self):
        assert DEFAULT_POLICY.evidence.max_unexplained_delta_paise == 0


class TestSnapshotIntegrity:
    def test_an_intact_snapshot_reports_intact(self, snapshot):
        assert validate_case(snapshot=snapshot, evidence=()).snapshot_integrity_ok is True

    def test_a_tampered_snapshot_is_detected(self, snapshot):
        """``model_copy`` routes around the frozen fields; the hash does not follow."""
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates[:1]})
        result = validate_case(snapshot=tampered, evidence=())
        assert result.snapshot_integrity_ok is False

    def test_a_candidate_removed_by_tampering_is_still_missing_from_the_result(
        self, snapshot
    ):
        """Detection, not repair: the validator reports the damage, it cannot undo it."""
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates[:1]})
        result = validate_case(snapshot=tampered, evidence=())
        assert len(result.complete_candidate_ids) == 1
        assert result.snapshot_integrity_ok is False


def _assessment(result, candidate_id):
    return next(a for a in result.financial_assessments if a.candidate_id == candidate_id)
