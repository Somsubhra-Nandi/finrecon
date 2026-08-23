"""Stage-2 Phase C: derived (no-direct-key) deterministic reconciliation."""

from __future__ import annotations

from datetime import timedelta

from finrecon.matchers.derived_reconciliation import match_derived, withdraw_contended
from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import (
    RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
    RULE_UNRESOLVED_COUNTERPARTY_CONTENTION,
    RULE_UNRESOLVED_MULTIPLE_DERIVED,
    RULE_UNRESOLVED_NO_CANDIDATE,
)
from finrecon.models import PaymentStatus, RefundStatus, SettlementLineType
from tests import stage2_factories as f

L = f.line
T = SettlementLineType


def decide(batch, case_id="case:bnk_1"):
    record = batch.bank_records[0]
    return match_derived(record, batch, batch.settlements, case_id)


class TestFeeAndTaxDerivation:
    def test_credit_net_of_fee_and_gst_reconciles_exactly(self):
        batch = f.simple_batch()
        decision = decide(batch)
        assert decision.status is DecisionStatus.RESOLVED
        assert decision.rule_id == RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING
        assert decision.settlement_ids == ("setl_1",)

    def test_every_paise_is_attributed_to_a_named_line_type(self):
        decision = decide(f.simple_batch())
        derivation = decision.evidence.money.per_settlement[0]
        assert derivation.unexplained_delta_paise == 0
        assert dict(derivation.breakup_by_type) == {
            "fee": -1_900,
            "payment": 100_000,
            "tax": -342,
        }
        assert derivation.breakup_total_paise == derivation.settlement_amount_paise


class TestRefundOffset:
    def test_refund_deducted_in_the_breakup_reconciles(self):
        settlement = f.settlement(
            net=87_758,
            breakup=(
                L(T.PAYMENT, 100_000, "pay_1"),
                L(T.FEE, -1_900),
                L(T.TAX, -342),
                L(T.REFUND, -10_000, "rfnd_1"),
            ),
        )
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            refunds=[f.refund(amount=10_000)],
            settlements=[settlement],
            bank_records=[f.bank(amount=87_758)],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.RESOLVED
        assert dict(decision.evidence.money.per_settlement[0].breakup_by_type)["refund"] == -10_000

    def test_refund_line_naming_a_failed_refund_blocks_resolution(self):
        settlement = f.settlement(
            net=87_758,
            breakup=(
                L(T.PAYMENT, 100_000, "pay_1"),
                L(T.FEE, -1_900),
                L(T.TAX, -342),
                L(T.REFUND, -10_000, "rfnd_1"),
            ),
        )
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            refunds=[f.refund(amount=10_000, status=RefundStatus.FAILED)],
            settlements=[settlement],
            bank_records=[f.bank(amount=87_758)],
        )
        assert decide(batch).status is DecisionStatus.UNRESOLVED


class TestAdjustmentAndTransfer:
    def _batch(self, adjustment: int):
        net = 97_758 - 5_000 + adjustment
        settlement = f.settlement(
            net=net,
            breakup=(
                L(T.PAYMENT, 100_000, "pay_1"),
                L(T.FEE, -1_900),
                L(T.TAX, -342),
                L(T.TRANSFER, -5_000),
                L(T.ADJUSTMENT, adjustment),
            ),
        )
        return f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[settlement],
            bank_records=[f.bank(amount=net)],
        )

    def test_transfer_deduction_reconciles(self):
        decision = decide(self._batch(0))
        assert decision.status is DecisionStatus.RESOLVED
        assert dict(decision.evidence.money.per_settlement[0].breakup_by_type)["transfer"] == -5_000

    def test_declared_one_paise_adjustment_is_explained_exactly(self):
        for adjustment in (-1, 1):
            decision = decide(self._batch(adjustment))
            assert decision.status is DecisionStatus.RESOLVED
            derivation = decision.evidence.money.per_settlement[0]
            assert derivation.declared_adjustment_paise == adjustment
            assert derivation.unexplained_delta_paise == 0


