"""Focused tests for the Razorpay settlement recon adapter.

Covers: grouping, multi-row settlements, integer money (no floats
anywhere), signed debit/credit movement, UTR resolution (none / single /
conflicting), null-UTR adjustment rows, fee/tax aggregation, stable
ordering independent of input order, duplicate-row detection vs.
conflicting-duplicate detection, provenance, timestamp conversion, and
that the adapter's output validates against the *unmodified* canonical
``Settlement`` model via the same strict JSON round-trip ``loader.py``
uses.

Nothing here reads ``benchmark/ground_truth`` or any hidden benchmark
data — the fixtures are the official-doc-derived recon samples under
``fixtures/razorpay/doc_samples/``.
"""

from __future__ import annotations

import json
import random
from datetime import timezone

import pytest

from finrecon.adapters.manifest import IngestManifest
from finrecon.adapters.razorpay import (
    NON_BLOCKING_CONFLICT_KINDS,
    AdapterInvariantError,
    RazorpayReconRow,
    ReconRowCollection,
    build_recon_result,
    is_blocking_conflict,
)
from finrecon.adapters.razorpay import recon as recon_module
from finrecon.models import Paise, Settlement, SettlementLineType

from .razorpay_fixtures import load_fixture_rows


def build(name: str, source_id: str | None = None):
    rows = load_fixture_rows(name)
    return build_recon_result(ReconRowCollection.of(source_id or name, rows))


class TestGroupingAndMultiRow:
    def test_multiple_payment_rows_group_into_one_settlement(self):
        result = build("multi_payment_settlement.json")
        assert len(result.settlements) == 1
        settlement = result.settlements[0]
        assert settlement.settlement_id == "setl_docsample_a001"
        payment_lines = [l for l in settlement.breakup if l.type is SettlementLineType.PAYMENT]
        assert len(payment_lines) == 2
        assert {l.reference_id for l in payment_lines} == {
            "pay_docsample_a001",
            "pay_docsample_a002",
        }

    def test_payment_and_refund_both_appear_as_distinct_lines(self):
        result = build("payment_and_refund.json")
        (settlement,) = result.settlements
        types = [l.type for l in settlement.breakup]
        assert SettlementLineType.PAYMENT in types
        assert SettlementLineType.REFUND in types

    def test_payment_and_transfer_both_appear_as_distinct_lines(self):
        result = build("payment_and_transfer.json")
        (settlement,) = result.settlements
        types = [l.type for l in settlement.breakup]
        assert SettlementLineType.PAYMENT in types
        assert SettlementLineType.TRANSFER in types

    def test_distinct_settlement_ids_never_merge(self):
        rows = load_fixture_rows("multi_payment_settlement.json") + load_fixture_rows(
            "payment_and_refund.json"
        )
        result = build_recon_result(ReconRowCollection.of("mixed", rows))
        assert len(result.settlements) == 2
        assert {s.settlement_id for s in result.settlements} == {
            "setl_docsample_a001",
            "setl_docsample_b001",
        }


class TestSignedMovement:
    def test_signed_movement_is_credit_minus_debit_for_a_credit_row(self):
        result = build("payment_and_refund.json")
        (settlement,) = result.settlements
        payment_line = next(l for l in settlement.breakup if l.type is SettlementLineType.PAYMENT)
        assert int(payment_line.amount) == 500000  # credit=500000, debit=0

    def test_signed_movement_is_negative_for_a_debit_row(self):
        result = build("payment_and_refund.json")
        (settlement,) = result.settlements
        refund_line = next(l for l in settlement.breakup if l.type is SettlementLineType.REFUND)
        assert int(refund_line.amount) == -120000  # credit=0, debit=120000

    def test_negative_settlement_produces_a_negative_net_line_correctly(self):
        result = build("negative_settlement.json")
        (settlement,) = result.settlements
        refund_line = next(l for l in settlement.breakup if l.type is SettlementLineType.REFUND)
        assert int(refund_line.amount) == -180000

    def test_no_float_anywhere_in_the_output(self):
        result = build("multi_row_fees_taxes.json")
        for settlement in result.settlements:
            assert isinstance(settlement.amount, Paise)
            assert not isinstance(int(settlement.amount), float)
            for line in settlement.breakup:
                assert isinstance(line.amount, Paise)
                for value in (line.amount,):
                    assert not isinstance(value, float)


