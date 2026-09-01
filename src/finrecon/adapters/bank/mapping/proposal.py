"""The proposal wire contract: one strict schema, decoded in exactly one place.

A mapping proposal is model output, and model output is treated here the way
every other untrusted boundary in this codebase is treated -- as bytes that
must survive a declared schema before anything reads a field off them. There
is no prose parser, no regular expression over free-form text, and no
"extract the JSON block" heuristic. The model is asked for a single tool
call whose argument grammar *is* the schema below, the arguments are decoded
with the strict duplicate-key decoder already used for Stage-3 tool calls,
and Pydantic then validates the result with ``extra="forbid"``.

**The schema is generated against the uploaded file's own headers.** Column
fields carry an ``enum`` of the exact header strings read from the CSV, so a
column that does not exist is not merely rejected afterwards -- under a
provider that honours strict tool schemas it is not expressible. That is
defence in depth and nothing more: :mod:`.validation` re-checks every column
locally, because a provider may ignore the flag, a gateway may strip it, and
a model may be served through a path that does not support it.

**What the reasoning summary is.** Short explanatory text for the review UI,
so a person can see *why* a column was proposed before approving it. It is
never read by the parser, never persisted as evidence, and never confers
authority. Hidden chain-of-thought is not requested and would not be stored
if it arrived.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..csv_profile import InactiveSideMarker
from .formats import SUPPORTED_VALUE_DATE_FORMATS

PROPOSAL_TOOL_NAME = "propose_bank_column_mapping"

PROPOSAL_SCHEMA_VERSION = "bank-mapping-proposal-v1"
"""Version of this wire contract, recorded on every proposal.

Bumped if the schema's own fields change, so a stored proposal record can
always be read back under the shape it was produced against.
"""

MAX_REASONING_CHARS = 400
MAX_UNCERTAINTIES = 6
MAX_UNCERTAINTY_CHARS = 200
MAX_DIRECTION_VALUES = 8
MAX_DIRECTION_VALUE_CHARS = 24


class ProposedMoneyColumns(BaseModel):
    """The money model, in the flat all-fields-present shape the wire uses.

    Both supported shapes travel in one object with every field required and
    the irrelevant side explicitly ``null``. A discriminated union would be
    tidier Python and worse wire: strict tool-schema support across providers
    is reliable for closed all-required objects and is not reliable for
    ``anyOf``. :meth:`resolved` collapses it back into the profile-shaped
    payload once ``kind`` has been read.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["debit_credit", "amount_direction"]
    debit_column: str | None = None
    credit_column: str | None = None
    inactive_side_marker: Literal["empty_only", "empty_or_zero"] | None = None
    amount_column: str | None = None
    direction_column: str | None = None
    credit_values: list[str] | None = None
    debit_values: list[str] | None = None

    def resolved(self) -> dict[str, Any]:
        """The ``money_columns`` payload :mod:`..profile_json` reads.

        Only the fields belonging to the declared ``kind`` are carried
        across, so a stray value on the unused side cannot reach a profile.
        """
        if self.kind == "debit_credit":
            return {
                "kind": "debit_credit",
                "debit_column": self.debit_column,
                "credit_column": self.credit_column,
                "inactive_side_marker": (
                    self.inactive_side_marker or InactiveSideMarker.EMPTY_ONLY.value
                ),
            }
        return {
            "kind": "amount_direction",
            "amount_column": self.amount_column,
            "direction_column": self.direction_column,
            "credit_values": list(self.credit_values or ()),
            "debit_values": list(self.debit_values or ()),
        }


class ProposedMapping(BaseModel):
    """The column mapping a model proposes. Not authoritative on its own."""

    model_config = ConfigDict(extra="forbid")

    value_date_column: str
    value_date_format: str
    value_date_format_certain: bool
    """The model's own claim that the sample settles the format.

    Recorded, and then ignored as authority: :func:`..formats.format_ambiguity`
    decides ambiguity from the sampled values, and a ``True`` here does not
    override it. It is kept because a model that says "uncertain" when the
    data is in fact ambiguous is worth surfacing to the reviewer, and a
    model that says "certain" about an ambiguous sample is worth not
    believing.
    """
    narration_column: str
    reference_id_column: str | None
    money: ProposedMoneyColumns

    def profile_payload(self, *, profile_id: str, currency: str = "INR") -> dict[str, Any]:
        """This mapping as a :mod:`..profile_json` payload.

        ``profile_id`` is supplied by the caller rather than proposed: a
        record-namespacing identifier is FinRecon's to assign, never the
        model's.
        """
        return {
            "profile_id": profile_id,
            "currency": currency,
            "value_date_column": self.value_date_column,
            "value_date_format": self.value_date_format,
            "narration_column": self.narration_column,
            "reference_id_column": self.reference_id_column,
            "money_columns": self.money.resolved(),
        }


