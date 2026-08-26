"""Multi-field bank narrations for the v4 pilot, and their provenance.

Why this is not an extension of the Stage-0 library
---------------------------------------------------

:mod:`finrecon.benchmark.generator.narration_library` is a *frozen* Stage-0
deliverable whose entries all carry exactly one ``{ref}`` slot -- they model a
statement line built around a single reference field. A v4 conjunction case
needs a line with several fields, which is a different template shape, and
adding one to the frozen library would edit an artifact the freeze protocol
says stops changing (DESIGN.md 5.1 step 2/6).

So v4 states its own templates here, under the same labelling discipline the
frozen library enforces: every entry is
``SOURCE_INFORMED_SYNTHETIC``, none claims to be a verbatim capture of any
bank's statement, and each records what documented convention it follows.
The frozen library is still *used* -- v4's referenceless narrations are its
frozen strings verbatim, imported rather than retyped.

Two character-class rules, both load-bearing
--------------------------------------------

**No mask characters.** ``*`` and ``#`` are what
:data:`finrecon.evidence.reference.MASK_CHARACTERS` treats as "one hidden
character", so a narration containing either could make an unintended
fragment ``mask_consistent`` with a reference. v4 narrations contain neither,
which removes that relation from the analysis entirely. (Note that
``design_doc_example_upi`` in the frozen library does contain a ``*`` -- one
more reason v4 does not reuse those templates directly.)

**A reference field is always bounded by a non-alphanumeric character.** A
head fragment can then never be extended into a longer prefix, and a tail
fragment never into a longer suffix, because any longer substring picks up a
delimiter and no declared relation survives that. This is what makes the
clue lengths in a v4 case mean what the ground truth says they mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from random import Random

from finrecon.benchmark.generator.narration_library import NarrationProvenance
from finrecon.benchmark.generator.templates import REFERENCELESS_NARRATIONS

__all__ = [
    "REFERENCELESS_NARRATIONS",
    "V4NarrationTemplate",
    "V4_TEMPLATES",
    "batch_token",
    "date_token",
    "money_token",
    "render",
    "template_ids_for",
]


@dataclass(frozen=True)
class V4NarrationTemplate:
    """One multi-field v4 narration shape."""

    id: str
    template: str
    slots: tuple[str, ...]
    provenance: NarrationProvenance
    source_note: str

    def __post_init__(self) -> None:
        if self.provenance is not NarrationProvenance.SOURCE_INFORMED_SYNTHETIC:
            raise ValueError(
                f"v4 narration template {self.id!r} claims provenance "
                f"{self.provenance!r}; every v4 template is synthetic by construction"
            )
        for character in "*#":
            if character in self.template:
                raise ValueError(
                    f"v4 narration template {self.id!r} contains the mask character "
                    f"{character!r}, which would let an unintended fragment stand in a "
                    "mask_consistent relation to a reference"
                )
        for slot in self.slots:
            if "{" + slot + "}" not in self.template:
                raise ValueError(f"template {self.id!r} declares slot {slot!r} but has no such field")


_SPLIT = ("head", "filler", "tail")
_TRIPLE = ("reordered", "filler", "head", "tail")
_PREFIX = ("prefix", "decoys")
_AMOUNT = ("head", "filler", "money")
_DATED = ("head", "filler", "vdate")


V4_TEMPLATES: tuple[V4NarrationTemplate, ...] = (
    # --- reference split across two fields (head + tail) ------------------
    V4NarrationTemplate(
        id="v4_neft_split_ref",
        template="NEFT CR-RZRPAY-{head}/{filler}/{tail}-MUM",
        slots=_SPLIT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "NEFT credit narration where a fixed-width export cut the reference "
            "across the remittance-information field and the remark field. Follows "
            "the frozen library's 'design_doc_example_neft' field order (rail, "
            "direction, remitter tag, reference, branch); the split itself is the "
            "'field_truncation' + 'separator_swap' corruption categories from the "
            "frozen Stage-0 taxonomy."
        ),
    ),
    V4NarrationTemplate(
        id="v4_rtgs_split_ref",
        template="RTGS CR REF {head} {filler} REF2 {tail} RAZORPAY SOFTWARE",
        slots=_SPLIT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "RTGS credit carrying two reference fields, as statements do when the "
            "originating and beneficiary legs each supply one. Field vocabulary "
            "follows the frozen 'generic_rtgs_ref' template."
        ),
    ),
    V4NarrationTemplate(
        id="v4_upi_split_ref",
        template="UPI/{head}/{filler}/{tail}/RAZORPAY SETTLEMENT",
        slots=_SPLIT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "Slash-delimited UPI narration per the frozen 'generic_upi_slash_ref' "
            "convention, with the reference occupying two of the delimited fields."
        ),
    ),
    V4NarrationTemplate(
        id="v4_imps_split_ref",
        template="IMPS/P2A/{head}-{filler}-{tail}/RAZORPAY",
        slots=_SPLIT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "IMPS person-to-account narration per the frozen 'generic_imps_p2a' "
            "convention; the reference is split across the remark sub-fields."
        ),
    ),
    # --- reference rendered twice, once reordered -------------------------
    V4NarrationTemplate(
        id="v4_neft_reordered_and_split",
        template="NEFT CR-RZRPAY-{reordered}-{filler}-{head}/{tail}-MUM",
        slots=_TRIPLE,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "The bank's own reference field and the remitter's free-text remark "
            "both carry the reference, degraded differently: the first with its "
            "digit groups reordered ('token_reordering'), the second cut across "
            "fields ('field_truncation'). The most synthetic template in this "
            "module, and labelled as such in benchmark/V4-PILOT.md."
        ),
    ),
    V4NarrationTemplate(
        id="v4_rtgs_reordered_and_split",
        template="RTGS CR REF {reordered} {filler} ORIG {head} SEQ {tail} RAZORPAY",
        slots=_TRIPLE,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "As 'v4_neft_reordered_and_split', in RTGS field vocabulary."
        ),
    ),
    # --- one long, right-truncated reference plus decoy text --------------
    V4NarrationTemplate(
        id="v4_neft_long_prefix",
        template="NEFT CR-RZRPAY-{prefix}-{decoys}-MUM",
        slots=_PREFIX,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "A reference cut at a fixed column width ('field_truncation') with "
            "channel boilerplate and stale remark tokens around it "
            "('extraneous_prefix_suffix')."
        ),
    ),
    V4NarrationTemplate(
        id="v4_upi_long_prefix",
        template="UPI/{prefix}/{decoys}/RAZORPAY SETTLEMENT",
        slots=_PREFIX,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="As 'v4_neft_long_prefix', in the frozen UPI field convention.",
    ),
    # --- reference head plus a money field --------------------------------
    V4NarrationTemplate(
        id="v4_neft_head_and_refund",
        template="NEFT CR-RZRPAY-{head}-{filler}-RFND {money}-MUM",
        slots=_AMOUNT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "A credit narration naming the refund netted off inside the "
            "settlement. Rupee-and-paise money fields in remittance information "
            "are ordinary; the value is stated in rupees to two places."
        ),
    ),
    V4NarrationTemplate(
        id="v4_rtgs_head_and_refund",
        template="RTGS CR REF {head} {filler} RFND {money} RAZORPAY SOFTWARE",
        slots=_AMOUNT,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="As 'v4_neft_head_and_refund', in RTGS field vocabulary.",
    ),
    # --- reference head plus a value-date field ---------------------------
    V4NarrationTemplate(
        id="v4_neft_head_and_valuedate",
        template="NEFT CR-RZRPAY-{head}-{filler}-VALDT {vdate}-MUM",
        slots=_DATED,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note=(
            "A credit narration restating the settlement's own value date, which "
            "bank exports commonly do when the posting date and the value date "
            "differ. Rendered DDMONYY, the conventional Indian statement form."
        ),
    ),
    V4NarrationTemplate(
        id="v4_upi_head_and_valuedate",
        template="UPI/{head}/{filler}/VALDT {vdate}/RAZORPAY SETTLEMENT",
        slots=_DATED,
        provenance=NarrationProvenance.SOURCE_INFORMED_SYNTHETIC,
        source_note="As 'v4_neft_head_and_valuedate', in the frozen UPI convention.",
    ),
)

_BY_ID: dict[str, V4NarrationTemplate] = {t.id: t for t in V4_TEMPLATES}

_SLOTS_TO_IDS: dict[tuple[str, ...], tuple[str, ...]] = {}
for _template in V4_TEMPLATES:
    _SLOTS_TO_IDS.setdefault(_template.slots, ())
    _SLOTS_TO_IDS[_template.slots] += (_template.id,)


def template_ids_for(slots: tuple[str, ...]) -> tuple[str, ...]:
    """Every template with exactly this slot signature, in declared order."""
    try:
        return _SLOTS_TO_IDS[slots]
    except KeyError as exc:
        raise KeyError(f"no v4 narration template has the slot signature {slots}") from exc


def render(template_id: str, **fields: str) -> str:
    """Fill one template. Every declared slot must be supplied."""
    template = _BY_ID[template_id]
    missing = [slot for slot in template.slots if slot not in fields]
    if missing:
        raise KeyError(f"template {template_id!r} is missing field(s) {missing}")
    return template.template.format(**fields)


# --- non-reference field values -------------------------------------------

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def date_token(value: date) -> str:
    """A settlement value date as ``DDMONYY``.

    Contains letters in its middle, so no four-character window of it is a
    plain digit run -- which keeps it out of every ``suffix_of_reference``
    relation against a reference whose tail is numeric.
    """
    return f"{value.day:02d}{_MONTHS[value.month - 1]}{value.year % 100:02d}"


def money_token(paise: int) -> str:
    """An amount in rupees to two places, e.g. ``47.38`` for 4,738 paise."""
    if paise <= 0:
        raise ValueError(f"a narration money field states a positive amount; got {paise}")
    return f"{paise // 100}.{paise % 100:02d}"


def batch_token(rng: Random) -> str:
    """A settlement batch marker, e.g. ``BATCH47``.

    Digits are drawn without zeros for the same reason references are: a
    numeric fragment that cannot contain a zero cannot be the suffix of a
    zero-padded record identifier.
    """
    return f"BATCH{rng.choice('123456789')}{rng.choice('123456789')}"