class TestUtrResolution:
    def test_single_utr_across_rows_resolves_to_that_value(self):
        result = build("multi_payment_settlement.json")
        (settlement,) = result.settlements
        assert settlement.utr == "HDFCRTA0000001"

    def test_all_null_utr_resolves_to_none_with_no_conflict(self):
        result = build("adjustment_null_utr.json")
        (settlement,) = result.settlements
        assert settlement.utr is None
        assert result.conflicts == ()

    def test_mixed_utr_and_null_in_same_group_resolves_to_the_single_value(self):
        result = build("mixed_utr_and_null.json")
        (settlement,) = result.settlements
        assert settlement.utr == "HDFCRTE0000001"
        assert result.conflicts == ()

    def test_conflicting_utrs_are_quarantined_not_emitted_as_utr_none(self):
        result = build("conflicting_utr.json")
        # Never reaches the eligible collection with utr=None -- that would
        # be indistinguishable from an ordinary missing UTR downstream.
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        assert quarantined.settlement_id == "setl_docsample_f001"
        assert quarantined.settlement is not None
        assert quarantined.settlement.utr is None  # the reconstruction attempt, for audit only
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.kind == "conflicting_settlement_utr"
        assert conflict.settlement_id == "setl_docsample_f001"

    def test_missing_utr_and_conflicting_utr_are_distinguishable_facts(self):
        """The core hardening invariant: missing UTR stays eligible with
        utr=None; conflicting UTR never reaches the eligible collection at
        all, so the two cannot be conflated downstream."""
        missing = build("adjustment_null_utr.json")
        conflicting = build("conflicting_utr.json")
        assert missing.settlements[0].utr is None
        assert missing.quarantined_settlements == ()
        assert missing.conflicts == ()

        assert conflicting.settlements == ()
        assert len(conflicting.quarantined_settlements) == 1
        assert len(conflicting.conflicts) == 1

    def test_a_utr_conflict_does_not_fail_the_whole_batch(self):
        rows = load_fixture_rows("conflicting_utr.json") + load_fixture_rows(
            "multi_payment_settlement.json"
        )
        result = build_recon_result(ReconRowCollection.of("mixed", rows))
        assert len(result.settlements) == 1
        clean = result.settlements[0]
        assert clean.settlement_id == "setl_docsample_a001"
        assert clean.utr == "HDFCRTA0000001"
        assert len(result.quarantined_settlements) == 1
        assert result.quarantined_settlements[0].settlement_id == "setl_docsample_f001"


class TestFeeTaxAggregation:
    def test_fees_and_taxes_on_multiple_rows_aggregate_to_exact_totals(self):
        result = build("multi_row_fees_taxes.json")
        (settlement,) = result.settlements
        fee_lines = [l for l in settlement.breakup if l.type is SettlementLineType.FEE]
        tax_lines = [l for l in settlement.breakup if l.type is SettlementLineType.TAX]
        assert len(fee_lines) == 1
        assert len(tax_lines) == 1
        fee_total = 1900 + 3800 + 5700
        tax_total = 342 + 684 + 1026
        # FEE holds the non-tax component (fee is tax-inclusive); TAX holds
        # the tax component. FEE + TAX always sums to -fee_total exactly.
        assert int(fee_lines[0].amount) == -(fee_total - tax_total)
        assert int(tax_lines[0].amount) == -tax_total
        assert int(fee_lines[0].amount) + int(tax_lines[0].amount) == -fee_total

    def test_zero_fee_across_a_group_emits_no_fee_line(self):
        # The transfer row in this fixture carries no fee/tax of its own;
        # only the payment row does, so exactly one fee line (net of the
        # tax component) and one tax line still appear.
        result = build("payment_and_transfer.json")
        (settlement,) = result.settlements
        fee_lines = [l for l in settlement.breakup if l.type is SettlementLineType.FEE]
        tax_lines = [l for l in settlement.breakup if l.type is SettlementLineType.TAX]
        assert len(fee_lines) == 1
        assert len(tax_lines) == 1
        assert int(fee_lines[0].amount) == -(4750 - 855)
        assert int(tax_lines[0].amount) == -855

    def test_tax_exceeding_fee_is_not_split_and_quarantines_the_settlement(self):
        result = build("tax_exceeds_fee.json")
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        settlement = quarantined.settlement
        assert settlement is not None
        fee_lines = [l for l in settlement.breakup if l.type is SettlementLineType.FEE]
        tax_lines = [l for l in settlement.breakup if l.type is SettlementLineType.TAX]
        assert len(fee_lines) == 1
        assert tax_lines == []
        assert int(fee_lines[0].amount) == -5000  # full fee, undifferentiated
        assert any(c.kind == "tax_exceeds_fee_unsplit_deduction" for c in result.conflicts)
        assert any(c.kind == "tax_exceeds_fee_unsplit_deduction" for c in quarantined.blocking_conflicts)


class TestStableOrdering:
    def test_shuffled_input_order_produces_byte_identical_settlement(self):
        rows = load_fixture_rows("multi_row_fees_taxes.json")
        forward = build_recon_result(ReconRowCollection.of("s", rows))
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        backward = build_recon_result(ReconRowCollection.of("s", shuffled))
        assert forward.settlements[0].model_dump_json() == backward.settlements[0].model_dump_json()

    def test_shuffled_input_order_produces_the_same_settlement_list_order(self):
        rows = load_fixture_rows("multi_payment_settlement.json") + load_fixture_rows(
            "payment_and_refund.json"
        )
        forward = build_recon_result(ReconRowCollection.of("s", rows))
        shuffled = list(rows)
        random.Random(3).shuffle(shuffled)
        backward = build_recon_result(ReconRowCollection.of("s", shuffled))
        assert [s.settlement_id for s in forward.settlements] == [
            s.settlement_id for s in backward.settlements
        ]


