"""Narration format library — Stage 0 deliverable (DESIGN.md §5.1 step 2, §9).

DESIGN.md requires this library to be "sourced from real-world formats and
frozen before the agent exists" (§5.1) and, per the methodological-honesty
note in §5.1/§10, forbids presenting fabricated strings as literal captures
of a specific bank's live statement format.

**Sourcing status, stated plainly:** a full verbatim bank *statement line*
(the whole free-text narration, as it would appear on a real account
statement) was not found published anywhere with defensible provenance —
banks do not publish literal statement excerpts, and third-party blog
posts describing the narration format present it as a bracketed template
(e.g. "NEFT CR: [bank name] [UTR] RAZORPAY SETTLEMENT"), not a literal
example, so it is correctly excluded from ``VERBATIM_PUBLIC`` here.

What *is* published, literally, by an authoritative primary source is the
**UTR reference token** itself: Razorpay's own official API and webhook
documentation embed literal example ``utr`` values in their sample JSON
payloads. Two such values are captured below as ``VERBATIM_PUBLIC``, each
with its exact source URL. They are reference-token examples, not full
narration lines — the library does not overclaim a verbatim narration
*sentence* just because a verbatim reference *token* exists.

Every other entry is labelled ``SOURCE_INFORMED_SYNTHETIC``: constructed
from the publicly documented *structural* conventions of Indian payment
rails (NEFT/RTGS/IMPS/UPI narration commonly concatenates a
transaction-type code, a counterparty or gateway marker, and a
reference/UTR-shaped token, per RBI/NPCI circulars on remittance
information fields), plus the four illustrative strings already written
into ``DESIGN.md`` §1. None of those claim to be verbatim, bank-specific
captures. See the module-level ``NarrationProvenance`` enum for the full
labelling contract, which is enforced structurally (``VERBATIM_PUBLIC``
requires a ``citation``) rather than just by convention.

This module defines the template library only. It does not generate
benchmark narration instances against real records — that is Stage 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NarrationProvenance(str, Enum):
    VERBATIM_PUBLIC = "verbatim_public"
    """A verbatim, citable capture of a real bank/gateway narration string."""

    SOURCE_INFORMED_SYNTHETIC = "source_informed_synthetic"
    """Constructed from documented structural conventions; not a verbatim capture."""

    GENERATED_CORRUPTION = "generated_corruption"
    """Derived by deliberately applying a named corruption to another template."""


@dataclass(frozen=True)
class NarrationTemplate:
    id: str
    template: str
    """A narration string, possibly containing a ``{ref}`` placeholder for the reference token."""
    provenance: NarrationProvenance
    source_note: str
    """Required for every entry: what grounds this template, or an honest admission it's synthetic."""
    corruption_category_ids: tuple[str, ...] = ()
    """IDs from :mod:`finrecon.benchmark.generator.corruptions`, if this template exhibits any."""
    citation: str | None = None
    """Required (non-None) when provenance is VERBATIM_PUBLIC; otherwise unused."""

    def __post_init__(self) -> None:
        if not self.source_note.strip():
            raise ValueError(f"narration template {self.id!r} is missing a source_note")
        if self.provenance is NarrationProvenance.VERBATIM_PUBLIC and not self.citation:
            raise ValueError(
                f"narration template {self.id!r} claims VERBATIM_PUBLIC provenance "
                "but has no citation"
            )
        if self.provenance is not NarrationProvenance.VERBATIM_PUBLIC and self.citation:
            raise ValueError(
                f"narration template {self.id!r} is {self.provenance!r} but carries a "
                "citation, which implies verbatim provenance it does not have"
            )


