# Benchmark generator changelog

Per DESIGN.md §5.1 step 6 ("generator stops changing; changes go in
CHANGELOG.md with rationale"): once a frozen-eval hash is committed here,
the generator that produced it does not change silently. Any future edit
to the generator, the taxonomy, or the tier recipes gets a new entry below
with a stated rationale, and — if it changes frozen-eval's content — a new
frozen-eval hash recorded in the manifest for that version and here.

**Current frozen version: v3.0.0** (`manifests/v3.json`). Superseded versions
keep their own manifest file, unchanged, so a correction is auditable rather
than a silent rewrite.

**Current pilot: v4.0.0-pilot** (`manifests/v4-pilot.json`), which is **not**
frozen and is not part of the v1→v2→v3 lineage. It supersedes nothing, changes
nothing, and its fingerprint is a reproducibility marker rather than a freeze.
Entry below.

## v4.0.0-pilot — 2026-08-26 — compositional-evidence pilot (NOT FROZEN)

**Status: PILOT.** No match rate, precision, coverage or value-at-risk figure
from the `v4-pilot` split may be presented as a benchmark result. A freeze
decision, if taken, gets its own generator version, its own seed, its own
manifest and its own entry here.

**Nothing about v1, v2 or v3 changed.** This is a new split
(`datasets/v4-pilot/`, `ground_truth/v4-pilot.jsonl`), a new manifest
(`manifests/v4-pilot.json`) and a new generator package
(`finrecon.benchmark.generator_v4`). The v3 generator, its seeds, its target
tier counts, its `MANIFEST_FILENAME` and its `GroundTruthCase` model were not
touched — the last of those matters, because that model's `model_dump` feeds
the v3 fingerprint, so v4 states its own ground-truth schema rather than
extending v3's. FROZEN-EVAL's SHA-256 is unchanged:
`f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b`.

### Why a v4 pilot exists

`notes/STAGE3-FINDINGS.md` §1 recorded that exhaustive substring enumeration,
with no model in the loop, identifies the correct settlement in 200 of 200 DEV
T2 cases. v3 T2's degraded reference is causally necessary — v2 established
that — but necessary did not imply hard to recover, so v3 T2 does not
establish that a model contributes anything a substring loop does not.

v3 keeps three uses, stated in `benchmark/V4-PILOT.md` §1: safety regression,
deterministic validator/policy regression, and tool-contract regression. It
loses one: it is not evidence of unique AI reasoning value.

### What the pilot is

64 cases, 778 records, seed 4242, nine archetypes, three to five candidates
each. Resolvable cases require *composing* evidence: a reference head in one
narration field and its tail in another, a reference reach set intersected with
a break-up line amount, a reference reach set intersected with a settlement
date. 16 of the 64 are intentionally unresolvable, split between "more than one
candidate is consistent with everything" and "no candidate is".

Full design, family taxonomy, baseline results, leakage audit and known
limitations: `benchmark/V4-PILOT.md`. Findings:
`notes/BENCHMARK-V4-FINDINGS.md`.

### The two results that matter

**The shipped decision layer cannot express a conjunction.** Its resolution
predicate is one discriminating fragment plus financial exactness, and
financial exactness is uniform across a case because Stage 2's candidate
generator has already filtered on it. So every compositional case escalates
today, with `no_reference_link` as the blocker, and the pilot's match rate
under the shipped architecture is 8/48. **No validator, policy, tool or prompt
change was made to alter that** — the gap is the measurement.

**The pilot is nonetheless fully solvable deterministically.** A baseline
composing the same declared feature vocabulary the generator uses to define
its cases resolves 48 of 48, correctly, with zero at risk. That is close to a
tautology and is reported as one. It is why the recommendation in
`notes/BENCHMARK-V4-FINDINGS.md` §5 is *not* to freeze a full v4 yet.

### Pilot fingerprint

```
38e7e67eb79f51f946f2a0042f5ee2f0edd9497dea24d83f067bd0082bee1e1c
```

Computed by the same algorithm as `frozen_eval_sha256` — same file ordering,
same git-tree-hash construction — so a later freeze needs no new scheme. The
field is named `pilot_sha256` and the manifest says `frozen: false`, because a
pilot whose manifest looked frozen would invite exactly the mistake the
protocol exists to prevent.

## v3.0.0 — 2026-08-23 — T0 usable-direct-key correction

**Status: FROZEN.** This is the generator version that produced the
currently committed DEV and FROZEN-EVAL datasets and `manifests/v3.json`.

**Created before any Stage-3 LLM, agent, prompt or extraction code
existed.** No such code exists in the repository at this version. The
correction was driven entirely by a discrepancy between the two splits'
*deterministic* behaviour, described below, and no model outcome was
consulted, because none was available to consult.

### Why v2 was superseded

FROZEN-EVAL and DEV disagreed about which rule resolved T0, and only one of
them was right:

| Split | T0 resolved by `direct_key` | T0 resolved by `derived` |
|---|---:|---:|
| DEV (v2) | 350 / 350 | 0 |
| **FROZEN-EVAL (v2)** | **175 / 350** | **175** |

DESIGN.md §5.2 defines T0 as the tier where "a usable direct join key
survives", and §5.2's own gradient puts it above T2 precisely because an ID
join settles it. T0 exists to measure `pandas.merge`. On FROZEN-EVAL, 175
of its 350 cases were not resolvable by an ID join at all.

**Root cause.** Record identifiers embedded the split name verbatim, so a
FROZEN-EVAL settlement ID read:

```
setl_frozen-eval_000042
```

The reconciliation tokenizer (`finrecon.normalize.tokens`) declares
`[^A-Za-z0-9_]+` as its delimiter class, so `-` splits tokens. A T0
settlement-ID narration therefore tokenized as:

```
narration: RZPY/SETL/setl_frozen-eval_000042 CREDIT
tokens   : RZPY | SETL | setl_frozen | eval_000042 | CREDIT
```

No whole token equals `setl_frozen-eval_000042`, so the direct-key matcher
— which requires whole-token equality — could never reach it. The cases
fell through to derived reconciliation, resolved correctly there, and thus
produced a *correct outcome by the wrong mechanism*.

The generator did not catch this because its T0 admission test used
**substring containment**:

```python
if settlement.settlement_id in bank_record.narration:   # v2: too weak
```

Containment is strictly weaker than the whole-token equality the matcher
applies. `setl_frozen-eval_000042` is trivially a substring of its own
narration, so every one of those 175 cases was certified T0 and silently
mislabelled.

**Why a green test suite missed it.** DEV's slug is `dev`, which carries no
delimiter, so DEV tokenizes cleanly and all 350 DEV T0 cases really were
direct-key resolvable. Every DEV-based coverage assertion passed. The
defect existed *only* in the split used for reporting, which is the split
least often inspected case-by-case. That asymmetry is the actual lesson
here, and it is why v3 adds a cross-split rule-distribution test rather
than only fixing the slug.

**Impact had it shipped.** No wrong auto-resolutions and no change to match
rate — those 175 cases resolved correctly either way. What would have been
wrong is every *mechanism* claim: any per-rule breakdown in the Stage-4
results table would have understated direct-key coverage by 175 on the
reported split, and T0 and T1 would have been measuring the same rule while
the README asserted they measured different ones.

### What changed

Three things, all on the benchmark side.

- **New `token_contract.py`.** The benchmark's own independent statement of
  what makes a reference *usable*: the delimiter class, case folding,
  `is_token_safe()` and `is_usable_direct_key()`. Deliberately a
  reimplementation of the production tokenizer, not an import — an
  assertion that imported the code it checks would prove nothing. `t2_evidence`
  now delegates its token handling here, so T2's "no direct key survives"
  invariant and T0's "a direct key survives" admission test are one
  predicate read in opposite directions.

- **Hardened T0 admission** (`assertions.py`). `_has_clean_settlement_id_key`
  now requires whole-token reachability instead of substring containment.
  `_has_intact_utr_direct_key` gained the same token check on top of its
  existing template-equality requirement — the template match keeps T0
  structurally distinct from T2's noisy-embed narrations, and the token
  check keeps the usability claim honest. A T0 case whose supposed direct
  key cannot survive as one usable token now **fails generation** with
  `TierDisjointnessError` instead of being emitted.

- **Token-safe split slugs** (`config.py`, `record_factory.py`). Split
  *names* keep their hyphen — they remain the on-disk directory name, the
  CLI argument and the manifest keys. Split *slugs* are what go inside
  identifiers, and are an explicit committed mapping:

  ```
  dev          -> dev
  frozen-eval  -> frozeneval
  ```

  The mapping is declared, not computed by stripping punctuation, so adding
  a split is a deliberate decision about its identifier text. `RecordFactory`
  validates its slug in `__post_init__`, making a delimiter-bearing slug
  unrepresentable rather than merely discouraged.

**The matcher was not touched.** Not the tokenizer, normalization, direct
matcher, derived matcher, candidate generation, snapshots, ledger, audit or
pipeline. The benchmark was made to satisfy the matcher's declared
contract; the contract was not relaxed to admit the benchmark. Weakening
the tokenizer to accept hyphens would have been the wrong fix twice over —
it would have silently changed T2's `separator_altered` semantics, since
`-` is exactly the separator that category manipulates.

### What did not change

- Seeds: `DEV_SEED = 42`, `FROZEN_EVAL_SEED = 1337`, unchanged since v1.
- RNG streams: seeding is still keyed on the split *name*, not the slug, so
  every amount, date, UTR, template choice and degradation on FROZEN-EVAL
  is bit-for-bit what v2 produced.
- Case counts: T0 = 350, T1 = 300, T2 = 200, T3 = 40, total 890, both splits.
- Record counts: 4,580 per split — orders 1,190, payments 1,250,
  settlements 1,190, refunds 60, bank records 890. Unchanged from v2.
- **DEV artifacts are byte-identical to v2.** The `dev` slug was already
  token-safe, so regeneration reproduced DEV exactly. Only the six
  FROZEN-EVAL files changed.
- T0/T1/T2/T3 semantics, archetypes, the corruption taxonomy, the UTR
  degradation ladder, the narration library, the tier plan and shuffle,
  serialization, and the hashing algorithm and hashed-file list.

Because seeding and record counts are untouched, the entire v2 -> v3
FROZEN-EVAL diff is *identifier text and the T0 narrations that embed a
settlement ID*. Verified mechanically: normalizing `frozen-eval` and
`frozeneval` to a common placeholder makes all five visible v2 and v3
dataset files compare identical.

### Frozen-eval SHA-256

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

Computed by `finrecon.benchmark.generator.hashing.compute_fingerprint` over
exactly the six files listed as `frozen_eval_hashed_files` in
`manifests/v3.json`. Algorithm unchanged from v1.

- Superseded hash (v2, retained):
  `d130c42c4bb52b6dc6b88e24f89257f4586c72423a22fdc4606440e53545b897`
- Superseded hash (v1, retained):
  `cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243`
- Frozen date: 2026-08-23.

### Resulting rule distribution

Both splits now resolve each tier by the mechanism the tier is defined by:

| Tier | Cases | DEV | FROZEN-EVAL |
|---|---:|---|---|
| T0 | 350 | 350 `direct_key` | 350 `direct_key` |
| T1 | 300 | 300 `derived` | 300 `derived` |
| T2 | 200 | 200 unresolved, 2 candidates | 200 unresolved, 2 candidates |
| T3 | 40 | 40 unresolved | 40 unresolved |

Zero wrong auto-resolutions on either split. Both T0 archetypes
(`utr_intact_direct_key`, `settlement_id_clean_direct_key`) resolve via
`direct_key` on both splits, 175 each.

Per DESIGN.md §5.1 the generator now stops changing again — and in
particular does not change in response to Stage-3 performance.

## v2.0.0 — 2026-08-23 — T2 construct correction

**Status: SUPERSEDED by v3.0.0 (2026-08-23).** See the v3.0.0 entry above
for why. Its manifest (`manifests/v2.json`), seeds, counts and frozen-eval
SHA-256 are preserved verbatim and are not to be rewritten. v2's T2
construct is carried forward into v3 unchanged; v2 was retired for an
unrelated T0 identifier defect, not because anything below was wrong.

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
