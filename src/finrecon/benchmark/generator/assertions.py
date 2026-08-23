"""Generator-level tier-disjointness assertions (DESIGN.md §5.2, Stage 1 exit condition).

``classify_case`` derives a case's tier from the actual content of its
generated records — never from the archetype label the case builder used
— by mirroring the decision tree DESIGN.md §5.2 states explicitly:

    direct key survives          -> T0
    no key, structure survives   -> T1
    key survives only degraded   -> T2
    nothing distinguishing       -> T3

``assert_tier_disjoint`` compares that independently-derived tier against
the tier a case builder declared and raises loudly on any mismatch,
satisfying the hard requirement that each case belong to exactly one tier.

"Direct key survives" is evaluated with *usable-token* semantics — see
:mod:`finrecon.benchmark.generator.token_contract`. A reference merely
*present* in a narration is not a surviving direct key if the declared
tokenization cannot reach it; that is the difference between T0 and T2, and
getting it wrong is what benchmark v3 fixed.
"""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.models import BankRecord, Order, Payment, Refund, Settlement
from finrecon.benchmark.generator.narration_library import get_narration_template
from finrecon.benchmark.generator.templates import T0_CLEAN_TEMPLATE_IDS
from finrecon.benchmark.generator.token_contract import is_usable_direct_key


class TierDisjointnessError(AssertionError):
    pass


@dataclass
class CaseRecords:
    orders: tuple[Order, ...]
    payments: tuple[Payment, ...]
    settlements: tuple[Settlement, ...]
    refunds: tuple[Refund, ...]
    bank_records: tuple[BankRecord, ...]


def _has_intact_utr_direct_key(case: CaseRecords) -> bool:
    """A UTR rendered into a T0-clean template *and* reachable as one whole token.

    Both halves are load-bearing. The template match keeps T0 structurally
    distinct from T2's noisy-embed narrations (a character-intact UTR glued
    into boilerplate is T2, per DESIGN.md §5.2). The token check keeps the
    claim honest: a reference the declared tokenization cannot reach is not
    a usable direct key, whatever template produced it.
    """
    for settlement in case.settlements:
        if not settlement.utr:
            continue
        for bank_record in case.bank_records:
            if bank_record.narration == "" or settlement.utr not in bank_record.narration:
                continue
            if not is_usable_direct_key(bank_record.narration, settlement.utr):
                continue
            for template_id in T0_CLEAN_TEMPLATE_IDS:
                template = get_narration_template(template_id)
                if bank_record.narration == template.template.format(ref=settlement.utr):
                    return True
    return False


def _has_clean_settlement_id_key(case: CaseRecords) -> bool:
    """A settlement ID reachable from the narration as one whole token.

    Benchmark v3 replaced substring containment here. Containment is
    strictly weaker than the whole-token equality the direct-key matcher
    applies, and the gap was not hypothetical: FROZEN-EVAL settlement IDs
    read ``setl_frozen-eval_000042``, which is trivially a substring of its
    own narration while tokenizing to ``setl_frozen`` + ``eval_000042``.
    All 175 such cases were certified T0 and then resolved by derived
    reconciliation instead, so T0 stopped measuring the ID join it exists
    to measure. DEV's slug has no delimiter, so DEV never showed it.
    """
    for settlement in case.settlements:
        for bank_record in case.bank_records:
            if is_usable_direct_key(bank_record.narration, settlement.settlement_id):
                return True
    return False


def _has_any_utr(case: CaseRecords) -> bool:
    return any(settlement.utr for settlement in case.settlements)


def _is_ambiguous(case: CaseRecords) -> bool:
    """True iff at least two settlements share both amount and timestamp with no key to tell them apart."""
    settlements = list(case.settlements)
    for i, s1 in enumerate(settlements):
        for s2 in settlements[i + 1 :]:
            if s1.amount == s2.amount and s1.created_at == s2.created_at:
                return True
    return False


def classify_case(case: CaseRecords) -> str:
    direct_key = _has_intact_utr_direct_key(case) or _has_clean_settlement_id_key(case)
    if direct_key:
        return "T0"

    degraded_key = _has_any_utr(case)  # a UTR exists but failed the direct-key check above
    if degraded_key:
        return "T2"

    # No key at all (settlement_id also confirmed absent from narration above).
    if _is_ambiguous(case):
        return "T3"
    return "T1"


def assert_tier_disjoint(case: CaseRecords, declared_tier: str, case_id: str) -> None:
    computed_tier = classify_case(case)
    if computed_tier != declared_tier:
        raise TierDisjointnessError(
            f"case {case_id!r} declared tier {declared_tier!r} but the generated "
            f"records classify as {computed_tier!r} (direct_key="
            f"{_has_intact_utr_direct_key(case) or _has_clean_settlement_id_key(case)}, "
            f"has_utr={_has_any_utr(case)}, ambiguous={_is_ambiguous(case)})"
        )