class TestDuplicateDetection:
    def test_an_exact_duplicate_row_is_collapsed_not_double_counted(self):
        result = build("duplicate_rows.json")
        (settlement,) = result.settlements
        payment_lines = [l for l in settlement.breakup if l.type is SettlementLineType.PAYMENT]
        assert len(payment_lines) == 1
        assert len(result.manifest.duplicate_rows_dropped) == 1
        assert result.conflicts == ()

    def test_same_entity_id_with_different_content_is_a_conflict_not_a_duplicate(self):
        rows = load_fixture_rows("duplicate_rows.json")
        mutated = rows[1].model_copy(update={"credit": rows[1].credit + 1})
        result = build_recon_result(ReconRowCollection.of("s", [rows[0], mutated]))
        assert any(c.kind == "duplicate_entity_id_conflict" for c in result.conflicts)
        # Neither conflicting copy is silently trusted into the breakup.
        assert result.settlements == ()


class TestTimestampConversion:
    def test_settlement_created_at_is_timezone_aware_utc(self):
        result = build("multi_payment_settlement.json")
        (settlement,) = result.settlements
        assert settlement.created_at.tzinfo is not None
        assert settlement.created_at.astimezone(timezone.utc) == settlement.created_at

    def test_settled_at_unavailable_quarantines_rather_than_using_created_at_silently(self):
        """created_at.date() drives Stage-2's ±day candidate window
        (finrecon.matchers.blocking); a transaction-creation-time proxy is
        not safe to feed it, so this settlement must not reach the
        eligible collection even though a best-effort reconstruction is
        still produced for audit."""
        result = build("on_hold_settlement.json")
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        assert quarantined.settlement is not None
        assert quarantined.settlement.created_at.tzinfo is not None
        assert any(c.kind == "settled_at_unavailable" for c in quarantined.blocking_conflicts)

    def test_inconsistent_settled_at_across_rows_quarantines_the_settlement(self):
        rows = load_fixture_rows("multi_payment_settlement.json")
        mutated_second = _mutate_row(rows[1], settled_at=rows[1].settled_at + 3600)
        result = build_recon_result(ReconRowCollection.of("s", [rows[0], mutated_second]))
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        assert quarantined.settlement is not None  # best-effort, earliest value, for audit
        assert any(c.kind == "inconsistent_settled_at" for c in quarantined.blocking_conflicts)

    def test_agreeing_settled_at_across_rows_is_not_a_conflict(self):
        result = build("multi_payment_settlement.json")
        assert len(result.settlements) == 1
        assert result.quarantined_settlements == ()
        assert not any(c.kind == "inconsistent_settled_at" for c in result.conflicts)


class TestOnHoldAndDispute:
    def test_on_hold_settlement_is_quarantined_not_silently_dropped(self):
        """on_hold_settlement.json's only row has settled_at=None, which is
        now a blocking settled_at_unavailable conflict (see
        TestTimestampConversion) -- it must still surface as an
        ingestion-review artifact, not vanish from the result entirely."""
        result = build("on_hold_settlement.json")
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        assert result.quarantined_settlements[0].settlement_id == "setl_docsample_k001"

    def test_dispute_row_still_produces_a_breakup_line(self):
        result = build("dispute_present.json")
        (settlement,) = result.settlements
        assert any(l.type is SettlementLineType.ADJUSTMENT for l in settlement.breakup)


class TestConformanceReport:
    def test_every_settlement_gets_a_conformance_report(self):
        result = build("multi_payment_settlement.json")
        assert len(result.manifest.conformance) == len(result.settlements)

    def test_totals_always_agree_by_construction(self):
        """breakup_does_not_balance_to_source_net was removed: given
        _row_principal/_settlement_deductions, canonical_breakup_total ==
        source_net is a proven algebraic identity, not merely observed --
        build_recon_result asserts it rather than warning about it."""
        for fixture_name in (
            "multi_payment_settlement.json",
            "multi_row_fees_taxes.json",
            "tax_exceeds_fee.json",
            "negative_settlement.json",
        ):
            result = build(fixture_name)
            for report in result.manifest.conformance:
                assert report.totals_agree is True
                assert report.difference == 0
                assert report.canonical_breakup_total == report.source_net


class TestProvenance:
    def test_every_kept_row_gets_a_provenance_entry(self):
        rows = load_fixture_rows("multi_row_fees_taxes.json")
        result = build_recon_result(ReconRowCollection.of("g", rows))
        assert len(result.manifest.rows) == len(rows)
        entity_ids = {p.entity_id for p in result.manifest.rows}
        assert entity_ids == {r.entity_id for r in rows}

    def test_dropped_amount_field_is_recorded_per_row(self):
        result = build("multi_payment_settlement.json")
        for entry in result.manifest.rows:
            assert "amount" in entry.dropped_fields

    def test_unrecognized_fields_are_recorded_not_silently_dropped(self):
        rows = load_fixture_rows("multi_payment_settlement.json")
        payload = json.loads(rows[0].model_dump_json())
        payload["acquirer_data"] = {"rrn": "999"}
        row_with_extra = RazorpayReconRow.model_validate_json(json.dumps(payload))
        result = build_recon_result(ReconRowCollection.of("s", [row_with_extra, rows[1]]))
        entry = next(p for p in result.manifest.rows if p.entity_id == row_with_extra.entity_id)
        assert "acquirer_data" in entry.unrecognized_fields

    def test_manifest_is_the_declared_pydantic_type(self):
        result = build("multi_payment_settlement.json")
        assert isinstance(result.manifest, IngestManifest)


