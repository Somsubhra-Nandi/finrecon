"""Focused tests for the generic profile-driven bank CSV adapter.

Covers task brief §11 items A-O: direction resolution (both money-column
strategies), exact decimal money conversion, no date-format sniffing,
malformed-row rejection, row-order-independent identity, exact vs.
conflicting duplicate handling, raw narration preservation, structured
value_date, mixed-batch continuation, and provenance completeness.

All CSV content here is synthetic, authored for this test only -- not
derived from, and not claiming to represent, any real bank's export
format. See ``src/finrecon/adapters/bank/README.md`` for why no concrete
bank profile (ICICI included) ships in this module.
"""

from __future__ import annotations

from datetime import date

import pytest

from finrecon.adapters.bank import (
    AmountDirectionColumns,
    BankCsvAdapterResult,
    BankCsvDecodeError,
    BankCsvProfile,
    DebitCreditColumns,
    parse_bank_csv,
)
from finrecon.models import BankRecord, BankRecordDirection, Paise

DC_PROFILE = BankCsvProfile(
    profile_id="synthetic_dc_v1",
    currency="INR",
    value_date_column="Value Date",
    value_date_format="%d/%m/%Y",
    narration_column="Narration",
    money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
    reference_id_column="Ref No",
)

AD_PROFILE = BankCsvProfile(
    profile_id="synthetic_ad_v1",
    currency="INR",
    value_date_column="ValueDate",
    value_date_format="%Y-%m-%d",
    narration_column="Desc",
    money_columns=AmountDirectionColumns(
        amount_column="Amount",
        direction_column="Type",
        credit_values=frozenset({"CR"}),
        debit_values=frozenset({"DR"}),
    ),
)

# No `reference_id_column` declared: every row falls back to content-keyed
# identity, exercising the multiplicity-preserving path this test module's
# `TestFallbackIdentityPreservesMultiplicity` class covers.
NO_REF_DC_PROFILE = BankCsvProfile(
    profile_id="synthetic_no_ref_dc_v1",
    currency="INR",
    value_date_column="Value Date",
    value_date_format="%d/%m/%Y",
    narration_column="Narration",
    money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
)


def _parse(profile: BankCsvProfile, csv_text: str, source_id: str = "s") -> BankCsvAdapterResult:
    return parse_bank_csv(profile, csv_text.encode("utf-8"), source_id)


