"""Deterministic candidate generation for cases the rules could not settle.

DESIGN.md §3/§4.1 is explicit about this component's job and its limits:
it blocks and shortlists counterparties, and it **never chooses between
them**. The set it produces is handed to the decision layer directly,
before any agent exists, precisely so that a later investigation stage can
add evidence to a case but can never quietly shrink it (§4.1, §11 —
"fishing by omission").

Two declared blocking rules, applied in order:

``exact_total_in_window``
    Every in-window settlement group, up to the declared size bound, whose
    amounts total the credit exactly and whose break-up accounts for every
    paise. This is the *same* enumeration the derived matcher searched
    (:mod:`finrecon.matchers.blocking`), which is what makes the candidate
    set complete with respect to the matcher: the matcher cannot have seen
    a group this generator would omit.

``date_window_only``
    A widening fallback, used **only** when the strict rule yields nothing:
    every unclaimed settlement inside the declared value-date window,
    regardless of amount. A credit whose counterparty cannot be totalled
    exactly still needs a non-empty case file for a later stage to work
    from, and an empty candidate set would silently foreclose that.

Both rules are structural. Neither reads narration, computes similarity,
consults hidden ground truth, or ranks what it returns — candidates come
back in settlement-ID order, which carries no preference.
"""

from __future__ import annotations

from finrecon.candidates.snapshot import (
    BankRecordFacts,
    BaseEvidence,
    CandidateRecord,
    CaseSnapshot,
    SettlementFacts,
    build_case_snapshot,
)
from finrecon.matchers.derivation import derive_settlement
from finrecon.matchers.result import ReconciliationDecision
from finrecon.matchers.blocking import (
    index_by_settlement_date,
    settlements_in_window,
)
from finrecon.matchers.derivation import derive_group
from finrecon.matchers.derived_reconciliation import provable_groups
from finrecon.matchers.rules import (
    CANDIDATE_GENERATOR_ID,
    MAX_SETTLEMENT_GROUP_SIZE,
    VALUE_DATE_WINDOW_DAYS_AFTER,
    VALUE_DATE_WINDOW_DAYS_BEFORE,
)
from finrecon.normalize.records import (
    NormalizedBankRecord,
    NormalizedBatch,
    NormalizedSettlement,
)

BLOCKING_RULE_EXACT_TOTAL = "exact_total_in_window"
BLOCKING_RULE_DATE_WINDOW_ONLY = "date_window_only"


def candidate_id_for(bank_record_id: str, settlement_ids: tuple[str, ...]) -> str:
    """Stable candidate identity: the credit plus its settlement group.

    Deterministic and content-derived, so the same batch reprocessed
    produces the same candidate IDs and the ledger's uniqueness
    constraints deduplicate on rerun rather than accumulating rows.
    """
    return f"{bank_record_id}|" + "+".join(settlement_ids)


def generate_candidates(
    bank_record: NormalizedBankRecord,
    batch: NormalizedBatch,
    available: tuple[NormalizedSettlement, ...],
) -> tuple[CandidateRecord, ...]:
    """The complete plausible candidate set for one unresolved credit.

    Returns candidates in deterministic settlement-ID order. Ordering is
    presentation, not ranking: no candidate is marked preferred, and the
    generator returns two candidates for a genuinely ambiguous credit
    rather than picking one.
    """
    payments = batch.payment_by_id()
    refunds = batch.refund_by_id()
    by_date = index_by_settlement_date(available)

    groups, in_window = provable_groups(bank_record, batch, available, by_date)

    candidates: list[CandidateRecord] = []
    for group in groups:
        money = derive_group(bank_record, group.settlements, payments, refunds)
        candidates.append(
            CandidateRecord(
                candidate_id=candidate_id_for(bank_record.bank_record_id, group.settlement_ids),
                settlement_ids=group.settlement_ids,
                total_paise=group.total_paise,
                blocking_rule=BLOCKING_RULE_EXACT_TOTAL,
                unexplained_delta_paise=money.unexplained_delta_paise,
                settlement_dates=tuple(s.settlement_date_utc for s in group.settlements),
            )
        )

    if not candidates:
        for settlement in settlements_in_window(bank_record, by_date):
            money = derive_group(bank_record, (settlement,), payments, refunds)
            candidates.append(
                CandidateRecord(
                    candidate_id=candidate_id_for(
                        bank_record.bank_record_id, (settlement.settlement_id,)
                    ),
                    settlement_ids=(settlement.settlement_id,),
                    total_paise=int(settlement.amount_paise),
                    blocking_rule=BLOCKING_RULE_DATE_WINDOW_ONLY,
                    unexplained_delta_paise=money.unexplained_delta_paise,
                    settlement_dates=(settlement.settlement_date_utc,),
                )
            )

    candidates.sort(key=lambda c: (c.settlement_ids, c.blocking_rule))
    del in_window
    return tuple(candidates)


def build_unresolved_snapshot(
    *,
    batch_id: str,
    decision: ReconciliationDecision,
    bank_record: NormalizedBankRecord,
    batch: NormalizedBatch,
    candidates: tuple[CandidateRecord, ...],
) -> CaseSnapshot:
    """Freeze one unresolved case together with its complete candidate set.

    Settlement facts are emitted for the union of every settlement named
    by any candidate, so the snapshot is self-contained: an auditor — or a
    later stage — never has to go back to the batch to learn what a
    candidate refers to, and cannot be handed a candidate whose facts were
    withheld.
    """
    payments = batch.payment_by_id()
    refunds = batch.refund_by_id()
    by_id = batch.settlement_by_id()

    referenced = sorted({sid for candidate in candidates for sid in candidate.settlement_ids})
    settlement_facts = tuple(
        SettlementFacts(
            settlement_id=settlement.settlement_id,
            utr=settlement.utr,
            utr_key=settlement.utr_key,
            amount_paise=int(settlement.amount_paise),
            created_at_utc=settlement.created_at_utc,
            settlement_date_utc=settlement.settlement_date_utc,
            derivation=derive_settlement(settlement, payments, refunds),
            source=settlement.source,
        )
        for settlement in (by_id[sid] for sid in referenced)
    )

    base_evidence = BaseEvidence(
        bank_record=BankRecordFacts(
            bank_record_id=bank_record.bank_record_id,
            amount_paise=int(bank_record.amount_paise),
            direction=bank_record.direction,
            narration=bank_record.narration,
            reference_tokens=bank_record.reference_tokens,
            value_date=bank_record.value_date,
            source=bank_record.source,
        ),
        settlement_facts=settlement_facts,
        decision_evidence=decision.evidence,
        blocking=tuple(sorted((k, str(v)) for k, v in blocking_description().items())),
    )

    return build_case_snapshot(
        case_id=decision.case_id,
        batch_id=batch_id,
        bank_record_id=bank_record.bank_record_id,
        unresolved_rule_id=decision.rule_id,
        unresolved_matcher_id=decision.matcher_id,
        candidates=candidates,
        base_evidence=base_evidence,
    )


def blocking_description() -> dict[str, int | str]:
    """The declared blocking parameters, recorded on every snapshot.

    Written into the case snapshot so a bounded search is visible as a
    bounded search: an auditor can see the window and the group-size cap
    that shaped the candidate set, rather than inferring that the set was
    exhaustive.
    """
    return {
        "generator_id": CANDIDATE_GENERATOR_ID,
        "value_date_window_days_before": VALUE_DATE_WINDOW_DAYS_BEFORE,
        "value_date_window_days_after": VALUE_DATE_WINDOW_DAYS_AFTER,
        "max_settlement_group_size": MAX_SETTLEMENT_GROUP_SIZE,
    }
