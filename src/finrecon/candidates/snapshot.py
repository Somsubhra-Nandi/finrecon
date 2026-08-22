"""The immutable case snapshot — Stage 2's most load-bearing deliverable.

DESIGN.md §4.1 makes the snapshot the structural answer to an agent that
"fishes by omission": the complete deterministic candidate set plus the
base evidence is built *before any agent exists* and handed to the
decision layer directly, so a later stage may enrich a case and can never
shrink it.

Immutability here is defence in depth, three layers:

1. Every model is a frozen, strict, closed-schema pydantic model, and
   every collection is a ``tuple``. Attribute assignment raises; there is
   no ``append`` or ``remove`` to reach for.
2. :attr:`CaseSnapshot.content_hash` is computed over the canonical
   serialization at construction and stored inside the snapshot.
   :meth:`CaseSnapshot.verify_integrity` recomputes it. Any route around
   layer 1 — ``model_copy(update=...)``, ``model_construct``, pickling
   surgery — changes the content without changing the recorded hash, and
   is therefore detectable rather than silent.
3. The ledger persists that same hash alongside the snapshot, so tampering
   after persistence is detectable too.

A snapshot is *evidence*, not working state. Nothing in Stage 2 mutates
one; a corrected case is a new snapshot.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from finrecon.matchers.evidence import DecisionEvidence, SettlementDerivation
from finrecon.models import BankRecordDirection
from finrecon.normalize.provenance import FrozenModel, SourceProvenance


class CandidateRecord(FrozenModel):
    """One plausible counterparty group. Carries no score and no rank."""

    candidate_id: str
    settlement_ids: tuple[str, ...]
    total_paise: int
    blocking_rule: str
    """Which declared blocking rule surfaced this candidate."""
    unexplained_delta_paise: int
    """``bank credit - group total``. Zero for exact-total candidates."""
    settlement_dates: tuple[date, ...]


class SettlementFacts(FrozenModel):
    """Normalized, immutable facts about one settlement in the case's orbit."""

    settlement_id: str
    utr: str | None
    utr_key: str | None
    amount_paise: int
    created_at_utc: datetime
    settlement_date_utc: date
    derivation: SettlementDerivation
    source: SourceProvenance


class BankRecordFacts(FrozenModel):
    """Normalized, immutable facts about the credit under reconciliation."""

    bank_record_id: str
    amount_paise: int
    direction: BankRecordDirection
    narration: str
    """Raw narration, preserved byte-identical from the source record."""
    reference_tokens: tuple[str, ...]
    value_date: date
    source: SourceProvenance


class BaseEvidence(FrozenModel):
    """Everything the deterministic core established about the case."""

    bank_record: BankRecordFacts
    settlement_facts: tuple[SettlementFacts, ...]
    """Facts for every settlement appearing in any candidate. Never a subset."""
    decision_evidence: DecisionEvidence
    """The evidence behind the refusal, including what the matchers considered."""
    blocking: tuple[tuple[str, str], ...]
    """Declared blocking parameters as sorted ``(name, value)`` pairs."""


class CaseSnapshot(FrozenModel):
    """An unresolved case, frozen with its complete candidate set."""

    case_id: str
    batch_id: str
    bank_record_id: str
    unresolved_rule_id: str
    """The declared rule under which deterministic reconciliation stopped."""
    unresolved_matcher_id: str
    candidates: tuple[CandidateRecord, ...]
    base_evidence: BaseEvidence
    content_hash: str
    """SHA-256 over the canonical serialization of every field above."""

    def canonical_payload(self) -> dict:
        payload = self.model_dump(mode="json")
        payload.pop("content_hash", None)
        return payload

    def computed_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    def verify_integrity(self) -> bool:
        """True when the content still matches the hash recorded at construction."""
        return self.content_hash == self.computed_hash()

    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(c.candidate_id for c in self.candidates)


def build_case_snapshot(
    *,
    case_id: str,
    batch_id: str,
    bank_record_id: str,
    unresolved_rule_id: str,
    unresolved_matcher_id: str,
    candidates: tuple[CandidateRecord, ...],
    base_evidence: BaseEvidence,
) -> CaseSnapshot:
    """Construct a snapshot and seal it with its content hash.

    The only sanctioned way to make a :class:`CaseSnapshot`: it is the
    single place ``content_hash`` is computed honestly from the content,
    which is what gives :meth:`CaseSnapshot.verify_integrity` its meaning.
    """
    draft = CaseSnapshot(
        case_id=case_id,
        batch_id=batch_id,
        bank_record_id=bank_record_id,
        unresolved_rule_id=unresolved_rule_id,
        unresolved_matcher_id=unresolved_matcher_id,
        candidates=candidates,
        base_evidence=base_evidence,
        content_hash="",
    )
    return CaseSnapshot(
        case_id=case_id,
        batch_id=batch_id,
        bank_record_id=bank_record_id,
        unresolved_rule_id=unresolved_rule_id,
        unresolved_matcher_id=unresolved_matcher_id,
        candidates=candidates,
        base_evidence=base_evidence,
        content_hash=draft.computed_hash(),
    )