class TestCanonicalModelValidation:
    def test_output_settlements_round_trip_through_the_same_strict_json_loader_uses(self):
        result = build("multi_row_fees_taxes.json")
        for settlement in result.settlements:
            reparsed = Settlement.model_validate_json(settlement.model_dump_json())
            assert reparsed == settlement

    def test_output_settlements_are_frozen_canonical_records(self):
        result = build("multi_payment_settlement.json")
        (settlement,) = result.settlements
        with pytest.raises(Exception):
            settlement.utr = "changed"  # frozen=True on CanonicalRecord


class TestStrictSourceParsing:
    def test_a_float_subunit_value_is_rejected(self):
        rows = load_fixture_rows("multi_payment_settlement.json")
        payload = json.loads(rows[0].model_dump_json())
        payload["credit"] = 380128.5
        with pytest.raises(Exception):
            RazorpayReconRow.model_validate_json(json.dumps(payload))

    def test_an_unknown_type_value_is_rejected(self):
        rows = load_fixture_rows("multi_payment_settlement.json")
        payload = json.loads(rows[0].model_dump_json())
        payload["type"] = "chargeback"
        with pytest.raises(Exception):
            RazorpayReconRow.model_validate_json(json.dumps(payload))


class TestBreakupReferenceIdentity:
    """Section 1 of the correction brief.

    ``payment_id`` is null on a payment row and carries the *linked*
    payment on a refund/transfer row (documented recon contract) — it is
    never the line's own identity. ``entity_id`` is. This is what
    :func:`finrecon.matchers.derivation.derive_settlement` /
    ``breakup_references_are_sound`` look up against ``Payment.payment_id``
    / ``Refund.refund_id``.
    """

    def test_payment_line_reference_id_is_the_rows_own_entity_id(self):
        result = build("official_doc_payment_example.json")
        (settlement,) = result.settlements
        (line,) = [l for l in settlement.breakup if l.type is SettlementLineType.PAYMENT]
        assert line.reference_id == "pay_docsample_official001"

    def test_payment_row_payment_id_field_is_null_per_the_documented_contract(self):
        (row,) = load_fixture_rows("official_doc_payment_example.json")
        assert row.payment_id is None

    def test_refund_line_reference_id_is_the_rows_own_entity_id_not_the_linked_payment(self):
        result = build("payment_and_refund.json")
        (settlement,) = result.settlements
        (refund_line,) = [l for l in settlement.breakup if l.type is SettlementLineType.REFUND]
        rows = load_fixture_rows("payment_and_refund.json")
        refund_row = next(r for r in rows if r.type.value == "refund")
        assert refund_line.reference_id == refund_row.entity_id
        assert refund_row.payment_id is not None
        assert refund_line.reference_id != refund_row.payment_id

    def test_transfer_line_carries_no_reference_id(self):
        result = build("official_doc_transfer_example.json")
        (settlement,) = result.settlements
        (line,) = settlement.breakup[:1]
        assert line.type is SettlementLineType.TRANSFER
        assert line.reference_id is None

    def test_transfer_rows_linked_payment_id_is_preserved_in_provenance_not_the_line(self):
        (row,) = load_fixture_rows("official_doc_transfer_example.json")
        assert row.payment_id == "pay_docsample_official_linked"
        result = build("official_doc_transfer_example.json")
        entry = result.manifest.rows[0]
        assert "payment_id" in entry.dropped_fields

    def test_adjustment_line_carries_no_reference_id_even_when_linked_to_a_payment(self):
        result = build("dispute_present.json")
        (settlement,) = result.settlements
        adjustment_line = next(l for l in settlement.breakup if l.type is SettlementLineType.ADJUSTMENT)
        assert adjustment_line.reference_id is None


class TestOfficialDocMoneyEquations:
    """Section 2 of the correction brief: the exact documented examples.

    payment: amount=100000, credit=97100, fee=2900, tax=0
    transfer: amount=100000, debit=100296, fee=296, tax=46
    """

    def test_payment_example_principal_and_fee_line(self):
        result = build("official_doc_payment_example.json")
        (settlement,) = result.settlements
        payment_line = next(l for l in settlement.breakup if l.type is SettlementLineType.PAYMENT)
        fee_line = next(l for l in settlement.breakup if l.type is SettlementLineType.FEE)
        assert int(payment_line.amount) == 100000
        assert int(fee_line.amount) == -2900
        assert not [l for l in settlement.breakup if l.type is SettlementLineType.TAX]

    def test_payment_example_breakup_sums_to_credit_minus_debit(self):
        result = build("official_doc_payment_example.json")
        (settlement,) = result.settlements
        assert sum(int(l.amount) for l in settlement.breakup) == 97100  # credit(97100) - debit(0)

    def test_payment_example_produces_no_conflicts(self):
        result = build("official_doc_payment_example.json")
        assert result.conflicts == ()
        assert result.manifest.conformance[0].totals_agree is True

    def test_transfer_example_principal_fee_and_tax_lines(self):
        result = build("official_doc_transfer_example.json")
        (settlement,) = result.settlements
        transfer_line = next(l for l in settlement.breakup if l.type is SettlementLineType.TRANSFER)
        fee_line = next(l for l in settlement.breakup if l.type is SettlementLineType.FEE)
        tax_line = next(l for l in settlement.breakup if l.type is SettlementLineType.TAX)
        assert int(transfer_line.amount) == -100000
        assert int(fee_line.amount) == -(296 - 46)
        assert int(tax_line.amount) == -46

    def test_transfer_example_breakup_sums_to_credit_minus_debit(self):
        result = build("official_doc_transfer_example.json")
        (settlement,) = result.settlements
        assert sum(int(l.amount) for l in settlement.breakup) == -100296  # 0 - debit(100296)

    def test_transfer_example_produces_no_conflicts(self):
        result = build("official_doc_transfer_example.json")
        assert result.conflicts == ()
        assert result.manifest.conformance[0].totals_agree is True

    def test_fee_is_never_deducted_twice(self):
        """The regression this whole section guards against: emitting
        ``credit - debit`` as the principal line AND a ``-fee`` aggregate
        line double-counts the fee. The corrected total must equal
        ``credit - debit`` exactly, not ``(credit - debit) - fee``."""
        result = build("official_doc_payment_example.json")
        (settlement,) = result.settlements
        total = sum(int(l.amount) for l in settlement.breakup)
        double_counted = 97100 - 2900  # what the old, buggy construction produced
        assert total == 97100
        assert total != double_counted


