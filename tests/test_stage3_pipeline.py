"""Stage 3 end to end: orchestration, ledger integration, and idempotency.

Two invariants carry over from Stage 2 and are re-asserted here because
Stage 3 is the first thing that could break them:

**Idempotency.** Running the same investigation twice -- or replaying it
from cache -- must produce the same rows, not a second history. The
mechanism is the same one Stage 2 uses: deterministic keys plus
``ON CONFLICT DO NOTHING``.

**Order independence.** Contention between two cases claiming the same
settlement is settled by retracting both, never by awarding it to whichever
case was processed first.

Plus one Stage-3 property: nothing here touches a Stage-2 row, so the record
of what the deterministic core decided survives intact.
"""

from __future__ import annotations

import pytest

from finrecon.agent.cache import TrajectoryCache
from finrecon.agent.loop import LoopConfig
from finrecon.agent.providers.chain import ProviderChain
from finrecon.decide import policy as gate
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch
from finrecon.stage3 import (
    SnapshotIntegrityError,
    investigate_snapshots,
    persist_stage3,
    run_stage3,
)
from tests.stage3_factories import (
    DECOY_UTR,
    OTHER_SETTLEMENT_ID,
    TRUE_SETTLEMENT_ID,
    TRUE_UTR,
    settlement_facts,
    snapshot_of,
    two_candidate_snapshot,
)
from tests.stage3_fakes import ExplodingProvider, MechanicalInvestigator


@pytest.fixture
def chain():
    return ProviderChain((MechanicalInvestigator(),))


@pytest.fixture
def cache(tmp_path):
    return TrajectoryCache(tmp_path / "trajectories")


@pytest.fixture
def dev_batch(benchmark_dir, tmp_path):
    store = LedgerStore(":memory:")
    result = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
    yield store, result
    store.close()


def sample(batch_result, count=12):
    return frozenset(s.case_id for s in sorted(batch_result.snapshots, key=lambda s: s.case_id)[:count])


class TestOrchestration:
    def test_only_unresolved_cases_are_investigated(self, dev_batch, chain, cache):
        store, batch = dev_batch
        chosen = sample(batch, 6)
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        assert {o.case_id for o in result.outcomes} == chosen
        stage2_resolved = {d.case_id for d in batch.resolved()}
        assert not (chosen & stage2_resolved)

    def test_every_outcome_carries_its_full_provenance_chain(self, dev_batch, chain, cache):
        store, batch = dev_batch
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 3)
        )
        for outcome in result.outcomes:
            assert outcome.trajectory.snapshot_hash == outcome.snapshot.content_hash
            assert outcome.validator_result.snapshot_hash == outcome.snapshot.content_hash
            assert outcome.decision.case_id == outcome.case_id
            assert outcome.cache_key

    def test_a_tampered_snapshot_is_refused_before_any_model_call(self, chain, cache):
        snapshot = two_candidate_snapshot()
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates[:1]})
        with pytest.raises(SnapshotIntegrityError):
            investigate_snapshots((tampered,), chain=chain, cache=cache)

    def test_processing_order_does_not_change_the_outcomes(self, dev_batch, chain, cache):
        store, batch = dev_batch
        snapshots = tuple(sorted(batch.snapshots, key=lambda s: s.case_id)[:8])
        forward = investigate_snapshots(snapshots, chain=chain, cache=cache)
        backward = investigate_snapshots(tuple(reversed(snapshots)), chain=chain, cache=cache)
        assert [(o.case_id, o.decision.outcome) for o in forward] == [
            (o.case_id, o.decision.outcome) for o in backward
        ]


