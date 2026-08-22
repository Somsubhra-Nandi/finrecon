"""Canonical settlement record.

DESIGN.md §5.2: "Synthetic schema mirrors the documented Razorpay
settlement break-up: payment, refund, adjustment, fee, tax, transfer."
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from .base import CanonicalRecord
from .money import Paise


class SettlementLineType(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"
    FEE = "fee"
    TAX = "tax"
    TRANSFER = "transfer"


class SettlementLineItem(CanonicalRecord):
    """One entry in a settlement's break-up.

    ``amount`` is signed: credits to the merchant (e.g. ``payment``) are
    positive, deductions (``fee``, ``tax``) are negative.
    """

    type: SettlementLineType
    amount: Paise
    reference_id: str | None = None


class Settlement(CanonicalRecord):
    settlement_id: str
    utr: str | None = None
    amount: Paise
    """Net amount credited to the bank, after the break-up is applied."""
    created_at: datetime
    breakup: tuple[SettlementLineItem, ...] = ()
