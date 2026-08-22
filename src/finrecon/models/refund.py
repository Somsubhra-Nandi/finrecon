"""Canonical refund record."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from .base import CanonicalRecord
from .money import Paise


class RefundStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class Refund(CanonicalRecord):
    refund_id: str
    payment_id: str
    amount: Paise
    status: RefundStatus
    created_at: datetime
