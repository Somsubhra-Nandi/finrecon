"""Pydantic input and output schemas for every read-only investigation tool.

DESIGN.md 4.1 requires tool I/O to be schema-validated and makes validation
failure an escalation path rather than a retry loop. These models are that
contract, and they are strict on both sides for different reasons:

* **Inputs** are strict and closed (``extra="forbid"``) because they are the
  boundary where a model's text becomes an executed call. An unknown field,
  a number where a string belongs, a missing argument -- none of it reaches
  a handler. The call is refused, recorded, and the case escalates.
* **Outputs** are strict and closed because they are the evidence the
  deterministic validator later reads. A tool that could return an
  unmodelled field could return a *conclusion*, and conclusions are exactly
  what tools are forbidden to produce.

Read the output models with one question in mind: *can a caller learn which
candidate is correct by reading a single one of these?* Every field is an
identifier, an integer paise amount, a date, a count, or a mechanical
lexical relation. There is no ``recommended_candidate``, no ``score``, no
``confidence`` and no ``is_match``. The tools expose facts; the validator
decides, over the complete candidate set, what those facts prove.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from finrecon.evidence.reference import ReferenceComparison
from finrecon.normalize.provenance import FrozenModel


class ToolInput(BaseModel):
    """Base for tool arguments: strict, closed, immutable.

    Not merely tidy. ``extra="forbid"`` is what stops a model from smuggling
    an extra argument past a handler, and ``strict=True`` is what stops
    ``"candidate_id": 5`` from being coerced into a lookup key.
    """

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ToolOutput(FrozenModel):
    """Base for tool results: strict, closed, immutable, and purely factual."""


# --- lookup_candidate_records --------------------------------------------


class LookupCandidateRecordsInput(ToolInput):
    candidate_id: str = Field(
        description="A candidate_id from this case's immutable candidate set."
    )


class SettlementRecordFacts(ToolOutput):
    """Normalized facts about one settlement inside a candidate group."""

    settlement_id: str
    utr: str | None
    """The settlement's UTR exactly as the source carried it, or null."""
    amount_paise: int
    created_at_utc: datetime
    settlement_date_utc: date
    breakup_line_count: int
    breakup_total_paise: int
    breakup_unexplained_delta_paise: int
    """``settlement amount - sum(break-up lines)``. Zero means fully accounted."""


class LookupCandidateRecordsOutput(ToolOutput):
    candidate_id: str
    settlement_ids: tuple[str, ...]
    total_paise: int
    blocking_rule: str
    """The declared Stage-2 blocking rule that surfaced this candidate."""
    settlements: tuple[SettlementRecordFacts, ...]


# --- inspect_settlement_breakup ------------------------------------------


class InspectSettlementBreakupInput(ToolInput):
    settlement_id: str = Field(
        description="A settlement_id named by one of this case's candidates."
    )


class BreakupLineFacts(ToolOutput):
    line_type: str
    amount_paise: int
    """Signed: credits to the merchant positive, deductions negative."""
    reference_id: str | None
    reference_status: str | None
    """Status of the payment or refund the line references, when it references one."""


class InspectSettlementBreakupOutput(ToolOutput):
    settlement_id: str
    settlement_amount_paise: int
    breakup_total_paise: int
    unexplained_delta_paise: int
    declared_adjustment_paise: int
    """Total of explicit ``adjustment`` lines -- the only declared rounding channel."""
    totals_by_line_type: tuple[tuple[str, int], ...]
    lines: tuple[BreakupLineFacts, ...]


# --- compute_expected_net -------------------------------------------------


class ComputeExpectedNetInput(ToolInput):
    candidate_id: str = Field(
        description="A candidate_id from this case's immutable candidate set."
    )


class SettlementNetFacts(ToolOutput):
    settlement_id: str
    settlement_amount_paise: int
    breakup_total_paise: int
    unexplained_delta_paise: int


class ComputeExpectedNetOutput(ToolOutput):
    """Exact integer-paise arithmetic for one candidate. No tolerance anywhere.

    ``*_is_exact`` fields are arithmetic facts (a delta equals zero), not
    judgements about the candidate. A candidate can be arithmetically exact
    and still be the wrong counterparty -- which is the whole reason this
    case reached Stage 3.
    """

    candidate_id: str
    bank_amount_paise: int
    settlement_group_total_paise: int
    group_unexplained_delta_paise: int
    """``bank credit - sum(settlement amounts)``."""
    group_total_is_exact: bool
    every_breakup_is_exact: bool
    per_settlement: tuple[SettlementNetFacts, ...]


# --- compare_reference_fragment ------------------------------------------


class CompareReferenceFragmentInput(ToolInput):
    candidate_id: str = Field(
        description="A candidate_id from this case's immutable candidate set."
    )
    fragment: str = Field(
        description=(
            "A literal substring of this case's bank narration to test against "
            "the candidate's references. Copy it from the narration exactly; do "
            "not clean it up, complete it, or invent characters."
        )
    )


class CompareReferenceFragmentOutput(ToolOutput):
    """Mechanical comparison of one fragment against one candidate's references.

    Every declared relation is evaluated against every reference the
    candidate's settlements carry, and all results are returned whether they
    hold or not. There is deliberately no aggregate verdict: which candidate
    a fragment identifies is a question about the *complete* candidate set,
    and this tool only ever sees one candidate.
    """

    candidate_id: str
    fragment: str
    fragment_present_in_narration: bool
    """Whether the fragment occurs literally in the immutable case narration.

    ``false`` means the fragment was invented rather than observed. The tool
    still reports the comparison -- suppressing it would hide the attempt --
    but the validator refuses to admit evidence from a fragment that is not
    in the narration it claims to come from.
    """
    fragment_offsets: tuple[int, ...]
    """Every start index at which the fragment occurs in the narration."""
    narration_length: int
    comparisons: tuple[ReferenceComparison, ...]
    """One entry per (settlement, reference kind) pair, in deterministic order."""


__all__ = [
    "BreakupLineFacts",
    "CompareReferenceFragmentInput",
    "CompareReferenceFragmentOutput",
    "ComputeExpectedNetInput",
    "ComputeExpectedNetOutput",
    "InspectSettlementBreakupInput",
    "InspectSettlementBreakupOutput",
    "LookupCandidateRecordsInput",
    "LookupCandidateRecordsOutput",
    "SettlementNetFacts",
    "SettlementRecordFacts",
    "ToolInput",
    "ToolOutput",
]
