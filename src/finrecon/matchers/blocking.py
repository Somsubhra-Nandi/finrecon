"""Deterministic blocking: which settlement groups a credit could plausibly be.

One definition of "plausible", used by both the derived matcher and the
candidate generator. That sharing is deliberate and load-bearing: it is
what makes the immutable candidate set provably *complete* with respect to
the matcher's own search. The matcher cannot have considered a group the
candidate generator would not also emit, because there is only one
enumeration and both call it.

Blocking is structural only — declared date window, exact amount
arithmetic, and the declared group-size bound. It reads no narration text,
computes no similarity, and consults no ground truth.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta

from finrecon.matchers.rules import (
    MAX_SETTLEMENT_GROUP_SIZE,
    VALUE_DATE_WINDOW_DAYS_AFTER,
    VALUE_DATE_WINDOW_DAYS_BEFORE,
)
from finrecon.normalize.records import NormalizedBankRecord, NormalizedSettlement


@dataclass(frozen=True)
class SettlementGroup:
    """One plausible counterparty for a bank credit: an ordered settlement set."""

    settlement_ids: tuple[str, ...]
    settlements: tuple[NormalizedSettlement, ...]
    total_paise: int

    @property
    def size(self) -> int:
        return len(self.settlement_ids)


def window_dates(value_date: date) -> tuple[date, ...]:
    """Settlement dates admissible for a credit with this value date.

    The window is expressed as an offset on ``value_date - settlement_date``
    and bounded by the declared constants in :mod:`finrecon.matchers.rules`.
    """
    return tuple(
        value_date - timedelta(days=offset)
        for offset in range(-VALUE_DATE_WINDOW_DAYS_AFTER, VALUE_DATE_WINDOW_DAYS_BEFORE + 1)
    )


def index_by_settlement_date(
    settlements: tuple[NormalizedSettlement, ...],
) -> dict[date, list[NormalizedSettlement]]:
    index: dict[date, list[NormalizedSettlement]] = {}
    for settlement in settlements:
        index.setdefault(settlement.settlement_date_utc, []).append(settlement)
    for bucket in index.values():
        bucket.sort(key=lambda s: s.settlement_id)
    return index


def settlements_in_window(
    bank_record: NormalizedBankRecord,
    by_date: dict[date, list[NormalizedSettlement]],
) -> tuple[NormalizedSettlement, ...]:
    """Every available settlement whose date falls inside the declared window."""
    found: list[NormalizedSettlement] = []
    for day in window_dates(bank_record.value_date):
        found.extend(by_date.get(day, ()))
    found.sort(key=lambda s: s.settlement_id)
    return tuple(found)


def enumerate_exact_groups(
    bank_record: NormalizedBankRecord,
    in_window: tuple[NormalizedSettlement, ...],
    max_group_size: int = MAX_SETTLEMENT_GROUP_SIZE,
) -> tuple[SettlementGroup, ...]:
    """All in-window settlement groups whose amounts total the credit exactly.

    Groups are enumerated up to the declared ``max_group_size`` bound and
    returned in a deterministic order (by settlement-ID tuple), never
    ranked. A larger group that would also total exactly is *not* searched
    for; the bound is recorded on the case snapshot so the omission is
    visible rather than silent.
    """
    target = int(bank_record.amount_paise)
    groups: list[SettlementGroup] = []
    ordered = tuple(sorted(in_window, key=lambda s: s.settlement_id))

    for size in range(1, max(1, max_group_size) + 1):
        for combo in itertools.combinations(ordered, size):
            total = sum(int(s.amount_paise) for s in combo)
            if total == target:
                groups.append(
                    SettlementGroup(
                        settlement_ids=tuple(s.settlement_id for s in combo),
                        settlements=combo,
                        total_paise=total,
                    )
                )

    groups.sort(key=lambda g: g.settlement_ids)
    return tuple(groups)
