"""The bounded loop: its budget, its terminations, and what it refuses to do.

The property worth stating up front, because every test below is a corner of
it: **there is no path through this loop that turns a failure into a
choice.** Running out of steps, receiving a malformed call, being handed a
candidate that does not exist, losing every provider -- each ends in a
recorded termination and an empty-handed trajectory, never in a best guess.
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent.loop import DEFAULT_MAX_STEPS, LoopConfig, run_investigation
from finrecon.agent.providers.base import ProviderInfrastructureError
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.prompt import case_briefing, system_prompt
from finrecon.agent.tools import ToolValidationError
from finrecon.agent.trajectory import (
    INVOCATION_SKIPPED_BATCH_REJECTED,
    INVOCATION_VALIDATION_FAILED,
    TERMINATION_DETERMINISTIC_POLICY_RESOLVED,
    TERMINATION_INVESTIGATION_COMPLETE,
    TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
    TERMINATION_STEP_BUDGET_EXHAUSTED,
    TERMINATION_TOOL_VALIDATION_FAILED,
)
from finrecon.decide import policy as gate
from finrecon.decide.policy import adjudicate
from tests.stage3_factories import (
    DECOY_UTR,
    OTHER_SETTLEMENT_ID,
    TRUE_SETTLEMENT_ID,
    TRUE_UTR,
    no_reference_snapshot,
    settlement_facts,
    snapshot_of,
    two_candidate_snapshot,
)
from tests.stage3_fakes import (
    FailingProvider,
    MechanicalInvestigator,
    ScriptedProvider,
    tool_call,
    turn,
)


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


def chain_of(*turns, repeat_last=False):
    return ProviderChain(
        (ScriptedProvider(turns, provider_id="fake", model="fake-v1", repeat_last=repeat_last),)
    )


def compare_turn(fragment, call_id="call_1"):
    """One comparison call. The tool takes the fragment alone -- there is no
    candidate argument to supply, because the controller fans the fragment
    across the complete snapshot itself."""
    return turn(
        text=f"testing {fragment}",
        calls=[
            tool_call(
                "compare_reference_fragment",
                {"fragment": fragment},
                call_id=call_id,
            )
        ],
    )


class TestOrdinaryCompletion:
    def test_a_model_that_stops_asking_for_tools_completes(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                compare_turn("RTGS"),
                turn(text="tested one fragment; I make no claim"),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_INVESTIGATION_COMPLETE
        assert trajectory.step_count == 2
        assert len(trajectory.successful_tool_invocations()) == 1

    def test_the_trajectory_records_the_raw_tool_output(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                compare_turn("PF*******VQ"), turn(text="done")
            ),
        )
        invocation = trajectory.tool_invocations[0]
        assert invocation.output["fragment"] == "PF*******VQ"
        assert invocation.output["fragment_present_in_narration"] is True
        assert invocation.validated_arguments == {"fragment": "PF*******VQ"}

    def test_provider_and_model_are_recorded_on_every_step(self, snapshot):
        trajectory = run_investigation(snapshot=snapshot, chain=chain_of(turn(text="nothing")))
        assert trajectory.steps[0].provider == "fake"
        assert trajectory.steps[0].model == "fake-v1"
        assert trajectory.models_used == ("fake:fake-v1",)
        assert trajectory.provider_chain == ("fake:fake-v1",)

    def test_versions_and_budget_are_recorded(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot, chain=chain_of(turn()), config=LoopConfig(max_steps=3)
        )
        assert trajectory.prompt_version
        assert trajectory.tool_schema_version
        assert trajectory.agent_loop_version
        assert trajectory.max_steps == 3
        assert trajectory.snapshot_hash == snapshot.content_hash

    def test_token_usage_is_totalled_when_the_provider_reports_it(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(compare_turn("RTGS"), turn()),
        )
        assert trajectory.total_tokens() == 240
        assert trajectory.input_tokens() == 200
        assert trajectory.output_tokens() == 40
        assert trajectory.provider_latency_ms() == 2
        assert trajectory.provider_call_count == 2
        assert trajectory.tool_invocation_count == 1
        assert trajectory.total_latency_ms is not None


class TestStepBudget:
    def test_the_default_budget_is_small_and_fixed(self):
        assert 1 < DEFAULT_MAX_STEPS <= 12

    def test_a_model_that_never_stops_is_stopped(self, snapshot):
        endless = compare_turn("RTGS")
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(endless, repeat_last=True),
            config=LoopConfig(max_steps=4),
        )
        assert trajectory.termination_reason == TERMINATION_STEP_BUDGET_EXHAUSTED
        assert trajectory.step_count == 4
        assert trajectory.budget_exhausted is True

    def test_budget_exhaustion_never_produces_a_choice(self, snapshot):
        """The trajectory carries evidence and a stop reason, never a winner."""
        endless = compare_turn("RTGS")
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(endless, repeat_last=True),
            config=LoopConfig(max_steps=2),
        )
        serialized = json.dumps(trajectory.model_dump(mode="json")).lower()
        for forbidden in ("winner", "chosen_candidate", "resolved", "best_guess"):
            assert forbidden not in serialized

    def test_a_budget_of_one_step_is_legal_and_bounded(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(compare_turn("RTGS"), repeat_last=True),
            config=LoopConfig(max_steps=1),
        )
        assert trajectory.step_count == 1
        assert trajectory.budget_exhausted

    @pytest.mark.parametrize("bad", [0, -1])
    def test_a_nonsense_budget_is_refused(self, bad):
        with pytest.raises(ValueError):
            LoopConfig(max_steps=bad)


class TestMalformedCalls:
    def test_an_unknown_tool_stops_the_loop_and_is_recorded(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(calls=[tool_call("recover_correct_settlement", {})]),
                turn(text="never reached"),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.step_count == 1
        assert trajectory.had_validation_failure
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.UNKNOWN_TOOL
        )

    def test_unparsable_arguments_stop_the_loop(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(turn(calls=[tool_call("compute_expected_net", "{oops")]), turn()),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.MALFORMED_ARGUMENTS_JSON
        )

    def test_a_duplicate_key_call_stops_the_loop_and_executes_no_tool(self, snapshot):
        """{"candidate_id":"A","candidate_id":"B"} must never resolve to just B."""
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call(
                            "compute_expected_net",
                            '{"candidate_id":"A","candidate_id":"B"}',
                        )
                    ]
                ),
                turn(text="never reached"),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.step_count == 1
        assert trajectory.had_validation_failure
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.DUPLICATE_ARGUMENT_KEY
        )
        assert trajectory.tool_invocations[0].validated_arguments is None
        assert trajectory.tool_invocations[0].output is None

    def test_a_duplicate_nested_key_call_stops_the_loop(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call(
                            "compare_reference_fragment",
                            '{"fragment":"PF","extra":{"nested":1,"nested":2}}',
                        )
                    ]
                ),
                turn(),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.DUPLICATE_ARGUMENT_KEY
        )

    def test_duplicate_key_failure_does_not_trigger_provider_fallback(self, snapshot):
        """No retry through a second provider -- this is the model's behaviour, not
        an infrastructure fault, so re-rolling it elsewhere is never attempted."""
        primary = ScriptedProvider(
            [
                turn(
                    calls=[
                        tool_call(
                            "compute_expected_net",
                            '{"candidate_id":"A","candidate_id":"B"}',
                        )
                    ]
                )
            ],
            provider_id="openrouter",
            model="o",
        )
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((primary, secondary))
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert secondary.call_count == 0
        assert trajectory.steps[0].fallback_used is False

    def test_duplicate_key_failure_is_recorded_without_any_provider_credential(
        self, snapshot, monkeypatch
    ):
        """The failure is caught before a live provider is ever needed -- no
        credential in the environment is required to detect or record it."""
        for env_var in ("OPENROUTER_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(env_var, raising=False)
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call(
                            "compute_expected_net",
                            '{"candidate_id":"A","candidate_id":"B"}',
                        )
                    ]
                )
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.DUPLICATE_ARGUMENT_KEY
        )

    def test_a_hallucinated_candidate_id_stops_the_loop(self, snapshot):
        """Access control lives on the tools that still name a record.

        ``compare_reference_fragment`` has no candidate argument to forge any
        more; ``lookup_candidate_records`` does, and it is still checked
        against the immutable snapshot before any handler runs.
        """
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call(
                            "lookup_candidate_records",
                            {"candidate_id": "setl_invented"},
                        )
                    ]
                ),
                turn(),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.tool_invocations[0].validation_error_reason == (
            ToolValidationError.UNKNOWN_CANDIDATE
        )
        assert trajectory.tool_invocations[0].output is None

    def test_a_refused_call_records_the_arguments_the_model_actually_sent(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(turn(calls=[tool_call("compute_expected_net", '{"candidate_id": 5}')])),
        )
        assert trajectory.tool_invocations[0].raw_arguments == '{"candidate_id": 5}'
        assert trajectory.tool_invocations[0].validated_arguments is None

    def test_no_second_provider_is_tried_after_a_malformed_call(self, snapshot):
        primary = ScriptedProvider(
            [turn(calls=[tool_call("compute_expected_net", "{oops")])],
            provider_id="openrouter",
            model="o",
        )
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((primary, secondary))
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert secondary.call_count == 0


class TestRepeatedAndExcessCalls:
    def test_the_same_call_twice_is_executed_and_recorded_twice(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                compare_turn("RTGS", call_id="a"),
                compare_turn("RTGS", call_id="b"),
                turn(text="done"),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_INVESTIGATION_COMPLETE
        assert len(trajectory.tool_invocations) == 2

    def test_two_valid_calls_in_one_turn_are_both_executed_and_recorded(self, snapshot):
        a, b = snapshot.candidate_ids()
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call("compute_expected_net", {"candidate_id": a}, call_id="1"),
                        tool_call("compute_expected_net", {"candidate_id": b}, call_id="2"),
                    ]
                ),
                turn(text="done"),
            ),
        )
        assert len(trajectory.steps[0].requested_tool_calls) == 2, "the request is recorded"
        assert len(trajectory.successful_tool_invocations()) == 2
        assert [inv.validated_arguments["candidate_id"] for inv in trajectory.tool_invocations] == [
            a,
            b,
        ]
        assert [inv.call_index for inv in trajectory.tool_invocations] == [0, 1]

    def test_batch_execution_order_is_deterministic(self, snapshot):
        a, b = snapshot.candidate_ids()

        def run_once():
            return run_investigation(
                snapshot=snapshot,
                chain=chain_of(
                    turn(
                        calls=[
                            tool_call("lookup_candidate_records", {"candidate_id": b}, call_id="2"),
                            tool_call("lookup_candidate_records", {"candidate_id": a}, call_id="1"),
                        ]
                    ),
                    turn(text="done"),
                ),
            )

        first = run_once()
        second = run_once()
        assert [i.output["candidate_id"] for i in first.tool_invocations] == [b, a]
        assert [i.output["candidate_id"] for i in second.tool_invocations] == [b, a]

    def test_one_invalid_call_rejects_the_whole_batch_before_execution(
        self, snapshot, monkeypatch
    ):
        a = snapshot.candidate_ids()[0]

        def must_not_execute(*_args, **_kwargs):
            raise AssertionError("a mixed-invalid batch must execute no handler")

        monkeypatch.setattr("finrecon.agent.loop.execute_prepared", must_not_execute)
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call("compute_expected_net", {"candidate_id": a}, call_id="valid"),
                        tool_call(
                            "compute_expected_net",
                            '{"candidate_id":"A","candidate_id":"B"}',
                            call_id="duplicate",
                        ),
                    ]
                )
            ),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.successful_tool_invocations() == ()
        # Every requested call is written down, in request order: the valid
        # one as skipped, the malformed one as the failure that rejected the
        # batch. Neither executed.
        assert [inv.call_index for inv in trajectory.tool_invocations] == [0, 1]
        assert [inv.status for inv in trajectory.tool_invocations] == [
            INVOCATION_SKIPPED_BATCH_REJECTED,
            INVOCATION_VALIDATION_FAILED,
        ]
        assert trajectory.tool_invocations[1].validation_error_reason == (
            ToolValidationError.DUPLICATE_ARGUMENT_KEY
        )
        assert trajectory.tool_invocations[0].validated_arguments == {"candidate_id": a}
        assert trajectory.tool_invocations[0].output is None

    def test_a_batch_over_the_bound_is_rejected_not_partially_executed(self, snapshot):
        a, b = snapshot.candidate_ids()
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call("compute_expected_net", {"candidate_id": a}, call_id="1"),
                        tool_call("compute_expected_net", {"candidate_id": b}, call_id="2"),
                    ]
                )
            ),
            config=LoopConfig(max_tool_calls_per_step=1),
        )
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.successful_tool_invocations() == ()
        assert [inv.call_index for inv in trajectory.tool_invocations] == [0, 1]
        assert [inv.status for inv in trajectory.tool_invocations] == [
            INVOCATION_SKIPPED_BATCH_REJECTED,
            INVOCATION_VALIDATION_FAILED,
        ]
        assert trajectory.tool_invocations[1].validation_error_reason == (
            ToolValidationError.TOOL_CALL_BATCH_LIMIT_EXCEEDED
        )


class TestDeterministicEarlyStop:
    def test_existing_validator_and_policy_stop_before_an_extra_model_turn(self, snapshot):
        """One comparison call is now enough to reach a deterministic resolution.

        Under the old per-candidate signature this took two calls for a
        two-candidate case. The second added nothing the validator used, and
        it is no longer expressible.
        """
        provider = ScriptedProvider(
            [
                turn(
                    calls=[
                        tool_call(
                            "compare_reference_fragment",
                            {"fragment": "PF*******VQ"},
                            call_id="a",
                        ),
                    ]
                ),
                turn(text="redundant and must not be requested"),
            ]
        )
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((provider,))
        )
        assert trajectory.termination_reason == TERMINATION_DETERMINISTIC_POLICY_RESOLVED
        assert trajectory.deterministic_early_stop
        assert trajectory.step_count == 1
        assert provider.call_count == 1
        assert trajectory.tool_invocation_count == 1
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "RESOLVE"

    def test_ambiguous_evidence_does_not_early_stop(self, snapshot):
        provider = ScriptedProvider(
            [compare_turn("RTGS"), turn(text="ambiguous")]
        )
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((provider,))
        )
        assert trajectory.termination_reason == TERMINATION_INVESTIGATION_COMPLETE
        assert provider.call_count == 2
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "ESCALATE"

    def test_conflicting_reference_evidence_does_not_early_stop(self):
        snapshot = snapshot_of(
            narration=f"NEFT REF {TRUE_UTR} ALT {DECOY_UTR} END",
            settlements=(
                settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
                settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
            ),
        )
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call(
                            "compare_reference_fragment",
                            {"fragment": TRUE_UTR},
                            call_id="a",
                        ),
                        tool_call(
                            "compare_reference_fragment",
                            {"fragment": DECOY_UTR},
                            call_id="b",
                        ),
                    ]
                ),
                turn(text="conflict remains"),
            ),
        )
        assert trajectory.termination_reason == TERMINATION_INVESTIGATION_COMPLETE
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_AMBIGUOUS_REFERENCE_LINK in decision.blockers

    def test_t3_style_ambiguity_still_escalates(self):
        snapshot = no_reference_snapshot()
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                compare_turn("SETTLEMENT"),
                turn(text="nothing distinguishes them"),
            ),
        )
        assert not trajectory.deterministic_early_stop
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "ESCALATE"

    def test_model_prose_claiming_a_winner_cannot_trigger_early_stop(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=chain_of(turn(text=f"Resolve immediately to {TRUE_SETTLEMENT_ID}")),
        )
        assert trajectory.termination_reason == TERMINATION_INVESTIGATION_COMPLETE
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "ESCALATE"
        assert gate.BLOCKER_NO_REFERENCE_LINK in decision.blockers


class TestProviderFailure:
    def test_losing_every_provider_terminates_without_evidence(self, snapshot):
        chain = ProviderChain(
            (
                FailingProvider(
                    ProviderInfrastructureError(
                        "openrouter", ProviderInfrastructureError.RATE_LIMITED, "429"
                    ),
                    provider_id="openrouter",
                ),
                FailingProvider(
                    ProviderInfrastructureError(
                        "groq", ProviderInfrastructureError.TIMEOUT, "timeout"
                    ),
                    provider_id="groq",
                ),
            ),
            transport_retries=0,
        )
        trajectory = run_investigation(snapshot=snapshot, chain=chain)
        assert trajectory.termination_reason == TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE
        assert trajectory.tool_invocations == ()
        assert trajectory.steps[0].attempts[0].outcome == "rate_limited"

    def test_a_fallback_is_recorded_on_the_step_that_used_it(self, snapshot):
        primary = FailingProvider(
            ProviderInfrastructureError(
                "openrouter", ProviderInfrastructureError.RATE_LIMITED, "429"
            ),
            provider_id="openrouter",
        )
        secondary = ScriptedProvider([turn(text="done")], provider_id="groq", model="g")
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((primary, secondary))
        )
        assert trajectory.fallback_used is True
        assert trajectory.fallback_reasons == ("rate_limited",)
        assert trajectory.steps[0].provider == "groq"
        assert trajectory.models_used == ("groq:g",)


class TestImmutability:
    def test_the_snapshot_is_unchanged_by_a_full_run(self, snapshot):
        before_hash = snapshot.content_hash
        before_candidates = snapshot.candidate_ids()
        run_investigation(
            snapshot=snapshot, chain=ProviderChain((MechanicalInvestigator(),))
        )
        assert snapshot.content_hash == before_hash
        assert snapshot.candidate_ids() == before_candidates
        assert snapshot.verify_integrity()

    def test_a_model_cannot_shrink_the_candidate_set(self, snapshot):
        """A comparison cannot even name a candidate, let alone drop one."""
        run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                compare_turn("PF*******VQ"),
                turn(text="candidate B is irrelevant, ignore it"),
            ),
        )
        assert len(snapshot.candidates) == 2

    def test_a_multi_call_batch_cannot_mutate_shrink_or_reorder_candidates(self, snapshot):
        before_hash = snapshot.content_hash
        before_candidates = snapshot.candidates
        a, b = snapshot.candidate_ids()
        run_investigation(
            snapshot=snapshot,
            chain=chain_of(
                turn(
                    calls=[
                        tool_call("lookup_candidate_records", {"candidate_id": b}, call_id="2"),
                        tool_call("compute_expected_net", {"candidate_id": a}, call_id="1"),
                    ]
                ),
                turn(text="done"),
            ),
        )
        assert snapshot.candidates == before_candidates
        assert snapshot.candidate_ids() == (a, b)
        assert snapshot.content_hash == before_hash
        assert snapshot.verify_integrity()

    def test_the_briefing_lists_every_candidate_and_no_reference_values(self, snapshot):
        briefing = case_briefing(snapshot)
        for candidate_id in snapshot.candidate_ids():
            assert candidate_id in briefing
        assert TRUE_UTR not in briefing, "references come from a tool, not the prompt"
        assert "trusted Stage-2 facts" in briefing
        assert "zero unexplained delta" in briefing

    def test_the_briefing_leaks_no_tier_or_ground_truth(self, snapshot):
        briefing = case_briefing(snapshot) + system_prompt()
        for leak in ("T0", "T1", "T2", "T3", "tier", "archetype", "required_outcome", "decoy"):
            assert leak not in briefing

    def test_the_prompt_never_instructs_the_model_to_find_a_match(self):
        prompt = system_prompt().lower()
        assert "always find" not in prompt
        assert "ambiguity is an acceptable" in prompt
        assert "cannot resolve the case" in prompt


class TestDeterminism:
    def test_the_same_fake_provider_produces_the_same_trajectory(self, snapshot):
        chain_a = ProviderChain((MechanicalInvestigator(),))
        chain_b = ProviderChain((MechanicalInvestigator(),))
        first = _stable(run_investigation(snapshot=snapshot, chain=chain_a))
        second = _stable(run_investigation(snapshot=snapshot, chain=chain_b))
        assert first == second

    def test_the_mechanical_investigator_finds_the_masked_fragment(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((MechanicalInvestigator(),))
        )
        fragments = [
            inv.output["fragment"]
            for inv in trajectory.successful_tool_invocations()
            if inv.tool_name == "compare_reference_fragment"
        ]
        assert "PF*******VQ" in fragments


def _stable(trajectory) -> str:
    """Trajectory JSON minus wall-clock fields, which are not part of the record."""
    payload = trajectory.model_dump(mode="json")
    payload["total_latency_ms"] = None
    for step in payload["steps"]:
        step["latency_ms"] = None
    for invocation in payload["tool_invocations"]:
        invocation["latency_ms"] = None
    return json.dumps(payload, sort_keys=True)
