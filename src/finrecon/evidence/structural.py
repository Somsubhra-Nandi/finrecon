"""Closed, deterministic structural evidence over an immutable case snapshot.

Narration-labelled fields are assertions only when their labels declare the
semantics: ``VALDT`` is a bank value date and ``RFND`` is a refund amount.
Every asserted field is evaluated against every candidate.  The caller may
gate use of this closure, but cannot select a token, line, or candidate within
it.

A second, narration-independent value-date fact is also produced directly
from the canonical :class:`~finrecon.candidates.snapshot.BankRecordFacts`
field -- see :func:`build_structured_value_date_fact`. It reproduces the same
declared relation (:data:`RELATION_BANK_VALUE_DATE_EXACT`) but is *not* folded
into :attr:`StructuralClosure.intersection_candidate_ids` /
``union_candidate_ids``: the ``VALDT`` narration token's exact-match relation
is deliberately stricter than Stage 2's declared candidate-generation window
(``notes/VALIDATOR-V3-FINDINGS.md``), and unlike that token -- which is present
on only a minority of cases -- ``bank_value_date`` is populated on every case.
Folding an always-present exact-match fact into the closed intersection would
let a legitimate settlement that lands inside the window but off the bank's
value date by a day be refuted outright, which is not a decision this module
is authorized to make silently. The fact is therefore reported for audit and
downstream use, not wired into resolution.
"""

from __future__ import annotations

import re
from datetime import date
from enum import Enum

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


class EvidenceSource(str, Enum):
    """Where a structural fact's date text came from -- never the model."""

    RAW_NARRATION = "raw_narration"
    """Parsed from a labelled token in :attr:`BankRecordFacts.narration`.

    Legacy/synthetic path: :data:`ValueDateFact.raw_source_span` and
    :data:`ValueDateFact.source_offsets` point back into that narration.
    """

    STRUCTURED_BANK_FIELD = "structured_bank_field"
    """Read directly from :attr:`BankRecordFacts.value_date`.

    No narration span exists for this fact -- claiming one would misattribute
    a structured column as free text the model could have read differently.
    """


class ValueDateCandidateResult(FrozenModel):
    candidate_id: str
    candidate_settlement_dates: tuple[date, ...]
    consistent: bool


class ValueDateFact(FrozenModel):
    source: EvidenceSource = EvidenceSource.RAW_NARRATION
    raw_source_span: str
    source_offsets: tuple[int, ...]
    parsed_value_date: date
    bank_value_date: date
    narration_agrees_with_bank_value_date: bool
    relation_id: str
    candidate_results: tuple[ValueDateCandidateResult, ...]
    reached_candidate_ids: tuple[str, ...]


class StructuredValueDateFact(FrozenModel):
    """The same declared date relation, sourced directly from ``BankRecord``.

    Carries no narration span or offsets by construction: its ``source`` is
    :attr:`EvidenceSource.STRUCTURED_BANK_FIELD`, and the value it compares is
    read from :attr:`BankRecordFacts.value_date` on the immutable snapshot,
    never from the model or from narration text.

    Not folded into :attr:`StructuralClosure.intersection_candidate_ids` --
    see the module docstring for why. Reported for audit and for future
    resolution use once that wiring decision is made.
    """

    source: EvidenceSource = EvidenceSource.STRUCTURED_BANK_FIELD
    bank_record_id: str
    bank_value_date: date
    relation_id: str
    candidate_results: tuple[ValueDateCandidateResult, ...]
    reached_candidate_ids: tuple[str, ...]
    """Candidates whose settlement_dates contains bank_value_date exactly.

    Support only -- see the module docstring. A candidate absent from this
    tuple is not thereby refuted; it only means this fact does not support it.
    """


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
    structured_value_date_fact: StructuredValueDateFact
    """Audit-only. Not part of the reach this closure intersects/unions."""
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


def build_structured_value_date_fact(snapshot: CaseSnapshot) -> StructuredValueDateFact:
    """The exact-date relation, evaluated directly from ``BankRecord.value_date``.

    Reads nothing but the immutable snapshot: the bounded, already-normalized
    structured fact Stage 2 produced for this case. No narration is
    inspected, no model output is consulted, and a candidate with no
    settlement dates (an incomplete or malformed authoritative record)
    simply cannot appear in ``reached_candidate_ids`` -- there is no branch
    here that guesses a match for it.
    """
    bank = snapshot.base_evidence.bank_record
    results = tuple(
        ValueDateCandidateResult(
            candidate_id=candidate.candidate_id,
            candidate_settlement_dates=tuple(candidate.settlement_dates),
            consistent=bank.value_date in candidate.settlement_dates,
        )
        for candidate in snapshot.candidates
    )
    reached = tuple(sorted(result.candidate_id for result in results if result.consistent))
    return StructuredValueDateFact(
        bank_record_id=bank.bank_record_id,
        bank_value_date=bank.value_date,
        relation_id=RELATION_BANK_VALUE_DATE_EXACT,
        candidate_results=results,
        reached_candidate_ids=reached,
    )


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
        structured_value_date_fact=build_structured_value_date_fact(snapshot),
        intersection_candidate_ids=tuple(sorted(intersection)),
        union_candidate_ids=tuple(sorted(union)),
    )


__all__ = [
    "BreakupAmountFact", "BreakupLineMatch", "EvidenceSource", "StructuralClosure",
    "StructuredValueDateFact", "ValueDateCandidateResult", "ValueDateFact",
    "build_structural_closure", "build_structured_value_date_fact",
]
