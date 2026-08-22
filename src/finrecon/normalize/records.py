"""Normalized views over the canonical Stage-0 record models.

DESIGN.md §3 gives the normalizer a narrow job — canonical schema, UTC,
integer paise — and forbids it from doing any matching. These models are
that job's output: a frozen, deterministic view of each visible record,
carrying the few normalized forms the deterministic matchers need plus
:class:`~finrecon.normalize.provenance.SourceProvenance` for everything
that was rewritten.

Deliberately **not** normalized:

* **Raw bank narration.** Held byte-identical. Any cleaning here would be
  the first step of degraded-reference recovery, which DESIGN.md §5.2
  reserves for a later stage.
* **Money.** Amounts stay exactly the ``Paise`` integers the canonical
  models validated. There is nothing to normalize and no rounding to do;
  introducing either would violate §4.6.
* **Record identity.** IDs are never rewritten, only upper-cased into a
  separate comparison *key* field that sits alongside the original.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from functools import cached_property

from finrecon.models import (
    BankRecord,
    BankRecordDirection,
    Order,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    Settlement,
    SettlementLineType,
)
from finrecon.models.money import Paise
from finrecon.normalize.provenance import (
    FieldNormalization,
    FrozenModel,
    SourceProvenance,
)
from finrecon.normalize.tokens import token_key, tokenize_narration

UTC = timezone.utc


def normalize_timestamp(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    Canonical Stage-0/Stage-1 records carry naive datetimes on a single
    implicit timeline. Naive values are *interpreted* as UTC (not shifted);
    aware values are converted. Either way the result is aware and
    comparable, so no downstream date-window predicate can silently
    compare a naive against an aware timestamp and raise, or compare two
    values on different offsets and quietly be wrong.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timestamp_normalization(
    field: str, source: datetime, normalized: datetime
) -> tuple[FieldNormalization, ...]:
    if source.isoformat() == normalized.isoformat():
        return ()
    return (
        FieldNormalization(
            field=field,
            source_value=source.isoformat(),
            normalized_value=normalized.isoformat(),
            rule="timestamp.assume_utc",
        ),
    )


class NormalizedOrder(FrozenModel):
    order_id: str
    amount_paise: Paise
    status: str
    created_at_utc: datetime
    source: SourceProvenance


class NormalizedPayment(FrozenModel):
    payment_id: str
    order_id: str
    amount_paise: Paise
    status: PaymentStatus
    created_at_utc: datetime
    source: SourceProvenance

    @property
    def is_captured(self) -> bool:
        return self.status is PaymentStatus.CAPTURED


class NormalizedRefund(FrozenModel):
    refund_id: str
    payment_id: str
    amount_paise: Paise
    status: RefundStatus
    created_at_utc: datetime
    source: SourceProvenance


class NormalizedSettlementLine(FrozenModel):
    type: SettlementLineType
    amount_paise: Paise
    reference_id: str | None


class NormalizedSettlement(FrozenModel):
    settlement_id: str
    settlement_id_key: str
    """Upper-cased comparison key. The original ``settlement_id`` is unchanged."""
    utr: str | None
    """The UTR exactly as the source carried it, or ``None``."""
    utr_key: str | None
    """Whitespace-stripped, upper-cased UTR comparison key, or ``None``.

    Only surrounding whitespace and letter case are normalized. Interior
    characters — including any separator — are preserved, because removing
    them would begin reconstructing a degraded reference.
    """
    amount_paise: Paise
    created_at_utc: datetime
    breakup: tuple[NormalizedSettlementLine, ...]
    source: SourceProvenance

    @property
    def settlement_date_utc(self) -> date:
        return self.created_at_utc.date()

    @property
    def breakup_total_paise(self) -> int:
        """Signed sum of every break-up line, in exact integer paise."""
        return sum(int(line.amount_paise) for line in self.breakup)

    def breakup_total_by_type(self) -> dict[str, int]:
        """Signed per-line-type totals, keyed by line-type value.

        Deterministically ordered by line-type value so serialized
        derivation evidence is byte-stable across runs.
        """
        totals: dict[str, int] = {}
        for line in self.breakup:
            totals[line.type.value] = totals.get(line.type.value, 0) + int(line.amount_paise)
        return {key: totals[key] for key in sorted(totals)}

    @property
    def has_utr(self) -> bool:
        return self.utr_key is not None


class NormalizedBankRecord(FrozenModel):
    bank_record_id: str
    amount_paise: Paise
    direction: BankRecordDirection
    narration: str
    """Raw narration, byte-identical to the source record. Never rewritten."""
    reference_tokens: tuple[str, ...]
    """Lexical split of ``narration``; see :mod:`finrecon.normalize.tokens`."""
    reference_token_keys: tuple[str, ...]
    value_date: date
    source: SourceProvenance


class NormalizedBatch(FrozenModel):
    """Every visible record of one batch, normalized and deterministically ordered.

    Ordering is by canonical record ID within each record type, not by
    input order, so two runs over the same records — read from files in
    any order — produce an identical batch and therefore identical
    downstream decisions.
    """

    orders: tuple[NormalizedOrder, ...]
    payments: tuple[NormalizedPayment, ...]
    refunds: tuple[NormalizedRefund, ...]
    settlements: tuple[NormalizedSettlement, ...]
    bank_records: tuple[NormalizedBankRecord, ...]

    # These indexes are built once and cached on the instance. The batch is
    # frozen, so the cache can never go stale, and the matchers look records
    # up per candidate group — rebuilding a thousand-entry dict on each of
    # those lookups turned a fast pass into a slow one.

    @cached_property
    def _settlement_index(self) -> dict[str, NormalizedSettlement]:
        return {s.settlement_id: s for s in self.settlements}

    @cached_property
    def _payment_index(self) -> dict[str, NormalizedPayment]:
        return {p.payment_id: p for p in self.payments}

    @cached_property
    def _refund_index(self) -> dict[str, NormalizedRefund]:
        return {r.refund_id: r for r in self.refunds}

    def settlement_by_id(self) -> dict[str, NormalizedSettlement]:
        return self._settlement_index

    def payment_by_id(self) -> dict[str, NormalizedPayment]:
        return self._payment_index

    def refund_by_id(self) -> dict[str, NormalizedRefund]:
        return self._refund_index

    def record_count(self) -> int:
        return (
            len(self.orders)
            + len(self.payments)
            + len(self.refunds)
            + len(self.settlements)
            + len(self.bank_records)
        )


def normalize_order(order: Order) -> NormalizedOrder:
    created = normalize_timestamp(order.created_at)
    return NormalizedOrder(
        order_id=order.order_id,
        amount_paise=order.amount,
        status=order.status.value,
        created_at_utc=created,
        source=SourceProvenance(
            record_type="order",
            record_id=order.order_id,
            normalizations=_timestamp_normalization("created_at", order.created_at, created),
        ),
    )


def normalize_payment(payment: Payment) -> NormalizedPayment:
    created = normalize_timestamp(payment.created_at)
    return NormalizedPayment(
        payment_id=payment.payment_id,
        order_id=payment.order_id,
        amount_paise=payment.amount,
        status=payment.status,
        created_at_utc=created,
        source=SourceProvenance(
            record_type="payment",
            record_id=payment.payment_id,
            normalizations=_timestamp_normalization("created_at", payment.created_at, created),
        ),
    )


def normalize_refund(refund: Refund) -> NormalizedRefund:
    created = normalize_timestamp(refund.created_at)
    return NormalizedRefund(
        refund_id=refund.refund_id,
        payment_id=refund.payment_id,
        amount_paise=refund.amount,
        status=refund.status,
        created_at_utc=created,
        source=SourceProvenance(
            record_type="refund",
            record_id=refund.refund_id,
            normalizations=_timestamp_normalization("created_at", refund.created_at, created),
        ),
    )


def normalize_settlement(settlement: Settlement) -> NormalizedSettlement:
    created = normalize_timestamp(settlement.created_at)
    normalizations = list(_timestamp_normalization("created_at", settlement.created_at, created))

    utr_key: str | None = None
    if settlement.utr is not None:
        stripped = settlement.utr.strip()
        utr_key = token_key(stripped) or None
        if utr_key is not None and utr_key != settlement.utr:
            normalizations.append(
                FieldNormalization(
                    field="utr",
                    source_value=settlement.utr,
                    normalized_value=utr_key,
                    rule="utr.strip_upper",
                )
            )

    settlement_id_key = token_key(settlement.settlement_id)
    if settlement_id_key != settlement.settlement_id:
        normalizations.append(
            FieldNormalization(
                field="settlement_id",
                source_value=settlement.settlement_id,
                normalized_value=settlement_id_key,
                rule="identifier.upper",
            )
        )

    return NormalizedSettlement(
        settlement_id=settlement.settlement_id,
        settlement_id_key=settlement_id_key,
        utr=settlement.utr,
        utr_key=utr_key,
        amount_paise=settlement.amount,
        created_at_utc=created,
        breakup=tuple(
            NormalizedSettlementLine(
                type=line.type,
                amount_paise=line.amount,
                reference_id=line.reference_id,
            )
            for line in settlement.breakup
        ),
        source=SourceProvenance(
            record_type="settlement",
            record_id=settlement.settlement_id,
            normalizations=tuple(normalizations),
        ),
    )


def normalize_bank_record(record: BankRecord) -> NormalizedBankRecord:
    tokens = tokenize_narration(record.narration)
    return NormalizedBankRecord(
        bank_record_id=record.bank_record_id,
        amount_paise=record.amount,
        direction=record.direction,
        narration=record.narration,
        reference_tokens=tokens,
        reference_token_keys=tuple(token_key(t) for t in tokens),
        value_date=record.value_date,
        source=SourceProvenance(
            record_type="bank_record",
            record_id=record.bank_record_id,
            normalizations=(),
        ),
    )


def normalize_batch(
    *,
    orders: list[Order],
    payments: list[Payment],
    refunds: list[Refund],
    settlements: list[Settlement],
    bank_records: list[BankRecord],
) -> NormalizedBatch:
    """Normalize one batch of visible records into a deterministic frozen view."""
    return NormalizedBatch(
        orders=tuple(normalize_order(o) for o in sorted(orders, key=lambda r: r.order_id)),
        payments=tuple(normalize_payment(p) for p in sorted(payments, key=lambda r: r.payment_id)),
        refunds=tuple(normalize_refund(r) for r in sorted(refunds, key=lambda r: r.refund_id)),
        settlements=tuple(
            normalize_settlement(s) for s in sorted(settlements, key=lambda r: r.settlement_id)
        ),
        bank_records=tuple(
            normalize_bank_record(b) for b in sorted(bank_records, key=lambda r: r.bank_record_id)
        ),
    )


__all__ = [
    "NormalizedBankRecord",
    "NormalizedBatch",
    "NormalizedOrder",
    "NormalizedPayment",
    "NormalizedRefund",
    "NormalizedSettlement",
    "NormalizedSettlementLine",
    "BankRecordDirection",
    "SettlementLineType",
    "normalize_bank_record",
    "normalize_batch",
    "normalize_order",
    "normalize_payment",
    "normalize_refund",
    "normalize_settlement",
    "normalize_timestamp",
]
