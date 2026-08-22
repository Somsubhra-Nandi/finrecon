"""The single decision type every Stage-2 matcher returns."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from finrecon.matchers.evidence import DecisionEvidence
from finrecon.normalize.provenance import FrozenModel


class DecisionStatus(str, Enum):
    RESOLVED = "resolved"
    """A deterministic rule proved exactly one settlement group for this credit."""

    UNRESOLVED = "unresolved"
    """No rule proved a unique group. The case flows on to candidate generation."""


Relationship = Literal["one_to_one", "many_to_one"]


class ReconciliationDecision(FrozenModel):
    """One deterministic decision about one bank record.

    Frozen: a decision is a fact about a batch, and nothing downstream —
    including anything Stage 3 might later add — may edit one in place.
    """

    case_id: str
    bank_record_id: str
    status: DecisionStatus
    matcher_id: str
    """Which matcher produced this decision (or examined the case and declined)."""
    rule_id: str
    """The specific declared rule that fired; see :mod:`finrecon.matchers.rules`."""
    settlement_ids: tuple[str, ...] = ()
    """The proven counterparties. Always empty when ``status`` is UNRESOLVED."""
    relationship: Relationship | None = None
    evidence: DecisionEvidence = DecisionEvidence()

    def model_post_init(self, __context: object) -> None:
        if self.status is DecisionStatus.UNRESOLVED and self.settlement_ids:
            raise ValueError("an unresolved decision may not carry settlement links")
        if self.status is DecisionStatus.RESOLVED and not self.settlement_ids:
            raise ValueError("a resolved decision must carry at least one settlement link")
        if self.status is DecisionStatus.RESOLVED:
            expected = "one_to_one" if len(self.settlement_ids) == 1 else "many_to_one"
            if self.relationship != expected:
                raise ValueError(
                    f"relationship {self.relationship!r} disagrees with "
                    f"{len(self.settlement_ids)} linked settlement(s)"
                )
            if tuple(sorted(self.settlement_ids)) != self.settlement_ids:
                raise ValueError("settlement_ids must be in deterministic sorted order")
