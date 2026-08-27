"""Ingestion provenance sidecar.

RAZORPAY-INPUT-GAP.md §4.5 (second bullet): row-level provenance is a
sidecar, not engine input. ``loader.py`` reads exactly five files; nothing
here widens that contract, and nothing under :mod:`finrecon.decide`,
:mod:`finrecon.matchers`, :mod:`finrecon.evidence`, :mod:`finrecon.agent` or
the rest of the reconciliation path ever imports this module. It exists so
an ingestion run can be audited and debugged: which source row produced
which canonical object, which fields were used, which documented fields
were deliberately not projected, and which settlements carry an ingestion
conflict a decision-engine case cannot auto-resolve.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ManifestModel(BaseModel):
    """Immutable, strict, closed-schema base for every manifest entry."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class RowProvenance(ManifestModel):
    """What one source recon row produced, and what of it was used."""

    source_id: str
    """Identity of the source fixture/file/collection this row came from."""
    row_index: int
    """Position of this row within the collection passed to the adapter."""
    row_fingerprint: str
    """SHA-256 over the row's canonical JSON (sorted keys, full content)."""
    entity_id: str
    settlement_id: str
    produced: tuple[str, ...]
    """Canonical object(s)/line(s) this row contributed to, e.g.
    ``("settlement:setl_x#breakup[2]",)``."""
    source_fields_used: tuple[str, ...]
    dropped_fields: tuple[str, ...]
    """Documented fields present on the row but not projected into any
    canonical field (see ``recon_row.DOCUMENTED_NOT_PROJECTED``)."""
    unrecognized_fields: tuple[str, ...]
    """Fields present on the row that are not part of the documented
    contract at all. Recorded, never silently discarded and never raised
    on, since real Razorpay data may carry additional documented fields
    this adapter has not been told to support."""


class IngestConflict(ManifestModel):
    """A per-settlement (never whole-batch) ingestion contradiction.

    "missing" and "conflicting" are different facts and must stay
    distinguishable — see RAZORPAY-INPUT-GAP.md §4.5 and item 5 of the task
    brief. A conflict never aborts the batch; it marks the one settlement
    group affected so the case depending on it cannot auto-resolve.
    """

    kind: str
    settlement_id: str
    detail: str
    entity_ids: tuple[str, ...] = ()


class IngestWarning(ManifestModel):
    kind: str
    settlement_id: str | None = None
    detail: str


class ConformanceReport(ManifestModel):
    """Per-settlement accounting diagnostic. Never a manufactured balance.

    ``source_net`` is ``source_credit_total - source_debit_total`` over
    every non-duplicate, non-conflicted row in the group.
    ``canonical_breakup_total`` is the signed sum of the reconstructed
    breakup lines. Whether the two agree is *reported*, not assumed and
    never forced.
    """

    settlement_id: str
    source_credit_total: int
    source_debit_total: int
    source_net: int
    canonical_breakup_total: int
    totals_agree: bool
    difference: int
    """``canonical_breakup_total - source_net``. Zero iff ``totals_agree``."""


class IngestManifest(ManifestModel):
    source_id: str
    rows: tuple[RowProvenance, ...]
    duplicate_rows_dropped: tuple[str, ...]
    """Row fingerprints of exact duplicate rows dropped before grouping."""
    conflicts: tuple[IngestConflict, ...]
    warnings: tuple[IngestWarning, ...]
    conformance: tuple[ConformanceReport, ...]


__all__ = [
    "ConformanceReport",
    "IngestConflict",
    "IngestManifest",
    "IngestWarning",
    "ManifestModel",
    "RowProvenance",
]
