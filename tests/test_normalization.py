"""Stage-2 Phase A: normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from finrecon.loader import load_visible_split
from finrecon.models.money import MoneyError, Paise
from finrecon.normalize import (
    normalize_bank_record,
    normalize_batch,
    normalize_settlement,
    normalize_timestamp,
    token_key,
    tokenize_narration,
)
from finrecon.normalize.records import UTC
from tests import stage2_factories as f


class TestTimestampNormalization:
    def test_naive_timestamp_is_interpreted_as_utc_not_shifted(self):
        naive = datetime(2026, 5, 4, 10, 30, 0)
        normalized = normalize_timestamp(naive)
        assert normalized.tzinfo is UTC
        assert normalized.replace(tzinfo=None) == naive

    def test_aware_timestamp_is_converted_to_utc(self):
        ist = timezone(timedelta(hours=5, minutes=30))
        aware = datetime(2026, 5, 4, 16, 0, 0, tzinfo=ist)
        normalized = normalize_timestamp(aware)
        assert normalized.tzinfo is UTC
        assert normalized == datetime(2026, 5, 4, 10, 30, 0, tzinfo=UTC)

    def test_normalization_is_idempotent(self):
        once = normalize_timestamp(datetime(2026, 5, 4, 10, 30, 0))
        assert normalize_timestamp(once) == once

    def test_shifted_timestamp_records_its_source_value(self):
        ist = timezone(timedelta(hours=5, minutes=30))
        settlement = f.settlement(at=datetime(2026, 5, 4, 16, 0, 0, tzinfo=ist))
        normalized = normalize_settlement(settlement)
        source = normalized.source.source_value_of("created_at")
        assert source == settlement.created_at.isoformat()
        assert source != normalized.created_at_utc.isoformat()


class TestMoneyPreservation:
    def test_amounts_stay_exact_integer_paise(self):
        batch = f.simple_batch()
        assert int(batch.settlements[0].amount_paise) == 97_758
        assert int(batch.bank_records[0].amount_paise) == 97_758
        assert isinstance(batch.settlements[0].amount_paise, int)

    def test_breakup_totals_are_exact_integers(self):
        settlement = normalize_settlement(f.settlement())
        assert settlement.breakup_total_paise == 97_758 == 100_000 - 1_900 - 342
        assert settlement.breakup_total_by_type() == {
            "fee": -1_900,
            "payment": 100_000,
            "tax": -342,
        }

    def test_float_money_cannot_enter_the_normalized_path(self):
        with pytest.raises(MoneyError):
            f.bank(amount=97.60)
        with pytest.raises(MoneyError):
            Paise(97.60)

    def test_normalized_amounts_survive_a_round_trip_as_integers(self):
        normalized = normalize_bank_record(f.bank(amount=1))
        assert int(normalized.amount_paise) == 1
        assert normalized.model_dump(mode="json")["amount_paise"] == 1


class TestNarrationPreservation:
    RAW = "  RZPY*ORD293 UPI/98273192  "

    def test_raw_narration_is_byte_identical_after_normalization(self):
        normalized = normalize_bank_record(f.bank(narration=self.RAW))
        assert normalized.narration == self.RAW

    def test_bank_record_declares_no_normalizations(self):
        normalized = normalize_bank_record(f.bank(narration=self.RAW))
        assert normalized.source.normalizations == ()

    def test_tokenization_preserves_token_characters_and_order(self):
        assert tokenize_narration("NEFT CR-RZRPAY-SETX9F2K1-MUM") == (
            "NEFT",
            "CR",
            "RZRPAY",
            "SETX9F2K1",
            "MUM",
        )

    def test_tokenization_keeps_underscores_inside_identifiers(self):
        assert tokenize_narration("RZPY/SETL/setl_dev_000123 CREDIT") == (
            "RZPY",
            "SETL",
            "setl_dev_000123",
            "CREDIT",
        )

    def test_token_key_only_folds_case(self):
        assert token_key("setl_dev_000123") == "SETL_DEV_000123"
        assert token_key("AB*CD") == "AB*CD"


class TestIdentifierTraceability:
    def test_utr_normalization_keeps_the_source_value(self):
        normalized = normalize_settlement(f.settlement(utr="  ax1b2c3d4e5f  "))
        assert normalized.utr == "  ax1b2c3d4e5f  "
        assert normalized.utr_key == "AX1B2C3D4E5F"
        assert normalized.source.source_value_of("utr") == "  ax1b2c3d4e5f  "

    def test_interior_separators_are_preserved_not_stripped(self):
        # Removing them would begin reconstructing a degraded reference.
        normalized = normalize_settlement(f.settlement(utr="AX1B-2C3D"))
        assert normalized.utr_key == "AX1B-2C3D"

    def test_settlement_id_is_never_rewritten_only_keyed(self):
        normalized = normalize_settlement(f.settlement(settlement_id="setl_dev_000001"))
        assert normalized.settlement_id == "setl_dev_000001"
        assert normalized.settlement_id_key == "SETL_DEV_000001"


class TestDeterministicOrdering:
    def test_records_are_sorted_by_id_regardless_of_input_order(self):
        s1, s2, s3 = (f.settlement(sid) for sid in ("setl_c", "setl_a", "setl_b"))
        forward = normalize_batch(
            orders=[], payments=[], refunds=[], settlements=[s1, s2, s3], bank_records=[]
        )
        backward = normalize_batch(
            orders=[], payments=[], refunds=[], settlements=[s3, s2, s1], bank_records=[]
        )
        ids = tuple(s.settlement_id for s in forward.settlements)
        assert ids == ("setl_a", "setl_b", "setl_c")
        assert forward == backward

    def test_normalization_is_deterministic_across_repeated_runs(self, benchmark_dir):
        visible = load_visible_split(benchmark_dir, "dev")
        kwargs = dict(
            orders=visible.orders,
            payments=visible.payments,
            refunds=visible.refunds,
            settlements=visible.settlements,
            bank_records=visible.bank_records,
        )
        assert normalize_batch(**kwargs) == normalize_batch(**kwargs)


class TestNormalizedModelsAreFrozen:
    def test_bank_record_cannot_be_mutated(self):
        normalized = normalize_bank_record(f.bank())
        with pytest.raises(ValidationError):
            normalized.narration = "tampered"

    def test_batch_collections_are_tuples(self):
        batch = f.simple_batch()
        assert isinstance(batch.settlements, tuple)
        assert isinstance(batch.bank_records, tuple)
