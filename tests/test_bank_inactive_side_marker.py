"""Profile-driven zero-filled debit/credit semantics.

Some statement exports never leave the unused money column blank -- they
zero-fill it, so ``Withdrawal = "0.0"`` beside ``Deposit = "1250.00"`` is
one ordinary credit rather than a contradiction. Under the historical
reading (populated-ness decided on cleaned text, *before* the money is
parsed) every such row was quarantined as
``both_debit_and_credit_populated``.

The fix is a declaration, not a heuristic: ``DebitCreditColumns`` carries
an explicit :class:`InactiveSideMarker`, defaulting to ``EMPTY_ONLY`` so
every profile written before the field existed is byte-for-byte unaffected.
Nothing here is bank-specific; ``EMPTY_OR_ZERO`` is opted into per profile.

All CSV content is synthetic, authored for this test only. The
ICICI-*shaped* rows below are column names plus zero-filled values, not a
claim about any real bank's export.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.adapters.bank import (
    AmountDirectionColumns,
    BankCsvAdapterResult,
    BankCsvProfile,
    DebitCreditColumns,
    InactiveSideMarker,
    parse_bank_csv,
)
from finrecon.api.app import DEMO_ROOT, _profile_from_payload, create_app
from finrecon.models import BankRecordDirection, Paise
from finrecon.adapters.razorpay.recon import ReconRowCollection, build_recon_result
from finrecon.orchestrate import _persist_ingestion_audit
from finrecon.orchestrate_cli import OrchestrationInputError, _load_bank_profile
from finrecon.ledger.store import LedgerStore

HEADER = "Value Date,Transaction Remarks,Withdrawal Amount (INR ),Deposit Amount (INR )\n"

ZERO_FILLED_PROFILE = BankCsvProfile(
    profile_id="synthetic_zero_filled_v1",
    currency="INR",
    value_date_column="Value Date",
    value_date_format="%d/%m/%Y",
    narration_column="Transaction Remarks",
    money_columns=DebitCreditColumns(
        debit_column="Withdrawal Amount (INR )",
        credit_column="Deposit Amount (INR )",
        inactive_side_marker=InactiveSideMarker.EMPTY_OR_ZERO,
    ),
)

# Identical in every respect except the declared semantic: this is the
# control for every backwards-compatibility assertion below.
EMPTY_ONLY_PROFILE = BankCsvProfile(
    profile_id="synthetic_empty_only_v1",
    currency="INR",
    value_date_column="Value Date",
    value_date_format="%d/%m/%Y",
    narration_column="Transaction Remarks",
    money_columns=DebitCreditColumns(
        debit_column="Withdrawal Amount (INR )",
        credit_column="Deposit Amount (INR )",
    ),
)


def _parse(profile: BankCsvProfile, rows: str, source_id: str = "s") -> BankCsvAdapterResult:
    return parse_bank_csv(profile, (HEADER + rows).encode("utf-8"), source_id)


def _row(debit: str, credit: str, *, date: str = "18/08/2026", narration: str = "NEFT") -> str:
    return f"{date},{narration},{debit},{credit}\n"


class TestEmptyOrZeroDirectionResolution:
    def test_zero_debit_beside_a_positive_credit_is_a_credit(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("0.0", "1250.00"))
        assert result.rejected_rows == ()
        (record,) = result.records
        assert record.direction is BankRecordDirection.CREDIT
        assert record.amount == Paise(125000)
        assert isinstance(record.amount, Paise)

    def test_zero_credit_beside_a_positive_debit_is_a_debit(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("900.00", "0.0"))
        assert result.rejected_rows == ()
        (record,) = result.records
        assert record.direction is BankRecordDirection.DEBIT
        assert record.amount == Paise(90000)

    def test_an_absent_side_still_resolves_exactly_as_before(self):
        credit = _parse(ZERO_FILLED_PROFILE, _row("", "1250.00")).records[0]
        assert credit.direction is BankRecordDirection.CREDIT
        assert credit.amount == Paise(125000)
        debit = _parse(ZERO_FILLED_PROFILE, _row("900.00", "")).records[0]
        assert debit.direction is BankRecordDirection.DEBIT
        assert debit.amount == Paise(90000)

    @pytest.mark.parametrize("zero_text", ["0", "0.0", "0.00", "00.000"])
    def test_every_spelling_of_zero_marks_the_inactive_side(self, zero_text: str):
        (record,) = _parse(ZERO_FILLED_PROFILE, _row(zero_text, "10.00")).records
        assert record.direction is BankRecordDirection.CREDIT
        assert record.amount == Paise(1000)

    def test_two_positive_sides_are_still_rejected_never_preferred(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("10.00", "20.00"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "both_debit_and_credit_populated"

    def test_zero_on_both_sides_is_not_a_financial_movement(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("0.0", "0.00"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "neither_amount_populated"

    def test_absent_on_both_sides_is_not_a_financial_movement(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("", ""))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "neither_amount_populated"

    def test_malformed_debit_beside_zero_credit_is_malformed_money(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("not-money", "0.0"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"
        assert "Withdrawal Amount (INR )" in rejected.detail

    def test_malformed_credit_beside_zero_debit_is_malformed_money(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("0.0", "not-money"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"
        assert "Deposit Amount (INR )" in rejected.detail

    def test_malformed_text_is_never_coerced_into_an_inactive_side(self):
        """A malformed side beside a perfectly good one still fails closed."""
        result = _parse(ZERO_FILLED_PROFILE, _row("not-money", "1250.00"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"

    def test_sub_paise_precision_is_still_rejected_not_rounded(self):
        result = _parse(ZERO_FILLED_PROFILE, _row("0.0", "1250.005"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "malformed_money"

    def test_a_non_finite_amount_is_quarantined_not_raised(self):
        """``Decimal("Infinity")`` parses, then overflows converting to int.

        On the new classification path that arithmetic failure is caught and
        recorded as ``malformed_money`` rather than escaping as an uncaught
        ``OverflowError`` (which, through the API, would have been a 500).
        """
        for infinite in ("Infinity", "-Infinity"):
            result = _parse(ZERO_FILLED_PROFILE, _row(infinite, "0.0"))
            assert result.records == ()
            (rejected,) = result.rejected_rows
            assert rejected.reason == "malformed_money"

    def test_a_declared_thousands_separator_still_applies(self):
        profile = BankCsvProfile(
            profile_id="synthetic_zero_filled_sep_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Transaction Remarks",
            money_columns=DebitCreditColumns(
                debit_column="Withdrawal Amount (INR )",
                credit_column="Deposit Amount (INR )",
                inactive_side_marker=InactiveSideMarker.EMPTY_OR_ZERO,
            ),
            thousands_separator=",",
        )
        (record,) = parse_bank_csv(
            profile,
            (HEADER + '18/08/2026,NEFT,0.0,"1,250.00"\n').encode("utf-8"),
            "s",
        ).records
        assert record.amount == Paise(125000)


class TestEmptyOnlyBehaviourIsUnchanged:
    """The load-bearing half: omitting the field changes nothing."""

    def test_the_default_is_empty_only(self):
        columns = DebitCreditColumns(debit_column="D", credit_column="C")
        assert columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY

    def test_the_icici_shaped_zero_filled_row_is_still_rejected_under_empty_only(self):
        result = _parse(EMPTY_ONLY_PROFILE, _row("0.0", "1250.00"))
        assert result.records == ()
        (rejected,) = result.rejected_rows
        assert rejected.reason == "both_debit_and_credit_populated"

    def test_empty_only_keeps_its_pre_existing_blank_plus_zero_reading(self):
        """Pre-existing behaviour, deliberately untouched by this patch.

        Under ``EMPTY_ONLY`` a lone ``"0"`` is a populated side and yields a
        zero-amount record. That is a separate, older question about whether
        a ₹0 movement is a record at all; this patch does not answer it, and
        this test pins the behaviour so a future change to it is deliberate.
        """
        (record,) = _parse(EMPTY_ONLY_PROFILE, _row("", "0")).records
        assert record.direction is BankRecordDirection.CREDIT
        assert record.amount == Paise(0)

    def test_empty_only_and_empty_or_zero_agree_on_every_unambiguous_row(self):
        for rows in (_row("", "1250.00"), _row("900.00", ""), _row("10.00", "20.00"), _row("", "")):
            plain = _parse(EMPTY_ONLY_PROFILE, rows)
            zeroed = _parse(ZERO_FILLED_PROFILE, rows)
            assert [
                (r.direction, int(r.amount)) for r in plain.records
            ] == [(r.direction, int(r.amount)) for r in zeroed.records]
            assert [r.reason for r in plain.rejected_rows] == [
                r.reason for r in zeroed.rejected_rows
            ]

    def test_the_amount_direction_strategy_is_untouched(self):
        profile = BankCsvProfile(
            profile_id="synthetic_ad_marker_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Transaction Remarks",
            money_columns=AmountDirectionColumns(
                amount_column="Amount",
                direction_column="Type",
                credit_values=frozenset({"CR"}),
                debit_values=frozenset({"DR"}),
            ),
        )
        (record,) = parse_bank_csv(
            profile,
            b"Value Date,Transaction Remarks,Amount,Type\n18/08/2026,NEFT,12.50,CR\n",
            "s",
        ).records
        assert record.direction is BankRecordDirection.CREDIT
        assert record.amount == Paise(1250)


class TestNarrowProfileValidation:
    def test_an_unknown_marker_object_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="inactive_side_marker"):
            DebitCreditColumns(
                debit_column="D", credit_column="C", inactive_side_marker="empty_or_zero_ish"
            )

    def test_one_column_cannot_declare_both_sides(self):
        with pytest.raises(ValueError, match="cannot declare both sides"):
            DebitCreditColumns(debit_column="Amount", credit_column="Amount")

    def test_empty_column_names_are_refused(self):
        with pytest.raises(ValueError, match="non-empty"):
            DebitCreditColumns(debit_column="", credit_column="C")


class TestProvenanceIsUnaffectedByInterpretation:
    """Raw source evidence must not move when the reading changes."""

    ROW = _row("0.0", "1250.00")

    def test_raw_fields_keep_the_literal_source_text(self):
        rejected = _parse(EMPTY_ONLY_PROFILE, self.ROW).rejected_rows[0]
        raw = dict(rejected.raw_fields)
        assert raw["Withdrawal Amount (INR )"] == "0.0"
        assert raw["Deposit Amount (INR )"] == "1250.00"

    def test_row_fingerprints_are_identical_across_both_semantics(self):
        plain = _parse(EMPTY_ONLY_PROFILE, self.ROW)
        zeroed = _parse(ZERO_FILLED_PROFILE, self.ROW)
        assert (
            plain.manifest.rows[0].row_fingerprint == zeroed.manifest.rows[0].row_fingerprint
        )

    def test_content_derived_identity_is_identical_across_both_semantics(self):
        """Same raw input, same source identity -- the parser may read it
        differently, provenance may not."""
        zeroed = _parse(ZERO_FILLED_PROFILE, self.ROW)
        (record,) = zeroed.records
        # Rebuild the same content identity under a profile that only differs
        # by profile_id-independent semantics: strip the profile_id namespace
        # and compare the content digest itself.
        control = BankCsvProfile(
            profile_id=ZERO_FILLED_PROFILE.profile_id,
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Transaction Remarks",
            money_columns=DebitCreditColumns(
                debit_column="Withdrawal Amount (INR )",
                credit_column="Deposit Amount (INR )",
            ),
        )
        rejected = _parse(control, self.ROW).rejected_rows[0]
        assert rejected.row_fingerprint == zeroed.manifest.rows[0].row_fingerprint
        # And the id the accepted row received is derived from that same raw
        # content, unchanged by the marker.
        assert record.bank_record_id.startswith(f"{ZERO_FILLED_PROFILE.profile_id}:content:")

    def test_the_declared_semantic_appears_in_bank_row_audit_metadata(self, tmp_path: Path):
        """The free-form bank-row audit payload records which reading applied.

        Interpretation metadata only, alongside the untouched raw evidence --
        no schema migration, and the marker never changes a fingerprint.
        """
        rows = (
            _row("0.0", "1250.00")  # accepted under empty_or_zero
            + _row("0.0", "1250.00", date="not-a-date", narration="BROKEN")  # rejected
        )
        collection = ReconRowCollection.of("test:razorpay", ())
        razorpay = build_recon_result(collection)
        bank = parse_bank_csv(
            ZERO_FILLED_PROFILE, (HEADER + rows).encode("utf-8"), "test:bank"
        )
        assert len(bank.records) == 1 and len(bank.rejected_rows) == 1

        store = LedgerStore(str(tmp_path / "audit.sqlite3"))
        try:
            store.register_batch(batch_id="batch:marker-audit", split="test",
                                content_fingerprint="fp-marker", record_count=1, case_count=0)
            _persist_ingestion_audit(
                store=store,
                batch_id="batch:marker-audit",
                razorpay=razorpay,
                bank=bank,
                bank_profile=ZERO_FILLED_PROFILE,
            )
            events = [
                dict(row) for row in store.ingestion_audit_rows("batch:marker-audit")
            ]
        finally:
            store.close()

        bank_events = [
            json.loads(row["payload_json"]) for row in events if row["source_kind"] == "bank"
        ]
        assert {row["event_type"] for row in events if row["source_kind"] == "bank"} == {
            "accepted_bank_row",
            "rejected_bank_row",
            "bank_row_not_produced",
        }
        assert bank_events
        assert all(payload["inactive_side_marker"] == "empty_or_zero" for payload in bank_events)

    def test_an_amount_direction_profile_records_no_marker(self, tmp_path: Path):
        """The declaration belongs to the debit/credit strategy alone."""
        profile = BankCsvProfile(
            profile_id="synthetic_ad_audit_v1",
            currency="INR",
            value_date_column="Value Date",
            value_date_format="%d/%m/%Y",
            narration_column="Transaction Remarks",
            money_columns=AmountDirectionColumns(
                amount_column="Amount",
                direction_column="Type",
                credit_values=frozenset({"CR"}),
                debit_values=frozenset({"DR"}),
            ),
        )
        bank = parse_bank_csv(
            profile,
            b"Value Date,Transaction Remarks,Amount,Type\nnot-a-date,NEFT,12.50,CR\n",
            "test:bank",
        )
        store = LedgerStore(str(tmp_path / "audit-ad.sqlite3"))
        try:
            store.register_batch(batch_id="batch:ad-audit", split="test",
                                content_fingerprint="fp-ad", record_count=0, case_count=0)
            _persist_ingestion_audit(
                store=store,
                batch_id="batch:ad-audit",
                razorpay=build_recon_result(ReconRowCollection.of("test:razorpay", ())),
                bank=bank,
                bank_profile=profile,
            )
            events = [dict(row) for row in store.ingestion_audit_rows("batch:ad-audit")]
        finally:
            store.close()
        payloads = [
            json.loads(row["payload_json"]) for row in events if row["source_kind"] == "bank"
        ]
        assert payloads
        assert all("inactive_side_marker" not in payload for payload in payloads)


class TestApiWireForm:
    def _payload(self, money_columns: dict) -> dict:
        return {
            "profile_id": "wire_v1",
            "currency": "INR",
            "value_date_column": "Value Date",
            "value_date_format": "%d/%m/%Y",
            "narration_column": "Transaction Remarks",
            "money_columns": money_columns,
        }

    DC = {
        "kind": "debit_credit",
        "debit_column": "Withdrawal Amount (INR )",
        "credit_column": "Deposit Amount (INR )",
    }

    def test_it_accepts_empty_or_zero(self):
        profile = _profile_from_payload(
            self._payload({**self.DC, "inactive_side_marker": "empty_or_zero"})
        )
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_OR_ZERO

    def test_it_accepts_an_explicit_empty_only(self):
        profile = _profile_from_payload(
            self._payload({**self.DC, "inactive_side_marker": "empty_only"})
        )
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY

    def test_omitting_the_field_defaults_to_empty_only(self):
        profile = _profile_from_payload(self._payload(dict(self.DC)))
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY

    def test_an_unknown_marker_is_invalid_profile_input_never_a_silent_fallback(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as caught:
            _profile_from_payload(
                self._payload({**self.DC, "inactive_side_marker": "zero_is_blank"})
            )
        assert caught.value.status_code == 422
        assert caught.value.detail["code"] == "invalid_bank_profile"
        assert "inactive_side_marker" in caught.value.detail["message"]

    def test_the_existing_demo_profile_json_still_deserializes(self):
        payload = json.loads((DEMO_ROOT / "bank-profile.json").read_text(encoding="utf-8"))
        profile = _profile_from_payload(payload)
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY


class TestCliWireForm:
    def _write(self, tmp_path: Path, money_columns: dict) -> Path:
        payload = {
            "profile_id": "wire_cli_v1",
            "currency": "INR",
            "value_date_column": "Value Date",
            "value_date_format": "%d/%m/%Y",
            "narration_column": "Transaction Remarks",
            "money_columns": money_columns,
        }
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    DC = TestApiWireForm.DC

    def test_it_accepts_empty_or_zero(self, tmp_path: Path):
        path = self._write(tmp_path, {**self.DC, "inactive_side_marker": "empty_or_zero"})
        profile = _load_bank_profile(path)
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_OR_ZERO

    def test_omitting_the_field_defaults_to_empty_only(self, tmp_path: Path):
        path = self._write(tmp_path, dict(self.DC))
        profile = _load_bank_profile(path)
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY

    def test_an_unknown_marker_is_a_hard_input_error(self, tmp_path: Path):
        path = self._write(tmp_path, {**self.DC, "inactive_side_marker": "empty_or_null"})
        with pytest.raises(OrchestrationInputError, match="inactive_side_marker"):
            _load_bank_profile(path)

    def test_one_column_for_both_sides_is_a_hard_input_error(self, tmp_path: Path):
        path = self._write(
            tmp_path,
            {"kind": "debit_credit", "debit_column": "Amount", "credit_column": "Amount"},
        )
        with pytest.raises(OrchestrationInputError, match="invalid bank profile"):
            _load_bank_profile(path)

    def test_the_existing_demo_profile_json_still_loads(self):
        profile = _load_bank_profile(DEMO_ROOT / "bank-profile.json")
        assert profile.money_columns.inactive_side_marker is InactiveSideMarker.EMPTY_ONLY


def test_the_api_upload_path_carries_the_marker_end_to_end(tmp_path: Path, monkeypatch):
    """A zero-filled statement uploaded with a declaring profile reconciles."""
    monkeypatch.setattr(
        "finrecon.orchestrate.build_chain",
        lambda: (_ for _ in ()).throw(AssertionError("replay attempted a provider call")),
    )
    profile = json.loads((DEMO_ROOT / "bank-profile.json").read_text(encoding="utf-8"))
    profile["money_columns"]["inactive_side_marker"] = "empty_or_zero"

    bank_lines = (DEMO_ROOT / "bank.csv").read_text(encoding="utf-8").splitlines()
    header, first = bank_lines[0], bank_lines[1]
    columns = header.split(",")
    values = next(iter([first.split(",")]))
    # Zero-fill whichever money side this demo row left blank.
    for index, name in enumerate(columns):
        if name in ("Debit", "Credit") and values[index] == "":
            values[index] = "0.0"
    bank_csv = (header + "\n" + ",".join(values) + "\n").encode("utf-8")

    rows = json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
    with TestClient(create_app(ledger_path=tmp_path / "wire.sqlite3")) as client:
        response = client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": "batch:zero-filled"},
            files={
                "razorpay_file": ("razorpay.json", json.dumps(rows), "application/json"),
                "bank_file": ("bank.csv", bank_csv, "text/csv"),
                "bank_profile": ("profile.json", json.dumps(profile), "application/json"),
            },
        )
    assert response.status_code == 200, response.text
    metrics = response.json()["result"]["metrics"]
    assert metrics["deterministic_resolved"] == 1
    assert metrics["ingestion_issues"] == 0
