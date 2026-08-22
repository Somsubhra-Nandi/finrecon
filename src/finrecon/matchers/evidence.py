"""Structured, machine-inspectable evidence attached to every Stage-2 decision.

DESIGN.md §8's audit requirement is that a reader can answer *"why did
FinRecon reconcile these records?"* from the record itself. These models
are that answer, and they are deliberately data rather than prose: every
field is an identifier, an integer paise amount, a date, or an enum. No
free-text explanation is generated anywhere in Stage 2.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from finrecon.normalize.provenance import FrozenModel

IdentifierKind = Literal["utr", "settlement_id"]


class ReferenceEvidence(FrozenModel):
    """One narration token that exactly equalled a canonical identifier."""

    matched_token: str
    """The token exactly as it appeared in the raw narration."""
    matched_token_key: str
    """The upper-cased comparison key both sides were compared on."""
    identifier_kind: IdentifierKind
    identifier_value: str
    settlement_id: str


class DateWindowEvidence(FrozenModel):
    """The declared value-date window predicate, and the values it was applied to."""

    bank_value_date: date
    settlement_dates: tuple[date, ...]
    offset_days: tuple[int, ...]
    """``bank_value_date - settlement_date`` per settlement, same order."""
    window_days_before: int
    window_days_after: int


class BreakupLineEvidence(FrozenModel):
    line_type: str
    amount_paise: int
    reference_id: str | None
    reference_status: str | None
    """Status of the referenced payment/refund record, when the line references one."""


class SettlementDerivation(FrozenModel):
    """Exact paise accounting for one settlement's break-up.

    ``unexplained_delta_paise`` is ``settlement_amount - sum(break-up)``.
    Any non-zero value is a hard blocker (DESIGN.md §4.3); it is carried
    here rather than merely raised so a refusal is as auditable as a
    resolution.
    """

    settlement_id: str
    settlement_amount_paise: int
    breakup_total_paise: int
    breakup_by_type: tuple[tuple[str, int], ...]
    """Signed per-line-type totals, ordered by line type. Tuples, not a dict, so it is frozen."""
    lines: tuple[BreakupLineEvidence, ...]
    unexplained_delta_paise: int
    declared_adjustment_paise: int
    """Total of explicit ``adjustment`` lines — the only declared rounding channel."""


class MoneyDerivation(FrozenModel):
    """Whole-case money accounting: bank credit against the settlement group."""

    bank_amount_paise: int
    settlement_group_total_paise: int
    unexplained_delta_paise: int
    """``bank_amount - sum(settlement amounts)``. Must be exactly 0 to resolve."""
    per_settlement: tuple[SettlementDerivation, ...]

    @property
    def is_exact(self) -> bool:
        return self.unexplained_delta_paise == 0 and all(
            d.unexplained_delta_paise == 0 for d in self.per_settlement
        )


class DecisionEvidence(FrozenModel):
    """Everything the deterministic core looked at, for one decision."""

    references: tuple[ReferenceEvidence, ...] = ()
    money: MoneyDerivation | None = None
    date_window: DateWindowEvidence | None = None
    considered_settlement_ids: tuple[str, ...] = ()
    """Every settlement the rule examined, not only the one it selected."""
    competing_solution_ids: tuple[tuple[str, ...], ...] = ()
    """When a rule refused for ambiguity: the tied settlement groups, in full."""