NARRATION_LIBRARY: tuple[NarrationTemplate, ...] = (
    # Verbatim, citable UTR reference-token examples published in
    # Razorpay's own official API/webhook documentation. These are the
    # only two entries in this library with defensible VERBATIM_PUBLIC
    # provenance: the literal token was retrieved directly from an
    # authoritative primary source, not paraphrased or reconstructed.
    # They are reference tokens, not full bank narration lines — no
    # surrounding narration text is claimed as verbatim.
    NarrationTemplate(
        id="razorpay_docs_settlement_entity_utr_example",
        template="1597813219e1pq6w",
        provenance=NarrationProvenance.VERBATIM_PUBLIC,
        source_note=(
            "Literal `utr` example value from the sample Settlement entity JSON "
            "in Razorpay's official API documentation "
            "(`{\"id\":\"setl_7IZKKI4Pnt2kEe\", ... \"utr\":\"1597813219e1pq6w\", ...}`). "
            "Retrieved 2026-08-22."
        ),
        citation="https://razorpay.com/docs/api/settlements/entity/",
    ),
    NarrationTemplate(
        id="razorpay_docs_settlement_webhook_utr_example",
        template="AXISCN1153863727",
        provenance=NarrationProvenance.VERBATIM_PUBLIC,
        source_note=(
            "Literal `utr` example value from the sample `settlement.processed` "
            "webhook payload in Razorpay's official webhook documentation "
            "(`\"utr\": \"AXISCN1153863727\"`). Retrieved 2026-08-22."
        ),
        citation="https://razorpay.com/docs/webhooks/settlements/",
    ),
    # The four illustrative strings already frozen into DESIGN.md §1. They
    # are the design author's own illustration of "every bank mangles
    # differently", not a captured statement line, so they are labelled
    # synthetic and cited to the design doc rather than to a bank.
    NarrationTemplate(
        id="design_doc_example_upi",
        template="RZPY*ORD293 UPI/{ref}",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="DESIGN.md §1 illustrative example; structure follows documented UPI narration convention (channel marker + gateway order tag + UPI reference).",
    ),
    NarrationTemplate(
        id="design_doc_example_neft",
        template="NEFT CR-RZRPAY-SET{ref}-MUM",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="DESIGN.md §1 illustrative example; structure follows documented NEFT credit narration convention (rail + direction + remitter tag + settlement id + branch code).",
    ),
    NarrationTemplate(
        id="design_doc_example_settlement_ref",
        template="RZPY/SETL/{ref} REF:PAY88/REV",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="DESIGN.md §1 illustrative example; not tied to a specific bank's field layout.",
    ),
    NarrationTemplate(
        id="design_doc_example_reversal",
        template="CR NEFT-RZPY-STLMNT/{ref}/REV-8271",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="DESIGN.md §1 illustrative example; not tied to a specific bank's field layout.",
    ),
    # Additional structurally-motivated templates, each honestly synthetic.
    NarrationTemplate(
        id="generic_neft_prefix_ref",
        template="NEFT-CR-{ref}",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="Minimal NEFT credit form (rail + direction + reference) documented as a common baseline across bank statement exports; not a specific bank's exact format.",
    ),
    NarrationTemplate(
        id="generic_imps_p2a",
        template="IMPS/P2A/{ref}/RAZORPAY",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="IMPS person-to-account narration commonly encodes channel/transfer-type/reference/remark fields per NPCI IMPS specification; exact field text is synthetic.",
    ),
    NarrationTemplate(
        id="generic_upi_slash_ref",
        template="UPI/{ref}/RAZORPAY SETTLEMENT",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="UPI narration commonly slash-delimits channel, reference, and remark per NPCI UPI narration guidance; exact remark text is synthetic.",
    ),
    NarrationTemplate(
        id="generic_rtgs_ref",
        template="RTGS CR REF {ref} RAZORPAY SOFTWARE",
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="RTGS credit narration commonly carries rail, direction, reference, and remitter name; exact spacing/wording is synthetic.",
    ),
    # A deliberately corrupted derivative, to exercise the
    # GENERATED_CORRUPTION label end to end. Built by applying the
    # `field_truncation` corruption category to `generic_neft_prefix_ref`.
    NarrationTemplate(
        id="generic_neft_prefix_ref_truncated",
        template="NEFT-CR-{ref",
        provenance=NarrationProvenance.GENERATED_CORRUPTION,
        source_note="Derived from 'generic_neft_prefix_ref' by applying the 'field_truncation' corruption category (fixed column-width cut).",
        corruption_category_ids=("field_truncation",),
    ),
)

_LIBRARY_BY_ID: dict[str, NarrationTemplate] = {t.id: t for t in NARRATION_LIBRARY}


def get_narration_template(template_id: str) -> NarrationTemplate:
    """Look up a library entry by its frozen ID."""
    try:
        return _LIBRARY_BY_ID[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown narration template id: {template_id!r}") from exc
