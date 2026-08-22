"""Stage-2 Phase B: direct-key (exact identifier) reconciliation."""

from __future__ import annotations

import pytest

from finrecon.matchers.direct_key_matcher import DirectKeyIndex, match_direct_key
from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import (
    RULE_DIRECT_KEY_EXACT_TOKEN,
    RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS,
    RULE_UNRESOLVED_NO_CANDIDATE,
    RULE_UNRESOLVED_UNEXPLAINED_DELTA,
)
from finrecon.models import SettlementLineType
from tests import stage2_factories as f

UTR = "AX1B2C3D4E5F"


def decide(batch, case_id="case:bnk_1"):
    record = batch.bank_records[0]
    return match_direct_key(record, batch, DirectKeyIndex(batch.settlements), case_id)


def batch_with(narration, *, utr=UTR, settlements=None, amount=97_758):
    return f.batch_of(
        orders=[f.order()],
        payments=[f.payment()],
        settlements=settlements or [f.settlement(utr=utr)],
        bank_records=[f.bank(narration=narration, amount=amount)],
    )


class TestIntactUtrMatch:
    @pytest.mark.parametrize(
        "narration",
        [
            f"NEFT-CR-{UTR}",
            f"IMPS/P2A/{UTR}/RAZORPAY",
            f"UPI/{UTR}/RAZORPAY SETTLEMENT",
        ],
    )
    def test_intact_utr_token_resolves(self, narration):
        decision = decide(batch_with(narration))
        assert decision.status is DecisionStatus.RESOLVED
        assert decision.rule_id == RULE_DIRECT_KEY_EXACT_TOKEN
        assert decision.settlement_ids == ("setl_1",)
        assert decision.relationship == "one_to_one"

    def test_match_is_case_insensitive_but_otherwise_exact(self):
        decision = decide(batch_with(f"NEFT-CR-{UTR.lower()}"))
        assert decision.status is DecisionStatus.RESOLVED

    def test_reference_evidence_records_the_matching_token(self):
        decision = decide(batch_with(f"NEFT-CR-{UTR}"))
        reference = decision.evidence.references[0]
        assert reference.matched_token == UTR
        assert reference.identifier_kind == "utr"
        assert reference.settlement_id == "setl_1"


class TestSettlementIdMatch:
    def test_clean_settlement_id_resolves(self):
        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment()],
            settlements=[f.settlement("setl_dev_000042")],
            bank_records=[f.bank(narration="RZPY/SETL/setl_dev_000042 CREDIT")],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.RESOLVED
        assert decision.settlement_ids == ("setl_dev_000042",)
        assert decision.evidence.references[0].identifier_kind == "settlement_id"


class TestNoFuzzyMatching:
    """Every DESIGN.md §5.2 degradation must be out of reach for this matcher."""

    @pytest.mark.parametrize(
        ("label", "narration"),
        [
            ("truncated_right", f"NEFT-CR-{UTR[:8]}"),
            ("truncated_left", f"NEFT-CR-{UTR[-8:]}"),
            ("masked", f"NEFT-CR-{UTR[:2]}{'*' * 8}{UTR[-2:]}"),
            ("separator_inserted", f"NEFT-CR-{UTR[:6]}-{UTR[6:]}"),
            ("reordered", f"NEFT-CR-{UTR[6:]}{UTR[:6]}"),
            ("embedded_with_prefix", f"NEFT CR-RZRPAY-SET{UTR}-MUM"),
            ("embedded_with_suffix", f"CR NEFT-RZPY-STLMNT-{UTR}X/REV"),
        ],
    )
    def test_degraded_reference_never_matches(self, label, narration):
        decision = decide(batch_with(narration))
        assert decision.status is DecisionStatus.UNRESOLVED, label
        assert decision.rule_id == RULE_UNRESOLVED_NO_CANDIDATE
        assert decision.settlement_ids == ()

    def test_referenceless_narration_does_not_match(self):
        decision = decide(batch_with("NEFT CREDIT - SETTLEMENT"))
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_NO_CANDIDATE


class TestRefusalOverArbitraryChoice:
    def test_two_settlements_sharing_a_utr_are_refused_not_tie_broken(self):
        batch = batch_with(
            f"NEFT-CR-{UTR}",
            settlements=[
                f.settlement("setl_a", utr=UTR),
                f.settlement("setl_b", utr=UTR),
            ],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS
        assert decision.settlement_ids == ()
        assert decision.evidence.competing_solution_ids == (("setl_a",), ("setl_b",))

    def test_narration_naming_two_different_settlements_is_refused(self):
        batch = batch_with(
            f"NEFT-CR-{UTR} REF setl_b",
            settlements=[
                f.settlement("setl_a", utr=UTR),
                f.settlement("setl_b", utr=None),
            ],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_MULTIPLE_DIRECT_KEYS


class TestMoneyInvariant:
    def test_one_paise_disagreement_blocks_an_otherwise_exact_key_match(self):
        batch = batch_with(f"NEFT-CR-{UTR}", amount=97_759)
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_UNEXPLAINED_DELTA
        assert decision.evidence.money.unexplained_delta_paise == 1

    def test_breakup_that_does_not_total_the_settlement_blocks_resolution(self):
        broken = f.settlement(
            utr=UTR,
            net=97_758,
            breakup=(
                f.line(SettlementLineType.PAYMENT, 100_000, "pay_1"),
                f.line(SettlementLineType.FEE, -1_900),
                f.line(SettlementLineType.TAX, -341),  # one paise unaccounted for
            ),
        )
        batch = batch_with(f"NEFT-CR-{UTR}", settlements=[broken])
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.evidence.money.per_settlement[0].unexplained_delta_paise == -1

    def test_settlement_naming_a_failed_payment_blocks_resolution(self):
        from finrecon.models import PaymentStatus

        batch = f.batch_of(
            orders=[f.order()],
            payments=[f.payment(status=PaymentStatus.FAILED)],
            settlements=[f.settlement(utr=UTR)],
            bank_records=[f.bank(narration=f"NEFT-CR-{UTR}")],
        )
        decision = decide(batch)
        assert decision.status is DecisionStatus.UNRESOLVED
        assert decision.rule_id == RULE_UNRESOLVED_UNEXPLAINED_DELTA


class TestDevCoverage:
    def test_every_dev_direct_key_case_is_resolved_and_correct(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        direct = [d for d in result.resolved() if d.rule_id == RULE_DIRECT_KEY_EXACT_TOKEN]
        assert len(direct) == 350

        t0_case_ids = {
            case_id for case_id, entry in dev_ground_truth.items() if entry["tier"] == "T0"
        }
        assert {d.case_id for d in direct} == t0_case_ids

        for decision in direct:
            expected = dev_ground_truth[decision.case_id]["correct_relationship"]
            assert tuple(sorted(expected["settlement_ids"])) == decision.settlement_ids
