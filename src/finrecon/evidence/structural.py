"""Closed, deterministic structural evidence over an immutable case snapshot.

Structural fields are assertions only when their narration labels declare the
semantics: ``VALDT`` is a bank value date and ``RFND`` is a refund amount.
Every asserted field is evaluated against every candidate.  The caller may
gate use of this closure, but cannot select a token, line, or candidate within
it.
"""

from __future__ import annotations

import re
from datetime import date

from finrecon.candidates.snapshot import CaseSnapshot, SettlementFacts
from finrecon.normalize.provenance import FrozenModel

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d{1,9})"
_REFUND_FIELD = re.compile(rf"(?<![A-Z0-9])RFND\s+({_AMOUNT})\.(\d{{2}})(?![\d.])")
_VALUE_DATE_FIELD = re.compile(
    r"(?<![A-Z0-9])VALDT\s+(\d{2})(" + "|".join(MONTHS) + r")(\d{2})(?!\d)"
)

RELATION_BANK_VALUE_DATE_EXACT = "bank_value_date_equals_settlement_date"
RELATION_REFUND_LINE_AMOUNT_EXACT = "refund_field_equals_signed_breakup_refund"
RELATION_NARRATED_DATE_BANK_DATE_MISMATCH = "narrated_value_date_contradicts_bank_value_date"


class ValueDateCandidateResult(FrozenModel):
    candidate_id: str
    candidate_settlement_dates: tuple[date, ...]
    consistent: bool


class ValueDateFact(FrozenModel):
    raw_source_span: str
    source_offsets: tuple[int, ...]
    parsed_value_date: date
    bank_value_date: date
    narration_agrees_with_bank_value_date: bool
    relation_id: str
    candidate_results: tuple[ValueDateCandidateResult, ...]
    reached_candidate_ids: tuple[str, ...]


class BreakupLineMatch(FrozenModel):
    candidate_id: str
    settlement_id: str
    line_index: int
    line_type: str
    signed_amount_paise: int
    reference_id: str | None
    reference_status: str | None


class BreakupAmountFact(FrozenModel):
    raw_source_span: str
    raw_amount_token: str
    source_offsets: tuple[int, ...]
    parsed_amount_paise: int
    expected_signed_amount_paise: int
    relation_id: str
    matches: tuple[BreakupLineMatch, ...]
    reached_candidate_ids: tuple[str, ...]


class StructuralClosure(FrozenModel):
    complete_candidate_ids: tuple[str, ...]
    value_date_facts: tuple[ValueDateFact, ...]
    breakup_amount_facts: tuple[BreakupAmountFact, ...]
    intersection_candidate_ids: tuple[str, ...]
    union_candidate_ids: tuple[str, ...]

    @property
    def has_evidence(self) -> bool:
        return bool(self.value_date_facts or self.breakup_amount_facts)

    @property
    def is_contradictory(self) -> bool:
        return self.has_evidence and not self.intersection_candidate_ids


def _facts_by_id(snapshot: CaseSnapshot) -> dict[str, SettlementFacts]:
    return {facts.settlement_id: facts for facts in snapshot.base_evidence.settlement_facts}


def _all_offsets(narration: str, span: str) -> tuple[int, ...]:
    return tuple(match.start() for match in re.finditer(re.escape(span), narration))


def build_structural_closure(snapshot: CaseSnapshot) -> StructuralClosure:
    """Enumerate every declared structural assertion and its complete reach."""
    narration = snapshot.base_evidence.bank_record.narration
    bank_date = snapshot.base_evidence.bank_record.value_date
    facts_by_id = _facts_by_id(snapshot)
    candidate_ids = tuple(sorted(snapshot.candidate_ids()))

    date_facts: list[ValueDateFact] = []
    seen_dates: set[tuple[str, date]] = set()
    for match in _VALUE_DATE_FIELD.finditer(narration):
        span = match.group(0)
        try:
            parsed = date(2000 + int(match.group(3)), MONTHS.index(match.group(2)) + 1, int(match.group(1)))
        except ValueError:
            continue
        if (span, parsed) in seen_dates:
            continue
        seen_dates.add((span, parsed))
        agrees = parsed == bank_date
        results = tuple(
            ValueDateCandidateResult(
                candidate_id=candidate.candidate_id,
                candidate_settlement_dates=tuple(candidate.settlement_dates),
                consistent=agrees and parsed in candidate.settlement_dates,
            )
            for candidate in snapshot.candidates
        )
        reached = tuple(sorted(result.candidate_id for result in results if result.consistent))
        date_facts.append(
            ValueDateFact(
                raw_source_span=span,
                source_offsets=_all_offsets(narration, span),
                parsed_value_date=parsed,
                bank_value_date=bank_date,
                narration_agrees_with_bank_value_date=agrees,
                relation_id=(RELATION_BANK_VALUE_DATE_EXACT if agrees else RELATION_NARRATED_DATE_BANK_DATE_MISMATCH),
                candidate_results=results,
                reached_candidate_ids=reached,
            )
        )

    amount_facts: list[BreakupAmountFact] = []
    seen_amounts: set[tuple[str, int]] = set()
    for match in _REFUND_FIELD.finditer(narration):
        span = match.group(0)
        raw_amount = f"{match.group(1)}.{match.group(2)}"
        paise = int(match.group(1).replace(",", "")) * 100 + int(match.group(2))
        if (span, paise) in seen_amounts:
            continue
        seen_amounts.add((span, paise))
        line_matches: list[BreakupLineMatch] = []
        for candidate in snapshot.candidates:
            for settlement_id in candidate.settlement_ids:
                facts = facts_by_id.get(settlement_id)
                if facts is None:
                    continue
                for index, line in enumerate(facts.derivation.lines):
                    if line.line_type == "refund" and line.amount_paise == -paise:
                        line_matches.append(
                            BreakupLineMatch(
                                candidate_id=candidate.candidate_id,
                                settlement_id=settlement_id,
                                line_index=index,
                                line_type=line.line_type,
                                signed_amount_paise=line.amount_paise,
                                reference_id=line.reference_id,
                                reference_status=line.reference_status,
                            )
                        )
        reached = tuple(sorted({item.candidate_id for item in line_matches}))
        amount_facts.append(
            BreakupAmountFact(
                raw_source_span=span,
                raw_amount_token=raw_amount,
                source_offsets=_all_offsets(narration, span),
                parsed_amount_paise=paise,
                expected_signed_amount_paise=-paise,
                relation_id=RELATION_REFUND_LINE_AMOUNT_EXACT,
                matches=tuple(line_matches),
                reached_candidate_ids=reached,
            )
        )

    reaches = [frozenset(f.reached_candidate_ids) for f in (*date_facts, *amount_facts)]
    if reaches:
        intersection = frozenset(candidate_ids)
        union: frozenset[str] = frozenset()
        for reach in reaches:
            intersection &= reach
            union |= reach
    else:
        intersection = frozenset(candidate_ids)
        union = frozenset()
    return StructuralClosure(
        complete_candidate_ids=candidate_ids,
        value_date_facts=tuple(date_facts),
        breakup_amount_facts=tuple(amount_facts),
        intersection_candidate_ids=tuple(sorted(intersection)),
        union_candidate_ids=tuple(sorted(union)),
    )


__all__ = [
    "BreakupAmountFact", "BreakupLineMatch", "StructuralClosure", "ValueDateCandidateResult",
    "ValueDateFact", "build_structural_closure",
]
