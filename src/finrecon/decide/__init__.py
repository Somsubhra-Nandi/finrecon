"""Deterministic adjudication: the validator and the policy gate.

This package decides whether money moves. Nothing in it consumes a model's
prose, a model's confidence, or a candidate list a model produced. Its
inputs are the immutable Stage-2 snapshot, the declared policy, and raw tool
outputs -- in that order of authority.

``config``     declared evidence thresholds and value-aware limits
``validator``  predicates over the complete candidate set + raw evidence
``policy``     hard blockers, the value ladder, RESOLVE or ESCALATE
"""

from finrecon.decide.config import (
    DEFAULT_POLICY,
    EvidencePolicy,
    Stage3Policy,
    ValuePolicy,
)
from finrecon.decide.policy import (
    HARD_BLOCKERS,
    PolicyDecision,
    adjudicate,
    decide,
)
from finrecon.decide.validator import (
    RawToolEvidence,
    ValidatorResult,
    raw_tool_evidence,
    validate_case,
)

__all__ = [
    "DEFAULT_POLICY",
    "HARD_BLOCKERS",
    "EvidencePolicy",
    "PolicyDecision",
    "RawToolEvidence",
    "Stage3Policy",
    "ValidatorResult",
    "ValuePolicy",
    "adjudicate",
    "decide",
    "raw_tool_evidence",
    "validate_case",
]