class TestDownstreamCompatibilityAudit:
    """Section 3: check the reconstructed Settlement against the
    reconciliation path's *existing*, unmodified expectations.

    This adapter builds no ``Payment``/``Refund`` companion objects (out of
    scope for this task), so ``breakup_references_are_sound`` cannot see a
    real payment/refund behind any reference and must report unsound —
    this is the honest, expected outcome, not a bug in the adapter.
    """

    def test_derive_settlement_accepts_the_reconstructed_settlement(self):
        from finrecon.matchers.derivation import derive_settlement
        from finrecon.normalize.records import normalize_settlement

        result = build("official_doc_payment_example.json")
        normalized = normalize_settlement(result.settlements[0])
        derivation = derive_settlement(normalized, payments={}, refunds={})
        assert derivation.settlement_id == "setl_docsample_official_pay"
        assert derivation.breakup_total_paise == 97100
        assert derivation.unexplained_delta_paise == 0

    def test_breakup_references_are_sound_is_false_without_companion_payment_records(self):
        """Documents what is still missing: a Payment adapter. Without one,
        no settlement this adapter produces can reach a sound reference,
        because the lookup dict is necessarily empty."""
        from finrecon.matchers.derivation import breakup_references_are_sound
        from finrecon.normalize.records import normalize_settlement

        result = build("official_doc_payment_example.json")
        normalized = normalize_settlement(result.settlements[0])
        assert breakup_references_are_sound(normalized, payments={}, refunds={}) is False

    def test_breakup_references_are_sound_is_true_once_a_matching_captured_payment_exists(self):
        """Proves the *mapping* is right: given the companion Payment this
        adapter deliberately does not build, the same reference_id (now
        the row's entity_id) resolves and is judged sound."""
        from datetime import datetime, timezone

        from finrecon.matchers.derivation import breakup_references_are_sound
        from finrecon.models import Payment, PaymentStatus
        from finrecon.normalize.records import normalize_payment, normalize_settlement

        result = build("official_doc_payment_example.json")
        normalized_settlement = normalize_settlement(result.settlements[0])
        payment = Payment(
            payment_id="pay_docsample_official001",
            order_id="order_docsample_official001",
            amount=Paise(100000),
            status=PaymentStatus.CAPTURED,
            created_at=datetime(2025, 8, 13, tzinfo=timezone.utc),
        )
        normalized_payment = normalize_payment(payment)
        assert (
            breakup_references_are_sound(
                normalized_settlement,
                payments={normalized_payment.payment_id: normalized_payment},
                refunds={},
            )
            is True
        )


def _mutate_row(row: RazorpayReconRow, **changes) -> RazorpayReconRow:
    payload = json.loads(row.model_dump_json())
    payload.update(changes)
    return RazorpayReconRow.model_validate_json(json.dumps(payload))


class TestConflictTaxonomy:
    """Section 1 of the hardening brief: every conflict kind this adapter
    can emit is individually classified, not guessed at in bulk."""

    def test_all_six_known_conflict_kinds_are_currently_blocking(self):
        known_kinds = {
            "duplicate_entity_id_conflict",
            "conflicting_settlement_utr",
            "row_principal_amount_mismatch",
            "tax_exceeds_fee_unsplit_deduction",
            "inconsistent_settled_at",
            "settled_at_unavailable",
        }
        assert known_kinds.isdisjoint(NON_BLOCKING_CONFLICT_KINDS)

    def test_is_blocking_conflict_is_fail_closed_for_an_unknown_kind(self):
        from finrecon.adapters.manifest import IngestConflict

        hypothetical_future_kind = IngestConflict(
            kind="some_kind_nobody_has_classified_yet",
            settlement_id="setl_x",
            detail="unclassified",
        )
        assert is_blocking_conflict(hypothetical_future_kind) is True


