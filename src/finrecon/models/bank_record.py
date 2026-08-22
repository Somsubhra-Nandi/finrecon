"""Canonical bank statement line.

DESIGN.md §1: "The bank's side is free text." This model holds the raw
line exactly as the bank reports it — no reference extraction happens
here; that is later-stage matching/investigation work, not normalization.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from .base import CanonicalRecord
from .money import Paise


class BankRecordDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class BankRecord(CanonicalRecord):
    bank_record_id: str
    amount: Paise
    direction: BankRecordDirection
    narration: str
    """Raw, unparsed free-text narration as it appears on the statement."""
    value_date: date
