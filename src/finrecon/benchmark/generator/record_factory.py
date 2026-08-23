"""Deterministic record construction helpers for the Stage 1 generator.

IDs are assigned sequentially as cases are built, in the fixed shuffled
case order produced by :mod:`finrecon.benchmark.generator.plan` — never
keyed on tier. This matters: a tier label functionally encodes the correct
outcome for T3 (tier T3 <=> ``required_outcome == "ESCALATE"``), so if a
visible record ID's numeric range correlated with tier, that would leak
ground truth through the ID space alone. Sequential-by-shuffled-order IDs
avoid that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

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

from finrecon.benchmark.generator.token_contract import is_token_safe

BASE_DATE = datetime(2026, 1, 1, 0, 0, 0)
"""Fixed anchor for all generated timestamps. Never derived from wall-clock time."""

_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def synthetic_utr(rng, length: int | None = None) -> str:
    length = length if length is not None else rng.randint(11, 16)
    return "".join(rng.choice(_ALNUM) for _ in range(length))


def case_base_timestamp(rng) -> datetime:
    return BASE_DATE + timedelta(days=rng.randint(0, 364), minutes=rng.randint(0, 1439))


@dataclass
class RecordFactory:
    """Hands out sequential, split-scoped record IDs. No hidden global state.

    One instance is threaded explicitly through the generation of a single
    split; it never reads wall-clock time or global ``random`` state.

    ``id_slug`` is the *token-safe* form of the split name
    (:func:`finrecon.benchmark.generator.config.split_id_slug`), not the
    split name itself. A settlement ID is printed verbatim into T0
    narrations and has to be reachable there as one whole token, so a slug
    carrying a tokenizer delimiter would silently demote every T0
    settlement-ID case — which is the benchmark v3 defect. The check in
    ``__post_init__`` makes that unrepresentable rather than merely
    documented.
    """

    id_slug: str
    _order_seq: int = 0
    _payment_seq: int = 0
    _settlement_seq: int = 0
    _refund_seq: int = 0
    _bank_seq: int = 0

    def __post_init__(self) -> None:
        if not is_token_safe(self.id_slug):
            raise ValueError(
                f"RecordFactory id_slug {self.id_slug!r} does not survive tokenization as a "
                f"single token; settlement IDs built from it could never be matched as a "
                f"direct key (see benchmark/manifests/CHANGELOG.md v3.0.0)"
            )

    def order_id(self) -> str:
        self._order_seq += 1
        return f"ORD-{self.id_slug}-{self._order_seq:06d}"

    def payment_id(self) -> str:
        self._payment_seq += 1
        return f"pay_{self.id_slug}_{self._payment_seq:06d}"

    def settlement_id(self) -> str:
        self._settlement_seq += 1
        return f"setl_{self.id_slug}_{self._settlement_seq:06d}"

    def refund_id(self) -> str:
        self._refund_seq += 1
        return f"rfnd_{self.id_slug}_{self._refund_seq:06d}"

    def bank_record_id(self) -> str:
        self._bank_seq += 1
        return f"bnk_{self.id_slug}_{self._bank_seq:06d}"

    def make_order(self, *, amount: int, created_at: datetime, status: OrderStatus = OrderStatus.PAID) -> Order:
        return Order(
            order_id=self.order_id(),
            amount=Paise(amount),
            status=status,
            created_at=created_at,
        )

    def make_payment(
        self,
        *,
        order_id: str,
        amount: int,
        created_at: datetime,
        status: PaymentStatus = PaymentStatus.CAPTURED,
    ) -> Payment:
        return Payment(
            payment_id=self.payment_id(),
            order_id=order_id,
            amount=Paise(amount),
            status=status,
            created_at=created_at,
        )

    def make_refund(
        self,
        *,
        payment_id: str,
        amount: int,
        created_at: datetime,
        status: RefundStatus = RefundStatus.PROCESSED,
    ) -> Refund:
        return Refund(
            refund_id=self.refund_id(),
            payment_id=payment_id,
            amount=Paise(amount),
            status=status,
            created_at=created_at,
        )

    def make_settlement(
        self,
        *,
        utr: str | None,
        net_amount: int,
        created_at: datetime,
        breakup: tuple[SettlementLineItem, ...],
    ) -> Settlement:
        return Settlement(
            settlement_id=self.settlement_id(),
            utr=utr,
            amount=Paise(net_amount),
            created_at=created_at,
            breakup=breakup,
        )

    def make_bank_record(
        self,
        *,
        amount: int,
        narration: str,
        value_date,
        direction: BankRecordDirection = BankRecordDirection.CREDIT,
    ) -> BankRecord:
        return BankRecord(
            bank_record_id=self.bank_record_id(),
            amount=Paise(amount),
            direction=direction,
            narration=narration,
            value_date=value_date,
        )


def fee_breakup(gross_paise: int, fee_bps: int = 190, gst_bps: int = 1800) -> tuple[int, int, int]:
    """Integer-paise fee/GST arithmetic. Returns ``(fee, gst_on_fee, net)``.

    All arithmetic is integer floor division on paise — no floats enter
    this path, matching :mod:`finrecon.models.money`'s invariant.
    """
    fee = (gross_paise * fee_bps) // 10_000
    gst = (fee * gst_bps) // 10_000
    net = gross_paise - fee - gst
    return fee, gst, net


def tds_amount(gross_paise: int, tds_bps: int = 10) -> int:
    return (gross_paise * tds_bps) // 10_000


def payment_line(amount: int, reference_id: str) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.PAYMENT, amount=Paise(amount), reference_id=reference_id)


def fee_line(amount: int) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.FEE, amount=Paise(-amount))


def tax_line(amount: int, reference_id: str | None = None) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.TAX, amount=Paise(-amount), reference_id=reference_id)


def refund_line(amount: int, reference_id: str) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.REFUND, amount=Paise(-amount), reference_id=reference_id)


def transfer_line(amount: int, reference_id: str | None = None) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.TRANSFER, amount=Paise(amount), reference_id=reference_id)


def adjustment_line(amount: int, reference_id: str | None = None) -> SettlementLineItem:
    return SettlementLineItem(type=SettlementLineType.ADJUSTMENT, amount=Paise(amount), reference_id=reference_id)
