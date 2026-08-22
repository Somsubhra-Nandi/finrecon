"""Corruption taxonomy — Stage 0 deliverable (DESIGN.md §9, Stage 0; §5.1 step 1).

This module names and freezes the vocabulary of narration-level corruptions
the benchmark generator will draw on in Stage 1. It defines the taxonomy
only: no benchmark cases, no dataset, no generation of corrupted strings
against real records. See :mod:`finrecon.benchmark.generator.utr_degradation`
for the separate, narrower UTR reference-survival gradient (DESIGN.md §5.2).

Each :class:`CorruptionCategory` is a free-text-narration corruption a bank
statement line can exhibit, independent of whether a UTR is present at all.
IDs are frozen once committed — Stage 1's generator consumes them by ID, so
renaming or removing an entry here is a taxonomy break, not a rename.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorruptionCategory:
    id: str
    name: str
    description: str


CORRUPTION_TAXONOMY: tuple[CorruptionCategory, ...] = (
    CorruptionCategory(
        id="separator_swap",
        name="Separator swap",
        description=(
            "Delimiters between narration fields are substituted "
            "(e.g. '/' becomes '-' or '*', or vice versa)."
        ),
    ),
    CorruptionCategory(
        id="separator_removal",
        name="Separator removal",
        description="Fields that are normally delimited are concatenated with no separator at all.",
    ),
    CorruptionCategory(
        id="whitespace_noise",
        name="Whitespace noise",
        description="Extra, missing, or irregular whitespace is introduced around narration fields.",
    ),
    CorruptionCategory(
        id="case_folding",
        name="Case folding",
        description="Narration case is altered (e.g. forced upper-case, or inconsistent mixed case).",
    ),
    CorruptionCategory(
        id="token_reordering",
        name="Token reordering",
        description="The order of narration fields (bank code, product code, reference, remark) is permuted.",
    ),
    CorruptionCategory(
        id="field_truncation",
        name="Field truncation",
        description="The narration string is cut at a fixed column width, dropping trailing fields or characters.",
    ),
    CorruptionCategory(
        id="extraneous_prefix_suffix",
        name="Extraneous prefix/suffix",
        description="Bank- or channel-specific boilerplate is prepended or appended around the meaningful fields.",
    ),
    CorruptionCategory(
        id="reference_embedding",
        name="Reference embedding",
        description="A reference token is embedded inside an otherwise noisy free-text remark rather than in a fixed field position.",
    ),
    CorruptionCategory(
        id="reference_omission",
        name="Reference omission",
        description="No distinguishing reference token is present in the narration at all.",
    ),
    CorruptionCategory(
        id="channel_code_substitution",
        name="Channel code substitution",
        description="The rail/channel marker (NEFT/RTGS/UPI/IMPS) is altered, abbreviated, or misapplied relative to the actual settlement channel.",
    ),
    CorruptionCategory(
        id="numeric_id_noise",
        name="Numeric ID noise",
        description="Digits within an embedded numeric identifier are altered, dropped, or padded without changing the surrounding structure.",
    ),
)

_TAXONOMY_BY_ID: dict[str, CorruptionCategory] = {c.id: c for c in CORRUPTION_TAXONOMY}


def get_corruption_category(category_id: str) -> CorruptionCategory:
    """Look up a taxonomy entry by its frozen ID."""
    try:
        return _TAXONOMY_BY_ID[category_id]
    except KeyError as exc:
        raise KeyError(f"unknown corruption category id: {category_id!r}") from exc