class TestUnexplainedDeltaBlocks:
    def test_one_paise_with_no_line_behind_it_blocks_resolution(self):
        """The 'probably rounding' case DESIGN.md §4.3 forbids by name."""
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[f.settlement()],
            bank_records=[f.bank(amount=97_757)],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_NO_CANDIDATE

    def test_a_declared_adjustment_line_is_required_not_merely_a_small_gap(self):
        # Identical 1-paise gap, but declared as an adjustment line: resolves.
        settlement = f.settlement(
            net=97_757,
            breakup=(
                L(T.PAYMENT, 100_000, "pay_1"),
                L(T.FEE, -1_900),
                L(T.TAX, -342),
                L(T.ADJUSTMENT, -1),
            ),
        )
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[settlement],
            bank_records=[f.bank(amount=97_757)],
        )
        assert decide(batch).status is DecisionStatus.RESOLVED


class TestBatchedSettlement:
    def _batch(self, credit_amount: int):
        s1 = f.settlement("setl_a", net=50_000, breakup=(L(T.PAYMENT, 50_000, "pay_1"),))
        s2 = f.settlement(
            "setl_b",
            net=30_000,
            at=f.BASE + timedelta(days=1, hours=2),
            breakup=(L(T.PAYMENT, 30_000, "pay_2"),),
        )
        return f.batch_of(
            orders=[f.order(), f.order("ORD-2")],
            payments=[
                f.payment("pay_1", amount=50_000),
                f.payment("pay_2", order_id="ORD-2", amount=30_000),
            ],
            settlements=[s1, s2],
            bank_records=[f.bank(amount=credit_amount)],
        )

    def test_one_credit_covering_two_settlements_reconciles(self):
        decision = decide(self._batch(80_000))
        assert decision.status is DecisionStatus.RESOLVED
        assert decision.settlement_ids == ("setl_a", "setl_b")
        assert decision.relationship == "many_to_one"
        assert decision.evidence.money.unexplained_delta_paise == 0

    def test_group_sums_that_miss_by_one_paise_do_not_reconcile(self):
        assert self._batch(79_999) is not None
        assert decide(self._batch(79_999)).status is DecisionStatus.UNRESOLVED


class TestDuplicateDisambiguation:
    def test_settlement_naming_the_captured_attempt_excludes_the_failed_sibling(self):
        """Provable by structure, not preference: the break-up names the attempt."""
        batch = f.batch_of(
            orders=[f.order()],
            payments=[
                f.payment("pay_failed", amount=100_000, status=PaymentStatus.FAILED),
                f.payment("pay_captured", amount=100_000),
            ],
            settlements=[
                f.settlement(
                    breakup=(
                        L(T.PAYMENT, 100_000, "pay_captured"),
                        L(T.FEE, -1_900),
                        L(T.TAX, -342),
                    )
                )
            ],
            bank_records=[f.bank()],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.RESOLVED
        payment_lines = [
            line
            for line in decision.evidence.money.per_settlement[0].lines
            if line.line_type == "payment"
        ]
        assert payment_lines[0].reference_id == "pay_captured"
        assert payment_lines[0].reference_status == "captured"


class TestAmbiguityIsNeverGuessed:
    def test_two_indistinguishable_settlements_are_refused(self):
        batch = f.batch_of(
            orders=[f.order(), f.order("ORD-2")],
            payments=[f.payment(), f.payment("pay_2", order_id="ORD-2")],
            settlements=[
                f.settlement("setl_a"),
                f.settlement(
                    "setl_b",
                    breakup=(
                        L(T.PAYMENT, 100_000, "pay_2"),
                        L(T.FEE, -1_900),
                        L(T.TAX, -342),
                    ),
                ),
            ],
            bank_records=[f.bank()],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_MULTIPLE_DERIVED
        assert decision.settlement_ids == ()
        assert decision.evidence.competing_solution_ids == (("setl_a",), ("setl_b",))


class TestDateWindow:
    def test_settlement_outside_the_declared_window_is_not_considered(self):
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[f.settlement(at=f.BASE + timedelta(days=10))],
            bank_records=[f.bank()],
        )
        assert decide(batch).status is DecisionStatus.UNRESOLVED

    def test_window_evidence_records_the_offsets_it_applied(self):
        decision = decide(f.simple_batch())
        window = decision.evidence.date_window
        assert window.offset_days == (0,)
        assert window.window_days_before == 1
        assert window.window_days_after == 1


class TestCounterpartyContention:
    def test_two_credits_claiming_one_settlement_both_retract(self):
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[f.settlement()],
            bank_records=[f.bank("bnk_1"), f.bank("bnk_2")],
        )
        decisions = tuple(
            match_derived(record, batch, batch.settlements, f"case:{record.bank_record_id}")
            for record in batch.bank_records
        )
        assert all(d.status is DecisionStatus.RESOLVED for d in decisions)

        after = withdraw_contended(decisions)
        assert all(d.status is DecisionStatus.UNRESOLVED for d in after)
        assert all(d.rule_id == RULE_UNRESOLVED_COUNTERPARTY_CONTENTION for d in after)
        assert all(d.settlement_ids == () for d in after)

    def test_uncontended_decisions_pass_through_unchanged(self):
        decisions = (decide(f.simple_batch()),)
        assert withdraw_contended(decisions) == decisions