class TestQuarantineInvariants:
    """Section 9 of the hardening brief: A-J."""

    def test_a_valid_settlement_is_eligible_and_not_quarantined(self):
        result = build("multi_payment_settlement.json")
        assert len(result.settlements) == 1
        assert result.quarantined_settlements == ()

    def test_b_missing_utr_is_eligible_with_utr_none_not_a_contradiction(self):
        result = build("adjustment_null_utr.json")
        assert len(result.settlements) == 1
        assert result.settlements[0].utr is None
        assert result.quarantined_settlements == ()
        assert result.conflicts == ()

    def test_c_conflicting_utrs_are_quarantined_and_absent_from_eligible(self):
        result = build("conflicting_utr.json")
        assert result.settlements == ()
        assert [q.settlement_id for q in result.quarantined_settlements] == [
            "setl_docsample_f001"
        ]

    def test_d_exact_duplicate_row_is_collapsed_and_settlement_stays_eligible(self):
        result = build("duplicate_rows.json")
        assert len(result.settlements) == 1
        assert result.quarantined_settlements == ()
        assert len(result.manifest.duplicate_rows_dropped) == 1

    def test_e_same_entity_id_conflicting_content_quarantines_the_settlement(self):
        rows = load_fixture_rows("duplicate_rows.json")
        mutated = _mutate_row(rows[1], credit=rows[1].credit + 1)
        result = build_recon_result(ReconRowCollection.of("s", [rows[0], mutated]))
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        assert quarantined.settlement_id == "setl_docsample_j001"
        assert quarantined.settlement is None  # both conflicting copies excluded, nothing left
        assert any(c.kind == "duplicate_entity_id_conflict" for c in quarantined.blocking_conflicts)

    def test_f_row_principal_amount_mismatch_quarantines_the_settlement(self):
        (row,) = load_fixture_rows("official_doc_payment_example.json")
        # amount no longer agrees with (credit - debit) + fee = 100000
        mutated = _mutate_row(row, amount=row.amount + 1)
        result = build_recon_result(ReconRowCollection.of("s", [mutated]))
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        quarantined = result.quarantined_settlements[0]
        assert quarantined.settlement is not None  # reconstruction still attempted
        assert any(
            c.kind == "row_principal_amount_mismatch" for c in quarantined.blocking_conflicts
        )

    def test_g_tax_fee_representation_conflict_quarantines_the_settlement(self):
        result = build("tax_exceeds_fee.json")
        assert result.settlements == ()
        assert len(result.quarantined_settlements) == 1
        assert any(
            c.kind == "tax_exceeds_fee_unsplit_deduction"
            for c in result.quarantined_settlements[0].blocking_conflicts
        )

    def test_h_mixed_batch_isolates_the_invalid_settlement_without_failing_the_batch(self):
        rows = (
            load_fixture_rows("multi_payment_settlement.json")  # A: valid
            + load_fixture_rows("conflicting_utr.json")  # B: invalid
            + load_fixture_rows("payment_and_refund.json")  # C: valid
        )
        result = build_recon_result(ReconRowCollection.of("mixed", rows))
        eligible_ids = {s.settlement_id for s in result.settlements}
        quarantined_ids = {q.settlement_id for q in result.quarantined_settlements}
        assert eligible_ids == {"setl_docsample_a001", "setl_docsample_b001"}
        assert quarantined_ids == {"setl_docsample_f001"}
        assert eligible_ids.isdisjoint(quarantined_ids)

    def test_i_provenance_for_a_quarantined_settlement_remains_complete(self):
        result = build("conflicting_utr.json")
        quarantined = result.quarantined_settlements[0]
        rows = load_fixture_rows("conflicting_utr.json")
        assert set(quarantined.row_fingerprints) == {r.fingerprint() for r in rows}
        manifest_fingerprints = {p.row_fingerprint for p in result.manifest.rows}
        assert set(quarantined.row_fingerprints) <= manifest_fingerprints

    def test_i_provenance_survives_even_when_every_row_was_excluded(self):
        rows = load_fixture_rows("duplicate_rows.json")
        mutated = _mutate_row(rows[1], credit=rows[1].credit + 1)
        result = build_recon_result(ReconRowCollection.of("s", [rows[0], mutated]))
        quarantined = result.quarantined_settlements[0]
        assert len(quarantined.row_fingerprints) == 2
        assert len(result.manifest.rows) == 2
        assert all(p.produced == () for p in result.manifest.rows)

    def test_j_input_order_permutation_does_not_change_quarantine_outcome(self):
        rows = (
            load_fixture_rows("multi_payment_settlement.json")
            + load_fixture_rows("conflicting_utr.json")
            + load_fixture_rows("payment_and_refund.json")
        )
        forward = build_recon_result(ReconRowCollection.of("s", rows))
        shuffled = list(rows)
        random.Random(11).shuffle(shuffled)
        backward = build_recon_result(ReconRowCollection.of("s", shuffled))

        forward_eligible = {s.settlement_id for s in forward.settlements}
        backward_eligible = {s.settlement_id for s in backward.settlements}
        forward_quarantined = {q.settlement_id for q in forward.quarantined_settlements}
        backward_quarantined = {q.settlement_id for q in backward.quarantined_settlements}
        assert forward_eligible == backward_eligible
        assert forward_quarantined == backward_quarantined

    def test_mechanical_invariant_eligible_and_quarantined_ids_are_always_disjoint(self):
        """Direct assertion of the brief's core invariant, across every fixture at once."""
        import glob
        import os

        from .razorpay_fixtures import FIXTURE_DIR

        all_rows = []
        for path in sorted(glob.glob(os.path.join(str(FIXTURE_DIR), "*.json"))):
            all_rows.extend(load_fixture_rows(os.path.basename(path)))
        result = build_recon_result(ReconRowCollection.of("everything", all_rows))
        eligible_ids = {s.settlement_id for s in result.settlements}
        quarantined_ids = {q.settlement_id for q in result.quarantined_settlements}
        assert eligible_ids.isdisjoint(quarantined_ids)
        # At least one of each, so this test isn't vacuous.
        assert eligible_ids
        assert quarantined_ids

    def test_serialization_boundary_is_documented_and_the_two_collections_are_distinct_types(self):
        """Section 8: nothing here builds a serializer yet, but the contract
        that a future one must respect -- settlements vs
        quarantined_settlements are never interchangeable -- is checked
        structurally: they are different fields with different element
        types, and eligible_settlements() is the only sanctioned read path
        for feeding loader.py-shaped output."""
        result = build("multi_payment_settlement.json")
        assert result.eligible_settlements() == result.settlements
        assert all(isinstance(s, Settlement) for s in result.settlements)
        assert all(hasattr(q, "blocking_conflicts") for q in result.quarantined_settlements)


