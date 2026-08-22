"""Deterministic Stage-2 batch orchestration.

    normalize
        -> direct-key reconciliation
            -> derived reconciliation over the residual
                -> candidate generation + immutable snapshot for what remains
                    -> ledger + audit trail

This is a fixed sequence of pure functions with a persistence step at the
end. It is not, and must not become, an orchestrator that decides what to
run next — that shape belongs to the Stage-3 investigation agent, which
does not exist. Nothing here loops, retries, branches on a model output, or
calls out of process.

Determinism, concretely: every stage consumes deterministically ordered
input, each bank credit is matched independently against the same pool
rather than racing others for it, and contention is settled afterwards by
retracting both claims. So the result depends on the batch's *content*
alone — never on file read order, dict iteration order, or which credit
happened to be processed first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from finrecon.candidates.generator import build_unresolved_snapshot, generate_candidates
from finrecon.candidates.snapshot import CandidateRecord, CaseSnapshot
from finrecon.ledger.store import LedgerStore
from finrecon.loader import load_visible_split
from finrecon.matchers.blocking import index_by_settlement_date
from finrecon.matchers.derived_reconciliation import match_derived, withdraw_contended
from finrecon.matchers.direct_key_matcher import DirectKeyIndex, match_direct_key
from finrecon.matchers.result import DecisionStatus, ReconciliationDecision
from finrecon.normalize.records import NormalizedBatch, normalize_batch


def case_id_for(bank_record_id: str) -> str:
    """Stage-2 case identity, derived from the credit under reconciliation.

    One bank credit is one reconciliation decision (DESIGN.md §5.0's
    "case"), so the credit's ID is the natural case key. It is derived from
    a *visible* record deliberately: the benchmark's own case IDs live in
    the hidden ground truth, and keying off those would wire the answer
    into the pipeline.
    """
    return f"case:{bank_record_id}"


@dataclass(frozen=True)
class BatchResult:
    """Everything one deterministic pass produced. Holds no metrics."""

    batch_id: str
    split: str
    content_fingerprint: str
    batch: NormalizedBatch
    decisions: tuple[ReconciliationDecision, ...]
    snapshots: tuple[CaseSnapshot, ...]
    candidates_by_case: dict[str, tuple[CandidateRecord, ...]]

    def resolved(self) -> tuple[ReconciliationDecision, ...]:
        return tuple(d for d in self.decisions if d.status is DecisionStatus.RESOLVED)

    def unresolved(self) -> tuple[ReconciliationDecision, ...]:
        return tuple(d for d in self.decisions if d.status is DecisionStatus.UNRESOLVED)


def reconcile_batch(batch: NormalizedBatch, batch_id: str = "batch:unnamed") -> tuple[
    tuple[ReconciliationDecision, ...],
    tuple[CaseSnapshot, ...],
    dict[str, tuple[CandidateRecord, ...]],
]:
    """Run the deterministic core over a normalized batch. Touches no storage."""
    # --- direct-key stage ------------------------------------------------
    index = DirectKeyIndex(batch.settlements)
    direct = tuple(
        match_direct_key(record, batch, index, case_id_for(record.bank_record_id))
        for record in batch.bank_records
    )
    direct = withdraw_contended(direct)

    claimed = {sid for d in direct for sid in d.settlement_ids}
    residual = tuple(d for d in direct if d.status is DecisionStatus.UNRESOLVED)

    # --- derived stage ---------------------------------------------------
    available = tuple(s for s in batch.settlements if s.settlement_id not in claimed)
    by_date = index_by_settlement_date(available)
    by_bank_id = {r.bank_record_id: r for r in batch.bank_records}

    derived = tuple(
        match_derived(
            by_bank_id[d.bank_record_id],
            batch,
            available,
            d.case_id,
            by_date,
        )
        for d in residual
    )
    derived = withdraw_contended(derived)

    decisions = tuple(
        sorted(
            [d for d in direct if d.status is DecisionStatus.RESOLVED] + list(derived),
            key=lambda d: d.case_id,
        )
    )

    # --- candidate generation for the residual ---------------------------
    still_claimed = {sid for d in decisions for sid in d.settlement_ids}
    still_available = tuple(s for s in batch.settlements if s.settlement_id not in still_claimed)

    snapshots: list[CaseSnapshot] = []
    candidates_by_case: dict[str, tuple[CandidateRecord, ...]] = {}
    for decision in decisions:
        if decision.status is not DecisionStatus.UNRESOLVED:
            continue
        record = by_bank_id[decision.bank_record_id]
        candidates = generate_candidates(record, batch, still_available)
        candidates_by_case[decision.case_id] = candidates
        snapshots.append(
            build_unresolved_snapshot(
                batch_id=batch_id,
                decision=decision,
                bank_record=record,
                batch=batch,
                candidates=candidates,
            )
        )

    return decisions, tuple(snapshots), candidates_by_case


def persist_batch(
    store: LedgerStore,
    *,
    batch_id: str,
    split: str,
    content_fingerprint: str,
    batch: NormalizedBatch,
    decisions: tuple[ReconciliationDecision, ...],
    snapshots: tuple[CaseSnapshot, ...],
    candidates_by_case: dict[str, tuple[CandidateRecord, ...]],
) -> None:
    """Write one pass to the ledger. Replaying an identical pass is a no-op."""
    store.register_batch(
        batch_id=batch_id,
        split=split,
        content_fingerprint=content_fingerprint,
        record_count=batch.record_count(),
        case_count=len(decisions),
    )

    amounts = {r.bank_record_id: int(r.amount_paise) for r in batch.bank_records}
    snapshot_by_case = {s.case_id: s for s in snapshots}

    # `sequence` is the decision's index in case-ID order, not an arrival
    # counter, so it is identical on every rerun and the audit IDs derived
    # from it deduplicate rather than accumulate.
    for sequence, decision in enumerate(decisions):
        store.record_decision(batch_id, decision, amounts[decision.bank_record_id])
        store.record_audit(batch_id, decision, sequence)
        candidates = candidates_by_case.get(decision.case_id)
        if candidates:
            store.record_candidates(batch_id, decision.case_id, candidates)
        snapshot = snapshot_by_case.get(decision.case_id)
        if snapshot is not None:
            store.record_snapshot(snapshot)


def process_batch(
    *,
    store: LedgerStore,
    benchmark_dir: Path,
    split: str,
    batch_id: str | None = None,
) -> BatchResult:
    """Load, normalize, reconcile and persist one split. The Stage-2 entry point."""
    visible = load_visible_split(benchmark_dir, split)
    batch = normalize_batch(
        orders=visible.orders,
        payments=visible.payments,
        refunds=visible.refunds,
        settlements=visible.settlements,
        bank_records=visible.bank_records,
    )
    resolved_batch_id = batch_id or f"batch:{split}"

    decisions, snapshots, candidates_by_case = reconcile_batch(batch, resolved_batch_id)

    persist_batch(
        store,
        batch_id=resolved_batch_id,
        split=split,
        content_fingerprint=visible.content_fingerprint,
        batch=batch,
        decisions=decisions,
        snapshots=snapshots,
        candidates_by_case=candidates_by_case,
    )

    return BatchResult(
        batch_id=resolved_batch_id,
        split=split,
        content_fingerprint=visible.content_fingerprint,
        batch=batch,
        decisions=decisions,
        snapshots=snapshots,
        candidates_by_case=candidates_by_case,
    )