class TestNoWeightedScoring:
    def test_decisions_expose_no_score_or_confidence_field(self):
        decision = decide(f.simple_batch())
        fields = set(decision.model_dump().keys()) | set(
            decision.evidence.model_dump().keys()
        )
        assert not {name for name in fields if "score" in name or "confidence" in name}


class TestDevCoverage:
    def test_dev_derived_resolutions_are_all_correct(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        derived = [
            d for d in result.resolved() if d.rule_id == RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING
        ]
        # Benchmark v2: the 300 T1 cases and nothing else. v1's 200 T2
        # cases also landed here, which is precisely the benchmark-validity
        # problem v2 corrects (notes/STAGE2-FINDINGS.md 1).
        assert len(derived) == 300

        incorrect = [
            d.case_id
            for d in derived
            if tuple(sorted(dev_ground_truth[d.case_id]["correct_relationship"]["settlement_ids"]))
            != d.settlement_ids
        ]
        assert incorrect == []

    def test_every_dev_t1_case_resolves_correctly(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        by_case = {d.case_id: d for d in result.decisions}
        t1 = [c for c, e in dev_ground_truth.items() if e["tier"] == "T1"]
        assert len(t1) == 300

        for case_id in t1:
            decision = by_case[case_id]
            expected = dev_ground_truth[case_id]["correct_relationship"]["settlement_ids"]
            assert decision.status is DecisionStatus.RESOLVED, case_id
            assert decision.settlement_ids == tuple(sorted(expected)), case_id

    def test_every_dev_t1_archetype_is_covered(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        by_case = {d.case_id: d for d in result.decisions}
        archetypes: dict[str, int] = {}
        for case_id, entry in dev_ground_truth.items():
            if entry["tier"] != "T1":
                continue
            if by_case[case_id].status is DecisionStatus.RESOLVED:
                archetypes[entry["archetype"]] = archetypes.get(entry["archetype"], 0) + 1

        assert archetypes == {
            "adjustment_and_transfer": 60,
            "batched_settlement": 60,
            "duplicate_disambiguation": 60,
            "fee_gst_arithmetic": 60,
            "refund_offset": 60,
        }

    def test_no_dev_resolution_carries_an_unexplained_paise(self, dev_result):
        result, _ = dev_result
        for decision in result.resolved():
            assert decision.evidence.money.is_exact, decision.case_id
