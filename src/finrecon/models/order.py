"""Canonical order record.

DESIGN.md §1 / §3: the first hop in the chain ORDER → PAYMENT → SETTLEMENT →
BANK CREDIT.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from .base import CanonicalRecord
from .money import Paise


class OrderStatus(str, Enum):
    CREATED = "created"
    ATTEMPTED = "attempted"
    PAID = "paid"


class Order(CanonicalRecord):
    order_id: str
    amount: Paise
    currency: str = "INR"
    status: OrderStatus
    created_at: datetime
