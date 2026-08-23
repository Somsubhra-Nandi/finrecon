# Stage 2 — findings against the frozen benchmark

Recorded, not acted on. The Stage-1 generator, seeds, taxonomy, case
distribution and datasets were frozen when this was written; nothing below
was "fixed" by changing them.

> **Status update — 2026-08-23.** Finding 1 was subsequently acted on, as
> a *benchmark-validity* correction rather than a Stage-2 change. Benchmark
> **v2.0.0** rebuilds the T2 construct so the degraded reference is
> causally necessary; see `benchmark/manifests/CHANGELOG.md`. Everything
> below describes benchmark **v1**, and is kept verbatim because it is the
> evidence that motivated v2. **No Stage-2 rule, window, bound or tolerance
> was changed** — the deterministic core that produced these numbers is
> the same code that now leaves all 200 v2 T2 cases unresolved.
>
> Findings 2 and 3 still describe v1 and were deliberately left alone; the
> v2 pass was scoped to T2 only.

> **Status update — 2026-08-23 (second).** Finding 4 below was found while
> reviewing v2 and acted on as benchmark **v3.0.0**, again with no Stage-2
> change. v2's T2 construct carries forward untouched.

---

## 1. T2's structural evidence is as strong as T1's, so a tier-blind rule resolves it

**Severity: material to the Stage-3 narrative. Not a bug.**

### What was observed

On DEV, the Stage-2 deterministic core resolves all 200 T2 cases
correctly, using only the derived-reconciliation rule — settlement
amount + declared date window + exact break-up accounting. No narration
was parsed and no reference was recovered.

| Tier | Cases | Resolved correctly | Correctly refused | Wrong |
|---|---:|---:|---:|---:|
| T0 | 350 | 350 | — | 0 |
| T1 | 300 | 300 | — | 0 |
| T2 | 200 | 200 | — | 0 |
| T3 | 40 | — | 40 | 0 |

### Why it happens

`build_t2_degraded_reference` (`case_builder.py`) degrades **only the
reference**. Everything else about a T2 case is byte-for-byte the shape of
`build_t1_fee_gst_arithmetic`: one order, one captured payment, one
settlement with a `payment/fee/tax` break-up, and one bank credit whose
amount equals the settlement's net, dated 0–1 days after it.

DESIGN.md §5.2 grades the tiers on *reference survival* alone:

```
direct key survives          -> T0
no key, structure survives   -> T1
key survives only degraded   -> T2
nothing distinguishing       -> T3
```

T2 is defined as "key survives only degraded". It is not defined as
"structure does *not* survive" — and in the frozen generator, structure
does survive, fully. So a deterministic rule that reasons from structure
reaches T2 without touching the degraded reference at all.

### Why Stage 2 did not avoid it

Three reasons, in order of weight:

1. **A case's tier lives only in hidden ground truth.** Production code
   cannot know a credit is "a T2 case", so it cannot decline to apply a
   rule to one. Any code that could would be consuming the answer.
2. **What production actually sees** for a T2 credit is a narration like
   `NEFT CR-RZRPAY-SETX9F2K1-MUM`. Distinguishing "this contains a
   degraded UTR" from "this is noise" requires parsing the narration —
   which is exactly the Stage-3 work Stage 2 is forbidden to do.
3. Suppressing the rule for cases that *look* degraded would be a
   heuristic built to protect a narrative, at the cost of correctly
   reconciling real money.

So the rule is applied uniformly, and the outcome is reported.

### What this implies for Stage 3

Ablation arm A ("rules only", DESIGN.md §5.5) will score far higher on
this benchmark than the design anticipated, and correspondingly the
C-vs-D delta — described in §5.5 as "the most interesting number in the
project" — has little room to move on T2 as the frozen data stands.

Options, none of which are Stage-2 decisions:

- **Report it as the finding it is.** DESIGN.md §5.5 already commits to
  this posture: "A measured negative result is a stronger submission than
  an unmeasured positive claim." A rules-only baseline that reaches T2 is
  a real, defensible result about deterministic-first architecture.
- **Add a T2 variant in a future generator version** where amount and date
  blocking is genuinely insufficient (e.g. several same-amount settlements
  inside the window, so only the recovered reference separates them).
  That is a Stage-1 change, requires a manifest bump, a CHANGELOG entry
  and a new hash, and is explicitly out of Stage-2 scope.
- Evaluate the agent on the sub-population where deterministic rules
  abstain, and say plainly that the frozen T2 set does not isolate
  reference recovery.

**Recommendation:** report it. Do not retro-fit the frozen benchmark to
make the agent look necessary.

### What was actually done (2026-08-23)

The second option above, and only that one: *"add a T2 variant in a future
generator version where amount and date blocking is genuinely
insufficient"*. It was taken as a Stage-1 change with the manifest bump,
CHANGELOG entry and new hash that option demanded.

What it is **not**: a retro-fit to make the agent look necessary. The
distinction is that v1's T2 did not measure what it claimed to measure —
it graded reference survival while leaving structural evidence fully
intact, so T2 and T1 were the same test with different narration. Fixing
the instrument before reading it is not the same as tuning the instrument
to the reading, and the ordering matters: this was done before any Stage-3
model, agent or prompt existed, and the matcher was not touched.

