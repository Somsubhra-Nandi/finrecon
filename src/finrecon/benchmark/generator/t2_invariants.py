"""Hard, independent verification of the benchmark-v2 T2 construct.

Benchmark v1 declared cases T2 and trusted the declaration. Stage 2 then
showed the declaration was not backed by the data: every v1 T2 case was
uniquely resolvable from structured financial evidence alone, so the
degraded reference — the entire point of the tier — was not causally
necessary (``notes/STAGE2-FINDINGS.md`` §1).

v2 does not trust the declaration. Every generated T2 case is re-derived
from its own records here and rejected unless all seven invariants below
hold:

1. No clean, directly usable join key survives in the narration.
2. Structured evidence alone leaves **at least two** plausible settlement
   candidates.
3. The true settlement is one of them.
4. Structured evidence alone therefore does **not** uniquely select it.
5. The surviving degraded reference is consistent with the true settlement.
6. It is consistent with **no other** candidate — so recovering it resolves
   the case, which is what makes the reference causally necessary.
7. Deleting the degraded evidence outright leaves the case ambiguous
   (checked by re-deriving the candidate set from a reference-free
   narration), so the case is not secretly a T1.

Invariants 5-7 together are also what keeps T2 out of T3's territory: T3
is ambiguity with *nothing* to recover, T2 is ambiguity with exactly one
thing to recover.

**The plausibility model here is deliberately reimplemented**, not
imported from :mod:`finrecon.matchers`. The declared Stage-2 blocking
rules — value-date window, exact integer-paise totals, a bounded group
size, break-up lines that name real records in a successful state — are
restated from DESIGN.md §4.3 in generator-local code. If the generator's
reading and the matcher's implementation ever diverge, a T2 case fails to
build; importing the matcher would make the check agree with the matcher
by construction and prove nothing. The constants themselves *are*
imported, so the two cannot drift apart on the numbers.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta

from finrecon.models import (
    BankRecord,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    Settlement,
    SettlementLineType,
)

from finrecon.benchmark.generator.t2_evidence import SurvivingReference

# Declared Stage-2 blocking parameters. Imported so the generator's
# independent re-derivation cannot drift from the matcher's numbers even
# though its logic is written separately.
from finrecon.matchers.rules import (
    MAX_SETTLEMENT_GROUP_SIZE,
    VALUE_DATE_WINDOW_DAYS_AFTER,
    VALUE_DATE_WINDOW_DAYS_BEFORE,
)


class T2ConstructError(AssertionError):
    """A case declared T2 does not satisfy the v2 causal-necessity construct."""


@dataclass(frozen=True)
class PlausibilityInputs:
    """The record pool a credit's plausible candidates are drawn from."""

    settlements: tuple[Settlement, ...]
    payments: tuple[Payment, ...]
    refunds: tuple[Refund, ...]


def _breakup_total(settlement: Settlement) -> int:
    return sum(int(line.amount) for line in settlement.breakup)


def _breakup_references_are_sound(
    settlement: Settlement,
    payments: dict[str, Payment],
    refunds: dict[str, Refund],
) -> bool:
    for line in settlement.breakup:
        if line.type is SettlementLineType.PAYMENT:
            payment = payments.get(line.reference_id) if line.reference_id else None
            if payment is None or payment.status is not PaymentStatus.CAPTURED:
                return False
            if int(payment.amount) != int(line.amount):
                return False
        elif line.type is SettlementLineType.REFUND:
            refund = refunds.get(line.reference_id) if line.reference_id else None
            if refund is None or refund.status is not RefundStatus.PROCESSED:
                return False
            if int(refund.amount) != -int(line.amount):
                return False
    return True


def _window_dates(value_date: date) -> tuple[date, ...]:
    return tuple(
        value_date - timedelta(days=offset)
        for offset in range(-VALUE_DATE_WINDOW_DAYS_AFTER, VALUE_DATE_WINDOW_DAYS_BEFORE + 1)
    )


def plausible_settlement_groups(
    bank_record: BankRecord,
    pool: PlausibilityInputs,
    max_group_size: int = MAX_SETTLEMENT_GROUP_SIZE,
) -> tuple[tuple[str, ...], ...]:
    """Every structurally plausible settlement group for ``bank_record``.

    "Plausible" is exactly the declared Stage-2 predicate set, and nothing
    softer: in the value-date window, totalling the credit to the paise,
    each settlement's break-up accounting for its own amount to the paise,
    and every referencing line naming a real record in a terminal
    successful state. Narration is never read.

    Returned in settlement-ID-tuple order. The ordering is not a ranking.
    """
    payments = {p.payment_id: p for p in pool.payments}
    refunds = {r.refund_id: r for r in pool.refunds}
    admissible = set(_window_dates(bank_record.value_date))

    in_window = sorted(
        (s for s in pool.settlements if s.created_at.date() in admissible),
        key=lambda s: s.settlement_id,
    )
    target = int(bank_record.amount)

    groups: list[tuple[str, ...]] = []
    for size in range(1, max(1, max_group_size) + 1):
        for combo in itertools.combinations(in_window, size):
            if sum(int(s.amount) for s in combo) != target:
                continue
            if any(_breakup_total(s) != int(s.amount) for s in combo):
                continue
            if not all(_breakup_references_are_sound(s, payments, refunds) for s in combo):
                continue
            groups.append(tuple(s.settlement_id for s in combo))

    groups.sort()
    return tuple(groups)


