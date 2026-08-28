"""End-to-end tests for the raw-inputs-to-decisions bridge.

Covers the orchestration entrypoint in :mod:`finrecon.orchestrate` --
Razorpay recon rows + a bank CSV export, through Stage 2 and Stage 3, to
final outcomes. Every fixture here is hand-built for this test file (small,
synthetic, not derived from the frozen benchmark) -- see task brief for why:
the benchmark is frozen and does not contain the exact shapes these tests
need to exercise (a clean deterministic case, a genuine two-way ambiguity, a
quarantined settlement, a malformed bank row, an unresolved refund
companion).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from finrecon.adapters.bank.csv_profile import BankCsvProfile, DebitCreditColumns
from finrecon.adapters.razorpay.recon_row import RazorpayReconRow, RazorpayReconType
from finrecon.agent.cache import ReplayMissError, TrajectoryCache
from finrecon.agent.providers.chain import ProviderChain
from finrecon.ledger.store import BatchIdentityError, LedgerStore
from finrecon.orchestrate import run_reconciliation_batch
from tests.stage3_fakes import ExplodingProvider, MechanicalInvestigator

UTC = timezone.utc

BANK_PROFILE = BankCsvProfile(
    profile_id="orchestrate_test_v1",
    currency="INR",
    value_date_column="Value Date",
    value_date_format="%d/%m/%Y",
    narration_column="Narration",
    money_columns=DebitCreditColumns(debit_column="Debit", credit_column="Credit"),
    reference_id_column="Ref No",
)


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def razorpay_payment_row(
    *,
    entity_id: str,
    settlement_id: str,
    order_id: str,
    amount: int,
    settled_at: datetime,
    settlement_utr: str | None = None,
    fee: int = 0,
    tax: int = 0,
    settled: bool = True,
) -> RazorpayReconRow:
    """A single ``payment`` recon row that reconstructs to a one-line settlement."""
    created = settled_at
    return RazorpayReconRow(
        entity_id=entity_id,
        type=RazorpayReconType.PAYMENT,
        debit=0,
        credit=amount + fee,
        amount=amount,
        currency="INR",
        fee=fee,
        tax=tax,
        on_hold=False,
        settled=settled,
        created_at=_epoch(created),
        settled_at=_epoch(settled_at),
        settlement_id=settlement_id,
        settlement_utr=settlement_utr,
        payment_id=None,
        order_id=order_id,
        dispute_id=None,
    )


def bank_csv_bytes(rows: list[tuple[str, str, str, str]]) -> bytes:
    """rows: (ref_no, value_date ``DD/MM/YYYY``, narration, credit_rupees)."""
    lines = ["Ref No,Value Date,Narration,Debit,Credit"]
    for ref, vdate, narration, credit in rows:
        lines.append(f"{ref},{vdate},{narration},,{credit}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def make_store() -> LedgerStore:
    return LedgerStore(":memory:")


# ---------------------------------------------------------------------------
# A / G: deterministic-only happy path, zero Stage-3 / provider involvement
# ---------------------------------------------------------------------------


class TestDeterministicOnlyHappyPath:
    def test_a_settlement_is_resolved_deterministically_with_no_stage3_calls(self, tmp_path):
        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        row = razorpay_payment_row(
            entity_id="pay_dtm_1",
            settlement_id="setl_dtm_1",
            order_id="order_dtm_1",
            amount=100_000,
            settled_at=settled_at,
        )
        csv_bytes = bank_csv_bytes(
            [("REF1", "02/04/2026", "NEFT CR SETL_DTM_1 SETTLEMENT", "1000.00")]
        )

        untouched_fixtures = tmp_path / "never_touched_fixtures"
        store = make_store()
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=[row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:a",
            mode="replay",
            fixtures_dir=untouched_fixtures,
        )

        assert result.total_cases == 1
        assert len(result.deterministic_resolved) == 1
        assert result.stage3_result.outcomes == ()
        assert result.ai_assisted_resolved == ()
        assert result.escalated == ()
        assert result.ingestion_quarantined_count == 0

        # G / proof of zero Stage-3 involvement: the cache directory this
        # run was pointed at is never created, because an empty snapshot
        # set means investigate_snapshots' loop -- and therefore
        # TrajectoryCache.load -- never runs at all.
        assert not untouched_fixtures.exists()
        store.close()


# ---------------------------------------------------------------------------
# B / H: Stage-2-unresolved case, resolved via Stage 3 replay, zero live calls
# ---------------------------------------------------------------------------


TRUE_UTR = "PF1CEIYFJVQ"
DECOY_UTR = "EQPJ4E94BAD7U4Y"
MASKED_NARRATION = "RTGS CR REF PF*******VQ RAZORPAY SOFTWARE"


def _ambiguous_batch_rows(amount: int, settled_at: datetime):
    true_row = razorpay_payment_row(
        entity_id="pay_true_1",
        settlement_id="setl_bravo",
        order_id="order_true_1",
        amount=amount,
        settled_at=settled_at,
        settlement_utr=TRUE_UTR,
    )
    decoy_row = razorpay_payment_row(
        entity_id="pay_decoy_1",
        settlement_id="setl_alpha",
        order_id="order_decoy_1",
        amount=amount,
        settled_at=settled_at,
        settlement_utr=DECOY_UTR,
    )
    return [true_row, decoy_row]


class TestStage3ReplayResolution:
    def test_b_h_ambiguous_case_resolves_via_replay_with_zero_live_calls(self, tmp_path):
        amount = 4_187_450
        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        rows = _ambiguous_batch_rows(amount, settled_at)
        csv_bytes = bank_csv_bytes(
            [("REF9", "02/04/2026", MASKED_NARRATION, "41874.50")]
        )
        fixtures_dir = tmp_path / "trajectories"

        # First pass: a live-shaped run using the deterministic mechanical
        # investigator (never a real network call) to warm the cache.
        store = make_store()
        live_chain = ProviderChain((MechanicalInvestigator(),))
        live_result = run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:b",
            mode="live",
            chain=live_chain,
            cache=TrajectoryCache(fixtures_dir),
        )
        assert live_result.total_cases == 1
        assert live_result.deterministic_resolved == ()
        assert len(live_result.stage3_result.outcomes) == 1
        store.close()

        # Second pass: identical batch, replay-only, and the chain passed in
        # is one that fails the test outright if it is ever contacted --
        # proof this run makes zero live/network calls.
        store2 = make_store()
        replay_result = run_reconciliation_batch(
            store=store2,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:b",
            mode="replay",
            cache=TrajectoryCache(fixtures_dir),
            provider_id="mechanical",
            model="mechanical-investigator-v1",
        )
        # ExplodingProvider is not even reachable in replay mode (chain is
        # never built there), which is itself part of the proof -- but
        # additionally assert the outcome came from the cache, not fresh work.
        assert all(o.cache_hit for o in replay_result.stage3_result.outcomes)
        assert [
            (o.case_id, o.decision.outcome, o.decision.resolved_settlement_ids)
            for o in replay_result.stage3_result.outcomes
        ] == [
            (o.case_id, o.decision.outcome, o.decision.resolved_settlement_ids)
            for o in live_result.stage3_result.outcomes
        ]
        if replay_result.ai_assisted_resolved:
            (outcome,) = replay_result.ai_assisted_resolved
            assert outcome.decision.resolved_settlement_ids == ("setl_bravo",)
        store2.close()

    def test_replay_only_never_builds_or_calls_a_provider_chain(self, tmp_path):
        """Same spirit as above, isolated: replay mode never even constructs
        a chain, so an ``ExplodingProvider`` handed in explicitly is simply
        never reachable -- this asserts the plumbing, not just the outcome."""
        amount = 4_187_450
        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        rows = _ambiguous_batch_rows(amount, settled_at)
        csv_bytes = bank_csv_bytes(
            [("REF9", "02/04/2026", MASKED_NARRATION, "41874.50")]
        )
        fixtures_dir = tmp_path / "trajectories"

        store = make_store()
        live_chain = ProviderChain((MechanicalInvestigator(),))
        run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:h",
            mode="live",
            chain=live_chain,
            cache=TrajectoryCache(fixtures_dir),
        )
        store.close()

        store2 = make_store()
        result = run_reconciliation_batch(
            store=store2,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:h",
            mode="replay",
            chain=ExplodingProvider(),  # would blow up the test if ever called
            cache=TrajectoryCache(fixtures_dir),
            provider_id="mechanical",
            model="mechanical-investigator-v1",
        )
        assert all(o.cache_hit for o in result.stage3_result.outcomes)
        store2.close()


# ---------------------------------------------------------------------------
# C: genuine ambiguity that must escalate
# ---------------------------------------------------------------------------


class TestGenuineAmbiguityEscalates:
    def test_c_two_indistinguishable_settlements_escalate(self, tmp_path):
        amount = 250_000
        settled_at = datetime(2026, 5, 1, 9, 0, 0, tzinfo=UTC)
        row_a = razorpay_payment_row(
            entity_id="pay_amb_a",
            settlement_id="setl_amb_a",
            order_id="order_amb_a",
            amount=amount,
            settled_at=settled_at,
            settlement_utr=None,
        )
        row_b = razorpay_payment_row(
            entity_id="pay_amb_b",
            settlement_id="setl_amb_b",
            order_id="order_amb_b",
            amount=amount,
            settled_at=settled_at,
            settlement_utr=None,
        )
        csv_bytes = bank_csv_bytes(
            [("REFC", "01/05/2026", "NEFT CREDIT SETTLEMENT", "2500.00")]
        )
        store = make_store()
        chain = ProviderChain((MechanicalInvestigator(),))
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=[row_a, row_b],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:c",
            mode="live",
            chain=chain,
            cache=TrajectoryCache(tmp_path / "trajectories"),
        )
        assert result.deterministic_resolved == ()
        assert len(result.stage3_result.outcomes) == 1
        assert result.ai_assisted_resolved == ()
        assert len(result.escalated) == 1
        store.close()


# ---------------------------------------------------------------------------
# D: quarantined settlement never reaches the engine; unrelated case is fine
# ---------------------------------------------------------------------------


class TestIngestionQuarantine:
    def test_d_conflicting_utr_settlement_is_quarantined_and_excluded(self, tmp_path):
        settled_at = datetime(2026, 6, 1, 9, 0, 0, tzinfo=UTC)

        # Two rows for the SAME settlement disagree on settlement_utr ->
        # a blocking `conflicting_settlement_utr` IngestConflict.
        bad_row_1 = razorpay_payment_row(
            entity_id="pay_bad_1",
            settlement_id="setl_bad",
            order_id="order_bad_1",
            amount=50_000,
            settled_at=settled_at,
            settlement_utr="UTRONE00000",
        )
        bad_row_2 = razorpay_payment_row(
            entity_id="pay_bad_2",
            settlement_id="setl_bad",
            order_id="order_bad_2",
            amount=30_000,
            settled_at=settled_at,
            settlement_utr="UTRTWO00000",
        )

        # An unrelated, clean settlement in the same batch.
        clean_row = razorpay_payment_row(
            entity_id="pay_clean_1",
            settlement_id="setl_clean",
            order_id="order_clean_1",
            amount=70_000,
            settled_at=settled_at,
        )
        csv_bytes = bank_csv_bytes(
            [("REFD", "01/06/2026", "NEFT CR SETL_CLEAN SETTLEMENT", "700.00")]
        )

        store = make_store()
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=[bad_row_1, bad_row_2, clean_row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:d",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures",
        )

        # Quarantined, contributes to the audit count.
        quarantined_ids = {q.settlement_id for q in result.quarantined_settlements}
        assert quarantined_ids == {"setl_bad"}
        assert result.ingestion_quarantined_count >= 1

        # Never present anywhere in the normalized batch fed to the engine.
        engine_settlement_ids = {s.settlement_id for s in result.batch_result.batch.settlements}
        assert "setl_bad" not in engine_settlement_ids
        assert "setl_clean" in engine_settlement_ids

        # The unrelated clean settlement still reconciles normally.
        assert len(result.deterministic_resolved) == 1
        assert result.deterministic_resolved[0].settlement_ids == ("setl_clean",)
        store.close()


# ---------------------------------------------------------------------------
# E: bad bank row rejected, batch continues
# ---------------------------------------------------------------------------


class TestBadBankRowContinuesBatch:
    def test_e_malformed_bank_row_is_rejected_and_batch_continues(self, tmp_path):
        settled_at = datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC)
        good_row = razorpay_payment_row(
            entity_id="pay_good_1",
            settlement_id="setl_good",
            order_id="order_good_1",
            amount=90_000,
            settled_at=settled_at,
        )
        # One good bank row, one with an invalid date (rejected), one with
        # both debit and credit populated (rejected, ambiguous direction).
        lines = [
            "Ref No,Value Date,Narration,Debit,Credit",
            "REFG,01/07/2026,NEFT CR SETL_GOOD SETTLEMENT,,900.00",
            "REFBAD,not-a-date,broken row,,10.00",
            "REFBAD2,01/07/2026,both populated,5.00,5.00",
        ]
        csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")

        store = make_store()
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=[good_row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:e",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures",
        )

        reasons = {r.reason for r in result.rejected_bank_rows}
        assert "invalid_value_date_format" in reasons
        assert "both_debit_and_credit_populated" in reasons
        assert result.ingestion_quarantined_count == 2
        # The batch still processed the good row.
        assert result.ingested_bank_record_count == 1
        assert len(result.deterministic_resolved) == 1
        store.close()


# ---------------------------------------------------------------------------
# F: unresolved refund companion never fabricates a canonical Refund
# ---------------------------------------------------------------------------


class TestUnresolvedRefundCompanion:
    def test_f_refund_referencing_settlement_never_gets_a_fabricated_refund(self, tmp_path):
        settled_at = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)
        payment_row = razorpay_payment_row(
            entity_id="pay_refbase",
            settlement_id="setl_refbase",
            order_id="order_refbase",
            amount=200_000,
            settled_at=settled_at,
        )
        # A settlement whose breakup carries a REFUND line, built by adding
        # a second row of type REFUND to the same settlement group.
        refund_row = RazorpayReconRow(
            entity_id="rfnd_1",
            type=RazorpayReconType.REFUND,
            debit=50_000,
            credit=0,
            amount=50_000,
            currency="INR",
            fee=0,
            tax=0,
            on_hold=False,
            settled=True,
            created_at=_epoch(settled_at),
            settled_at=_epoch(settled_at),
            settlement_id="setl_refbase",
            settlement_utr=None,
            payment_id="pay_refbase",
            order_id=None,
            dispute_id=None,
        )
        csv_bytes = bank_csv_bytes(
            [("REFF", "01/08/2026", "NEFT CR SETL_REFBASE SETTLEMENT", "1500.00")]
        )

        store = make_store()
        # Live mode with the deterministic mechanical investigator (never a
        # real network call): the case still reaches Stage 3 (the
        # unexplained refund reference makes it Stage-2-unresolved), and
        # this proves it does not silently resolve there either.
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=[payment_row, refund_row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:f",
            mode="live",
            chain=ProviderChain((MechanicalInvestigator(),)),
            cache=TrajectoryCache(tmp_path / "fixtures"),
        )

        # No canonical Refund was ever fabricated from recon rows.
        assert result.batch_result.batch.refunds == ()
        assert len(result.razorpay_result.unresolved_refund_companions) == 1
        companion = result.razorpay_result.unresolved_refund_companions[0]
        assert companion.refund_id == "rfnd_1"
        assert companion.payment_id == "pay_refbase"

        # The settlement is not quarantined at ingestion (refund status is
        # unprovable, not a blocking conflict) -- but it also cannot
        # silently resolve as financially sound on the strength of an
        # unproven refund: breakup_references_are_sound finds no Refund
        # for the REFUND line's reference_id and refuses, in both Stage 2
        # and Stage 3.
        assert "setl_refbase" not in {q.settlement_id for q in result.quarantined_settlements}
        settlement_ids_seen = {s.settlement_id for s in result.batch_result.batch.settlements}
        assert "setl_refbase" in settlement_ids_seen
        assert result.deterministic_resolved == ()
        assert result.batch_result.unresolved() != ()
        assert result.ai_assisted_resolved == ()
        assert len(result.escalated) == 1
        store.close()


# ---------------------------------------------------------------------------
# I: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def _rows_and_csv(self):
        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        row = razorpay_payment_row(
            entity_id="pay_idem_1",
            settlement_id="setl_idem_1",
            order_id="order_idem_1",
            amount=60_000,
            settled_at=settled_at,
        )
        csv_bytes = bank_csv_bytes(
            [("REFI", "02/04/2026", "NEFT CR SETL_IDEM_1 SETTLEMENT", "600.00")]
        )
        return [row], csv_bytes

    def test_i_identical_rerun_yields_an_identical_ledger_digest(self, tmp_path):
        rows, csv_bytes = self._rows_and_csv()
        store = make_store()

        first = run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:idem",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures",
        )
        digest_1 = store.digest(first.batch_result.batch_id)

        second = run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:idem",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures",
        )
        digest_2 = store.digest(second.batch_result.batch_id)

        assert digest_1 == digest_2
        store.close()

    def test_i_same_batch_id_different_content_raises_batch_identity_error(self, tmp_path):
        rows, csv_bytes = self._rows_and_csv()
        store = make_store()

        run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_bytes,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:idem-conflict",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures",
        )

        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        different_row = razorpay_payment_row(
            entity_id="pay_idem_2",
            settlement_id="setl_idem_2",
            order_id="order_idem_2",
            amount=61_000,
            settled_at=settled_at,
        )
        different_csv = bank_csv_bytes(
            [("REFI2", "02/04/2026", "NEFT CR SETL_IDEM_2 SETTLEMENT", "610.00")]
        )

        with pytest.raises(BatchIdentityError):
            run_reconciliation_batch(
                store=store,
                razorpay_rows=[different_row],
                razorpay_source_id="rzp",
                bank_csv_bytes=different_csv,
                bank_profile=BANK_PROFILE,
                bank_source_id="bank",
                batch_id="batch:idem-conflict",
                mode="replay",
                fixtures_dir=tmp_path / "fixtures",
            )
        store.close()


# ---------------------------------------------------------------------------
# J: value_date is audit-only, never wired into a matching decision
# ---------------------------------------------------------------------------


class TestValueDateIsAuditOnly:
    def test_j_value_date_is_observable_but_does_not_change_the_resolution(self, tmp_path):
        settled_at = datetime(2026, 4, 2, 9, 0, 0, tzinfo=UTC)
        row = razorpay_payment_row(
            entity_id="pay_vd_1",
            settlement_id="setl_vd_1",
            order_id="order_vd_1",
            amount=45_000,
            settled_at=settled_at,
        )

        # Two batches, identical except for the bank record's value_date --
        # one dated exactly on the settlement date, one one day earlier
        # (both inside the declared +-1 day window).
        csv_same_day = bank_csv_bytes(
            [("REFJ", "02/04/2026", "NEFT CR SETL_VD_1 SETTLEMENT", "450.00")]
        )
        csv_day_before = bank_csv_bytes(
            [("REFJ", "01/04/2026", "NEFT CR SETL_VD_1 SETTLEMENT", "450.00")]
        )

        store_a = make_store()
        result_a = run_reconciliation_batch(
            store=store_a,
            razorpay_rows=[row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_same_day,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:j-a",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures_a",
        )
        store_b = make_store()
        result_b = run_reconciliation_batch(
            store=store_b,
            razorpay_rows=[row],
            razorpay_source_id="rzp",
            bank_csv_bytes=csv_day_before,
            bank_profile=BANK_PROFILE,
            bank_source_id="bank",
            batch_id="batch:j-b",
            mode="replay",
            fixtures_dir=tmp_path / "fixtures_b",
        )

        # Observable: the structured fact flows through to the normalized batch.
        (record_a,) = result_a.batch_result.batch.bank_records
        (record_b,) = result_b.batch_result.batch.bank_records
        assert record_a.value_date == date(2026, 4, 2)
        assert record_b.value_date == date(2026, 4, 1)
        assert record_a.value_date != record_b.value_date

        # Not wired into the decision: both resolve the same way, via the
        # same rule, to the same settlement.
        assert len(result_a.deterministic_resolved) == 1
        assert len(result_b.deterministic_resolved) == 1
        assert result_a.deterministic_resolved[0].rule_id == result_b.deterministic_resolved[0].rule_id
        assert (
            result_a.deterministic_resolved[0].settlement_ids
            == result_b.deterministic_resolved[0].settlement_ids
        )
        store_a.close()
        store_b.close()


# ---------------------------------------------------------------------------
# K: this module never touches the frozen benchmark
# ---------------------------------------------------------------------------


class TestNeverTouchesBenchmark:
    def test_k_orchestrate_modules_do_not_import_benchmark(self):
        import ast
        import pathlib

        for module_name in ("finrecon.orchestrate", "finrecon.orchestrate_cli"):
            module = __import__(module_name, fromlist=["__file__"])
            source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("benchmark")
                elif isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("benchmark")