Under benchmark v2 the same rules-only baseline reports, on DEV:

| Tier | Cases | Resolved correctly | Correctly refused | Wrong |
|---|---:|---:|---:|---:|
| T0 | 350 | 350 | — | 0 |
| T1 | 300 | 300 | — | 0 |
| T2 | 200 | 0 | — (200 refused, but recoverable) | 0 |
| T3 | 40 | — | 40 | 0 |

All 200 T2 refusals carry exactly two plausible candidates, both including
the true settlement. The C-vs-D ablation delta of DESIGN.md §5.5 now has
room to move on T2 — not because the rules were weakened, but because T2
finally poses the question it was supposed to pose.

---

## 2. A batched settlement can be dated one day before its own bank credit's value date

**Severity: minor. Handled by a declared window; no generator change.**

`build_t1_batched_settlement` sets the credit's `value_date` from the
*first* settlement's date, while the second settlement is created one hour
later — which can fall on the next calendar day. One DEV case has a
`value_date − settlement_date` offset of −1.

The declared value-date window is therefore asymmetric-capable and set to
±1 day (`VALUE_DATE_WINDOW_DAYS_BEFORE/AFTER` in
`src/finrecon/matchers/rules.py`), which covers the observed range of
{−1, 0, +1}. Stated here so the constant reads as a deliberate,
evidence-backed choice rather than a tuned one.

---

## 3. No generator bug or contradiction found

Checked explicitly against DEV, all clean:

- Every settlement's break-up sums exactly to its `amount` (990/990). No
  unexplained paise anywhere in the dataset.
- Every `payment` break-up line references an existing payment in
  `captured` status for the exact same amount (990/990).
- Every `refund` break-up line references an existing refund in
  `processed` status for the exact same amount (60/60).
- No two settlements share a UTR.
- Exactly one bank record per case; 890 bank records, 890 ground-truth
  cases.
- Tier disjointness holds: the exact-token direct-key test reaches exactly
  the 350 T0 cases and no T1/T2/T3 case.

---

## 4. FROZEN-EVAL resolved half of T0 by the wrong rule (fixed in benchmark v3)

**Severity: benchmark-validity defect in the reporting split. Fixed.**

### What was observed

The two splits disagreed about which rule resolved T0:

| Split | T0 by `direct_key` | T0 by `derived` |
|---|---:|---:|
| DEV | 350 / 350 | 0 |
| **FROZEN-EVAL** | **175 / 350** | **175** |

Accuracy was identical and perfect on both — zero wrong auto-resolutions
either way — so no outcome-based assertion could see it. Only the
*mechanism* differed, and T0's whole purpose is the mechanism: DESIGN.md
§5.2 places it at the top of the reference-survival gradient precisely
because an ID join settles it.

### Why it happened

Record identifiers interpolated the split name verbatim, so a FROZEN-EVAL
settlement ID read `setl_frozen-eval_000042`. The declared tokenizer
(`finrecon.normalize.tokens`) uses `[^A-Za-z0-9_]+` as its delimiter class,
so `-` splits tokens:

```
narration: RZPY/SETL/setl_frozen-eval_000042 CREDIT
tokens   : RZPY | SETL | setl_frozen | eval_000042 | CREDIT
```

No whole token equals the settlement ID, and the direct-key matcher requires
whole-token equality. The cases fell through to derived reconciliation.

The generator certified them T0 anyway because its admission test used
**substring containment** — strictly weaker than the equality the matcher
applies. `setl_frozen-eval_000042` is trivially a substring of its own
narration.

### Why the test suite was green

DEV's slug is `dev`, which carries no delimiter. Every DEV-based coverage
assertion — including the one asserting all 350 T0 cases resolve by direct
key — passed truthfully. The defect existed *only* in the split reserved for
reporting, which by design is the split nobody walks case by case.

This is the transferable lesson, and worth more than the fix itself: a
held-out split that is never compared against the development split can
drift silently. The v3 pass therefore adds a cross-split rule-distribution
test (`tests/test_benchmark_v3_direct_key.py`), not just a corrected slug.

### What would have shipped

No wrong auto-resolutions and no change to match rate. What would have been
wrong is every mechanism claim: a per-rule breakdown in the Stage-4 results
table would have understated direct-key coverage by 175 on the reported
split, and T0 and T1 would have been measuring the same rule while the
README asserted they measured different ones.

### Fix

Benchmark **v3.0.0** — see `benchmark/manifests/CHANGELOG.md`. Whole-token
admission semantics in a new independent `token_contract.py`, an explicit
token-safe split slug (`frozen-eval` -> `frozeneval`) validated at
`RecordFactory` construction, and the cross-split test above. Seeds, RNG
streams, case counts and record counts unchanged; DEV byte-identical to v2;
the entire FROZEN-EVAL diff is identifier text and the T0 narrations that
embed a settlement ID. No matcher code was touched.

Note what was *not* done: the tokenizer was not taught to accept hyphens.
That would have fixed T0 by silently changing T2's `separator_altered`
semantics, since `-` is the separator that category manipulates — trading a
real defect for a subtler one.
