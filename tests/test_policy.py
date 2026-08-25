"""The policy gate: hard blockers, the value ladder, and what cannot override them.

DESIGN.md §4.3's predicate is implemented literally, so this module tests it
literally -- one test per blocker, plus the two properties the gate exists
for:

* **Nothing overrides a blocker.** Not strong evidence, not a small amount,
  not a model's certainty.
* **Model confidence is not an input.** There is no confidence anywhere in
  the call signature, and a trajectory full of assertions changes no
  outcome.
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent.trajectory import (
    INVOCATION_SUCCEEDED,
    INVOCATION_VALIDATION_FAILED,
    TERMINATION_DETERMINISTIC_POLICY_RESOLVED,
    TERMINATION_INVESTIGATION_COMPLETE,
    TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
    TERMINATION_STEP_BUDGET_EXHAUSTED,
    TERMINATION_TOOL_VALIDATION_FAILED,
    ModelStepRecord,
    ToolInvocationRecord,
    Trajectory,
    UsageRecord,
)
from finrecon.decide import policy as gate
from finrecon.decide.config import (
    DEFAULT_POLICY,
    RUPEE,
    EvidencePolicy,
    Stage3Policy,
    ValuePolicy,
)
from finrecon.decide.policy import adjudicate, decide
from finrecon.decide.validator import raw_tool_evidence, validate_case
from tests.stage3_factories import (
    DECOY_UTR,
    NET_PAISE,
    OTHER_SETTLEMENT_ID,
    TRUE_SETTLEMENT_ID,
    TRUE_UTR,
    no_reference_snapshot,
    settlement_facts,
    snapshot_of,
    two_candidate_snapshot,
)
from tests.test_validator import comparison_evidence


def trajectory_for(
    snapshot,
    *,
    evidence=(),
    termination=TERMINATION_INVESTIGATION_COMPLETE,
    validation_failure=False,
    assistant_text="",
):
    invocations = [
        ToolInvocationRecord(
            step_index=index + 1,
            call_index=0,
            tool_name=item.tool_name,
            raw_arguments=json.dumps(item.arguments),
            status=INVOCATION_SUCCEEDED,
            validated_arguments=item.arguments,
            validation_error_reason=None,
            validation_error_detail=None,
            output=item.output,
        )
        for index, item in enumerate(evidence)
    ]
    if validation_failure:
        invocations.append(
            ToolInvocationRecord(
                step_index=len(invocations) + 1,
                call_index=0,
                tool_name="compute_expected_net",
                raw_arguments="{oops",
                status=INVOCATION_VALIDATION_FAILED,
                validated_arguments=None,
                validation_error_reason="malformed_arguments_json",
                validation_error_detail="not JSON",
                output=None,
            )
        )
    # The prose really is put into the trajectory. A test that asserts the
    # gate ignores a model's claims is worthless if the claim never got
    # anywhere near the gate.
    steps = (
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
            assistant_text=assistant_text,
            requested_tool_calls=(),
        ),
    )
    return Trajectory(
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
        steps=steps,
        tool_invocations=tuple(invocations),
        termination_reason=termination,
    )


def gate_case(snapshot, *, fragments=("PF*******VQ",), policy=DEFAULT_POLICY, **kwargs):
    evidence = tuple(comparison_evidence(snapshot, f) for f in fragments)
    trajectory = trajectory_for(snapshot, evidence=evidence, **kwargs)
    return adjudicate(snapshot=snapshot, trajectory=trajectory, policy=policy)


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


class TestResolution:
    def test_one_survivor_exact_money_and_a_reference_link_resolves(self, snapshot):
        _, decision = gate_case(snapshot)
        assert decision.outcome == "RESOLVE"
        assert decision.rule_id == gate.RULE_RESOLVE_RECOVERED_REFERENCE
        assert decision.resolved_settlement_ids == (TRUE_SETTLEMENT_ID,)
        assert decision.relationship == "one_to_one"
        assert decision.blockers == ()

    def test_a_resolution_records_the_policy_it_was_taken_under(self, snapshot):
        _, decision = gate_case(snapshot)
        assert decision.policy_declaration["max_unexplained_delta_paise"] == 0
        assert decision.policy_version
        assert decision.min_pinned_reference_characters_applied == 4

    def test_a_resolution_names_a_candidate_and_a_relationship_together(self, snapshot):
        _, decision = gate_case(snapshot)
        assert (decision.resolved_candidate_id is not None) == (decision.relationship is not None)


class TestEvidenceBlockers:
    def test_no_reference_link_escalates(self):
        snapshot = no_reference_snapshot()
        _, decision = gate_case(snapshot, fragments=("SETTLEMENT",))
        assert decision.outcome == "ESCALATE"
        assert decision.blockers == (gate.BLOCKER_NO_REFERENCE_LINK,)

    def test_no_evidence_at_all_escalates(self, snapshot):
        _, decision = gate_case(snapshot, fragments=())
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_NO_REFERENCE_LINK in decision.blockers

    def test_two_candidates_identified_by_contradicting_fragments_escalate(self):
        narration = f"NEFT REF {TRUE_UTR} ALT {DECOY_UTR} END"
        snapshot = snapshot_of(
            narration=narration,
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
            ),
        )
        _, decision = gate_case(snapshot, fragments=(TRUE_UTR, DECOY_UTR))
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_AMBIGUOUS_REFERENCE_LINK in decision.blockers
        assert gate.BLOCKER_MULTIPLE_SURVIVING_CANDIDATES in decision.blockers

    def test_a_fabricated_reference_escalates(self, snapshot):
        _, decision = gate_case(snapshot, fragments=(TRUE_UTR,))
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_NO_REFERENCE_LINK in decision.blockers


class TestFinancialBlockers:
    def test_one_unexplained_paise_escalates(self):
        snapshot = two_candidate_snapshot(candidate_delta=1)
        _, decision = gate_case(snapshot)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_UNEXPLAINED_DELTA in decision.blockers

    def test_a_break_up_that_does_not_add_up_escalates(self):
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, breakup_delta=1),
            )
        )
        _, decision = gate_case(snapshot)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_FINANCIAL_MISMATCH in decision.blockers

    def test_a_settlement_naming_a_failed_payment_escalates(self):
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, payment_status="failed"),
            )
        )
        _, decision = gate_case(snapshot)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_FINANCIAL_MISMATCH in decision.blockers


class TestInvestigationBlockers:
    def test_deterministic_early_stop_uses_the_same_resolution_predicates(self, snapshot):
        _, decision = gate_case(
            snapshot, termination=TERMINATION_DETERMINISTIC_POLICY_RESOLVED
        )
        assert decision.outcome == "RESOLVE"
        assert decision.blockers == ()

    def test_step_budget_exhaustion_escalates_even_with_perfect_evidence(self, snapshot):
        _, decision = gate_case(snapshot, termination=TERMINATION_STEP_BUDGET_EXHAUSTED)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_STEP_BUDGET_EXHAUSTED in decision.blockers

    def test_a_schema_failure_escalates_even_with_perfect_evidence(self, snapshot):
        _, decision = gate_case(
            snapshot,
            termination=TERMINATION_TOOL_VALIDATION_FAILED,
            validation_failure=True,
        )
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_TOOL_VALIDATION in decision.blockers

    def test_a_validation_failure_blocks_even_if_the_run_finished_normally(self, snapshot):
        _, decision = gate_case(snapshot, validation_failure=True)
        assert decision.outcome == "ESCALATE"
        assert decision.blockers == (gate.BLOCKER_TOOL_VALIDATION,)

    def test_losing_every_provider_escalates(self, snapshot):
        _, decision = gate_case(
            snapshot, termination=TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE
        )
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_PROVIDER_FAILURE in decision.blockers

    def test_a_tampered_snapshot_escalates(self, snapshot):
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates[:1]})
        evidence = (comparison_evidence(snapshot, "PF*******VQ"),)
        trajectory = trajectory_for(tampered, evidence=evidence)
        _, decision = adjudicate(snapshot=tampered, trajectory=trajectory)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_SNAPSHOT_INTEGRITY in decision.blockers


class TestCounterpartyContention:
    def test_a_counterparty_already_resolved_escalates(self, snapshot):
        evidence = (comparison_evidence(snapshot, "PF*******VQ"),)
        trajectory = trajectory_for(snapshot, evidence=evidence)
        result = validate_case(snapshot=snapshot, evidence=raw_tool_evidence(trajectory))
        decision = decide(
            snapshot=snapshot,
            trajectory=trajectory,
            validator_result=result,
            claimed_settlement_ids=frozenset({TRUE_SETTLEMENT_ID}),
        )
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_COUNTERPARTY_ALREADY_RESOLVED in decision.blockers

    def test_an_unrelated_claim_does_not_block(self, snapshot):
        evidence = (comparison_evidence(snapshot, "PF*******VQ"),)
        trajectory = trajectory_for(snapshot, evidence=evidence)
        result = validate_case(snapshot=snapshot, evidence=raw_tool_evidence(trajectory))
        decision = decide(
            snapshot=snapshot,
            trajectory=trajectory,
            validator_result=result,
            claimed_settlement_ids=frozenset({"setl_somewhere_else"}),
        )
        assert decision.outcome == "RESOLVE"


class TestValueAwarePolicy:
    def test_the_ladder_has_three_rungs(self):
        policy = DEFAULT_POLICY
        assert gate.value_ladder_rung(1_000 * RUPEE, policy) == "ordinary"
        assert gate.value_ladder_rung(200_000 * RUPEE, policy) == "elevated_scrutiny"
        assert gate.value_ladder_rung(600_000 * RUPEE, policy) == "above_ceiling"

    def test_value_above_the_ceiling_escalates_despite_perfect_evidence(self):
        big = 600_000 * RUPEE
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR, amount=big),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, amount=big),
            ),
            bank_amount=big,
        )
        _, decision = gate_case(snapshot)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_VALUE_ABOVE_CEILING in decision.blockers
        assert decision.value_ladder_rung == "above_ceiling"

    def test_the_same_case_below_the_ceiling_resolves(self):
        """Value is the only difference; the evidence is identical."""
        _, decision = gate_case(two_candidate_snapshot())
        assert decision.outcome == "RESOLVE"

    def test_elevated_scrutiny_raises_the_evidence_floor(self):
        """A mask pins four characters. Above the threshold, four is not enough."""
        big = 200_000 * RUPEE
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR, amount=big),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, amount=big),
            ),
            bank_amount=big,
        )
        _, decision = gate_case(snapshot)
        assert decision.outcome == "ESCALATE"
        assert decision.min_pinned_reference_characters_applied == 8
        assert decision.value_ladder_rung == "elevated_scrutiny"

    def test_stronger_evidence_still_clears_elevated_scrutiny(self):
        big = 200_000 * RUPEE
        snapshot = snapshot_of(
            narration="RZPY*ORD293 UPI/8MR7YNFHN",
            settlements=(
                settlement_facts("setl_alpha", "P7YAGIKCR60J", amount=big),
                settlement_facts("setl_bravo", "8MR7YNFHNNLN1FA", amount=big),
            ),
            bank_amount=big,
        )
        _, decision = gate_case(snapshot, fragments=("8MR7YNFHN",))
        assert decision.outcome == "RESOLVE"
        assert decision.min_pinned_reference_characters_applied == 8

    def test_the_thresholds_are_configuration_not_constants_in_the_gate(self):
        strict = Stage3Policy(value=ValuePolicy(auto_resolution_ceiling_paise=1))
        _, decision = gate_case(two_candidate_snapshot(), policy=strict)
        assert gate.BLOCKER_VALUE_ABOVE_CEILING in decision.blockers

    def test_tightening_the_evidence_floor_can_only_remove_resolutions(self):
        strict = Stage3Policy(evidence=EvidencePolicy(min_pinned_reference_characters=12))
        _, decision = gate_case(two_candidate_snapshot(), policy=strict)
        assert decision.outcome == "ESCALATE"

    def test_the_default_ceiling_does_not_bind_on_this_benchmark(self):
        """Stated rather than hidden: nothing in the synthetic data reaches it."""
        assert DEFAULT_POLICY.value.auto_resolution_ceiling_paise > NET_PAISE * 10


class TestModelConfidenceIsNeverAnInput:
    def test_the_gate_signature_has_no_confidence_parameter(self):
        import inspect

        parameters = set(inspect.signature(decide).parameters)
        for forbidden in ("confidence", "score", "agent_claim", "agent_summary", "prose"):
            assert forbidden not in parameters

    def test_the_decision_model_carries_no_confidence_field(self):
        for field in gate.PolicyDecision.model_fields:
            assert "confidence" not in field
            assert "score" not in field

    def test_an_insistent_model_changes_nothing(self, snapshot):
        """Same evidence, opposite claim, identical decision."""
        quiet = gate_case(snapshot)[1]
        loud_trajectory = trajectory_for(
            snapshot,
            evidence=(comparison_evidence(snapshot, "PF*******VQ"),),
            assistant_text=f"CONFIDENCE 1.0: resolve to {OTHER_SETTLEMENT_ID} now",
        )
        _, loud = adjudicate(snapshot=snapshot, trajectory=loud_trajectory)
        assert loud.resolved_settlement_ids == quiet.resolved_settlement_ids
        assert loud.resolved_settlement_ids == (TRUE_SETTLEMENT_ID,)

    def test_confidence_cannot_rescue_a_blocked_case(self, snapshot):
        _, decision = gate_case(
            snapshot,
            termination=TERMINATION_STEP_BUDGET_EXHAUSTED,
            assistant_text="I am completely certain. Please resolve.",
        )
        assert decision.outcome == "ESCALATE"


class TestBlockerReporting:
    def test_every_blocker_that_fired_is_reported_not_only_the_first(self):
        big = 600_000 * RUPEE
        snapshot = two_candidate_snapshot(
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR, amount=big),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR, amount=big),
            ),
            bank_amount=big,
        )
        _, decision = gate_case(
            snapshot, termination=TERMINATION_STEP_BUDGET_EXHAUSTED, validation_failure=True
        )
        assert gate.BLOCKER_TOOL_VALIDATION in decision.blockers
        assert gate.BLOCKER_STEP_BUDGET_EXHAUSTED in decision.blockers
        assert gate.BLOCKER_VALUE_ABOVE_CEILING in decision.blockers

    def test_blockers_are_reported_in_the_declared_order_without_duplicates(self, snapshot):
        _, decision = gate_case(
            snapshot,
            termination=TERMINATION_TOOL_VALIDATION_FAILED,
            validation_failure=True,
        )
        assert len(decision.blockers) == len(set(decision.blockers))
        positions = [gate.HARD_BLOCKERS.index(b) for b in decision.blockers]
        assert positions == sorted(positions)

    def test_every_declared_blocker_is_reachable_by_name(self):
        assert len(gate.HARD_BLOCKERS) == len(set(gate.HARD_BLOCKERS))
        for name in gate.HARD_BLOCKERS:
            assert name.replace("_", "").isalnum()