class ProposalReasoning(BaseModel):
    """Short per-field rationale, for display only."""

    model_config = ConfigDict(extra="forbid")

    value_date: str = Field(max_length=MAX_REASONING_CHARS)
    money: str = Field(max_length=MAX_REASONING_CHARS)
    narration: str = Field(max_length=MAX_REASONING_CHARS)
    reference: str = Field(max_length=MAX_REASONING_CHARS)


class MappingProposal(BaseModel):
    """One decoded, schema-valid proposal. Still requires human confirmation."""

    model_config = ConfigDict(extra="forbid")

    mapping: ProposedMapping
    reasoning_summary: ProposalReasoning
    uncertainties: list[str] = Field(default_factory=list, max_length=MAX_UNCERTAINTIES)


def proposal_tool_schema(headers: tuple[str, ...]) -> dict[str, Any]:
    """The strict argument grammar, closed over this file's actual headers.

    Every property is required and ``additionalProperties`` is false at every
    level, which is what
    :func:`finrecon.agent.providers.openai_compatible.supports_strict_tool_schema`
    requires before it will send ``strict: true``.
    """
    column = {"type": "string", "enum": list(headers)}
    optional_column = {"type": ["string", "null"], "enum": [*headers, None]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["mapping", "reasoning_summary", "uncertainties"],
        "properties": {
            "mapping": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "value_date_column",
                    "value_date_format",
                    "value_date_format_certain",
                    "narration_column",
                    "reference_id_column",
                    "money",
                ],
                "properties": {
                    "value_date_column": {
                        **column,
                        "description": (
                            "Header of the column holding the transaction's value date."
                        ),
                    },
                    "value_date_format": {
                        "type": "string",
                        "enum": list(SUPPORTED_VALUE_DATE_FORMATS),
                        "description": (
                            "strptime format matching the value-date column exactly. "
                            "Only these formats are supported."
                        ),
                    },
                    "value_date_format_certain": {
                        "type": "boolean",
                        "description": (
                            "False when the sample rows cannot distinguish day-first "
                            "from month-first ordering. Say false rather than guessing."
                        ),
                    },
                    "narration_column": {
                        **column,
                        "description": (
                            "Header of the free-text description/narration column."
                        ),
                    },
                    "reference_id_column": {
                        **optional_column,
                        "description": (
                            "Header of the bank's own per-transaction reference/UTR "
                            "column, or null when the table has none."
                        ),
                    },
                    "money": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "kind",
                            "debit_column",
                            "credit_column",
                            "inactive_side_marker",
                            "amount_column",
                            "direction_column",
                            "credit_values",
                            "debit_values",
                        ],
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["debit_credit", "amount_direction"],
                                "description": (
                                    "debit_credit for two separate amount columns; "
                                    "amount_direction for one amount column plus a "
                                    "direction marker column."
                                ),
                            },
                            "debit_column": {
                                **optional_column,
                                "description": "debit_credit only; null otherwise.",
                            },
                            "credit_column": {
                                **optional_column,
                                "description": "debit_credit only; null otherwise.",
                            },
                            "inactive_side_marker": {
                                "type": ["string", "null"],
                                "enum": ["empty_only", "empty_or_zero", None],
                                "description": (
                                    "debit_credit only. empty_or_zero when the source "
                                    "zero-fills the side a row does not use; "
                                    "empty_only when it leaves that cell blank."
                                ),
                            },
                            "amount_column": {
                                **optional_column,
                                "description": "amount_direction only; null otherwise.",
                            },
                            "direction_column": {
                                **optional_column,
                                "description": "amount_direction only; null otherwise.",
                            },
                            "credit_values": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                                "description": (
                                    "amount_direction only: the exact raw strings that "
                                    "mark a credit. Null otherwise."
                                ),
                            },
                            "debit_values": {
                                "type": ["array", "null"],
                                "items": {"type": "string"},
                                "description": (
                                    "amount_direction only: the exact raw strings that "
                                    "mark a debit. Null otherwise."
                                ),
                            },
                        },
                    },
                },
            },
            "reasoning_summary": {
                "type": "object",
                "additionalProperties": False,
                "required": ["value_date", "money", "narration", "reference"],
                "properties": {
                    "value_date": {"type": "string"},
                    "money": {"type": "string"},
                    "narration": {"type": "string"},
                    "reference": {"type": "string"},
                },
                "description": (
                    "One short sentence per field naming the evidence in the sample. "
                    "Explanatory text for a human reviewer, not a decision."
                ),
            },
            "uncertainties": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything the sample does not settle. Empty list if none.",
            },
        },
    }


__all__ = [
    "MAX_DIRECTION_VALUES",
    "MAX_DIRECTION_VALUE_CHARS",
    "MAX_UNCERTAINTIES",
    "MAX_UNCERTAINTY_CHARS",
    "PROPOSAL_SCHEMA_VERSION",
    "PROPOSAL_TOOL_NAME",
    "MappingProposal",
    "ProposalReasoning",
    "ProposedMapping",
    "ProposedMoneyColumns",
    "proposal_tool_schema",
]