class TestCounterpartyContention:
    def test_two_cases_claiming_one_settlement_both_retract(self, cache):
        """Symmetric, like Stage 2's ``withdraw_contended``. Neither case wins."""
        shared = settlement_facts(TRUE_SETTLEMENT_ID, TRUE_UTR)
        first = snapshot_of(
            narration="RTGS CR REF PF*******VQ RAZORPAY SOFTWARE",
            settlements=(settlement_facts(OTHER_SETTLEMENT_ID, DECOY_UTR), shared),
            bank_record_id="bnk_test_0001",
        )
        second = snapshot_of(
            narration="RTGS CR REF PF*******VQ RAZORPAY SOFTWARE",
            settlements=(settlement_facts("setl_charlie", "ZZZZZZZZZZZ"), shared),
            bank_record_id="bnk_test_0002",
        )
        outcomes = investigate_snapshots(
            (first, second), chain=ProviderChain((MechanicalInvestigator(),)), cache=cache
        )
        assert [o.decision.outcome for o in outcomes] == ["ESCALATE", "ESCALATE"]
        for outcome in outcomes:
            assert gate.BLOCKER_COUNTERPARTY_ALREADY_RESOLVED in outcome.decision.blockers

    def test_a_settlement_already_linked_by_stage_two_blocks_stage_three(self, cache, chain):
        snapshot = two_candidate_snapshot()
        outcomes = investigate_snapshots(
            (snapshot,),
            chain=chain,
            cache=cache,
            already_claimed=frozenset({TRUE_SETTLEMENT_ID}),
        )
        assert outcomes[0].decision.outcome == "ESCALATE"
        assert gate.BLOCKER_COUNTERPARTY_ALREADY_RESOLVED in outcomes[0].decision.blockers


class TestLedgerIntegration:
    def test_the_chain_case_to_snapshot_to_trajectory_to_decision_is_walkable(
        self, dev_batch, chain, cache
    ):
        store, batch = dev_batch
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 4)
        )
        case_id = result.outcomes[0].case_id
        assert store.snapshot_payload(batch.batch_id, case_id) is not None
        trajectory = store.trajectory_payload(batch.batch_id, case_id)
        assert trajectory["case_id"] == case_id
        decision_row = next(
            r for r in store.stage3_decision_rows(batch.batch_id) if r["case_id"] == case_id
        )
        assert decision_row["snapshot_hash"] == trajectory["snapshot_hash"]
        assert decision_row["cache_key"] == trajectory["cache_key"]

    def test_raw_tool_outputs_are_persisted_not_summaries(self, dev_batch, chain, cache):
        store, batch = dev_batch
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 2)
        )
        rows = store.stage3_tool_call_rows(batch.batch_id, result.outcomes[0].case_id)
        assert rows
        assert any(row["output_json"] for row in rows)

    def test_a_resolution_writes_links_a_refusal_does_not(self, dev_batch, chain, cache):
        store, batch = dev_batch
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 20)
        )
        linked_cases = {row["case_id"] for row in store.stage3_link_rows(batch.batch_id)}
        assert linked_cases == {o.case_id for o in result.resolved()}

    def test_stage_two_rows_are_untouched_by_stage_three(self, dev_batch, chain, cache):
        store, batch = dev_batch
        before = store.digest(batch.batch_id)
        stage2_cases = {
            row["case_id"]: (row["status"], row["rule_id"])
            for row in store.case_rows(batch.batch_id)
        }
        stage2_links = len(store.link_rows(batch.batch_id))

        run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 10)
        )

        after_cases = {
            row["case_id"]: (row["status"], row["rule_id"])
            for row in store.case_rows(batch.batch_id)
        }
        assert after_cases == stage2_cases
        assert len(store.link_rows(batch.batch_id)) == stage2_links
        assert store.digest(batch.batch_id) != before, "the Stage-3 rows are new"

    def test_the_claimed_set_spans_both_stages(self, dev_batch, chain, cache):
        store, batch = dev_batch
        stage2_claimed = store.claimed_settlement_ids(batch.batch_id)
        result = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=sample(batch, 10)
        )
        after = store.claimed_settlement_ids(batch.batch_id)
        newly = {sid for o in result.resolved() for sid in o.decision.resolved_settlement_ids}
        assert after == stage2_claimed | newly


