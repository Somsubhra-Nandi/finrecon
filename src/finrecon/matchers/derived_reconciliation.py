"""Derived reconciliation — the deterministic no-direct-key stage.

DESIGN.md §5.2's derived case is one where no usable join key survives but
*structured financial evidence remains*: the settlement break-up, the
fee/GST arithmetic, refund offsets, transfers, declared adjustments, the
settlement-to-value-date relationship, and which payment attempt a
settlement actually names.

The rule, in full — every clause is a predicate, and all must hold:

1. The settlement group's date falls inside the declared value-date window.
2. The group's amounts total the bank credit **exactly**, to the paise.
3. Each settlement's break-up totals that settlement's amount **exactly**,
   so every paise of fee, tax, refund, transfer and declared adjustment is
   named by a line.
4. Every referencing break-up line points at a real record in a terminal
   successful state for the exact same amount — which is what makes a
   duplicate-payment case *provable*: the settlement names the captured
   attempt, so the failed sibling is excluded by evidence.
5. Exactly one group in the whole batch satisfies 1–4.
6. No other credit in the batch claims any settlement in that group.

There is no weighted score anywhere in this module, and deliberately so
(DESIGN.md §4.3): a case either satisfies the predicates or it does not.
Two satisfying groups is a refusal, not a contest between an 87 and an 81.

**This stage is tier-blind.** It cannot know whether a credit came from a
benchmark T1, T2 or T3 case, so it applies rules 1–6 uniformly to every
credit the direct-key stage did not resolve. Notably it does not attempt —
and must not attempt — to recover a degraded reference out of narration to
help itself along; the narration is never read here at all.
"""

from __future__ import annotations

from datetime import date

from finrecon.matchers.blocking import (
    SettlementGroup,
    enumerate_exact_groups,
    index_by_settlement_date,
    settlements_in_window,
)
from finrecon.matchers.derivation import breakup_references_are_sound, derive_group
from finrecon.matchers.evidence import DateWindowEvidence, DecisionEvidence
from finrecon.matchers.result import DecisionStatus, ReconciliationDecision
from finrecon.matchers.rules import (
    DERIVED_MATCHER_ID,
    MAX_SETTLEMENT_GROUP_SIZE,
    RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
    RULE_UNRESOLVED_COUNTERPARTY_CONTENTION,
    RULE_UNRESOLVED_MULTIPLE_DERIVED,
    RULE_UNRESOLVED_NO_CANDIDATE,
    VALUE_DATE_WINDOW_DAYS_AFTER,
    VALUE_DATE_WINDOW_DAYS_BEFORE,
)
from finrecon.normalize.records import (
    NormalizedBankRecord,
    NormalizedBatch,
    NormalizedSettlement,
)


def _date_window_evidence(
    bank_record: NormalizedBankRecord, settlements: tuple[NormalizedSettlement, ...]
) -> DateWindowEvidence:
    dates = tuple(s.settlement_date_utc for s in settlements)
    return DateWindowEvidence(
        bank_value_date=bank_record.value_date,
        settlement_dates=dates,
        offset_days=tuple((bank_record.value_date - d).days for d in dates),
        window_days_before=VALUE_DATE_WINDOW_DAYS_BEFORE,
        window_days_after=VALUE_DATE_WINDOW_DAYS_AFTER,
    )


def provable_groups(
    bank_record: NormalizedBankRecord,
    batch: NormalizedBatch,
    available: tuple[NormalizedSettlement, ...],
    by_date: dict[date, list[NormalizedSettlement]] | None = None,
) -> tuple[tuple[SettlementGroup, ...], tuple[NormalizedSettlement, ...]]:
    """Groups satisfying predicates 1–4, plus the in-window set they came from."""
    index = by_date if by_date is not None else index_by_settlement_date(available)
    in_window = settlements_in_window(bank_record, index)
    payments = batch.payment_by_id()
    refunds = batch.refund_by_id()

    surviving: list[SettlementGroup] = []
    for group in enumerate_exact_groups(bank_record, in_window, MAX_SETTLEMENT_GROUP_SIZE):
        money = derive_group(bank_record, group.settlements, payments, refunds)
        if not money.is_exact:
            continue
        if not all(breakup_references_are_sound(s, payments, refunds) for s in group.settlements):
            continue
        surviving.append(group)
    return tuple(surviving), in_window


