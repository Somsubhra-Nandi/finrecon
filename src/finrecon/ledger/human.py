"""Small, server-side boundary for durable human reconciliation actions."""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.candidates.snapshot import CaseSnapshot


class HumanResolutionError(ValueError):
    """A proposed human action is not valid for the immutable case shown."""


@dataclass(frozen=True)
class HumanResolution:
    resolution_id: str
    batch_id: str
    case_id: str
    bank_record_id: str
    snapshot_hash: str
    revision: int
    resolution_type: str
    selected_candidate_id: str | None
    reason: str
    actor: str | None
    recorded_at: str
    active: bool

    @property
    def resolved(self) -> bool:
        return self.resolution_type == "select_candidate"


def validate_human_resolution(
    snapshot: CaseSnapshot, selected_candidate_id: str | None
) -> str:
    """Validate the only two supported actions against the frozen snapshot."""
    if not snapshot.verify_integrity():
        raise HumanResolutionError("case snapshot integrity check failed")
    if selected_candidate_id is None:
        return "keep_escalated"
    if selected_candidate_id not in snapshot.candidate_ids():
        raise HumanResolutionError(
            "selected candidate is not a member of this immutable case snapshot"
        )
    return "select_candidate"