class TestIdempotency:
    def test_rerunning_the_same_investigation_adds_no_rows(self, dev_batch, chain, cache):
        store, batch = dev_batch
        chosen = sample(batch, 8)
        first = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        counts = {
            table: store.count(table)
            for table in (
                "stage3_investigations",
                "stage3_tool_calls",
                "stage3_decisions",
                "stage3_links",
            )
        }
        digest = store.digest(batch.batch_id)

        second = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        assert {t: store.count(t) for t in counts} == counts
        assert store.digest(batch.batch_id) == digest
        assert [o.decision.outcome for o in second.outcomes] == [
            o.decision.outcome for o in first.outcomes
        ]

    def test_the_second_run_is_served_from_cache(self, dev_batch, chain, cache):
        store, batch = dev_batch
        chosen = sample(batch, 5)
        first = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        assert first.cache_hits() == 0
        second = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        assert second.cache_hits() == len(chosen)
        assert second.provider_calls_made() is False

    def test_a_replay_run_reaches_the_same_decisions_with_no_provider(
        self, dev_batch, chain, cache
    ):
        store, batch = dev_batch
        chosen = sample(batch, 10)
        live = run_stage3(
            store=store, batch_result=batch, chain=chain, cache=cache, case_ids=chosen
        )
        replayed = run_stage3(
            store=store,
            batch_result=batch,
            chain=ProviderChain((ExplodingProvider(),)),
            cache=cache,
            provider_id="mechanical",
            model="mechanical-investigator-v1",
            case_ids=chosen,
        )
        assert [
            (o.case_id, o.decision.outcome, o.decision.resolved_settlement_ids)
            for o in replayed.outcomes
        ] == [
            (o.case_id, o.decision.outcome, o.decision.resolved_settlement_ids)
            for o in live.outcomes
        ]
        assert all(o.cache_hit for o in replayed.outcomes)

    def test_persisting_the_same_outcomes_twice_is_a_no_op(self, dev_batch, chain, cache):
        store, batch = dev_batch
        outcomes = investigate_snapshots(
            tuple(sorted(batch.snapshots, key=lambda s: s.case_id)[:4]),
            chain=chain,
            cache=cache,
        )
        persist_stage3(store, batch_id=batch.batch_id, outcomes=outcomes)
        digest = store.digest(batch.batch_id)
        persist_stage3(store, batch_id=batch.batch_id, outcomes=outcomes)
        assert store.digest(batch.batch_id) == digest


class TestConfiguration:
    def test_the_step_budget_flows_through_to_the_trajectory(self, dev_batch, chain, cache):
        store, batch = dev_batch
        result = run_stage3(
            store=store,
            batch_result=batch,
            chain=chain,
            cache=cache,
            config=LoopConfig(max_steps=3),
            case_ids=sample(batch, 2),
        )
        for outcome in result.outcomes:
            assert outcome.trajectory.max_steps == 3
            assert outcome.trajectory.step_count <= 3

    def test_a_tighter_budget_can_only_escalate_more(self, dev_batch, chain, cache, tmp_path):
        store, batch = dev_batch
        chosen = sample(batch, 20)
        generous = run_stage3(
            store=store,
            batch_result=batch,
            chain=chain,
            cache=TrajectoryCache(tmp_path / "a"),
            case_ids=chosen,
        )
        starved = run_stage3(
            store=store,
            batch_result=batch,
            chain=ProviderChain((MechanicalInvestigator(),)),
            cache=TrajectoryCache(tmp_path / "b"),
            config=LoopConfig(max_steps=2),
            case_ids=chosen,
        )
        assert len(starved.resolved()) <= len(generous.resolved())
        assert all(
            gate.BLOCKER_STEP_BUDGET_EXHAUSTED in o.decision.blockers
            for o in starved.escalated()
            if o.trajectory.budget_exhausted
        )