def match_derived(
    bank_record: NormalizedBankRecord,
    batch: NormalizedBatch,
    available: tuple[NormalizedSettlement, ...],
    case_id: str,
    by_date: dict[date, list[NormalizedSettlement]] | None = None,
) -> ReconciliationDecision:
    """Attempt derived reconciliation for one bank credit (predicates 1–5)."""
    groups, in_window = provable_groups(bank_record, batch, available, by_date)
    considered = tuple(s.settlement_id for s in in_window)

    if not groups:
        return ReconciliationDecision(
            case_id=case_id,
            bank_record_id=bank_record.bank_record_id,
            status=DecisionStatus.UNRESOLVED,
            matcher_id=DERIVED_MATCHER_ID,
            rule_id=RULE_UNRESOLVED_NO_CANDIDATE,
            evidence=DecisionEvidence(
                considered_settlement_ids=considered,
                date_window=_date_window_evidence(bank_record, in_window),
            ),
        )

    if len(groups) > 1:
        return ReconciliationDecision(
            case_id=case_id,
            bank_record_id=bank_record.bank_record_id,
            status=DecisionStatus.UNRESOLVED,
            matcher_id=DERIVED_MATCHER_ID,
            rule_id=RULE_UNRESOLVED_MULTIPLE_DERIVED,
            evidence=DecisionEvidence(
                considered_settlement_ids=considered,
                competing_solution_ids=tuple(g.settlement_ids for g in groups),
                date_window=_date_window_evidence(bank_record, in_window),
            ),
        )

    group = groups[0]
    money = derive_group(bank_record, group.settlements, batch.payment_by_id(), batch.refund_by_id())
    return ReconciliationDecision(
        case_id=case_id,
        bank_record_id=bank_record.bank_record_id,
        status=DecisionStatus.RESOLVED,
        matcher_id=DERIVED_MATCHER_ID,
        rule_id=RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
        settlement_ids=group.settlement_ids,
        relationship="one_to_one" if group.size == 1 else "many_to_one",
        evidence=DecisionEvidence(
            money=money,
            considered_settlement_ids=considered,
            date_window=_date_window_evidence(bank_record, group.settlements),
        ),
    )


def withdraw_contended(
    decisions: tuple[ReconciliationDecision, ...],
) -> tuple[ReconciliationDecision, ...]:
    """Predicate 6: retract any resolution whose settlement another credit also claims.

    Each credit is matched independently against the same pool, which
    keeps the stage order-independent — no credit gets first pick simply
    because its ID sorts earlier. The cost is that two credits can land on
    the same settlement, so contention is resolved here, afterwards, by
    retracting *both* rather than awarding it to either. That mirrors
    DESIGN.md §4.3's "counterparty already resolved in this run" blocker
    without smuggling in an arbitrary tie-break.
    """
    claim_count: dict[str, int] = {}
    for decision in decisions:
        for settlement_id in decision.settlement_ids:
            claim_count[settlement_id] = claim_count.get(settlement_id, 0) + 1

    out: list[ReconciliationDecision] = []
    for decision in decisions:
        contended = tuple(
            sorted(sid for sid in decision.settlement_ids if claim_count.get(sid, 0) > 1)
        )
        if not contended:
            out.append(decision)
            continue
        out.append(
            ReconciliationDecision(
                case_id=decision.case_id,
                bank_record_id=decision.bank_record_id,
                status=DecisionStatus.UNRESOLVED,
                matcher_id=decision.matcher_id,
                rule_id=RULE_UNRESOLVED_COUNTERPARTY_CONTENTION,
                evidence=DecisionEvidence(
                    references=decision.evidence.references,
                    money=decision.evidence.money,
                    date_window=decision.evidence.date_window,
                    considered_settlement_ids=decision.evidence.considered_settlement_ids,
                    competing_solution_ids=(decision.settlement_ids,),
                ),
            )
        )
    return tuple(out)
