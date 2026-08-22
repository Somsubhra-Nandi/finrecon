"""Deterministic candidate generation and immutable case snapshots."""

from .generator import (
    BLOCKING_RULE_DATE_WINDOW_ONLY,
    BLOCKING_RULE_EXACT_TOTAL,
    blocking_description,
    build_unresolved_snapshot,
    candidate_id_for,
    generate_candidates,
)
from .snapshot import (
    BankRecordFacts,
    BaseEvidence,
    CandidateRecord,
    CaseSnapshot,
    SettlementFacts,
    build_case_snapshot,
)

__all__ = [
    "BLOCKING_RULE_DATE_WINDOW_ONLY",
    "BLOCKING_RULE_EXACT_TOTAL",
    "blocking_description",
    "build_unresolved_snapshot",
    "candidate_id_for",
    "generate_candidates",
    "BankRecordFacts",
    "BaseEvidence",
    "CandidateRecord",
    "CaseSnapshot",
    "SettlementFacts",
    "build_case_snapshot",
]
