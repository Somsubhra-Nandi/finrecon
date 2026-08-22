"""Structured audit records for every Stage-2 decision.

The requirement (DESIGN.md §8, §13) is that every decision — resolution
*and* refusal — leaves a record sufficient to answer "why did FinRecon
reconcile these records?". These records are structured data only: rule
ID, matcher ID, linked record IDs, the exact-paise derivation, the
normalized dates the window predicate used, and what the matcher
considered but did not pick.

No prose is generated. A human-readable sentence assembled by the system
would be a narrative about the evidence rather than the evidence itself,
and DESIGN.md §4.1 is emphatic that decision-relevant inputs stay primary.
"""

from __future__ import annotations

import hashlib
import json

from finrecon.matchers.result import DecisionStatus, ReconciliationDecision


def canonical_json(payload: dict) -> str:
    """Byte-stable JSON: sorted keys, no incidental whitespace, ASCII-safe."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def audit_payload(decision: ReconciliationDecision) -> dict:
    """The structured evidence body recorded for one decision."""
    return {
        "case_id": decision.case_id,
        "bank_record_id": decision.bank_record_id,
        "decision": decision.status.value,
        "matcher_id": decision.matcher_id,
        "rule_id": decision.rule_id,
        "settlement_ids": list(decision.settlement_ids),
        "relationship": decision.relationship,
        "evidence": decision.evidence.model_dump(mode="json"),
    }


def audit_id_for(batch_id: str, decision: ReconciliationDecision, sequence: int) -> str:
    """Deterministic identity for one logical audit record.

    Derived from the batch, the case, its position in the trail, and a
    hash of the decision content. Reprocessing an identical batch
    regenerates identical IDs, so the primary key on ``audit_log``
    collapses the rewrite instead of appending a second copy of the same
    logical decision.
    """
    body = canonical_json(
        {
            "batch_id": batch_id,
            "sequence": sequence,
            "payload": audit_payload(decision),
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def audit_row(batch_id: str, decision: ReconciliationDecision, sequence: int) -> dict:
    """A complete, insert-ready audit row."""
    return {
        "audit_id": audit_id_for(batch_id, decision, sequence),
        "batch_id": batch_id,
        "case_id": decision.case_id,
        "sequence": sequence,
        "decision": decision.status.value,
        "matcher_id": decision.matcher_id,
        "rule_id": decision.rule_id,
        "settlement_ids": canonical_json(list(decision.settlement_ids)),
        "evidence_json": canonical_json(audit_payload(decision)["evidence"]),
    }


def is_resolution(decision: ReconciliationDecision) -> bool:
    return decision.status is DecisionStatus.RESOLVED
