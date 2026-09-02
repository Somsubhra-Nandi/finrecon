"""The closed set of value-date formats the mapping editor may offer.

Why an allowlist exists here and not in
:mod:`finrecon.adapters.bank.csv_parser`. The parser accepts any
``strptime`` format string, and that stays true -- a hand-written profile
JSON is a reviewed declaration and is not narrowed by this module. What is
narrowed is the *proposal and confirmation* surface: a dropdown a human
picks from, and a model that must choose one of a named few. An open format
space there buys nothing and costs the one thing that matters, namely the
reviewer's ability to recognise what they are approving.

**Ambiguity is the load-bearing part.** ``03/04/2024`` is the third of
April under ``%d/%m/%Y`` and the fourth of March under ``%m/%d/%Y``, and no
amount of sample data distinguishes them while every sampled day-of-month
is 12 or lower. A model asked which one it is will answer, confidently,
from priors about which bank it thinks wrote the file -- which is a guess
about meaning wearing the costume of an observation. So the pairs are
declared here, :func:`format_ambiguity` reports when a sample cannot tell
them apart, and the human is required to settle it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SUPPORTED_VALUE_DATE_FORMATS: tuple[str, ...] = (
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%Y/%m/%d",
    "%d/%m/%y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %B %Y",
)
"""Formats the editor offers and a proposal may name, in display order.

Deliberately small and all-numeric-or-abbreviated-month: these are the
shapes observed in Indian bank CSV exports. Anything else remains reachable
through a hand-written profile JSON, which is the reviewed escape hatch.
"""

SUPPORTED_VALUE_DATE_FORMAT_SET: frozenset[str] = frozenset(SUPPORTED_VALUE_DATE_FORMATS)

AMBIGUOUS_FORMAT_PAIRS: tuple[tuple[str, str], ...] = (
    ("%d/%m/%Y", "%m/%d/%Y"),
    ("%d-%m-%Y", "%m-%d-%Y"),
    ("%d/%m/%y", "%m/%d/%y"),
)
"""Format pairs that are indistinguishable on a day-first/month-first basis.

``%Y-%m-%d`` and ``%d.%m.%Y`` are absent because no allowlisted sibling
shares their field order and separator, so a value that parses under either
of them parses under exactly one allowlisted format.
"""

FORMAT_LABELS: dict[str, str] = {
    "%d/%m/%Y": "DD/MM/YYYY  (day first)",
    "%m/%d/%Y": "MM/DD/YYYY  (month first)",
    "%Y-%m-%d": "YYYY-MM-DD  (ISO)",
    "%d-%m-%Y": "DD-MM-YYYY  (day first)",
    "%m-%d-%Y": "MM-DD-YYYY  (month first)",
    "%d.%m.%Y": "DD.MM.YYYY  (day first)",
    "%Y/%m/%d": "YYYY/MM/DD",
    "%d/%m/%y": "DD/MM/YY  (day first)",
    "%m/%d/%y": "MM/DD/YY  (month first)",
    "%d-%b-%Y": "DD-Mon-YYYY  (e.g. 07-Aug-2024)",
    "%d-%b-%y": "DD-Mon-YY  (e.g. 07-Aug-24)",
    "%d %b %Y": "DD Mon YYYY  (e.g. 07 Aug 2024)",
    "%d %B %Y": "DD Month YYYY  (e.g. 07 August 2024)",
}
"""Human-readable label per format, for the editor's dropdown.

