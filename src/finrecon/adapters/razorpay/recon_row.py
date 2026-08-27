"""Source contract: one row of the Razorpay settlement recon feed.

This is a **source** model, not a canonical one. It mirrors
``GET /v1/settlements/recon/combined`` (and the equivalent dashboard
export) as documented in ``notes/RAZORPAY-INPUT-GAP.md`` §2.4, and is kept
strictly separate from :mod:`finrecon.models` — the recon feed is
transaction-shaped, the canonical ``Settlement`` is aggregate-shaped, and
conflating the two is exactly the mistake this adapter exists to avoid.

Field set is the one the task brief requires at minimum: ``entity_id``,
``type``, ``debit``, ``credit``, ``amount``, ``currency``, ``fee``, ``tax``,
``on_hold``, ``settled``, ``created_at``, ``settled_at``, ``settlement_id``,
``settlement_utr``, ``description``, ``payment_id``, ``order_id``,
``dispute_id``. A handful of other documented-but-not-projected fields
(``order_receipt``, ``method``, ``card_network``, ``card_issuer``,
``card_type``, ``notes``) are accepted and type-checked so a real payload
does not fail to parse, but deliberately never read by the transform —
see :data:`DOCUMENTED_NOT_PROJECTED`. Anything else present on a row is
neither rejected nor silently dropped: it is recorded as an
``unrecognized_fields`` entry in the ingestion manifest.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator


class RazorpayReconType(str, Enum):
    """The recon feed's four transaction types.

    Distinct from :class:`finrecon.models.SettlementLineType`, which has
    six — ``fee`` and ``tax`` are line *types* for us and per-row *columns*
    for Razorpay (RAZORPAY-INPUT-GAP.md §2.4).
    """

    PAYMENT = "payment"
    REFUND = "refund"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"


DOCUMENTED_NOT_PROJECTED: frozenset[str] = frozenset(
    {
        "amount",
        "payment_id",
        "description",
        "order_receipt",
        "method",
        "card_network",
        "card_issuer",
        "card_type",
        "notes",
    }
)
"""Fields the row model accepts and type-checks but the transform never
copies into a canonical field's value. Recorded per row as
``dropped_fields`` in the ingestion manifest — a written decision, not a
silent one (RAZORPAY-INPUT-GAP.md §4.2).

``amount`` is read (never written into a canonical value) as a
cross-check against the reconstructed principal — see
``recon._row_principal`` — so its presence here means "not copied", not
"ignored".

``payment_id`` is here because it does not mean "this row's identity" on
the recon feed: per the documented contract, ``payment_id`` is ``null``
on a ``payment`` row (the row's own ``entity_id`` *is* the payment id) and
carries the *linked* payment on a ``refund``/``transfer`` row instead. The
canonical breakup line's ``reference_id`` is derived from ``entity_id``,
never ``payment_id`` — see ``recon._reference_id_for``. ``payment_id`` is
still preserved in the ingestion manifest for audit, just never mistaken
for the line's own identity."""

REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "entity_id",
        "type",
        "debit",
        "credit",
        "amount",
        "currency",
        "fee",
        "tax",
        "on_hold",
        "settled",
        "created_at",
        "settled_at",
        "settlement_id",
        "settlement_utr",
        "payment_id",
        "order_id",
        "dispute_id",
    }
)


class RazorpayReconRow(BaseModel):
    """One row of the settlement recon feed. Strictly parsed, immutable.

    Monetary fields are the API's own integer subunits and are typed
    ``int`` directly — never ``float`` — matching
    :class:`finrecon.models.money.Paise`'s own no-float rule. This model
    itself is not a ``Paise`` consumer (it is a source-boundary shape); the
    conversion into ``Paise`` happens in :mod:`finrecon.adapters.razorpay.recon`.
    """

    model_config = ConfigDict(strict=True, extra="allow", frozen=True)

    entity_id: str
    type: RazorpayReconType
    debit: int
    credit: int
    amount: int
    currency: str
    fee: int
    tax: int
    on_hold: bool
    settled: bool
    created_at: int
    """Unix epoch seconds. Absolute — never a naive/local timestamp."""
    settled_at: int | None
    settlement_id: str
    settlement_utr: str | None
    description: str | None = None
    payment_id: str | None
    order_id: str | None
    dispute_id: str | None

    @model_validator(mode="after")
    def _reject_float_subunits(self) -> "RazorpayReconRow":
        for field in ("debit", "credit", "amount", "fee", "tax"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"{field} must be an int count of paise subunits, got "
                    f"{type(value).__name__}: {value!r}"
                )
        return self

    def unrecognized_fields(self) -> tuple[str, ...]:
        """Fields on the row outside the documented contract entirely.

        ``model_extra`` holds whatever ``extra=\"allow\"`` captured beyond
        the declared fields above.
        """
        extra = self.model_extra or {}
        return tuple(sorted(extra.keys()))

    def dropped_fields(self) -> tuple[str, ...]:
        """Documented fields present on this row but never projected."""
        return tuple(
            sorted(
                name
                for name in DOCUMENTED_NOT_PROJECTED
                if getattr(self, name, None) is not None
            )
        )

    def canonical_json(self) -> str:
        """Deterministic JSON of the full row (declared + extra fields).

        Sorted keys, no whitespace variance — used only to compute
        :meth:`fingerprint`; never written to a canonical file.
        """
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def fingerprint(self) -> str:
        """SHA-256 of :meth:`canonical_json`, the row's stable identity for dedup."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


__all__ = [
    "DOCUMENTED_NOT_PROJECTED",
    "REQUIRED_FIELDS",
    "RazorpayReconRow",
    "RazorpayReconType",
]