class TestBreakupTotalInvariantIsAnExceptionNotAnAssert:
    """The correction this class covers: the proven
    ``canonical_breakup_total == source_net`` identity is enforced with an
    explicit ``AdapterInvariantError`` raise, not a bare ``assert`` — so
    the check still runs under ``python -O``/``-OO``, where assertions are
    stripped. A violation is never classified as an ``IngestConflict``:
    the algebra guarantees every supported input mapping balances, so
    reaching this path means this adapter's own construction is broken,
    not that the settlement's source data is untrustworthy.
    """

    def test_assertions_are_not_relied_upon_for_this_invariant(self):
        """`python -O` strips `assert` statements; grep-level proof that
        the source no longer contains a bare `assert` guarding this
        invariant (the previous, rejected implementation)."""
        import inspect

        source = inspect.getsource(recon_module.build_recon_result)
        assert "assert report.totals_agree" not in source
        assert "raise AdapterInvariantError" in source

    def test_a_broken_conformance_computation_raises_adapter_invariant_error(self, monkeypatch):
        """Force the impossible state directly: mock `_conformance` to
        return a report that claims disagreement, bypassing the real
        (provably-always-exact) arithmetic entirely. This is the
        "deliberately violated/mocked" proof the algebra itself cannot
        provide, since real input can never trigger this path."""
        from finrecon.adapters.manifest import ConformanceReport

        def broken_conformance(settlement_id, rows, breakup):
            return ConformanceReport(
                settlement_id=settlement_id,
                source_credit_total=0,
                source_debit_total=0,
                source_net=0,
                canonical_breakup_total=1,  # deliberately wrong: != source_net
                totals_agree=False,
                difference=1,
            )

        monkeypatch.setattr(recon_module, "_conformance", broken_conformance)

        with pytest.raises(AdapterInvariantError) as excinfo:
            build("multi_payment_settlement.json")

        assert "setl_docsample_a001" in str(excinfo.value)
        assert "canonical breakup total" in str(excinfo.value)

    def test_adapter_invariant_error_is_not_swallowed_into_a_result(self, monkeypatch):
        """The failure must propagate as a raised exception -- it must
        NOT be caught and converted into an IngestConflict/quarantine
        entry, and build_recon_result must not return a result at all."""
        from finrecon.adapters.manifest import ConformanceReport, IngestConflict

        def broken_conformance(settlement_id, rows, breakup):
            return ConformanceReport(
                settlement_id=settlement_id,
                source_credit_total=0,
                source_debit_total=0,
                source_net=0,
                canonical_breakup_total=-1,
                totals_agree=False,
                difference=-1,
            )

        monkeypatch.setattr(recon_module, "_conformance", broken_conformance)

        with pytest.raises(AdapterInvariantError):
            build("multi_payment_settlement.json")
        # AdapterInvariantError is a plain exception type, never a member
        # of the IngestConflict/pydantic model hierarchy a caller might
        # mistakenly try to catch or serialize as ingestion data.
        assert not issubclass(AdapterInvariantError, IngestConflict)

    def test_adapter_invariant_error_is_a_runtime_error_not_a_bare_assertion_error(self):
        assert issubclass(AdapterInvariantError, RuntimeError)
        assert not issubclass(AdapterInvariantError, AssertionError)

    def test_a_correct_conformance_computation_never_raises(self):
        """Sanity check the mock actually exercises the guard: unpatched,
        real input never raises, for every fixture in the corpus."""
        import glob
        import os

        from .razorpay_fixtures import FIXTURE_DIR

        for path in sorted(glob.glob(os.path.join(str(FIXTURE_DIR), "*.json"))):
            build(os.path.basename(path))  # must not raise