Kept beside the allowlist so a format cannot be offered without a label
that says, in plain terms, which component comes first -- the one detail
the reviewer is actually being asked to confirm.
"""


def is_supported_value_date_format(value: str) -> bool:
    return value in SUPPORTED_VALUE_DATE_FORMAT_SET


def parses(value_date_format: str, value: str) -> bool:
    """Whether one value parses exactly under this format.

    Exact, like the parser: :func:`datetime.datetime.strptime` and nothing
    else, with no second attempt under a neighbouring format.
    """
    try:
        datetime.strptime(value.strip(), value_date_format)
    except ValueError:
        return False
    return True


def parses_all(value_date_format: str, samples: tuple[str, ...]) -> bool:
    """Whether *every* non-empty sample parses under this format.

    An empty sample set answers ``True`` -- there is no evidence against the
    format, which is a different statement from evidence for it, and callers
    needing that distinction consult :attr:`FormatAmbiguity.evidence_rows`.
    """
    return all(
        parses(value_date_format, value) for value in samples if value.strip()
    )


def plausible_formats(samples: tuple[str, ...]) -> tuple[str, ...]:
    """Every allowlisted format under which all samples parse, in display order.

    Strict on purpose, and used only for the *ambiguity* question -- "could
    another format read these same values". Whether a format is
    **contradicted** is a separate and more forgiving judgement; see
    :func:`format_ambiguity`.
    """
    return tuple(
        fmt for fmt in SUPPORTED_VALUE_DATE_FORMATS if parses_all(fmt, samples)
    )


@dataclass(frozen=True)
class FormatAmbiguity:
    """What a sample can and cannot settle about a proposed date format."""

    proposed: str
    plausible: tuple[str, ...]
    """Allowlisted formats that read *every* sampled value."""
    contradicted: bool
    """True when the proposed format reads **none** of the sampled values.

    Deliberately not "does not read all of them". A real statement export
    can contain a row whose date cell is junk -- the repository's own demo
    fixture ships one on purpose, as an ingestion-issue example -- and
    letting one such row veto an otherwise correct format would reject the
    right mapping and send the operator hunting for a format that does not
    exist. A format that reads some rows and not others is *right*, with
    rows that will be quarantined at ingestion; that is a warning, and
    :attr:`unparsed_rows` carries it.
    """
    ambiguous_with: tuple[str, ...] = ()
    """Other formats the sample cannot distinguish the proposal from."""
    evidence_rows: int = 0
    """How many non-empty sampled values the judgement rests on."""
    parsed_rows: int = 0
    """How many of those the proposed format actually reads."""

    @property
    def unparsed_rows(self) -> int:
        """Sampled values the proposed format cannot read. Rows like them
        would be recorded as ingestion issues, not silently dropped."""
        return self.evidence_rows - self.parsed_rows

    @property
    def requires_human_choice(self) -> bool:
        """The sample is not enough; a person must pick the format.

        True when the proposal is contradicted outright, when a sibling
        format fits the sample equally well, or when there was no sampled
        value to test at all. In every one of those cases the confirmation
        UI must make the operator choose rather than accept a default.
        """
        return self.contradicted or bool(self.ambiguous_with) or self.evidence_rows == 0


def format_ambiguity(proposed: str, samples: tuple[str, ...]) -> FormatAmbiguity:
    """Judge one proposed format against the sampled value-date column.

    Ambiguity is decided over the values the proposal actually reads, not
    over the whole sample. That is the correct comparison: the question is
    whether some other format is an equally good reading of the *same*
    dates, and a row neither format can read says nothing about which of
    them is right.
    """
    non_empty = tuple(s for s in samples if s.strip())
    parsed = tuple(s for s in non_empty if parses(proposed, s))
    contradicted = bool(non_empty) and not parsed
    siblings = (
        ()
        if contradicted or not parsed
        else tuple(
            other
            for pair in AMBIGUOUS_FORMAT_PAIRS
            if proposed in pair
            for other in pair
            if other != proposed and parses_all(other, parsed)
        )
    )
    return FormatAmbiguity(
        proposed=proposed,
        plausible=plausible_formats(non_empty),
        contradicted=contradicted,
        ambiguous_with=siblings,
        evidence_rows=len(non_empty),
        parsed_rows=len(parsed),
    )


def format_options() -> tuple[dict[str, str], ...]:
    """The dropdown payload: every supported format with its label."""
    return tuple(
        {"value": fmt, "label": FORMAT_LABELS[fmt]} for fmt in SUPPORTED_VALUE_DATE_FORMATS
    )


__all__ = [
    "AMBIGUOUS_FORMAT_PAIRS",
    "FORMAT_LABELS",
    "SUPPORTED_VALUE_DATE_FORMATS",
    "SUPPORTED_VALUE_DATE_FORMAT_SET",
    "FormatAmbiguity",
    "format_ambiguity",
    "format_options",
    "is_supported_value_date_format",
    "parses",
    "parses_all",
    "plausible_formats",
]
