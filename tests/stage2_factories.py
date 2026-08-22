"""Synthetic record builders for Stage-2 unit tests.

Hand-built records, not benchmark rows. Unit tests need cases the frozen
benchmark deliberately does not contain — two settlements sharing a UTR,
a break-up that is one paise short, a credit explainable two different
ways — and the benchmark is frozen, so those must be constructed here.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from finrecon.models import (
    BankRecord,
    BankRecordDirection,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    Settlement,
    SettlementLineItem,
    SettlementLineType,
)
from finrecon.models.money import Paise
from finrecon.normalize.records import NormalizedBatch, normalize_batch

BASE = datetime(2026, 3, 1, 9, 0, 0)


def order(order_id: str = "ORD-1", amount: int = 100_000, at: datetime = BASE) -> Order:
    return Order(order_id=order_id, amount=Paise(amount), status=OrderStatus.PAID, created_at=at)


def payment(
    payment_id: str = "pay_1",
    order_id: str = "ORD-1",
    amount: int = 100_000,
    at: datetime = BASE,
    status: PaymentStatus = PaymentStatus.CAPTURED,
) -> Payment:
    return Payment(
        payment_id=payment_id,
        order_id=order_id,
        amount=Paise(amount),
        status=status,
        created_at=at,
    )


def refund(
    refund_id: str = "rfnd_1",
    payment_id: str = "pay_1",
    amount: int = 10_000,
    at: datetime = BASE,
    status: RefundStatus = RefundStatus.PROCESSED,
) -> Refund:
    return Refund(
        refund_id=refund_id,
        payment_id=payment_id,
        amount=Paise(amount),
        status=status,
        created_at=at,
    )


def line(line_type: SettlementLineType, amount: int, reference_id: str | None = None):
    return SettlementLineItem(type=line_type, amount=Paise(amount), reference_id=reference_id)


def settlement(
    settlement_id: str = "setl_1",
    *,
    utr: str | None = None,
    net: int = 97_758,
    at: datetime = BASE + timedelta(days=1),
    breakup: tuple[SettlementLineItem, ...] | None = None,
) -> Settlement:
    if breakup is None:
        breakup = (
            line(SettlementLineType.PAYMENT, 100_000, "pay_1"),
            line(SettlementLineType.FEE, -1_900),
            line(SettlementLineType.TAX, -342),
        )
    return Settlement(
        settlement_id=settlement_id,
        utr=utr,
        amount=Paise(net),
        created_at=at,
        breakup=breakup,
    )


def bank(
    bank_record_id: str = "bnk_1",
    *,
    amount: int = 97_758,
    narration: str = "NEFT CREDIT - SETTLEMENT",
    value_date: date | None = None,
    direction: BankRecordDirection = BankRecordDirection.CREDIT,
) -> BankRecord:
    return BankRecord(
        bank_record_id=bank_record_id,
        amount=Paise(amount),
        direction=direction,
        narration=narration,
        value_date=value_date or (BASE + timedelta(days=1)).date(),
    )


def batch_of(
    *,
    orders=(),
    payments=(),
    refunds=(),
    settlements=(),
    bank_records=(),
) -> NormalizedBatch:
    return normalize_batch(
        orders=list(orders),
        payments=list(payments),
        refunds=list(refunds),
        settlements=list(settlements),
        bank_records=list(bank_records),
    )


def simple_batch(**overrides) -> NormalizedBatch:
    """One order, one captured payment, one settlement, one matching credit."""
    return batch_of(
        orders=[overrides.get("order", order())],
        payments=[overrides.get("payment", payment())],
        settlements=[overrides.get("settlement", settlement())],
        bank_records=[overrides.get("bank", bank())],
    )
