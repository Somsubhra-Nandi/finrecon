"""UTR degradation ladder — Stage 0 deliverable (DESIGN.md §5.1 step 1, §5.2).

DESIGN.md §5.2 grades benchmark difficulty by how much of the canonical
reference (the UTR, or a clean settlement-ID join key) survives:

    direct key survives          -> T0
    no key, structure survives   -> T1
    key survives only degraded   -> T2
    nothing distinguishing       -> T3

This module freezes the *vocabulary* of that gradient — named categories
plus one deterministic transform per category — so Stage 1's generator can
consume it without redefining the taxonomy. It does not generate benchmark
cases, does not touch any canonical record, and does not decide which
category a given case belongs to; that is Stage 1 work.

Determinism: every transform takes an explicit ``seed`` and uses a
``random.Random(seed)`` instance local to the call. None of them read or
mutate the global ``random`` module state, so the same ``(utr, seed)`` pair
always produces the same output.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random


@dataclass(frozen=True)
class DegradationCategory:
    id: str
    name: str
    description: str
    tier_hint: str
    """Which difficulty tier (DESIGN.md §5.2) this category typifies.

    Informational only — Stage 0 does not assign real cases to tiers.
    """


DEGRADATION_LADDER: tuple[DegradationCategory, ...] = (
    DegradationCategory(
        id="intact",
        name="Intact reference",
        description="The UTR survives unmodified and is directly usable as a join key.",
        tier_hint="T0",
    ),
    DegradationCategory(
        id="truncated_left",
        name="Left truncation",
        description="Leading characters of the UTR are dropped; a trailing suffix survives.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="truncated_right",
        name="Right truncation",
        description="Trailing characters of the UTR are dropped; a leading prefix survives.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="masked",
        name="Masking",
        description="Interior characters of the UTR are replaced with a mask character, e.g. asterisks.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="separator_altered",
        name="Separator insertion/removal",
        description="Separator characters are inserted into or removed from within the UTR.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="reordered",
        name="Token reordering",
        description="The UTR is split into sub-tokens which are then permuted.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="embedded_in_narration",
        name="Embedded in noisy narration",
        description="The (possibly further-degraded) UTR is embedded inside a longer noisy narration string rather than appearing as a clean field.",
        tier_hint="T2",
    ),
    DegradationCategory(
        id="omitted",
        name="Reference omission",
        description="No form of the UTR is present; the record carries no direct-key evidence at all.",
        tier_hint="T1",
    ),
    DegradationCategory(
        id="ambiguous",
        name="No distinguishing reference",
        description=(
            "No reference is present AND other structural evidence (amount, timing) fails to "
            "distinguish this record from at least one other plausible counterparty. This is a "
            "property of a case's full candidate set, not of a single UTR string, so it has no "
            "string transform below — Stage 1's candidate generator is what can observe it."
        ),
        tier_hint="T3",
    ),
)

_LADDER_BY_ID: dict[str, DegradationCategory] = {c.id: c for c in DEGRADATION_LADDER}


def get_degradation_category(category_id: str) -> DegradationCategory:
    """Look up a ladder entry by its frozen ID."""
    try:
        return _LADDER_BY_ID[category_id]
    except KeyError as exc:
        raise KeyError(f"unknown degradation category id: {category_id!r}") from exc


@dataclass(frozen=True)
class DegradationResult:
    category_id: str
    value: str | None
    """The degraded reference string, or ``None`` when the category removes it entirely."""


def degrade_intact(utr: str, seed: int) -> DegradationResult:
    del seed
    return DegradationResult(category_id="intact", value=utr)


def degrade_truncate_left(utr: str, seed: int, min_keep: int = 4) -> DegradationResult:
    rng = Random(seed)
    keep = min(len(utr), max(min_keep, rng.randint(min_keep, max(min_keep, len(utr) - 1))))
    return DegradationResult(category_id="truncated_left", value=utr[-keep:] if keep else "")


def degrade_truncate_right(utr: str, seed: int, min_keep: int = 4) -> DegradationResult:
    rng = Random(seed)
    keep = min(len(utr), max(min_keep, rng.randint(min_keep, max(min_keep, len(utr) - 1))))
    return DegradationResult(category_id="truncated_right", value=utr[:keep] if keep else "")


def degrade_mask(utr: str, seed: int, mask_char: str = "*") -> DegradationResult:
    rng = Random(seed)
    if len(utr) <= 2:
        return DegradationResult(category_id="masked", value=mask_char * len(utr))
    visible_head = 2
    visible_tail = 2
    mid_len = len(utr) - visible_head - visible_tail
    if mid_len <= 0:
        return DegradationResult(category_id="masked", value=mask_char * len(utr))
    # rng consulted for determinism/parity with the other operators even
    # though the mask window here is fixed; keeps the call signature and
    # seed-consumption pattern uniform across the ladder.
    rng.random()
    masked = utr[:visible_head] + (mask_char * mid_len) + utr[-visible_tail:]
    return DegradationResult(category_id="masked", value=masked)


def degrade_separator_altered(utr: str, seed: int, separator: str = "-") -> DegradationResult:
    rng = Random(seed)
    if separator in utr:
        return DegradationResult(category_id="separator_altered", value=utr.replace(separator, ""))
    if len(utr) < 2:
        return DegradationResult(category_id="separator_altered", value=utr)
    split_at = rng.randint(1, len(utr) - 1)
    value = utr[:split_at] + separator + utr[split_at:]
    return DegradationResult(category_id="separator_altered", value=value)


def degrade_reordered(utr: str, seed: int, chunk_size: int = 3) -> DegradationResult:
    rng = Random(seed)
    chunks = [utr[i : i + chunk_size] for i in range(0, len(utr), chunk_size)]
    if len(chunks) < 2:
        return DegradationResult(category_id="reordered", value=utr)
    shuffled = chunks[:]
    rng.shuffle(shuffled)
    if shuffled == chunks:
        shuffled.reverse()
    return DegradationResult(category_id="reordered", value="".join(shuffled))


def degrade_embedded_in_narration(
    utr: str, seed: int, narration_template: str = "NEFT CR-RZRPAY-{ref}-MUM"
) -> DegradationResult:
    del seed
    embedded = narration_template.format(ref=utr)
    return DegradationResult(category_id="embedded_in_narration", value=embedded)


def degrade_omitted(utr: str, seed: int) -> DegradationResult:
    del utr, seed
    return DegradationResult(category_id="omitted", value=None)


_OPERATORS = {
    "intact": degrade_intact,
    "truncated_left": degrade_truncate_left,
    "truncated_right": degrade_truncate_right,
    "masked": degrade_mask,
    "separator_altered": degrade_separator_altered,
    "reordered": degrade_reordered,
    "embedded_in_narration": degrade_embedded_in_narration,
    "omitted": degrade_omitted,
}
"""Every category except ``ambiguous`` (see its docstring above)."""


def degrade(utr: str, category_id: str, seed: int) -> DegradationResult:
    """Apply the operator registered for ``category_id`` to ``utr``."""
    try:
        operator = _OPERATORS[category_id]
    except KeyError as exc:
        raise KeyError(
            f"no degradation operator for category id: {category_id!r} "
            "(the 'ambiguous' category is a case-level property with no string transform)"
        ) from exc
    return operator(utr, seed)