class TestDirectionResolutionDebitCredit:
    def test_a_valid_credit_row(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF001,03/07/2026,NEFT credit,,1500.50\n",
        )
        (record,) = result.records
        assert record.direction is BankRecordDirection.CREDIT
        assert int(record.amount) == 150050

    def test_b_valid_debit_row(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF002,04/07/2026,UPI debit,250.00,\n",
        )
        (record,) = result.records
        assert record.direction is BankRecordDirection.DEBIT
        assert int(record.amount) == 25000

    def test_f_both_debit_and_credit_populated_fails_closed(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF010,03/07/2026,weird,10.00,20.00\n",
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "both_debit_and_credit_populated"

    def test_g_neither_amount_populated_is_rejected_explicitly(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF011,03/07/2026,weird,,\n",
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "neither_amount_populated"


class TestDirectionResolutionAmountDirection:
    def test_credit_marker_resolves_to_credit(self):
        result = _parse(
            AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-07-03,payout,1500.50,CR\n"
        )
        (record,) = result.records
        assert record.direction is BankRecordDirection.CREDIT
        assert int(record.amount) == 150050

    def test_debit_marker_resolves_to_debit(self):
        result = _parse(AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-07-04,fee,10.00,DR\n")
        (record,) = result.records
        assert record.direction is BankRecordDirection.DEBIT
        assert int(record.amount) == 1000

    def test_unrecognized_direction_marker_is_never_guessed(self):
        result = _parse(
            AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-07-06,x,10.00,XX\n"
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "unrecognized_direction_value"

    def test_marker_ambiguous_between_declared_sets_is_a_profile_misconfiguration(self):
        confused = BankCsvProfile(
            profile_id="confused",
            currency="INR",
            value_date_column="ValueDate",
            value_date_format="%Y-%m-%d",
            narration_column="Desc",
            money_columns=AmountDirectionColumns(
                amount_column="Amount",
                direction_column="Type",
                credit_values=frozenset({"X"}),
                debit_values=frozenset({"X"}),
            ),
        )
        result = _parse(confused, "ValueDate,Desc,Amount,Type\n2026-07-06,x,10.00,X\n")
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "ambiguous_direction_value"


class TestMoneyConversion:
    def test_c_exact_rupee_to_paise_conversion(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF003,03/07/2026,x,,41.50\n",
        )
        (record,) = result.records
        assert int(record.amount) == 4150
        assert isinstance(record.amount, Paise)

    def test_h_malformed_money_is_rejected_not_coerced(self):
        result = _parse(
            AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-07-05,bad,abc,CR\n"
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"

    def test_no_float_is_ever_used_for_money_conversion(self):
        """Sub-paise precision must be rejected exactly like
        ``Paise.from_rupees`` rejects it elsewhere -- proof this adapter
        routes through the same exact-decimal boundary, not float math."""
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF004,03/07/2026,x,,10.005\n",
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"

    def test_declared_thousands_separator_is_stripped_explicitly(self):
        profile = BankCsvProfile(
            profile_id="synthetic_dc_thousands_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Narration",
            money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
            thousands_separator=",",
        )
        result = _parse(
            profile,
            'Value Date,Narration,Debit,Credit\n03/07/2026,big,,"1,23,456.78"\n',
        )
        (record,) = result.records
        assert int(record.amount) == 12345678

    def test_undeclared_thousands_separator_is_rejected_not_guessed(self):
        result = _parse(
            DC_PROFILE,
            'Ref No,Value Date,Narration,Debit,Credit\nREF005,03/07/2026,big,,"1,234.56"\n',
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"


class TestNoDateSniffing:
    def test_d_ambiguous_date_is_parsed_only_under_the_declared_format(self):
        """03/07/2026 under %d/%m/%Y means 3 July; the adapter must never
        also try %m/%d/%Y (which would mean 7 March) to see if it parses
        'better'."""
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF006,03/07/2026,x,,10.00\n",
        )
        (record,) = result.records
        assert record.value_date == date(2026, 7, 3)

    def test_e_a_date_that_does_not_match_the_declared_format_is_rejected(self):
        result = _parse(
            AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-13-01,x,5.00,CR\n"
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "invalid_value_date_format"

    def test_e_a_date_in_the_wrong_declared_shape_is_rejected_not_reparsed(self):
        """DD/MM/YYYY text handed to a profile declaring %Y-%m-%d must be
        rejected outright, never silently reinterpreted under the other
        shape."""
        result = _parse(AD_PROFILE, "ValueDate,Desc,Amount,Type\n03/07/2026,x,5.00,CR\n")
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "invalid_value_date_format"

    def test_m_value_date_comes_from_the_structured_source_column(self):
        result = _parse(
            AD_PROFILE, "ValueDate,Desc,Amount,Type\n2026-07-03,x,5.00,CR\n"
        )
        (record,) = result.records
        assert record.value_date == date(2026, 7, 3)


class TestNarration:
    def test_l_raw_narration_is_preserved_text_identically(self):
        narration = "NEFT-HDFC0001234-RAZORPAY SOFTWARE PVT LTD-invoice#42"
        result = _parse(
            DC_PROFILE,
            f"Ref No,Value Date,Narration,Debit,Credit\nREF007,03/07/2026,{narration},,10.00\n",
        )
        (record,) = result.records
        assert record.narration == narration

    def test_missing_narration_is_a_required_field_rejection(self):
        result = _parse(
            DC_PROFILE, "Ref No,Value Date,Narration,Debit,Credit\nREF008,03/07/2026,,10.00,\n"
        )
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "missing_narration"


class TestRowIdentityAndOrdering:
    def test_i_input_row_ordering_does_not_change_canonical_identity(self):
        forward = (
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF001,03/07/2026,a,,1500.50\n"
            "REF002,04/07/2026,b,250.00,\n"
        )
        backward = (
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF002,04/07/2026,b,250.00,\n"
            "REF001,03/07/2026,a,,1500.50\n"
        )
        result_forward = _parse(DC_PROFILE, forward)
        result_backward = _parse(DC_PROFILE, backward)
        assert result_forward.records == result_backward.records

    def test_j_exact_duplicate_row_collapses_deterministically(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF020,03/07/2026,dup,10.00,\n"
            "REF020,03/07/2026,dup,10.00,\n",
        )
        assert len(result.records) == 1
        assert len(result.manifest.duplicate_rows_dropped) == 1
        assert result.conflicts == ()

    def test_k_conflicting_duplicate_identity_fails_closed(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF030,03/07/2026,conf,10.00,\n"
            "REF030,03/07/2026,conf,20.00,\n",
        )
        assert result.records == ()
        assert len(result.rejected_rows) == 2
        assert all(r.reason == "conflicting_duplicate_bank_record_id" for r in result.rejected_rows)
        assert any(c.kind == "conflicting_duplicate_bank_record_id" for c in result.conflicts)

    def test_content_identity_is_used_when_no_reference_id_column_is_declared(self):
        no_ref_profile = BankCsvProfile(
            profile_id="no_ref_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Narration",
            money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
        )
        result = _parse(
            no_ref_profile,
            "Value Date,Narration,Debit,Credit\n03/07/2026,x,,10.00\n",
        )
        (record,) = result.records
        assert record.bank_record_id.startswith("no_ref_v1:content:")

    def test_reference_id_takes_priority_over_content_identity_when_populated(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF040,03/07/2026,x,,10.00\n",
        )
        (record,) = result.records
        assert record.bank_record_id == "synthetic_dc_v1:ref:REF040"

    def test_empty_reference_id_falls_back_to_content_identity(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n,03/07/2026,x,,10.00\n",
        )
        (record,) = result.records
        assert record.bank_record_id.startswith("synthetic_dc_v1:content:")


class TestMixedBatchAndProvenance:
    def test_n_mixed_valid_and_invalid_batch_continues_safely(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF001,03/07/2026,good,,1500.50\n"
            "REF002,not-a-date,bad,,10.00\n"
            "REF003,04/07/2026,also good,250.00,\n",
        )
        assert len(result.records) == 2
        assert {r.bank_record_id for r in result.records} == {
            "synthetic_dc_v1:ref:REF001",
            "synthetic_dc_v1:ref:REF003",
        }
        assert len(result.rejected_rows) == 1
        assert result.rejected_rows[0].reason == "invalid_value_date_format"

    def test_o_provenance_is_complete_for_both_accepted_and_rejected_rows(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF001,03/07/2026,good,,1500.50\n"
            "REF002,not-a-date,bad,,10.00\n",
        )
        assert len(result.manifest.rows) == 2
        accepted = next(p for p in result.manifest.rows if p.row_index == 0)
        rejected_prov = next(p for p in result.manifest.rows if p.row_index == 1)
        assert accepted.produced == ("bank_record:synthetic_dc_v1:ref:REF001",)
        assert "Debit" in accepted.source_fields_used
        assert "Credit" in accepted.source_fields_used
        assert rejected_prov.produced == ()

    def test_dropped_fields_records_columns_the_profile_does_not_project(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit,Balance\n"
            "REF001,03/07/2026,x,,1500.50,99999.00\n",
        )
        assert result.manifest.rows[0].dropped_fields == ("Balance",)

    def test_output_records_round_trip_through_the_same_strict_json_loader_uses(self):
        result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\nREF001,03/07/2026,x,,10.00\n",
        )
        (record,) = result.records
        reparsed = BankRecord.model_validate_json(record.model_dump_json())
        assert reparsed == record


class TestUnsupportedCurrency:
    def test_currency_column_mismatch_is_rejected(self):
        profile = BankCsvProfile(
            profile_id="cur_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Narration",
            money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
            currency_column="Ccy",
        )
        result = _parse(
            profile,
            "Value Date,Narration,Debit,Credit,Ccy\n03/07/2026,x,10.00,,USD\n",
        )
        assert result.records == ()
        assert result.rejected_rows[0].reason == "unsupported_currency"

    def test_currency_column_match_is_accepted(self):
        profile = BankCsvProfile(
            profile_id="cur_v2",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Narration",
            money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
            currency_column="Ccy",
        )
        result = _parse(
            profile,
            "Value Date,Narration,Debit,Credit,Ccy\n03/07/2026,x,10.00,,INR\n",
        )
        assert len(result.records) == 1


class TestFatalDecodeErrors:
    def test_profile_column_absent_from_header_is_a_fatal_decode_error(self):
        bad_profile = BankCsvProfile(
            profile_id="bad_v1",
            currency="INR",
            value_date_column="DoesNotExist",
            value_date_format="%d/%m/%Y",
            narration_column="Narration",
            money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
        )
        with pytest.raises(BankCsvDecodeError):
            _parse(bad_profile, "Value Date,Narration,Debit,Credit\n03/07/2026,x,10.00,\n")

    def test_undecodable_bytes_raise_a_fatal_decode_error_not_a_row_rejection(self):
        with pytest.raises(BankCsvDecodeError):
            parse_bank_csv(DC_PROFILE, b"\xff\xfe\x00\x01not utf-8 header", source_id="s")


class TestFallbackIdentityPreservesMultiplicity:
    """A content hash is NOT a transaction identity. Two legitimate,
    physically distinct bank rows can share identical value date,
    narration, amount and direction (e.g. two separate UPI payments of the
    same amount posted the same day with the same narration) -- when no
    source-provided reference id is available to disambiguate them, this
    adapter must never collapse them into one canonical record merely
    because their financial content matches.
    """

    def test_two_genuinely_identical_500_rupee_rows_both_survive(self):
        """The exact scenario from the task brief: two rows with identical
        value date, narration, amount and direction, no reference column
        declared. Both must produce their own canonical BankRecord."""
        result = _parse(
            NO_REF_DC_PROFILE,
            "Value Date,Narration,Debit,Credit\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "03/07/2026,UPI PAYMENT,500.00,\n",
        )
        assert len(result.records) == 2
        for record in result.records:
            assert record.direction is BankRecordDirection.DEBIT
            assert int(record.amount) == 50000
            assert record.narration == "UPI PAYMENT"
            assert record.value_date == date(2026, 7, 3)
        # Never silently merged: no duplicate-collapse and no conflict is
        # recorded for this pair -- it is neither, by design.
        assert result.manifest.duplicate_rows_dropped == ()
        assert result.conflicts == ()
        assert result.rejected_rows == ()

    def test_ids_are_unique_within_the_statement_despite_identical_content(self):
        result = _parse(
            NO_REF_DC_PROFILE,
            "Value Date,Narration,Debit,Credit\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "03/07/2026,UPI PAYMENT,500.00,\n",
        )
        ids = [r.bank_record_id for r in result.records]
        assert len(ids) == 3
        assert len(set(ids)) == 3
        # Deterministic and derived from the shared content key, not an
        # opaque/random value -- an occurrence index disambiguates, it
        # does not assert non-equivalence of the underlying transactions.
        assert all(i.startswith("synthetic_no_ref_dc_v1:content:") for i in ids)

    def test_identity_is_never_asserted_for_content_matches_ids_are_still_deterministic(self):
        """Re-parsing the exact same bytes twice must yield the exact same
        ids -- determinism for a fixed input is required even though
        content-match is never treated as proof of duplication."""
        csv_text = (
            "Value Date,Narration,Debit,Credit\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
        )
        first = _parse(NO_REF_DC_PROFILE, csv_text)
        second = _parse(NO_REF_DC_PROFILE, csv_text)
        assert [r.bank_record_id for r in first.records] == [
            r.bank_record_id for r in second.records
        ]

    def test_distinct_content_among_fallback_rows_still_produces_distinct_records(self):
        result = _parse(
            NO_REF_DC_PROFILE,
            "Value Date,Narration,Debit,Credit\n"
            "03/07/2026,UPI PAYMENT A,500.00,\n"
            "04/07/2026,UPI PAYMENT B,700.00,\n",
        )
        assert len(result.records) == 2
        assert len({r.bank_record_id for r in result.records}) == 2

    def test_mixed_batch_of_identical_and_distinct_fallback_rows_preserves_every_row(self):
        result = _parse(
            NO_REF_DC_PROFILE,
            "Value Date,Narration,Debit,Credit\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "03/07/2026,UPI PAYMENT,500.00,\n"
            "04/07/2026,OTHER PAYMENT,250.00,\n",
        )
        assert len(result.records) == 3
        assert len({r.bank_record_id for r in result.records}) == 3

    def test_input_row_permutation_preserves_the_resulting_financial_multiset(self):
        rows = [
            "03/07/2026,UPI PAYMENT,500.00,",
            "03/07/2026,UPI PAYMENT,500.00,",
            "04/07/2026,OTHER PAYMENT,250.00,",
            "05/07/2026,THIRD PAYMENT,,100.00",
        ]
        header = "Value Date,Narration,Debit,Credit"

        def multiset(csv_body_lines):
            csv_text = header + "\n" + "\n".join(csv_body_lines) + "\n"
            result = _parse(NO_REF_DC_PROFILE, csv_text)
            return sorted(
                (r.value_date, r.narration, int(r.amount), r.direction.value)
                for r in result.records
            )

        forward = multiset(rows)
        shuffled = multiset(list(reversed(rows)))
        assert forward == shuffled
        assert len(forward) == 4

    def test_reference_identified_duplicates_still_collapse_and_conflict_as_before(self):
        """§3 of the task brief: reference-based identity semantics are
        unchanged by this fix -- same reference + identical content still
        collapses; same reference + contradictory content still fails
        closed. This is the control proving the fix is scoped to the
        fallback path only."""
        collapse_result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF900,03/07/2026,UPI PAYMENT,500.00,\n"
            "REF900,03/07/2026,UPI PAYMENT,500.00,\n",
        )
        assert len(collapse_result.records) == 1
        assert len(collapse_result.manifest.duplicate_rows_dropped) == 1

        conflict_result = _parse(
            DC_PROFILE,
            "Ref No,Value Date,Narration,Debit,Credit\n"
            "REF901,03/07/2026,UPI PAYMENT,500.00,\n"
            "REF901,03/07/2026,UPI PAYMENT,600.00,\n",
        )
        assert conflict_result.records == ()
        assert any(
            c.kind == "conflicting_duplicate_bank_record_id" for c in conflict_result.conflicts
        )
