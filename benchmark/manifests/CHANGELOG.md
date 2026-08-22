# Benchmark generator changelog

Per DESIGN.md §5.1 step 6 ("generator stops changing; changes go in
CHANGELOG.md with rationale"): once a frozen-eval hash is committed here,
the generator that produced it does not change silently. Any future edit
to the generator, the taxonomy, or the tier recipes gets a new entry below
with a stated rationale, and — if it changes frozen-eval's content — a new
frozen-eval hash recorded in `manifests/v1.json` and here.

## v1.0.0 — 2026-08-22 — Stage 1 freeze

**Status: FROZEN.** This is the generator version used to produce the
committed DEV and FROZEN-EVAL datasets.

- Taxonomy consumed as-is from Stage 0 (`src/finrecon/benchmark/generator/`):
  `corruptions.py` (11-category corruption taxonomy),
  `narration_library.py` (11 narration templates, provenance-labelled),
  `utr_degradation.py` (9-category UTR degradation ladder, `tier_hint`
  per category).
- Seeds: `DEV_SEED = 42`, `FROZEN_EVAL_SEED = 1337`
  (`src/finrecon/benchmark/generator/config.py`).
- Target case counts (DESIGN.md §5.2 v4 shape, met exactly):
  T0 = 350, T1 = 300, T2 = 200, T3 = 40, total = 890.
- Tier-to-archetype mapping:
  - T0: `utr_intact_direct_key` (175), `settlement_id_clean_direct_key` (175)
  - T1: `fee_gst_arithmetic`, `refund_offset`, `batched_settlement`,
    `duplicate_disambiguation`, `adjustment_and_transfer` (60 each)
  - T2: one archetype per UTR-degradation-ladder T2 category —
    `truncated_left`, `truncated_right`, `masked`, `separator_altered`,
    `reordered`, `embedded_in_narration` (cycled across 200 cases)
  - T3: `ambiguous_same_amount_same_date` (40)
- FROZEN-EVAL SHA-256: `cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243`
  (computed by `finrecon.benchmark.generator.hashing.compute_fingerprint`;
  see that module's docstring for exactly what is hashed).
- Frozen date: 2026-08-22.

**No future generator change may alter the committed FROZEN-EVAL dataset's
content without a new entry here documenting why, and a new hash.** DEV
may keep being regenerated for tuning; FROZEN-EVAL is for final reporting
only, per DESIGN.md §5.1.
