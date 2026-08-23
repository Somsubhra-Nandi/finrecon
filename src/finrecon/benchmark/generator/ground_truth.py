"""Hidden ground-truth schema for the Stage 1 benchmark.

DESIGN.md's Stage 1 requirements: ground truth must live separately from
the system-visible benchmark inputs, and must carry enough for later
evaluation to determine tier, participating records, the correct
reconciliation relationship (when unique), whether escalation is required,
the underlying undegraded reference, corruption/degradation metadata, and
value at stake in integer paise.

This module defines the shape only. Nothing here is consumed by any
matching/reconciliation code — that would be Stage 2+, out of scope.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Tier = Literal["T0", "T1", "T2", "T3"]
RequiredOutcome = Literal["AUTO_RESOLVABLE", "ESCALATE"]


class DegradationInfo(BaseModel):
    """Which UTR-degradation-ladder category and narration template produced the visible evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category_id: str
    narration_template_id: str | None = None
    surviving_evidence: str | None = None
    """Exactly what survived of the reference in the narration (benchmark v2).

    Recorded so evaluation can state *which* fragment a case turned on
    rather than re-deriving it, and so the generator's own causal-necessity
    assertions have a single declared subject. ``None`` wherever no
    reference survives at all (T1's ``omitted``, T3's ``ambiguous``).
    """


class ReconciliationRelationship(BaseModel):
    """The correct link between a bank record and its settlement(s), when unique."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bank_record_id: str
    settlement_ids: tuple[str, ...]
    relationship: Literal["one_to_one", "many_to_one"]


class GroundTruthCase(BaseModel):
    """One hidden ground-truth entry, keyed by ``case_id``.

    ``record_ids`` groups every financial record participating in this
    case by canonical record type, so evaluation can look records up in
    the system-visible datasets without any answer being embedded in the
    visible records themselves.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    tier: Tier
    archetype: str
    record_ids: dict[str, tuple[str, ...]]
    required_outcome: RequiredOutcome
    correct_relationship: ReconciliationRelationship | None
    true_reference: str | None
    degradation: DegradationInfo | None
    distractor_settlement_ids: tuple[str, ...] = ()
    """Settlements deliberately built to be structurally plausible but wrong (benchmark v2).

    Populated for T2, where the construct requires at least one decoy that
    every declared Stage-2 rule finds as compelling as the true
    counterparty. Empty elsewhere. Hidden, like the rest of this file —
    nothing on the reconciliation path may read it.
    """
    value_at_stake_paise: int

    def to_json_dict(self) -> dict:
        return self.model_dump(mode="json")
