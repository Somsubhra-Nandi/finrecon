# Benchmark generator changelog

Per DESIGN.md §5.1 step 6 ("generator stops changing; changes go in
CHANGELOG.md with rationale"): once a frozen-eval hash is committed here,
the generator that produced it does not change silently. Any future edit
to the generator, the taxonomy, or the tier recipes gets a new entry below
with a stated rationale, and — if it changes frozen-eval's content — a new
frozen-eval hash recorded in the manifest for that version and here.

**Current version: v2.0.0** (`manifests/v2.json`). Superseded versions keep
their own manifest file, unchanged, so a correction is auditable rather
than a silent rewrite.

## v2.0.0 — 2026-08-23 — T2 construct correction

**Status: FROZEN.** This is the generator version that produced the
currently committed DEV and FROZEN-EVAL datasets and `manifests/v2.json`.

### Why v1 was superseded

> During Stage 2, before any LLM/agent implementation, the deterministic
> rules-only baseline showed that all T2 cases were uniquely resolvable
> from structured financial evidence alone. This meant degraded-reference
> recovery was not necessary, so benchmark v1 did not isolate the intended
> T2 capability.

The full observation is recorded in `notes/STAGE2-FINDINGS.md` §1. In
short: v1's `build_t2_degraded_reference` degraded *only* the reference and
left the case's financial structure identical to a T1 case — one order,
one captured payment, one settlement, one credit whose amount equalled that
settlement's net inside the value-date window. A tier-blind deterministic
rule therefore resolved all 200 T2 cases without reading a character of
narration. The degraded reference was decorative, so T2 measured nothing
that T1 did not already measure.

This is a **benchmark-validity** correction, not a result. It was made
before any Stage-3 model, agent or prompt existed, and no matcher rule,
window, bound or tolerance was changed to accommodate it. The Stage-2
deterministic core is byte-for-byte the code that produced the v1 finding.

### What changed

Only the T2 case-generation path and the minimum supporting code.

- **New T2 construct** (`case_builder.build_t2_degraded_reference`). Each
  T2 case now builds **two** structurally indistinguishable settlements
  instead of one:
  - the same gross, therefore the same fee/GST/net to the paise;
  - the same settlement calendar date, at distinct times of day;
  - each with its own order and own captured payment, and its own
    break-up that accounts for its amount exactly;
  - each carrying its own UTR, so the decoy is not identifiable by the
    absence of a reference.

  Both are equally plausible under every declared Stage-2 rule, so
  structured evidence alone leaves two candidates and the deterministic
  core refuses. The bank narration carries a degraded fragment of the
  **true** settlement's UTR only, and the generator verifies that fragment
  is inconsistent with the decoy's UTR. Recovering the reference is now the
  only thing that resolves the case — which is what T2 was always meant
  to mean.

  Which chain is constructed first is randomised per case, so the true
  settlement's position in the sequential ID space carries no signal.

- **T2 degradation categories are unchanged**: `truncated_left`,
  `truncated_right`, `masked`, `separator_altered`, `reordered`,
  `embedded_in_narration`, still cycled across the 200 cases, still drawn
  from the frozen Stage-0 ladder. No corruption was tuned against any
  model; none exists.

- **New generator-level assertions** (`t2_invariants.py`, `t2_evidence.py`).
  Every T2 case is re-derived from its own records and rejected unless all
  seven invariants hold: no usable direct key; ≥2 structurally plausible
  candidates; the true candidate among them; therefore no unique structured
  resolution; the surviving fragment consistent with the true settlement;
  consistent with no other candidate; and still ambiguous with the
  narration deleted. Checked case-locally at build time and again
  batch-wide across the whole split before anything is written. The
  plausibility model is reimplemented from DESIGN.md §4.3 rather than
  imported from `finrecon.matchers`, so the check is independent of the
  code it is checking; the declared constants are imported so the two
  cannot drift on numbers.

- **Ground-truth schema additions** (defaulted, hidden):
  `DegradationInfo.surviving_evidence` records exactly what survived in the
  narration, and `GroundTruthCase.distractor_settlement_ids` names the
  decoys. T0/T1/T3 emit the defaults.

- **Unchanged**: T0, T1 and T3 semantics, archetypes and counts; the
  corruption taxonomy; the UTR degradation ladder; the narration library;
  the tier plan and shuffle; serialization; the hashing algorithm and the
  exact list of hashed files.

### Seeds

`DEV_SEED = 42`, `FROZEN_EVAL_SEED = 1337` — **unchanged from v1**,
deliberately. The construct change already separates the artifacts, and the
generator version distinguishes them; a seed change would add churn without
adding independence. Keeping v1's seeds also makes it plain that no seed
was shopped for a matcher outcome. Neither seed was chosen or revisited on
the basis of any reconciliation result.

### Counts

- Case counts unchanged: T0 = 350, T1 = 300, T2 = 200, T3 = 40, total 890,
  in both splits.
- Record counts rise, as expected, because each T2 case now needs a second
  order, payment and settlement:

  | | v1 | v2 |
  |---|---:|---:|
  | orders | 990 | 1190 |
  | payments | 1050 | 1250 |
  | settlements | 990 | 1190 |
  | refunds | 60 | 60 |
  | bank records | 890 | 890 |
  | **total** | **3980** | **4580** |

### Frozen-eval SHA-256

```
d130c42c4bb52b6dc6b88e24f89257f4586c72423a22fdc4606440e53545b897
```

Computed by `finrecon.benchmark.generator.hashing.compute_fingerprint` over
exactly the six files listed as `frozen_eval_hashed_files` in
`manifests/v2.json` — the five system-visible FROZEN-EVAL dataset files
plus the hidden FROZEN-EVAL ground truth. The algorithm is unchanged from
v1; see that module's docstring.

- Superseded hash (v1, retained):
  `cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243`
- Frozen date: 2026-08-23.

**No FROZEN-EVAL ground-truth outcome was inspected while producing this
version.** The construct was verified against DEV and against the
generator's own invariants; FROZEN-EVAL was generated once from the
committed configuration and hashed. Per DESIGN.md §5.1 the generator now
stops changing again — in particular, it does not change in response to
Stage-3 performance.

## v1.0.0 — 2026-08-22 — Stage 1 freeze

**Status: SUPERSEDED by v2.0.0 (2026-08-23).** See the v2.0.0 entry above
for why. Its manifest (`manifests/v1.json`), seeds, counts and frozen-eval
SHA-256 are preserved verbatim and are not to be rewritten. The v1 DEV and
FROZEN-EVAL dataset bytes themselves are recoverable from git at commit
`7328f98463fe3ead1b6f8caa78ce51dd0ec814e7`, the last commit before the v2
correction.

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
