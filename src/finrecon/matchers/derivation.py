"""Exact integer-paise derivation over settlement break-ups.

This module answers one question and only one: *does every paise of this
bank credit have a named source?* It never chooses between counterparties
and never scores anything — it computes the accounting and reports the
residual.

The accounting is the documented Razorpay settlement break-up
(DESIGN.md §5.2): ``payment`` credits the merchant, ``fee``, ``tax``,
``refund`` and ``transfer`` deduct, and ``adjustment`` is the single
declared channel for a paise-level correction. A credit reconciles when

    bank credit  ==  sum(settlement amounts)
    settlement amount  ==  sum(that settlement's break-up lines)

hold **exactly**, in integers, with no tolerance. A -1 paise
``adjustment`` line explains one paise because a line declares it; a
1-paise gap with no line behind it explains nothing and blocks the
resolution (DESIGN.md §4.3).
"""

from __future__ import annotations

from finrecon.matchers.evidence import (
    BreakupLineEvidence,
    MoneyDerivation,
    SettlementDerivation,
)
from finrecon.models import PaymentStatus, RefundStatus, SettlementLineType
from finrecon.normalize.records import (
    NormalizedBankRecord,
    NormalizedPayment,
    NormalizedRefund,
    NormalizedSettlement,
)


def derive_settlement(
    settlement: NormalizedSettlement,
    payments: dict[str, NormalizedPayment],
    refunds: dict[str, NormalizedRefund],
) -> SettlementDerivation:
    """Account for one settlement's break-up, line by line, in exact paise."""
    lines: list[BreakupLineEvidence] = []
    declared_adjustment = 0

    for line in settlement.breakup:
        reference_status: str | None = None
        if line.reference_id is not None:
            if line.type is SettlementLineType.PAYMENT:
                payment = payments.get(line.reference_id)
                reference_status = payment.status.value if payment else None
            elif line.type is SettlementLineType.REFUND:
                refund = refunds.get(line.reference_id)
                reference_status = refund.status.value if refund else None
        if line.type is SettlementLineType.ADJUSTMENT:
            declared_adjustment += int(line.amount_paise)
        lines.append(
            BreakupLineEvidence(
                line_type=line.type.value,
                amount_paise=int(line.amount_paise),
                reference_id=line.reference_id,
                reference_status=reference_status,
            )
        )

    breakup_total = settlement.breakup_total_paise
    return SettlementDerivation(
        settlement_id=settlement.settlement_id,
        settlement_amount_paise=int(settlement.amount_paise),
        breakup_total_paise=breakup_total,
        breakup_by_type=tuple(settlement.breakup_total_by_type().items()),
        lines=tuple(lines),
        unexplained_delta_paise=int(settlement.amount_paise) - breakup_total,
        declared_adjustment_paise=declared_adjustment,
    )


def derive_group(
    bank_record: NormalizedBankRecord,
    settlements: tuple[NormalizedSettlement, ...],
    payments: dict[str, NormalizedPayment],
    refunds: dict[str, NormalizedRefund],
) -> MoneyDerivation:
    """Account for a bank credit against a candidate group of settlements."""
    per_settlement = tuple(derive_settlement(s, payments, refunds) for s in settlements)
    group_total = sum(int(s.amount_paise) for s in settlements)
    return MoneyDerivation(
        bank_amount_paise=int(bank_record.amount_paise),
        settlement_group_total_paise=group_total,
        unexplained_delta_paise=int(bank_record.amount_paise) - group_total,
        per_settlement=per_settlement,
    )


def breakup_references_are_sound(
    settlement: NormalizedSettlement,
    payments: dict[str, NormalizedPayment],
    refunds: dict[str, NormalizedRefund],
) -> bool:
    """True when every referencing break-up line points at a real, consistent record.

    This is the structured predicate that makes duplicate-payment cases
    provable rather than guessed: a settlement's ``payment`` line names
    exactly which payment attempt it settled, so a failed sibling attempt
    on the same order is excluded by evidence and not by preference. The
    line is required to reference an existing record, in a terminal
    successful state, for the exact same amount.
    """
    for line in settlement.breakup:
        if line.type is SettlementLineType.PAYMENT:
            payment = payments.get(line.reference_id) if line.reference_id else None
            if payment is None or payment.status is not PaymentStatus.CAPTURED:
                return False
            if int(payment.amount_paise) != int(line.amount_paise):
                return False
        elif line.type is SettlementLineType.REFUND:
            refund = refunds.get(line.reference_id) if line.reference_id else None
            if refund is None or refund.status is not RefundStatus.PROCESSED:
                return False
            if int(refund.amount_paise) != -int(line.amount_paise):
                return False
    return True
