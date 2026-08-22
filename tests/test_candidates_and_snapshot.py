"""Stage-2 Phase D: candidate generation and the immutable case snapshot."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from finrecon.candidates.generator import (
    BLOCKING_RULE_DATE_WINDOW_ONLY,
    BLOCKING_RULE_EXACT_TOTAL,
    blocking_description,
    build_unresolved_snapshot,
    generate_candidates,
)
from finrecon.candidates.snapshot import CandidateRecord, build_case_snapshot
from finrecon.matchers.derived_reconciliation import match_derived
from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import MAX_SETTLEMENT_GROUP_SIZE
from finrecon.models import SettlementLineType
from tests import stage2_factories as f

L = f.line
T = SettlementLineType


def ambiguous_batch():
    """Two settlements a credit could equally be — the T3 shape."""
    return f.batch_of(
        orders=[f.order(), f.order("ORD-2")],
        payments=[f.payment(), f.payment("pay_2", order_id="ORD-2")],
        settlements=[
            f.settlement("setl_a"),
            f.settlement(
                "setl_b",
                breakup=(L(T.PAYMENT, 100_000, "pay_2"), L(T.FEE, -1_900), L(T.TAX, -342)),
            ),
        ],
        bank_records=[f.bank()],
    )


def snapshot_of(batch):
    record = batch.bank_records[0]
    decision = match_derived(record, batch, batch.settlements, "case:bnk_1")
    candidates = generate_candidates(record, batch, batch.settlements)
    return (
        build_unresolved_snapshot(
            batch_id="batch:test",
            decision=decision,
            bank_record=record,
            batch=batch,
            candidates=candidates,
        ),
        decision,
        candidates,
    )


class TestCompletenessAndNeutrality:
    def test_all_plausible_candidates_are_returned(self):
        batch = ambiguous_batch()
        candidates = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        assert [c.settlement_ids for c in candidates] == [("setl_a",), ("setl_b",)]

    def test_the_generator_never_chooses_a_winner(self):
        batch = ambiguous_batch()
        candidates = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        dumped = candidates[0].model_dump()
        assert not {k for k in dumped if k in {"score", "rank", "confidence", "preferred"}}
        assert len({c.blocking_rule for c in candidates}) == 1

    def test_candidate_set_matches_what_the_matcher_searched(self):
        """Completeness is structural: both use one enumeration."""
        batch = ambiguous_batch()
        record = batch.bank_records[0]
        decision = match_derived(record, batch, batch.settlements, "case:bnk_1")
        candidates = generate_candidates(record, batch, batch.settlements)
        assert decision.evidence.competing_solution_ids == tuple(
            c.settlement_ids for c in candidates
        )

    def test_ordering_is_deterministic_and_input_order_independent(self):
        batch = ambiguous_batch()
        once = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        twice = generate_candidates(
            batch.bank_records[0], batch, tuple(reversed(batch.settlements))
        )
        assert once == twice

    def test_group_candidates_are_generated_up_to_the_declared_bound(self):
        s1 = f.settlement("setl_a", net=50_000, breakup=(L(T.PAYMENT, 50_000, "pay_1"),))
        s2 = f.settlement(
            "setl_b",
            net=30_000,
            at=f.BASE + timedelta(days=1, hours=2),
            breakup=(L(T.PAYMENT, 30_000, "pay_2"),),
        )
        batch = f.batch_of(
            orders=[f.order(), f.order("ORD-2")],
            payments=[
                f.payment("pay_1", amount=50_000),
                f.payment("pay_2", order_id="ORD-2", amount=30_000),
            ],
            settlements=[s1, s2],
            bank_records=[f.bank(amount=80_000)],
        )
        candidates = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        assert [c.settlement_ids for c in candidates] == [("setl_a", "setl_b")]
        assert MAX_SETTLEMENT_GROUP_SIZE == 2

    def test_declared_blocking_bounds_are_recorded_on_the_snapshot(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        blocking = dict(snapshot.base_evidence.blocking)
        assert blocking["max_settlement_group_size"] == str(MAX_SETTLEMENT_GROUP_SIZE)
        assert blocking["value_date_window_days_before"] == "1"
        assert set(blocking) == set(blocking_description())


class TestWideningFallback:
    def test_a_credit_with_no_exact_total_still_gets_a_candidate_set(self):
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[f.settlement()],
            bank_records=[f.bank(amount=12_345)],
        )
        candidates = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        assert [c.blocking_rule for c in candidates] == [BLOCKING_RULE_DATE_WINDOW_ONLY]
        assert candidates[0].unexplained_delta_paise == 12_345 - 97_758

    def test_the_fallback_is_not_used_when_an_exact_total_exists(self):
        batch = ambiguous_batch()
        candidates = generate_candidates(batch.bank_records[0], batch, batch.settlements)
        assert {c.blocking_rule for c in candidates} == {BLOCKING_RULE_EXACT_TOTAL}


class TestNoProhibitedEvidence:
    def test_candidate_generation_ignores_narration_content(self):
        batch_a = ambiguous_batch()
        noisy = f.batch_of(
            orders=[f.order(), f.order("ORD-2")],
            payments=[f.payment(), f.payment("pay_2", order_id="ORD-2")],
            settlements=list(
                f.settlement(sid, breakup=b)
                for sid, b in (
                    ("setl_a", (L(T.PAYMENT, 100_000, "pay_1"), L(T.FEE, -1_900), L(T.TAX, -342))),
                    ("setl_b", (L(T.PAYMENT, 100_000, "pay_2"), L(T.FEE, -1_900), L(T.TAX, -342))),
                )
            ),
            bank_records=[f.bank(narration="RZPY/SETL/setl_a REF setl_a setl_a")],
        )
        plain = generate_candidates(batch_a.bank_records[0], batch_a, batch_a.settlements)
        loud = generate_candidates(noisy.bank_records[0], noisy, noisy.settlements)
        assert [c.settlement_ids for c in plain] == [c.settlement_ids for c in loud]


class TestSnapshotContents:
    def test_snapshot_carries_the_complete_candidate_set(self):
        snapshot, _, candidates = snapshot_of(ambiguous_batch())
        assert snapshot.candidates == candidates
        assert len(snapshot.candidates) == 2

    def test_snapshot_carries_facts_for_every_candidate_settlement(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        assert tuple(s.settlement_id for s in snapshot.base_evidence.settlement_facts) == (
            "setl_a",
            "setl_b",
        )
        for facts in snapshot.base_evidence.settlement_facts:
            assert facts.derivation.unexplained_delta_paise == 0

    def test_snapshot_preserves_the_raw_narration_and_provenance(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        bank = snapshot.base_evidence.bank_record
        assert bank.narration == "NEFT CREDIT - SETTLEMENT"
        assert bank.source.record_type == "bank_record"

    def test_snapshot_records_why_deterministic_reconciliation_stopped(self):
        snapshot, decision, _ = snapshot_of(ambiguous_batch())
        assert snapshot.unresolved_rule_id == decision.rule_id
        assert snapshot.unresolved_matcher_id == decision.matcher_id
        assert decision.status is DecisionStatus.UNRESOLVED


class TestSnapshotImmutability:
    def test_attributes_cannot_be_assigned(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        with pytest.raises(ValidationError):
            snapshot.candidates = ()
        with pytest.raises(ValidationError):
            snapshot.base_evidence = None

    def test_candidates_are_a_tuple_with_no_removal_api(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        assert isinstance(snapshot.candidates, tuple)
        assert not hasattr(snapshot.candidates, "remove")
        assert not hasattr(snapshot.candidates, "pop")

    def test_a_freshly_built_snapshot_verifies(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        assert snapshot.verify_integrity()

    def test_removing_a_candidate_via_model_copy_is_detected(self):
        """The escape hatch a future stage might reach for, closed by hash."""
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates[:1]})
        assert len(tampered.candidates) == 1
        assert not tampered.verify_integrity()

    def test_adding_a_candidate_is_detected_too(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        extra = CandidateRecord(
            candidate_id="bnk_1|setl_z",
            settlement_ids=("setl_z",),
            total_paise=1,
            blocking_rule=BLOCKING_RULE_EXACT_TOTAL,
            unexplained_delta_paise=0,
            settlement_dates=(f.BASE.date(),),
        )
        tampered = snapshot.model_copy(update={"candidates": snapshot.candidates + (extra,)})
        assert not tampered.verify_integrity()

    def test_editing_base_evidence_is_detected(self):
        snapshot, _, _ = snapshot_of(ambiguous_batch())
        edited_bank = snapshot.base_evidence.bank_record.model_copy(
            update={"narration": "rewritten"}
        )
        tampered = snapshot.model_copy(
            update={
                "base_evidence": snapshot.base_evidence.model_copy(
                    update={"bank_record": edited_bank}
                )
            }
        )
        assert not tampered.verify_integrity()

    def test_the_builder_is_the_only_honest_way_to_seal_a_snapshot(self):
        snapshot, _, candidates = snapshot_of(ambiguous_batch())
        rebuilt = build_case_snapshot(
            case_id=snapshot.case_id,
            batch_id=snapshot.batch_id,
            bank_record_id=snapshot.bank_record_id,
            unresolved_rule_id=snapshot.unresolved_rule_id,
            unresolved_matcher_id=snapshot.unresolved_matcher_id,
            candidates=candidates,
            base_evidence=snapshot.base_evidence,
        )
        assert rebuilt.content_hash == snapshot.content_hash


class TestDevSnapshots:
    def test_every_unresolved_dev_case_has_a_verified_snapshot(self, dev_result):
        result, _ = dev_result
        assert len(result.snapshots) == len(result.unresolved()) == 40
        for snapshot in result.snapshots:
            assert snapshot.verify_integrity()
            assert snapshot.candidates
            assert snapshot.base_evidence.settlement_facts

    def test_ambiguous_dev_cases_retain_multiple_plausible_candidates(self, dev_result):
        result, _ = dev_result
        assert all(len(s.candidates) >= 2 for s in result.snapshots)

    def test_no_dev_snapshot_names_a_settlement_already_linked_elsewhere(self, dev_result):
        result, _ = dev_result
        linked = {sid for d in result.resolved() for sid in d.settlement_ids}
        for snapshot in result.snapshots:
            named = {sid for c in snapshot.candidates for sid in c.settlement_ids}
            assert not (named & linked)
