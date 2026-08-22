"""Curated, disjoint narration-template subsets used to keep T0 vs T2 structurally distinct.

Both subsets draw only from the frozen Stage 0
:mod:`finrecon.benchmark.generator.narration_library` — nothing here adds
a new narration template, it only partitions the existing library.

The partition matters for tier disjointness: T0's "usable direct key"
narrations are built from short, single-field templates (the reference is
the whole meaningful content), while T2's "degraded reference" narrations
are built from longer templates with extra boilerplate around the
reference — the "embedded in noisy narration" category is legitimately T2
even when the embedded token itself is character-for-character intact,
because what's degraded is the *retrievability* of the token, not
necessarily its characters. See DESIGN.md §5.2 and
:mod:`finrecon.benchmark.generator.utr_degradation`'s ``tier_hint``s.
"""

from __future__ import annotations

from finrecon.benchmark.generator.narration_library import get_narration_template

T0_CLEAN_TEMPLATE_IDS: tuple[str, ...] = (
    "generic_neft_prefix_ref",
    "generic_imps_p2a",
    "generic_upi_slash_ref",
)

T2_NOISY_EMBED_TEMPLATE_IDS: tuple[str, ...] = (
    "design_doc_example_upi",
    "design_doc_example_neft",
    "design_doc_example_settlement_ref",
    "design_doc_example_reversal",
    "generic_rtgs_ref",
)

T2_DEGRADATION_CATEGORY_IDS: tuple[str, ...] = (
    "truncated_left",
    "truncated_right",
    "masked",
    "separator_altered",
    "reordered",
    "embedded_in_narration",
)

REFERENCELESS_NARRATIONS: tuple[str, ...] = (
    "NEFT CREDIT - SETTLEMENT",
    "RTGS CR - VENDOR PAYOUT",
    "IMPS CREDIT RECEIVED",
    "BANK CREDIT - ONLINE TXN SETTLEMENT",
    "NEFT CR - PAYMENT GATEWAY SETTLEMENT",
)

assert set(T0_CLEAN_TEMPLATE_IDS).isdisjoint(T2_NOISY_EMBED_TEMPLATE_IDS)


def render_t0_clean(ref: str, rng) -> tuple[str, str]:
    """Render ``ref`` into a randomly (seeded) chosen T0-clean template. Returns (narration, template_id)."""
    template_id = rng.choice(T0_CLEAN_TEMPLATE_IDS)
    template = get_narration_template(template_id)
    return template.template.format(ref=ref), template_id


def render_t2_noisy(ref: str, rng) -> tuple[str, str]:
    """Render ``ref`` into a randomly (seeded) chosen T2-noisy template. Returns (narration, template_id)."""
    template_id = rng.choice(T2_NOISY_EMBED_TEMPLATE_IDS)
    template = get_narration_template(template_id)
    return template.template.format(ref=ref), template_id


def is_rendered_from(narration: str, ref: str, template_ids: tuple[str, ...]) -> bool:
    """True if ``narration`` is exactly some ``template_ids`` entry filled with ``ref``."""
    for template_id in template_ids:
        template = get_narration_template(template_id)
        if narration == template.template.format(ref=ref):
            return True
    return False