@dataclass(frozen=True)
class T2Verification:
    """What the invariant check observed, for diagnostics as well as enforcement."""

    case_id: str
    candidate_groups: tuple[tuple[str, ...], ...]
    candidate_settlement_ids: tuple[str, ...]
    recovered_settlement_ids: tuple[str, ...]
    candidate_groups_without_reference: tuple[tuple[str, ...], ...]

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_groups)


def verify_t2_case(
    *,
    case_id: str,
    bank_record: BankRecord,
    pool: PlausibilityInputs,
    true_settlement_id: str,
    surviving_reference: SurvivingReference,
) -> T2Verification:
    """Enforce invariants 1-7 for one T2 case. Raises :class:`T2ConstructError`.

    ``pool`` is whatever record universe the caller wants the case checked
    against — its own records at build time, the whole split afterwards.
    A wider pool can only add candidates, so a case that passes case-local
    still has to pass batch-wide, and both are run.
    """
    by_id = {s.settlement_id: s for s in pool.settlements}

    # (1) No clean, directly usable join key.
    for settlement in pool.settlements:
        for identifier in (settlement.settlement_id, settlement.utr):
            if identifier and surviving_reference.is_directly_usable(identifier):
                raise T2ConstructError(
                    f"T2 case {case_id!r}: narration {surviving_reference.narration!r} carries "
                    f"{identifier!r} as a whole token — that is a usable direct key, i.e. a T0 case"
                )

    if not surviving_reference.is_present_in_narration():
        raise T2ConstructError(
            f"T2 case {case_id!r}: declared surviving evidence "
            f"{surviving_reference.evidence!r} does not appear in narration "
            f"{surviving_reference.narration!r}"
        )

    groups = plausible_settlement_groups(bank_record, pool)
    candidate_ids = tuple(sorted({sid for group in groups for sid in group}))

    # (2) At least two structurally plausible candidates.
    if len(groups) < 2:
        raise T2ConstructError(
            f"T2 case {case_id!r}: structured evidence alone leaves {len(groups)} plausible "
            f"candidate group(s) {groups} — a T2 case must leave at least two, or the degraded "
            "reference is not causally necessary"
        )

    # (3) The true settlement is among them.
    if (true_settlement_id,) not in groups:
        raise T2ConstructError(
            f"T2 case {case_id!r}: true settlement {true_settlement_id!r} is not among the "
            f"plausible candidate groups {groups}"
        )

    # (4) is (2) restated: two or more groups means no unique structured resolution.

    # (5)/(6) The degraded reference is consistent with the true candidate and no other.
    recovered = tuple(
        sorted(sid for sid in candidate_ids if surviving_reference.recovers(by_id[sid].utr))
    )
    if true_settlement_id not in recovered:
        raise T2ConstructError(
            f"T2 case {case_id!r}: surviving evidence {surviving_reference.evidence!r} "
            f"({surviving_reference.category_id}) is not consistent with the true settlement's "
            f"UTR {by_id[true_settlement_id].utr!r} — nothing distinguishes the case, which is T3"
        )
    if len(recovered) != 1:
        raise T2ConstructError(
            f"T2 case {case_id!r}: surviving evidence {surviving_reference.evidence!r} "
            f"({surviving_reference.category_id}) is consistent with {len(recovered)} candidate "
            f"settlements {recovered} — recovering it would not resolve the case"
        )

    # (7) Delete the evidence: the case must stay ambiguous.
    stripped = bank_record.model_copy(update={"narration": ""})
    groups_without = plausible_settlement_groups(stripped, pool)
    if len(groups_without) < 2:
        raise T2ConstructError(
            f"T2 case {case_id!r}: removing the degraded reference leaves "
            f"{len(groups_without)} plausible candidate group(s) — the case would be a T1"
        )

    return T2Verification(
        case_id=case_id,
        candidate_groups=groups,
        candidate_settlement_ids=candidate_ids,
        recovered_settlement_ids=recovered,
        candidate_groups_without_reference=groups_without,
    )