def _colliding_quarantined_settlement_factory(real_cls, forced_settlement_id):
    """A drop-in replacement for ``QuarantinedSettlement`` that overrides
    whatever ``settlement_id`` it is constructed with to ``forced_settlement_id``.

    ``build_recon_result`` resolves ``QuarantinedSettlement`` as a module
    global at call time (``LOAD_GLOBAL``, not a bound import inside the
    function), so patching ``recon_module.QuarantinedSettlement`` before
    calling it genuinely redirects that construction — this creates a
    REAL overlapping pair of settlement-id sets (no faked boolean, no
    fabricated "impossible" row data), the most honest way to reach the
    disjointness check's `raise` branch given that ordinary input can
    never produce a true overlap (routing is a single if/elif per
    settlement_id, evaluated exactly once).
    """

    def factory(*, settlement_id, settlement, row_fingerprints, blocking_conflicts):
        return real_cls(
            settlement_id=forced_settlement_id,
            settlement=settlement,
            row_fingerprints=row_fingerprints,
            blocking_conflicts=blocking_conflicts,
        )

    return factory


class TestQuarantineDisjointnessInvariantIsAnExceptionNotAnAssert:
    """The eligible/quarantined disjointness check
    (``build_recon_result``'s final invariant) is likewise a raised
    ``AdapterInvariantError``, not a bare ``assert`` — so it too survives
    ``python -O``.
    """

    def test_assertions_are_not_relied_upon_for_this_invariant(self):
        import inspect

        source = inspect.getsource(recon_module.build_recon_result)
        assert "assert eligible_ids" not in source
        assert "raise AdapterInvariantError" in source
        assert source.count("raise AdapterInvariantError") == 2  # this one + the totals one

    def test_a_forced_settlement_id_collision_raises_adapter_invariant_error(self, monkeypatch):
        """Feed one genuinely eligible settlement and one genuinely
        quarantined settlement, then force the quarantined one's
        settlement_id to collide with the eligible one's -- a real,
        non-empty intersection of the two id sets, reaching the exact
        `if not eligible_ids.isdisjoint(quarantined_ids): raise ...`
        branch."""
        real_quarantined_settlement = recon_module.QuarantinedSettlement
        monkeypatch.setattr(
            recon_module,
            "QuarantinedSettlement",
            _colliding_quarantined_settlement_factory(
                real_quarantined_settlement, "setl_docsample_a001"
            ),
        )

        rows = load_fixture_rows("multi_payment_settlement.json") + load_fixture_rows(
            "conflicting_utr.json"
        )
        with pytest.raises(AdapterInvariantError) as excinfo:
            build_recon_result(ReconRowCollection.of("s", rows))

        assert "quarantine invariant violated" in str(excinfo.value)
        assert "setl_docsample_a001" in str(excinfo.value)

    def test_unpatched_real_input_never_raises_this_invariant(self):
        """Sanity check the mock actually exercises the guard: the same
        two-fixture input, unpatched, resolves normally with the two
        settlements correctly split between eligible and quarantined."""
        rows = load_fixture_rows("multi_payment_settlement.json") + load_fixture_rows(
            "conflicting_utr.json"
        )
        result = build_recon_result(ReconRowCollection.of("s", rows))
        assert {s.settlement_id for s in result.settlements} == {"setl_docsample_a001"}
        assert {q.settlement_id for q in result.quarantined_settlements} == {
            "setl_docsample_f001"
        }

    def test_the_forced_collision_still_raises_under_python_dash_o(self):
        """The whole point of the fix: assertions are stripped under
        `-O`/`-OO`; this exception must not be. Proven in a fresh
        subprocess interpreter actually started with `-O`, not merely by
        inspecting source, since `__debug__`/assert-stripping is a
        startup-time interpreter setting this process cannot toggle
        retroactively."""
        import subprocess
        import sys

        from .razorpay_fixtures import FIXTURE_DIR

        eligible_path = FIXTURE_DIR / "multi_payment_settlement.json"
        quarantine_path = FIXTURE_DIR / "conflicting_utr.json"
        script = f"""
import json
import sys

from finrecon.adapters.razorpay import (
    AdapterInvariantError,
    RazorpayReconRow,
    ReconRowCollection,
)
from finrecon.adapters.razorpay import recon as recon_module

assert not __debug__, "this script must run under python -O"

real_quarantined_settlement = recon_module.QuarantinedSettlement

def colliding_factory(*, settlement_id, settlement, row_fingerprints, blocking_conflicts):
    return real_quarantined_settlement(
        settlement_id="setl_docsample_a001",  # forced collision with the eligible one
        settlement=settlement,
        row_fingerprints=row_fingerprints,
        blocking_conflicts=blocking_conflicts,
    )

recon_module.QuarantinedSettlement = colliding_factory

def load(path):
    payload = json.loads(open(path, encoding="utf-8").read())
    return [RazorpayReconRow.model_validate_json(json.dumps(p)) for p in payload]

rows = load({str(eligible_path)!r}) + load({str(quarantine_path)!r})

try:
    recon_module.build_recon_result(ReconRowCollection.of("s", rows))
except AdapterInvariantError:
    print("RAISED_AS_EXPECTED")
    sys.exit(0)
else:
    print("DID_NOT_RAISE")
    sys.exit(1)
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "RAISED_AS_EXPECTED" in result.stdout
