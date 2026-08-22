"""Canonical payment record."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from .base import CanonicalRecord
from .money import Paise


class PaymentStatus(str, Enum):
    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"


class Payment(CanonicalRecord):
    payment_id: str
    order_id: str
    amount: Paise
    currency: str = "INR"
    status: PaymentStatus
    method: str | None = None
    created_at: datetime
