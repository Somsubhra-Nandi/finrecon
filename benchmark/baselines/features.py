"""Mechanical evidence features, read from a case snapshot and nothing else.

Every function here takes a :class:`~finrecon.candidates.snapshot.CaseSnapshot`
and returns sets of candidate IDs. None of them reads hidden ground truth,
and none of them can: the snapshot carries no tier, no archetype, no family
and no answer, which ``tests/test_benchmark_isolation.py`` already asserts of
everything that produces one.

Two kinds of feature
--------------------

**Lexical.** A narration substring, and the candidates whose ``utr`` or
``settlement_id`` it stands in a declared relation to. Computed with the real
:mod:`finrecon.evidence.reference` predicates at the real evidence floor, so a
lexical feature means exactly what the shipped validator would mean by it.

**Structural.** A token read out of the narration by shape -- a money amount,
a date -- and the candidates whose *records* match it. These are the features
the declared relation set cannot express at all, because they compare a
narration token against a break-up line or a settlement date rather than
against a reference string.

The structural extractors are deliberately narrow. They recognise two token
shapes and consult two record fields. A wider extractor would find more, and
would also start inventing relationships nobody declared -- which is how a
baseline turns into an unaudited matcher.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from finrecon.candidates.snapshot import CaseSnapshot, SettlementFacts
from finrecon.evidence.reference import (
    DECLARED_RELATION_IDS,
    REFERENCE_KINDS,
    compare,
    strongest_admissible_relation,
)

FRAGMENT_MIN_LENGTH = 4
FRAGMENT_MAX_LENGTH = 20
"""Matches the generator's enumeration band; see its ``invariants`` module for
why that upper bound is exhaustive rather than a sample."""

ACCEPTED_RELATIONS = frozenset(DECLARED_RELATION_IDS)

MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

_MONEY = re.compile(r"(?<![\d.])(\d{1,7})\.(\d{2})(?![\d.])")
_DATE = re.compile(r"(?<!\d)(\d{2})(" + "|".join(MONTHS) + r")(\d{2})(?!\d)")


@dataclass(frozen=True)
class Feature:
    """One piece of evidence, and every candidate it is consistent with."""

    kind: str
    """``lexical`` / ``breakup_line_amount_paise`` / ``settlement_value_date``."""
    token: str
    """The literal narration text this feature was read from."""
    reach: frozenset[str]
    """Candidate IDs. Never empty -- a feature reaching nothing is not recorded."""


def narration_fragments(narration: str) -> tuple[str, ...]:
    """Every distinct contiguous substring within the admissible length band."""
    seen: set[str] = set()
    length = len(narration)
    for start in range(length):
        stop = min(length, start + FRAGMENT_MAX_LENGTH)
        for end in range(start + FRAGMENT_MIN_LENGTH, stop + 1):
            seen.add(narration[start:end])
    return tuple(sorted(seen))


def _facts_by_id(snapshot: CaseSnapshot) -> dict[str, SettlementFacts]:
    return {facts.settlement_id: facts for facts in snapshot.base_evidence.settlement_facts}


def _references_of(facts: SettlementFacts) -> tuple[tuple[str, str], ...]:
    values: dict[str, str | None] = {"utr": facts.utr, "settlement_id": facts.settlement_id}
    return tuple((kind, values[kind]) for kind in REFERENCE_KINDS if values[kind] is not None)  # type: ignore[misc]


def lexical_reach(snapshot: CaseSnapshot, fragment: str, floor: int) -> frozenset[str]:
    """Candidates this fragment reaches, by the shipped validator's own predicate."""
    facts_by_id = _facts_by_id(snapshot)
    reached: set[str] = set()
    for candidate in snapshot.candidates:
        for settlement_id in candidate.settlement_ids:
            facts = facts_by_id.get(settlement_id)
            if facts is None:
                continue
            hit = False
            for kind, value in _references_of(facts):
                relation = strongest_admissible_relation(
                    compare(fragment, value, kind),  # type: ignore[arg-type]
                    accepted_relation_ids=ACCEPTED_RELATIONS,
                    min_pinned_reference_characters=floor,
                )
                if relation is not None:
                    hit = True
                    break
            if hit:
                reached.add(candidate.candidate_id)
                break
    return frozenset(reached)


