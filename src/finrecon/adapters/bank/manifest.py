"""Ingestion provenance sidecar for the bank CSV adapter.

Same pattern as :mod:`finrecon.adapters.manifest` (the Razorpay adapter's
sidecar): a per-row audit trail plus per-row-group conflict record, written
*alongside* the canonical output, never read by the reconciliation path.
Reuses that module's :class:`~finrecon.adapters.manifest.ManifestModel`
base directly rather than duplicating its strict/frozen/closed-schema
config -- it carries no settlement- or entity-specific assumptions, so
nothing here needs its own copy.

Deliberately narrower than the Razorpay manifest shape: bank CSV ingestion
has no settlement-style grouping/aggregation step, so there is no
``ConformanceReport`` (nothing here reconstructs a total from parts) and no
blocking/non-blocking conflict classification (see ``csv_parser.py``'s
module docstring for why every rejection is already row-scoped and
therefore never needs to "quarantine" a larger object).
"""

from __future__ import annotations

from finrecon.adapters.manifest import ManifestModel


class BankRowProvenance(ManifestModel):
    """What one source CSV row produced, and what of it was used."""

    source_id: str
    """Identity of the source file/upload this row came from."""
    row_index: int
    """Position of this row within the data rows the CSV reader produced
    (0-based, excluding the header row)."""
    row_fingerprint: str
    """SHA-256 over the row's full raw column values (sorted keys),
    independent of column order or which columns the profile projects."""
    produced: tuple[str, ...]
    """The canonical object this row produced, e.g.
    ``("bank_record:icici_savings_v1:ref:S0000123",)`` -- empty when the
    row was rejected or excluded as a conflicting duplicate."""
    source_fields_used: tuple[str, ...]
    """CSV header columns the profile actually reads."""
    dropped_fields: tuple[str, ...]
    """CSV header columns present in the file but not referenced by the
    profile at all -- a profile is a closed, exhaustive column mapping
    (task brief §1), so anything outside it is a deliberate, recorded
    omission, not a silent one."""


class BankIngestConflict(ManifestModel):
    """A conflict spanning more than one row -- currently only the
    "same identity, contradictory content" duplicate case (see
    :func:`finrecon.adapters.bank.csv_parser._dedupe`). Every row it names
    is excluded from the canonical output; there is no separate
    blocking/non-blocking distinction here because this is the only
    multi-row conflict kind the adapter can produce.
    """

    kind: str
    detail: str
    row_indices: tuple[int, ...]


class BankIngestManifest(ManifestModel):
    source_id: str
    rows: tuple[BankRowProvenance, ...]
    duplicate_rows_dropped: tuple[str, ...]
    """Row fingerprints of exact duplicate rows collapsed before output."""
    conflicts: tuple[BankIngestConflict, ...]


__all__ = [
    "BankIngestConflict",
    "BankIngestManifest",
    "BankRowProvenance",
]
