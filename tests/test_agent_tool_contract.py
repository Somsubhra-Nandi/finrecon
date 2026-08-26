"""The Stage-3 tool boundary after candidate fan-out was taken off the model.

Three claims, and the middle one is the reason the other two exist.

1. **The comparison tool takes a fragment and nothing else.** There is no
   ``candidate_id`` to supply, to forge, or to duplicate. Choosing *which*
   literal evidence to test is the agent's contribution; enumerating the
   candidate set never was.
2. **The controller fans that fragment across the complete snapshot.** Every
   candidate, in snapshot order, none omitted, none ranked. This is the same
   fan-out :mod:`finrecon.decide.validator` already performed on its own, so
   moving it into the tool moved no authority anywhere.
3. **Every requested call is written down**, including the ones a rejected
   batch stopped from running. Atomic reject-all is unchanged; what changed
   is that the calls it discards are now visible instead of absent.

Also here: the fresh-import tests that pin the circular-import fix, because
the early-adjudication boundary in the loop is what made them necessary.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from finrecon.agent.cache import TrajectoryCache, cache_key
from finrecon.agent.loop import run_investigation
from finrecon.agent.prompt import system_prompt
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.tools import (
    TOOL_COMPARE_REFERENCE_FRAGMENT,
    TOOL_COMPUTE_EXPECTED_NET,
    TOOL_INSPECT_SETTLEMENT_BREAKUP,
    TOOL_LOOKUP_CANDIDATE_RECORDS,
    TOOLS_BY_NAME,
    ToolContext,
    ToolValidationError,
    execute,
    tool_specs,
)
from finrecon.agent.trajectory import (
    INVOCATION_SKIPPED_BATCH_REJECTED,
    INVOCATION_STATUSES,
    INVOCATION_SUCCEEDED,
    INVOCATION_VALIDATION_FAILED,
    TERMINATION_TOOL_VALIDATION_FAILED,
    ToolInvocationRecord,
)
from finrecon.decide.policy import adjudicate
from finrecon.decide.validator import raw_tool_evidence
from finrecon.evidence.reference import REFERENCE_KINDS, compare
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
from tests.stage3_fakes import ScriptedProvider, tool_call, turn


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


def three_candidate_snapshot():
    """Three candidates, only one of which the masked fragment can reach.

    Two candidates is the benchmark's shape and would let an off-by-one
    fan-out pass by accident. Three does not.
    """
    return snapshot_of(
        narration=MASKED_NARRATION,
        settlements=(
            settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR),
            settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR),
            settlement_facts("setl_charlie", "ZZ9QQ4TT7WW"),
        ),
    )


class TestFragmentOnlyComparisonContract:
    def test_the_comparison_tool_takes_exactly_one_literal_fragment(self):
        schema = TOOLS_BY_NAME[TOOL_COMPARE_REFERENCE_FRAGMENT].input_model.model_json_schema()
        assert set(schema["properties"]) == {"fragment"}
        assert schema["required"] == ["fragment"]
        assert schema["properties"]["fragment"]["type"] == "string"
        assert schema["additionalProperties"] is False

    def test_candidate_id_is_no_longer_accepted_by_the_comparison_tool(self, snapshot):
        context = ToolContext(snapshot=snapshot)
        with pytest.raises(ToolValidationError) as exc:
            execute(
                context,
                TOOL_COMPARE_REFERENCE_FRAGMENT,
                json.dumps(
                    {"candidate_id": snapshot.candidate_ids()[0], "fragment": "PF*******VQ"}
                ),
            )
        assert exc.value.reason == ToolValidationError.SCHEMA_VALIDATION_FAILED

    def test_a_duplicate_fragment_key_is_still_refused(self, snapshot):
        """The remaining single field cannot be duplicated either."""
        context = ToolContext(snapshot=snapshot)
        with pytest.raises(ToolValidationError) as exc:
            execute(
                context,
                TOOL_COMPARE_REFERENCE_FRAGMENT,
                '{"fragment":"PF*******VQ","fragment":"RTGS"}',
            )
        assert exc.value.reason == ToolValidationError.DUPLICATE_ARGUMENT_KEY

    def test_a_fragment_must_still_be_literally_present_to_carry_evidence(self, snapshot):
        """Presence is reported, and the validator re-derives it from the snapshot."""
        context = ToolContext(snapshot=snapshot)
        _, absent = execute(
            context, TOOL_COMPARE_REFERENCE_FRAGMENT, json.dumps({"fragment": TRUE_UTR})
        )
        _, present = execute(
            context,
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        assert absent.fragment_present_in_narration is False
        assert present.fragment_present_in_narration is True

    @pytest.mark.parametrize(
        ("tool_name", "field"),
        [
            (TOOL_LOOKUP_CANDIDATE_RECORDS, "candidate_id"),
            (TOOL_COMPUTE_EXPECTED_NET, "candidate_id"),
            (TOOL_INSPECT_SETTLEMENT_BREAKUP, "settlement_id"),
        ],
    )
    def test_the_record_reading_tools_stay_scalar(self, tool_name, field):
        """Targeted reads keep their identifier. That is real investigation,
        and it is where access control still has something to check."""
        schema = TOOLS_BY_NAME[tool_name].input_model.model_json_schema()
        assert set(schema["properties"]) == {field}
        assert schema["required"] == [field]

    @pytest.mark.parametrize(
        ("tool_name", "payload", "reason"),
        [
            (
                TOOL_LOOKUP_CANDIDATE_RECORDS,
                '{"candidate_id": "bnk_x|setl_smuggled"}',
                ToolValidationError.UNKNOWN_CANDIDATE,
            ),
            (
                TOOL_COMPUTE_EXPECTED_NET,
                '{"candidate_id": "bnk_x|setl_smuggled"}',
                ToolValidationError.UNKNOWN_CANDIDATE,
            ),
            (
                TOOL_INSPECT_SETTLEMENT_BREAKUP,
                '{"settlement_id": "setl_elsewhere"}',
                ToolValidationError.UNKNOWN_SETTLEMENT,
            ),
        ],
    )
    def test_hallucinated_identifiers_are_still_refused(
        self, snapshot, tool_name, payload, reason
    ):
        with pytest.raises(ToolValidationError) as exc:
            execute(ToolContext(snapshot=snapshot), tool_name, payload)
        assert exc.value.reason == reason

    def test_the_prompt_no_longer_carries_the_reverted_contract_wording(self):
        """Task-5 revert, asserted negatively so it cannot creep back in.

        This wording was measured against the same fifty DEV T2 cases and did
        not reduce malformed calls while raising mean tokens by a quarter.
        """
        prompt = system_prompt().lower()
        for gone in (
            "one tool invocation is exactly one logical operation",
            "every json field may",
            "requires two calls",
            "requires four calls",
            "never arrays, combined ids",
        ):
            assert gone not in prompt, gone

    def test_the_prompt_does_not_ask_the_model_to_fan_out_per_candidate(self):
        prompt = system_prompt().lower()
        assert "against each candidate" not in prompt
        assert "do not repeat a fragment per candidate" in prompt


class TestSnapshotWideComparison:
    def test_every_candidate_in_the_snapshot_is_evaluated(self):
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        assert output.candidates_evaluated == 3
        assert tuple(
            entry.candidate_id for entry in output.candidate_comparisons
        ) == snapshot.candidate_ids()

    def test_no_candidate_is_omitted_when_it_matches_nothing(self):
        """Two of the three cannot match. All three are still reported."""
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        reached = [
            entry.candidate_id
            for entry in output.candidate_comparisons
            if any(c.holding_relation_ids for c in entry.comparisons)
        ]
        assert len(reached) < len(output.candidate_comparisons), "not all can match"
        assert len(output.candidate_comparisons) == len(snapshot.candidates)

    def test_candidate_order_is_the_snapshot_order_not_a_ranking(self):
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        emitted = [entry.candidate_id for entry in output.candidate_comparisons]
        assert emitted == list(snapshot.candidate_ids())
        # The matching candidate is second in the snapshot and stays second.
        assert emitted[1] == snapshot.candidate_ids()[1]

    def test_each_entry_names_the_settlements_it_compared(self):
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        by_id = {c.candidate_id: c for c in snapshot.candidates}
        for entry in output.candidate_comparisons:
            assert entry.settlement_ids == by_id[entry.candidate_id].settlement_ids

    def test_the_relations_are_the_same_predicate_the_validator_uses(self):
        """The fan-out changed. The comparison itself did not."""
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        facts = {f.settlement_id: f for f in snapshot.base_evidence.settlement_facts}
        expected = []
        for candidate in snapshot.candidates:
            for settlement_id in candidate.settlement_ids:
                values = {
                    "utr": facts[settlement_id].utr,
                    "settlement_id": settlement_id,
                }
                for kind in REFERENCE_KINDS:
                    if values[kind] is None:
                        continue
                    expected.append(compare("PF*******VQ", values[kind], kind))
        emitted = [c for entry in output.candidate_comparisons for c in entry.comparisons]
        assert emitted == expected

    def test_the_output_still_names_no_winner(self):
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        serialized = json.dumps(output.model_dump(mode="json")).lower()
        for forbidden in (
            "confidence",
            "is_correct",
            "winner",
            "recommend",
            "score",
            "rank",
            "best",
        ):
            assert forbidden not in serialized, forbidden

    def test_the_fragment_stays_at_the_top_level_for_the_validator(self):
        """Load-bearing: ``validator._fragments_from`` harvests this field."""
        snapshot = three_candidate_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        payload = output.model_dump(mode="json")
        assert payload["fragment"] == "PF*******VQ"

    def test_one_call_is_enough_to_resolve_a_three_candidate_case(self):
        """End to end: fragment in, deterministic resolution out, one call."""
        snapshot = three_candidate_snapshot()
        provider = ScriptedProvider(
            [
                turn(
                    calls=[
                        tool_call(
                            TOOL_COMPARE_REFERENCE_FRAGMENT,
                            {"fragment": "PF*******VQ"},
                        )
                    ]
                ),
                turn(text="must not be needed"),
            ]
        )
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((provider,))
        )
        assert trajectory.tool_invocation_count == 1
        _, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert decision.outcome == "RESOLVE"
        assert decision.resolved_settlement_ids == (TRUE_SETTLEMENT_ID,)


class TestRejectedBatchAudit:
    """Atomic reject-all is unchanged. What it discards is now on the record."""

    @staticmethod
    def mixed_batch(snapshot):
        return run_investigation(
            snapshot=snapshot,
            chain=ProviderChain(
                (
                    ScriptedProvider(
                        [
                            turn(
                                calls=[
                                    tool_call(
                                        TOOL_COMPARE_REFERENCE_FRAGMENT,
                                        {"fragment": "PF*******VQ"},
                                        call_id="valid",
                                    ),
                                    tool_call(
                                        TOOL_LOOKUP_CANDIDATE_RECORDS,
                                        '{"candidate_id":"A","candidate_id":"B"}',
                                        call_id="malformed",
                                    ),
                                    tool_call(
                                        TOOL_COMPUTE_EXPECTED_NET,
                                        {"candidate_id": snapshot.candidate_ids()[0]},
                                        call_id="also_valid",
                                    ),
                                ]
                            )
                        ],
                        provider_id="fake",
                        model="fake-v1",
                    ),
                )
            ),
        )

    def test_one_malformed_call_means_no_handler_runs(self, snapshot, monkeypatch):
        monkeypatch.setattr(
            "finrecon.agent.loop.execute_prepared",
            lambda *a, **k: pytest.fail("a rejected batch must execute no handler"),
        )
        trajectory = self.mixed_batch(snapshot)
        assert trajectory.termination_reason == TERMINATION_TOOL_VALIDATION_FAILED
        assert trajectory.successful_tool_invocations() == ()

    def test_every_requested_call_is_recorded_in_request_order(self, snapshot):
        trajectory = self.mixed_batch(snapshot)
        assert len(trajectory.tool_invocations) == 3
        assert [i.call_index for i in trajectory.tool_invocations] == [0, 1, 2]
        assert [i.status for i in trajectory.tool_invocations] == [
            INVOCATION_SKIPPED_BATCH_REJECTED,
            INVOCATION_VALIDATION_FAILED,
            INVOCATION_SKIPPED_BATCH_REJECTED,
        ]

    def test_the_skipped_calls_keep_their_arguments(self, snapshot):
        """What the model asked for is preserved, raw and validated."""
        trajectory = self.mixed_batch(snapshot)
        skipped = trajectory.skipped_tool_invocations()
        assert len(skipped) == 2
        assert skipped[0].raw_arguments == json.dumps({"fragment": "PF*******VQ"})
        assert skipped[0].validated_arguments == {"fragment": "PF*******VQ"}
        assert skipped[1].validated_arguments == {
            "candidate_id": snapshot.candidate_ids()[0]
        }

    def test_a_skipped_call_carries_no_output(self, snapshot):
        trajectory = self.mixed_batch(snapshot)
        for invocation in trajectory.skipped_tool_invocations():
            assert invocation.output is None
            assert invocation.validation_error_reason is None
            assert invocation.succeeded is False

    def test_a_skipped_call_never_becomes_raw_tool_evidence(self, snapshot):
        trajectory = self.mixed_batch(snapshot)
        assert trajectory.skipped_tool_invocations(), "there are skipped calls to exclude"
        assert raw_tool_evidence(trajectory) == ()

    def test_a_skipped_call_changes_no_decision(self, snapshot):
        """The evidence was never gathered, so the outcome is unchanged.

        The comparison that was skipped would, had it run, have resolved this
        case. It did not run, so the case escalates -- exactly as it did
        before the skipped record existed.
        """
        trajectory = self.mixed_batch(snapshot)
        validator_result, decision = adjudicate(snapshot=snapshot, trajectory=trajectory)
        assert validator_result.fragments_tested_by_agent == ()
        assert decision.outcome == "ESCALATE"
        assert "tool_validation_failure" in decision.blockers

    def test_no_provider_fallback_after_a_rejected_batch(self, snapshot):
        primary = ScriptedProvider(
            [
                turn(
                    calls=[
                        tool_call(
                            TOOL_COMPARE_REFERENCE_FRAGMENT,
                            {"fragment": "PF*******VQ"},
                            call_id="valid",
                        ),
                        tool_call(
                            TOOL_COMPUTE_EXPECTED_NET,
                            '{"candidate_id":"A","candidate_id":"B"}',
                            call_id="malformed",
                        ),
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

    def test_a_successful_batch_records_every_call_as_succeeded(self, snapshot):
        trajectory = run_investigation(
            snapshot=snapshot,
            chain=ProviderChain(
                (
                    ScriptedProvider(
                        [
                            turn(
                                calls=[
                                    tool_call(
                                        TOOL_LOOKUP_CANDIDATE_RECORDS,
                                        {"candidate_id": snapshot.candidate_ids()[0]},
                                        call_id="1",
                                    ),
                                    tool_call(
                                        TOOL_LOOKUP_CANDIDATE_RECORDS,
                                        {"candidate_id": snapshot.candidate_ids()[1]},
                                        call_id="2",
                                    ),
                                ]
                            ),
                            turn(text="done"),
                        ]
                    ),
                )
            ),
        )
        assert [i.status for i in trajectory.tool_invocations] == [
            INVOCATION_SUCCEEDED,
            INVOCATION_SUCCEEDED,
        ]
        assert trajectory.skipped_tool_invocations() == ()


class TestInvocationStatusIntegrity:
    def test_the_three_declared_states_are_the_only_ones(self):
        assert INVOCATION_STATUSES == (
            "succeeded",
            "validation_failed",
            "skipped_due_to_batch_rejection",
        )

    @pytest.mark.parametrize(
        ("status", "error", "output"),
        [
            (INVOCATION_SUCCEEDED, "duplicate_argument_key", None),
            (INVOCATION_SUCCEEDED, None, None),
            (INVOCATION_VALIDATION_FAILED, None, {"fragment": "x"}),
            (INVOCATION_VALIDATION_FAILED, "duplicate_argument_key", {"fragment": "x"}),
            (INVOCATION_SKIPPED_BATCH_REJECTED, None, {"fragment": "x"}),
            (INVOCATION_SKIPPED_BATCH_REJECTED, "duplicate_argument_key", None),
        ],
    )
    def test_an_inconsistent_record_cannot_be_constructed(self, status, error, output):
        """A skipped record carrying an output would be evidence never gathered."""
        with pytest.raises(ValueError):
            ToolInvocationRecord(
                step_index=1,
                call_index=0,
                tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                raw_arguments="{}",
                status=status,
                validated_arguments=None,
                validation_error_reason=error,
                validation_error_detail=None,
                output=output,
            )


class TestCacheIdentityAndReplay:
    def test_the_tool_schema_version_is_part_of_the_cache_key(self, snapshot, monkeypatch):
        """A trajectory recorded under the old comparison contract must not
        be served against the new one."""
        before = cache_key(snapshot, provider="p", model="m")
        monkeypatch.setattr("finrecon.agent.cache.TOOL_SCHEMA_VERSION", "tools.v1")
        assert cache_key(snapshot, provider="p", model="m") != before

    def test_the_cache_schema_version_is_part_of_the_cache_key(self, snapshot, monkeypatch):
        before = cache_key(snapshot, provider="p", model="m")
        monkeypatch.setattr(
            "finrecon.agent.cache.CACHE_SCHEMA_VERSION", "trajectory-cache.v2"
        )
        assert cache_key(snapshot, provider="p", model="m") != before

    def test_replay_preserves_every_invocation_status(self, snapshot, tmp_path):
        live = TestRejectedBatchAudit.mixed_batch(snapshot)
        cache = TrajectoryCache(tmp_path)
        cache.store("k", live)
        replayed = cache.load("k")
        assert replayed is not None
        assert [i.status for i in replayed.tool_invocations] == [
            i.status for i in live.tool_invocations
        ]
        assert replayed.tool_invocations == live.tool_invocations
        assert raw_tool_evidence(replayed) == raw_tool_evidence(live)

    def test_replay_preserves_instrumentation(self, snapshot, tmp_path):
        live = run_investigation(
            snapshot=snapshot,
            chain=ProviderChain(
                (
                    ScriptedProvider(
                        [
                            turn(
                                calls=[
                                    tool_call(
                                        TOOL_COMPARE_REFERENCE_FRAGMENT,
                                        {"fragment": "RTGS"},
                                    )
                                ]
                            ),
                            turn(text="done"),
                        ]
                    ),
                )
            ),
        )
        cache = TrajectoryCache(tmp_path)
        cache.store("k", live)
        replayed = cache.load("k")
        assert replayed.total_tokens() == live.total_tokens()
        assert replayed.models_used == live.models_used
        assert [s.latency_ms for s in replayed.steps] == [s.latency_ms for s in live.steps]
        assert replayed.replayed is True


class TestNoCandidateCanBeShrunk:
    def test_a_comparison_leaves_the_snapshot_untouched(self):
        snapshot = three_candidate_snapshot()
        before = snapshot.content_hash
        execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "PF*******VQ"}),
        )
        assert snapshot.content_hash == before
        assert snapshot.verify_integrity()
        assert len(snapshot.candidates) == 3

    def test_a_case_with_no_reference_anywhere_still_reports_all_candidates(self):
        snapshot = no_reference_snapshot()
        _, output = execute(
            ToolContext(snapshot=snapshot),
            TOOL_COMPARE_REFERENCE_FRAGMENT,
            json.dumps({"fragment": "SETTLEMENT"}),
        )
        assert output.candidates_evaluated == len(snapshot.candidates)
        assert tuple(
            e.candidate_id for e in output.candidate_comparisons
        ) == snapshot.candidate_ids()

    def test_the_outbound_tool_spec_offers_no_candidate_argument(self):
        """What the provider is told, not merely what the handler accepts."""
        spec = next(s for s in tool_specs() if s.name == TOOL_COMPARE_REFERENCE_FRAGMENT)
        assert set(spec.parameters_json_schema["properties"]) == {"fragment"}
        assert "candidate_id" not in json.dumps(spec.parameters_json_schema)


class TestFreshImports:
    """The circular-import fix, pinned.

    ``finrecon.decide.policy`` imports the agent package; the loop's early
    adjudication imports ``policy``. The loop resolves that with a local
    import at the adjudication boundary, and these run in a fresh interpreter
    because the cycle only shows up on a cold module cache.
    """

    @staticmethod
    def _run(code: str) -> None:
        root = Path(__file__).resolve().parents[1]
        source = str(root / "src")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (source, environment.get("PYTHONPATH")))
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    def test_policy_imports_in_a_fresh_process(self):
        self._run("import finrecon.decide.policy")

    def test_loop_imports_in_a_fresh_process(self):
        self._run("import finrecon.agent.loop")

    def test_policy_then_loop_imports_in_one_fresh_process(self):
        self._run("import finrecon.decide.policy; import finrecon.agent.loop")

    def test_loop_then_policy_imports_in_one_fresh_process(self):
        self._run("import finrecon.agent.loop; import finrecon.decide.policy")

    def test_the_loop_does_not_import_the_policy_at_module_scope(self):
        """The fix itself, asserted directly rather than only by its effect."""
        import finrecon.agent.loop as loop_module

        source = Path(loop_module.__file__).read_text(encoding="utf-8")
        module_level = [
            line
            for line in source.splitlines()
            if line.startswith("from finrecon.decide.policy")
            or line.startswith("import finrecon.decide.policy")
        ]
        assert module_level == [], module_level
        assert "        from finrecon.decide.policy import adjudicate" in source