def lexical_features(snapshot: CaseSnapshot, floor: int) -> tuple[Feature, ...]:
    """Every admissible fragment that reaches at least one candidate.

    Fragments reaching nothing are dropped, and dropping them is
    decision-equivalent rather than a convenience: in
    :func:`finrecon.decide.validator.validate_case` such a fragment produces an
    empty ``matched_candidate_ids`` and ``is_discriminating=False``, so it can
    change neither ``reference_identified_candidate_ids`` nor
    ``surviving_candidate_ids``. ``tests/test_v4_baselines.py`` asserts the
    equivalence against an unfiltered run rather than leaving it as an
    argument.
    """
    narration = snapshot.base_evidence.bank_record.narration
    features: list[Feature] = []
    for fragment in narration_fragments(narration):
        reach = lexical_reach(snapshot, fragment, floor)
        if reach:
            features.append(Feature(kind="lexical", token=fragment, reach=reach))
    return tuple(features)


def money_tokens(narration: str) -> tuple[tuple[str, int], ...]:
    """``(token, paise)`` for every rupee-and-paise amount in the narration."""
    found: list[tuple[str, int]] = []
    for match in _MONEY.finditer(narration):
        rupees, paise = match.group(1), match.group(2)
        found.append((match.group(0), int(rupees) * 100 + int(paise)))
    return tuple(found)


def date_tokens(narration: str) -> tuple[tuple[str, date], ...]:
    """``(token, date)`` for every ``DDMONYY`` field in the narration."""
    found: list[tuple[str, date]] = []
    for match in _DATE.finditer(narration):
        day, month, year = match.group(1), match.group(2), match.group(3)
        try:
            found.append((match.group(0), date(2000 + int(year), MONTHS.index(month) + 1, int(day))))
        except ValueError:
            # A shape that parses as a date field but is not one -- 31FEB26 and
            # the like. Skipped rather than guessed at.
            continue
    return tuple(found)


def structural_features(snapshot: CaseSnapshot) -> tuple[Feature, ...]:
    """Money and date fields, matched against candidate records."""
    narration = snapshot.base_evidence.bank_record.narration
    facts_by_id = _facts_by_id(snapshot)
    features: list[Feature] = []

    for token, paise in money_tokens(narration):
        reach = frozenset(
            candidate.candidate_id
            for candidate in snapshot.candidates
            if any(
                any(
                    abs(line.amount_paise) == paise
                    for line in facts_by_id[settlement_id].derivation.lines
                )
                for settlement_id in candidate.settlement_ids
                if settlement_id in facts_by_id
            )
        )
        if reach:
            features.append(
                Feature(kind="breakup_line_amount_paise", token=token, reach=reach)
            )

    for token, value in date_tokens(narration):
        reach = frozenset(
            candidate.candidate_id
            for candidate in snapshot.candidates
            if any(
                facts_by_id[settlement_id].settlement_date_utc == value
                for settlement_id in candidate.settlement_ids
                if settlement_id in facts_by_id
            )
        )
        if reach:
            features.append(Feature(kind="settlement_value_date", token=token, reach=reach))

    return tuple(features)


def distinct_reach_sets(features: tuple[Feature, ...]) -> tuple[frozenset[str], ...]:
    """The distinct reach sets, in a deterministic order. Many fragments, few sets."""
    return tuple(
        sorted({feature.reach for feature in features}, key=lambda reach: tuple(sorted(reach)))
    )


__all__ = [
    "ACCEPTED_RELATIONS",
    "FRAGMENT_MAX_LENGTH",
    "FRAGMENT_MIN_LENGTH",
    "MONTHS",
    "Feature",
    "date_tokens",
    "distinct_reach_sets",
    "lexical_features",
    "lexical_reach",
    "money_tokens",
    "narration_fragments",
    "structural_features",
]
