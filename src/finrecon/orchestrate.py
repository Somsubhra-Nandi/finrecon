"""End-to-end FinRecon entrypoint: raw external inputs -> existing decisions.

This module is pure plumbing over mature, already-documented subsystems --
it adds no matching, no validator/policy rule, no value-date semantics, and
no new provider. It exists only to connect two ingestion adapters to the
Stage-2/Stage-3 pipeline the same way ``process_batch``/``run_stage3``
already connect ``loader.py`` to it, without touching any of those modules.

.. code-block:: text

    Razorpay recon rows -> build_recon_result -> eligible_settlements() + payments
    bank CSV bytes      -> parse_bank_csv     -> records
                                                     \\
                                                      normalize_batch(orders=[], refunds=[], ...)
                                                       -> reconcile_batch -> persist_batch   (Stage 2)
                                                        -> run_stage3                        (Stage 3, unresolved only)

Two adapter facts drive what is (and is not) fed to the engine, and neither
is re-derived here -- see the adapters' own docstrings for the reasoning:

* ``RazorpayReconAdapterResult.refunds`` is always empty by design (recon
  rows can never prove ``RefundStatus.PROCESSED``); this module passes
  ``refunds=[]`` into ``normalize_batch`` rather than inventing canonical
  ``Refund`` records. ``unresolved_refund_companions`` is carried through on
  the result for audit only.
* The adapter never builds a canonical ``Order`` (nothing in the engine's
  ``breakup_references_are_sound`` predicate looks one up), so ``orders=[]``
  is passed too -- not a stub, an accurate reflection of what recon rows can
  prove.
* ``quarantined_settlements`` (Razorpay) and ``rejected_rows`` (bank CSV)
  are audit-only ingestion artifacts and are never merged into the
  decision-eligible collections handed to ``normalize_batch``.

Stage-2 -> Stage-3 handoff is exactly what ``run_stage3`` already
guarantees: it reads only ``batch_result.snapshots``, i.e. the cases Stage 2
left unresolved. Nothing here re-derives that filter.

This module must never import anything under ``benchmark/`` (ground truth
or otherwise) -- it is a production entrypoint, not a benchmark harness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from finrecon.adapters.bank.csv_parser import (
    BankCsvAdapterResult,
    RejectedBankRow,
    parse_bank_csv,
)
from finrecon.adapters.bank.csv_profile import BankCsvProfile, DebitCreditColumns
from finrecon.adapters.bank.manifest import BankIngestConflict, BankIngestManifest
from finrecon.adapters.manifest import IngestConflict, IngestManifest, IngestWarning
from finrecon.adapters.razorpay.recon import (
    QuarantinedSettlement,
    RazorpayReconAdapterResult,
    ReconRowCollection,
    UnresolvedRefundCompanion,
    build_recon_result,
)
from finrecon.adapters.razorpay.recon_row import RazorpayReconRow
from finrecon.agent.cache import DEFAULT_FIXTURE_DIR, TrajectoryCache
from finrecon.agent.loop import LoopConfig
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.providers.config import build_chain, describe_configuration
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.ledger.audit import canonical_json
from finrecon.ledger.human import HumanResolution
from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import ReconciliationDecision
from finrecon.models import Order, Payment, Refund, Settlement, BankRecord
from finrecon.normalize.records import normalize_batch
from finrecon.pipeline import BatchResult, persist_batch, reconcile_batch
from finrecon.stage3 import CaseOutcome, Stage3Result, run_stage3

VALID_MODES: tuple[str, ...] = ("replay", "live")


def content_fingerprint_for_batch(
    *,
    orders: Sequence[Order],
    payments: Sequence[Payment],
    refunds: Sequence[Refund],
    settlements: Sequence[Settlement],
    bank_records: Sequence[BankRecord],
    ingestion_fingerprint: str | None = None,
) -> str:
    """A deterministic content fingerprint over raw eligible input records.

    ``loader.py``'s ``_fingerprint`` hashes visible *file bytes*; there are
    no files here, only in-memory canonical objects the adapters produced,
    so this hashes canonical JSON of the records themselves instead. Same
    spirit -- a stable digest ``register_batch`` can compare a rerun
    against -- over a different substrate.

    Records are sorted by their own canonical JSON (not by any ID field)
    before hashing, so the fingerprint is independent of whatever order the
    adapters happened to emit them in, matching ``normalize_batch``'s own
    determinism guarantee.
    """

    def _dump_sorted(records: Sequence) -> list[dict]:
        dumped = [r.model_dump(mode="json") for r in records]
        return sorted(dumped, key=canonical_json)

    manifest = {
        "orders": _dump_sorted(orders),
        "payments": _dump_sorted(payments),
        "refunds": _dump_sorted(refunds),
        "settlements": _dump_sorted(settlements),
        "bank_records": _dump_sorted(bank_records),
        # Includes source material that did not become canonical input, so
        # batch identity remains fail-closed for rejected/quarantined rows.
        "ingestion_fingerprint": ingestion_fingerprint,
    }
    return hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BatchOrchestrationResult:
    """One batch, end to end: ingestion audit trail plus Stage-2/Stage-3 outcomes.

    Deliberately holds references to what Stage 2/Stage 3 already produced
    (``BatchResult``, ``Stage3Result``) rather than copying their fields
    into a new schema -- this type is a bridge, not a second source of
    truth.
    """

    batch_result: BatchResult
    stage3_result: Stage3Result
    razorpay_result: RazorpayReconAdapterResult
    bank_result: BankCsvAdapterResult
    human_resolutions: tuple[HumanResolution, ...] = ()

    # --- Stage-2/Stage-3 outcomes -----------------------------------------

    @property
    def deterministic_resolved(self) -> tuple[ReconciliationDecision, ...]:
        """Cases Stage 2 resolved on its own -- no investigation involved."""
        return self.batch_result.resolved()

    @property
    def ai_assisted_resolved(self) -> tuple[CaseOutcome, ...]:
        """Stage-2-unresolved cases Stage 3 investigated and resolved."""
        return self.stage3_result.resolved()

    @property
    def escalated(self) -> tuple[CaseOutcome, ...]:
        """Stage-2-unresolved cases Stage 3 investigated and could not resolve."""
        return self.stage3_result.escalated()

    @property
    def human_resolved(self) -> tuple[HumanResolution, ...]:
        """Exact snapshot-bound human selections authoritative this run."""
        return tuple(resolution for resolution in self.human_resolutions if resolution.resolved)

    @property
    def total_cases(self) -> int:
        return len(self.batch_result.decisions)

    # --- ingestion audit ---------------------------------------------------

    @property
    def quarantined_settlements(self) -> tuple[QuarantinedSettlement, ...]:
        """Razorpay settlements kept out of the decision-eligible set. Audit only."""
        return self.razorpay_result.quarantined_settlements

    @property
    def rejected_bank_rows(self) -> tuple[RejectedBankRow, ...]:
        """Bank CSV rows that could not become a canonical record. Audit only."""
        return self.bank_result.rejected_rows

    @property
    def unresolved_refund_companions(self) -> tuple[UnresolvedRefundCompanion, ...]:
        """Refund facts recon could prove partially. Never fed to the engine."""
        return self.razorpay_result.unresolved_refund_companions

    @property
    def razorpay_conflicts(self) -> tuple[IngestConflict, ...]:
        return self.razorpay_result.conflicts

    @property
    def razorpay_warnings(self) -> tuple[IngestWarning, ...]:
        return self.razorpay_result.warnings

    @property
    def razorpay_manifest(self) -> IngestManifest:
        return self.razorpay_result.manifest

    @property
    def bank_conflicts(self) -> tuple[BankIngestConflict, ...]:
        return self.bank_result.conflicts

    @property
    def bank_manifest(self) -> BankIngestManifest:
        return self.bank_result.manifest

    @property
    def ingestion_quarantined_count(self) -> int:
        """Everything kept out of the engine at the ingestion boundary."""
        return len(self.quarantined_settlements) + len(self.rejected_bank_rows)

    # --- raw ingested counts -------------------------------------------------

    @property
    def ingested_settlement_count(self) -> int:
        return len(self.razorpay_result.eligible_settlements())

    @property
    def ingested_payment_count(self) -> int:
        return len(self.razorpay_result.payments)

    @property
    def ingested_bank_record_count(self) -> int:
        return len(self.bank_result.records)


def _ingestion_fingerprint(*, razorpay_rows: Sequence[RazorpayReconRow], razorpay_source_id: str,
                           bank_csv_bytes: bytes, bank_source_id: str,
                           bank_profile: BankCsvProfile) -> str:
    """Batch identity over all source material, including rejected input."""
    payload = {
        "razorpay_source_id": razorpay_source_id,
        "razorpay_rows": sorted((row.model_dump(mode="json") for row in razorpay_rows), key=canonical_json),
        "bank_source_id": bank_source_id,
        "bank_profile_id": bank_profile.profile_id,
        "bank_csv_sha256": hashlib.sha256(bank_csv_bytes).hexdigest(),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _persist_ingestion_audit(*, store: LedgerStore, batch_id: str,
                             razorpay: RazorpayReconAdapterResult,
                             bank: BankCsvAdapterResult, bank_profile: BankCsvProfile) -> None:
    """Persist bounded provenance/finding facts; never feed them to decisions."""
    for item in razorpay.manifest.rows:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="razorpay", source_id=item.source_id,
            event_type="source_row", subject_id=item.entity_id, fingerprint=item.row_fingerprint,
            payload={"settlement_id": item.settlement_id, "produced": list(item.produced),
                     "source_fields_used": list(item.source_fields_used), "dropped_fields": list(item.dropped_fields),
                     "unrecognized_fields": list(item.unrecognized_fields)})
    for item in razorpay.quarantined_settlements:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="razorpay", source_id=razorpay.manifest.source_id,
            event_type="quarantined_settlement", subject_id=item.settlement_id,
            fingerprint=hashlib.sha256(canonical_json({"settlement_id": item.settlement_id, "rows": list(item.row_fingerprints)}).encode()).hexdigest(),
            payload={"eligibility": "quarantined", "row_fingerprints": list(item.row_fingerprints),
                     "conflicts": [conflict.model_dump(mode="json") for conflict in item.blocking_conflicts]})
    for item in razorpay.conflicts:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="razorpay", source_id=razorpay.manifest.source_id,
            event_type="conflict", subject_id=item.settlement_id,
            fingerprint=hashlib.sha256(canonical_json(item.model_dump(mode="json")).encode()).hexdigest(),
            payload=item.model_dump(mode="json"))
    for item in razorpay.warnings:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="razorpay", source_id=razorpay.manifest.source_id,
            event_type="warning", subject_id=item.settlement_id,
            fingerprint=hashlib.sha256(canonical_json(item.model_dump(mode="json")).encode()).hexdigest(),
            payload=item.model_dump(mode="json"))
    for item in razorpay.unresolved_refund_companions:
        payload = {"refund_id": item.refund_id, "payment_id": item.payment_id, "amount_paise": int(item.amount),
                   "currency": item.currency, "settlement_id": item.settlement_id}
        store.record_ingestion_audit(batch_id=batch_id, source_kind="razorpay", source_id=razorpay.manifest.source_id,
            event_type="unresolved_refund_companion", subject_id=item.refund_id,
            fingerprint=hashlib.sha256(canonical_json(payload).encode()).hexdigest(), payload=payload)
    # The declared debit/credit inactive-side semantic travels with the
    # bank-row audit facts, so a reviewer reading a row's evidence can see
    # which reading produced it. Interpretation only -- raw values below are
    # never rewritten.
    money_semantics = (
        {"inactive_side_marker": bank_profile.money_columns.inactive_side_marker.value}
        if isinstance(bank_profile.money_columns, DebitCreditColumns)
        else {}
    )
    for item in bank.manifest.rows:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="bank", source_id=item.source_id,
            event_type="accepted_bank_row" if item.produced else "bank_row_not_produced", subject_id=str(item.row_index),
            fingerprint=item.row_fingerprint, payload={"profile_id": bank_profile.profile_id, "row_index": item.row_index,
            "produced": list(item.produced), "source_fields_used": list(item.source_fields_used),
            "dropped_fields": list(item.dropped_fields), **money_semantics})
    for item in bank.rejected_rows:
        store.record_ingestion_audit(batch_id=batch_id, source_kind="bank", source_id=bank.manifest.source_id,
            event_type="rejected_bank_row", subject_id=str(item.row_index), fingerprint=item.row_fingerprint,
            payload={"profile_id": bank_profile.profile_id, "row_index": item.row_index, "reason": item.reason,
                     "detail": item.detail, **money_semantics})
    for item in bank.conflicts:
        payload = item.model_dump(mode="json")
        store.record_ingestion_audit(batch_id=batch_id, source_kind="bank", source_id=bank.manifest.source_id,
            event_type="conflict", subject_id=",".join(str(index) for index in item.row_indices),
            fingerprint=hashlib.sha256(canonical_json(payload).encode()).hexdigest(), payload=payload)

def run_reconciliation_batch(
    *,
    store: LedgerStore,
    razorpay_rows: Sequence[RazorpayReconRow],
    razorpay_source_id: str,
    bank_csv_bytes: bytes,
    bank_profile: BankCsvProfile,
    bank_source_id: str,
    batch_id: str,
    split: str = "live",
    mode: str = "replay",
    chain: ProviderChain | None = None,
    cache: TrajectoryCache | None = None,
    fixtures_dir: Path | None = None,
    provider_id: str | None = None,
    model: str | None = None,
    config: LoopConfig | None = None,
    policy: Stage3Policy = DEFAULT_POLICY,
    case_ids: frozenset[str] | None = None,
    write_cache: bool = True,
) -> BatchOrchestrationResult:
    """Run one batch from raw Razorpay recon rows + a bank CSV to final outcomes.

    ``mode="replay"`` (the default) makes zero provider calls: every
    Stage-2-unresolved case must already have a cached trajectory or
    ``ReplayMissError`` propagates (never silently falls back to a live
    call). ``mode="live"`` builds the provider chain from the environment
    via ``build_chain()`` and lets ``ProviderConfigurationError`` propagate
    when nothing is configured -- resolved *before* any adapter work runs,
    so a misconfigured live run fails fast and cleanly, the same shape
    ``investigate_cli.py`` uses.

    A batch with only Stage-2-resolvable cases makes no Stage-3 call at all
    in either mode: ``run_stage3`` only ever reads ``batch_result.snapshots``
    (the Stage-2-unresolved cases), and an empty snapshot set means its
    internal loop never runs -- no cache path is touched, no chain is used.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode {mode!r}; expected one of {VALID_MODES}")

    resolved_provider_id = provider_id
    resolved_model = model
    resolved_chain: ProviderChain | None

    if mode == "replay":
        replay_only = True
        resolved_chain = None
        if resolved_provider_id is None or resolved_model is None:
            head = describe_configuration()["providers"][0]
            resolved_provider_id = resolved_provider_id or head["provider"]
            resolved_model = resolved_model or head["model"]
    else:
        replay_only = False
        # Built (or validated) before any ingestion work: a live run with no
        # credential configured should fail immediately and loudly, not
        # after quarantine/ingest bookkeeping has already happened.
        resolved_chain = chain if chain is not None else build_chain()
        if resolved_provider_id is None or resolved_model is None:
            resolved_provider_id = resolved_provider_id or resolved_chain.providers[0].provider_id
            resolved_model = resolved_model or resolved_chain.providers[0].model

    # --- ingestion: adapters only, never the loader ------------------------
    razorpay_result = build_recon_result(
        ReconRowCollection.of(razorpay_source_id, razorpay_rows)
    )
    bank_result = parse_bank_csv(bank_profile, bank_csv_bytes, bank_source_id)

    eligible_settlements = list(razorpay_result.eligible_settlements())
    payments = list(razorpay_result.payments)
    refunds: list[Refund] = list(razorpay_result.refunds)  # always (), by adapter design
    orders: list[Order] = []  # the adapter never builds one; nothing here should either
    bank_records = list(bank_result.records)

    batch = normalize_batch(
        orders=orders,
        payments=payments,
        refunds=refunds,
        settlements=eligible_settlements,
        bank_records=bank_records,
    )

    content_fingerprint = content_fingerprint_for_batch(
        orders=orders,
        payments=payments,
        refunds=refunds,
        settlements=eligible_settlements,
        bank_records=bank_records,
        ingestion_fingerprint=_ingestion_fingerprint(
            razorpay_rows=razorpay_rows,
            razorpay_source_id=razorpay_source_id,
            bank_csv_bytes=bank_csv_bytes,
            bank_source_id=bank_source_id,
            bank_profile=bank_profile,
        ),
    )

    # --- Stage 2: exactly what process_batch does, minus the file loader --
    decisions, snapshots, candidates_by_case = reconcile_batch(batch, batch_id)

    persist_batch(
        store,
        batch_id=batch_id,
        split=split,
        content_fingerprint=content_fingerprint,
        batch=batch,
        decisions=decisions,
        snapshots=snapshots,
        candidates_by_case=candidates_by_case,
    )
    _persist_ingestion_audit(
        store=store,
        batch_id=batch_id,
        razorpay=razorpay_result,
        bank=bank_result,
        bank_profile=bank_profile,
    )

    batch_result = BatchResult(
        batch_id=batch_id,
        split=split,
        content_fingerprint=content_fingerprint,
        batch=batch,
        decisions=decisions,
        snapshots=snapshots,
        candidates_by_case=candidates_by_case,
    )

    # --- Stage 3: only snapshots without exact active human authority ---
    # Snapshot hashing is deliberately the applicability test: same-looking
    # IDs with changed financial facts/candidates do not pass this boundary.
    active_human = tuple(
        resolution for snapshot in batch_result.snapshots
        if (resolution := store.get_active_human_resolution(snapshot)) is not None
    )
    snapshots_for_stage3 = tuple(
        snapshot for snapshot in batch_result.snapshots
        if store.get_active_human_resolution(snapshot) is None
    )
    stage3_batch = BatchResult(
        batch_id=batch_result.batch_id,
        split=batch_result.split,
        content_fingerprint=batch_result.content_fingerprint,
        batch=batch_result.batch,
        decisions=batch_result.decisions,
        snapshots=snapshots_for_stage3,
        candidates_by_case=batch_result.candidates_by_case,
    )
    resolved_cache = cache if cache is not None else TrajectoryCache(
        fixtures_dir if fixtures_dir is not None else DEFAULT_FIXTURE_DIR
    )

    stage3_result = run_stage3(
        store=store,
        batch_result=stage3_batch,
        chain=resolved_chain,
        cache=resolved_cache,
        config=config,
        policy=policy,
        replay_only=replay_only,
        provider_id=resolved_provider_id,
        model=resolved_model,
        case_ids=case_ids,
        write_cache=write_cache,
    )

    return BatchOrchestrationResult(
        batch_result=batch_result,
        stage3_result=stage3_result,
        razorpay_result=razorpay_result,
        bank_result=bank_result,
        human_resolutions=active_human,
    )


__all__ = [
    "BatchOrchestrationResult",
    "content_fingerprint_for_batch",
    "run_reconciliation_batch",
]
