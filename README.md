# FinRecon

FinRecon is a deterministic financial reconciliation controller with a
bounded AI investigation agent attached to the cases deterministic rules
cannot settle — built for Razorpay's AI Buildathon, Track 04 (AI Finance
Controller).

Architecture, benchmark design, and the implementation plan are fully
specified in [`DESIGN.md`](./DESIGN.md) (frozen, v4). This README does not
restate that document.

## Implementation status

**Currently at Stage 4 (Offline benchmark evaluation)** of the plan in
`DESIGN.md` §9. Stages 0-3 are complete; the Stage-4 evaluator lives in
`benchmark/eval/` and is the only layer that reads hidden ground truth.

Stage 0 delivered the foundations, reused as-is in Stage 1:

- Canonical financial record schemas (order, payment, settlement, refund,
  bank record) in `src/finrecon/models/`
- Integer-paise money representation (`src/finrecon/models/money.py`) —
  no float may enter the financial path
- The corruption taxonomy and UTR degradation ladder
  (`src/finrecon/benchmark/generator/`)
- The narration format library, with provenance labelling on every entry
  (`src/finrecon/benchmark/generator/narration_library.py`)

Stage 1 delivered the synthetic benchmark generator and its output: a DEV
dataset, a FROZEN-EVAL dataset, hidden ground truth for both, deterministic
manifests, and a SHA-256 fingerprint for FROZEN-EVAL.

The benchmark is now at **v3** (`benchmark/manifests/v3.json`), after two
benchmark-validity corrections. v1 was superseded by a correction to the T2
tier, described under
[Benchmark v1 finding, and why v2 exists](#benchmark-v1-finding-and-why-v2-exists)
below. v2 was superseded by a T0 correction: FROZEN-EVAL settlement IDs
embedded the split name verbatim, so `setl_frozen-eval_000042` carried a
tokenizer delimiter and 175 of 350 T0 cases could not be reached by the
direct-key matcher at all — they resolved correctly, by the wrong rule. DEV
was unaffected, which is why a green suite missed it. Full account in
`benchmark/manifests/CHANGELOG.md` v3.0.0.

**No Stage-2 code changed in either pass.** Both corrections made the
benchmark satisfy the matcher's declared contract; neither relaxed the
contract to admit the benchmark.

Stage 2 delivers the **deterministic reconciliation core**:

- **Normalization** (`src/finrecon/normalize/`) — UTC timestamps, integer
  paise preserved exactly, raw narration preserved byte-identical,
  deterministic record ordering, and source provenance for every value the
  normalizer rewrites.
- **Direct-key matching** (`src/finrecon/matchers/direct_key_matcher.py`) —
  whole-token exact equality against an intact UTR or a clean
  `settlement_id`. No substring search, no similarity, no degraded-reference
  recovery.
- **Derived reconciliation** (`src/finrecon/matchers/derived_reconciliation.py`)
  — settlement break-up accounting to the exact paise (fee, GST, refund,
  transfer, declared adjustment), a declared value-date window, and
  break-up line references that must name real records in a successful
  state. Predicates only; no weighted scoring.
- **Candidate generation + immutable case snapshots**
  (`src/finrecon/candidates/`) — every unresolved case is frozen together
  with its complete plausible candidate set, its base evidence, and a
  content hash that makes later tampering detectable.
- **SQLite ledger, structured audit trail and base idempotency**
  (`src/finrecon/ledger/`) — every decision, resolution *and* refusal, is
  persisted with its rule ID, matched IDs and exact-paise derivation.
  Reprocessing the same batch is a no-op.

Stage 3 delivers the **bounded AI investigation layer** and the
deterministic decision layer behind it:

- **Read-only investigation tools** (`src/finrecon/agent/tools.py`) — four
  of them, each a window onto the immutable case snapshot and nothing else,
  with Pydantic-validated input and output. They report facts (break-up
  lines, exact-paise residuals, mechanical reference comparisons) and never
  a verdict. There is no `recover_correct_settlement()`.
- **Provider-neutral model access** (`src/finrecon/agent/providers/`) —
  OpenRouter primary, Groq, Gemini and GoRouter as fallbacks, over an
  interface the agent loop can use without knowing any provider's wire
  format. Fallback is restricted to *infrastructure* failure by the exception
  type itself. Each step records the model requested and the model the
  provider reported answering, which a routing gateway can resolve to
  something else.
- **A bounded investigation loop** (`src/finrecon/agent/loop.py`) — an
  explicit state machine with a fixed model-step budget, a fixed per-turn
  tool-call bound, deterministic serial tool batches, and a deterministic
  early stop only when the existing validator/policy already resolves.
- **Full trajectory recording and deterministic replay**
  (`src/finrecon/agent/trajectory.py`, `cache.py`) — every step, every
  refused call, every fallback, keyed so a replay reproduces the same raw
  evidence with zero provider calls.
- **A deterministic validator** (`src/finrecon/decide/validator.py`,
  `validator.v2`) — predicates over the *complete* Stage-2 candidate set
  plus the **complete deterministic reference closure** of the bank narration
  (`src/finrecon/evidence/closure.py`). A candidate is identified when it is
  the only one consistent with every informative claim the narration supports,
  which admits *conjunctive* evidence: two clues that are each inconclusive can
  jointly identify one counterparty. Agent prose is not an input, structurally,
  and since v2 neither is the agent's choice of which clue to test — see
  [Validator v2](#validator-v2--safe-conjunctive-reference-evidence).
- **A deterministic policy gate** (`src/finrecon/decide/policy.py`) — hard
  blockers, exact-paise accounting, and value-aware thresholds. Model
  confidence is never consulted.

**The Stage-4 offline evaluation harness now exists** (`benchmark/eval/`,
`make eval`); ablation, API and UI remain Stage 5+ in `DESIGN.md` §9. Nothing
on the reconciliation *or* investigation path reads the hidden ground truth,
which `tests/test_benchmark_isolation.py` and `tests/test_stage3_isolation.py`
assert structurally. The evaluator reads it, and the evaluator is outside
`src/` and cannot decide anything or call a model — see
[Stage 4](#stage-4--offline-benchmark-evaluation).

**A benchmark v4 pilot now exists**, additively, as the `v4-pilot` split with
its own generator package (`finrecon.benchmark.generator_v4`), its own
ground-truth schema and its own unfrozen manifest. It supersedes nothing: v3's
generator, seeds, datasets, manifests and FROZEN-EVAL fingerprint are
untouched, and `make verify-frozen` passes before and after. See
[Benchmark v4 — the compositional-evidence pilot](#benchmark-v4--the-compositional-evidence-pilot)
below, and `benchmark/V4-PILOT.md` for the design.

**The FROZEN-EVAL v3 result is reported in**
[`benchmark/reports/final-eval.json`](benchmark/reports/final-eval.json) and
`.md`: 823 correct automatic resolutions, zero wrong automatic resolutions,
67 escalations, 96.82% match rate across 850 uniquely resolvable cases, zero
unsafe automatic matches, and zero paise value at risk. It is an offline,
deterministic recorded/replay evaluation—not a hosted-model quality claim.

**Baseline live smoke tests and a five-case DEV diagnostic have now been
performed.** They are engineering observations, not benchmark results; the
provider/model, aggregate steps/tokens, safe failures, and the separate
orchestration-optimization experiment are recorded in
[`notes/STAGE3-FINDINGS.md`](./notes/STAGE3-FINDINGS.md) §§8–9. The committed
trajectory corpus additionally contains persisted bounded-search-v1 hosted
records: OpenRouter Free has 50 persisted files with a 45-case valid
provider-response scored cohort; Opus has a complete 50-case frozen scored
cohort. Opus discovered sufficient admissible evidence for all 40 resolvable
cases, while deterministic validation and policy authorized the resulting
automatic resolutions; the remaining 10 cases safely escalated. The requested
model was `claude-opus-5-thinking` and the provider reported `claude-opus-5`.
The Free Model Pool and Opus cohorts have different recorded configurations,
which remain visible in the reports.

## Reproduce the evaluation

From a clean clone, install the development dependencies and run the one
submission command:

```bash
uv sync --extra dev
uv run python -m benchmark.final_eval
```

It verifies the frozen v3 fingerprint, runs all 890 FROZEN-EVAL cases, runs
the complete Stage-3 residual cohort through a deterministic recorded/replay
path, and separately evaluates the complete v4 adversarial pilot. It needs no
network connection and no provider/API-key environment variables. Reports are
written to `benchmark/reports/final-eval.json` and
`benchmark/reports/final-eval.md`.

The replay investigator is explicitly deterministic and non-LLM: this is a
reproducible orchestration/validator/policy evaluation, not a fresh claim
about hosted-model quality. Live provider work is always explicit and is never
run by `make eval`.

Run the full repository health suite with:

```bash
uv run pytest
```

On systems with GNU Make, `make eval` and `make test` are equivalent
convenience aliases.

### Record, case, batch (DESIGN.md §5.0)

| Term | Meaning here |
|---|---|
| Record | One row: an order, payment, settlement, refund, or bank line |
| Case | One reconciliation decision, spanning one or more records |
| Batch | All records generated together for one split, in one run |

### Benchmark structure

```
benchmark/
├── manifests/
│   ├── v3.json          ← current frozen: generator version, seeds, tier counts, frozen-eval SHA-256
│   ├── v2.json          ← superseded, retained verbatim so the correction is auditable
│   ├── v1.json          ← superseded, retained verbatim
│   ├── v4-pilot.json    ← NOT frozen; a separate lineage, supersedes nothing
│   └── CHANGELOG.md      ← freeze record; generator changes are logged here, not silent
├── datasets/
│   ├── dev/              ← system-visible inputs only: orders, payments, settlements, refunds, bank_records
│   ├── frozen-eval/       (same five files)
│   └── v4-pilot/          (same five files)
├── ground_truth/
│   ├── dev.jsonl          ← hidden: tier, correct relationship, true reference, value at stake
│   ├── frozen-eval.jsonl
│   └── v4-pilot.jsonl     ← hidden: plus families, required composition, clue reach sets
├── baselines/            ← deterministic diagnostic arms; zero provider calls
├── eval/                 ← Stage 4, the only layer that scores anything
└── V4-PILOT.md           ← the v4 pilot's design, results and limitations
```

DEV and FROZEN-EVAL are generated from the same taxonomy and the same tier
recipe, with different fixed seeds (`DEV_SEED = 42`,
`FROZEN_EVAL_SEED = 1337`; see `src/finrecon/benchmark/generator/config.py`).
DEV is for tuning; FROZEN-EVAL is for final reporting only, and its
contents do not change without a new `benchmark/manifests/CHANGELOG.md`
entry and a new hash.

### Tiers (DESIGN.md §5.2)

| Tier | Cases | Reference state |
|---|---:|---|
| T0 — Direct key | 350 | Intact UTR or clean `settlement_id` survives in the bank narration |
| T1 — Derived | 300 | No usable direct join key; structured evidence (fee/GST/TDS/adjustment/transfer arithmetic, refund offsets, batching, duplicate disambiguation) remains sufficient |
| T2 — Degraded reference | 200 | Two settlements are equally plausible on amount, date and break-up; a UTR survives only in truncated, masked, reordered, separator-mutated, or noise-embedded form, and only recovering it separates them |
| T3 — Truly ambiguous | 40 | No usable reference at all, plus a genuinely indistinguishable candidate; ground truth requires escalation |

Tiers are mutually exclusive by construction: the generator classifies
every case's actual generated records independently of the archetype that
built it and asserts the result matches the declared tier, failing loudly
on any mismatch (`src/finrecon/benchmark/generator/assertions.py`).

### Benchmark v1 finding, and why v2 exists

Stage 2's deterministic core resolved **all 200 v1 T2 cases correctly
without reading a character of narration**. That is a benchmark defect, not
a result.

v1 built a T2 case by degrading only the *reference*. The financial
structure it left behind — one order, one captured payment, one settlement
with an exact fee/GST break-up, one credit matching that settlement's net
inside the value-date window — was identical to a T1 case. So the derived
rule reached the right answer from structure alone, and the degraded
reference was never causally necessary. T2 was measuring what T1 already
measured.

> During Stage 2, before any LLM/agent implementation, the deterministic
> rules-only baseline showed that all T2 cases were uniquely resolvable
> from structured financial evidence alone. This meant degraded-reference
> recovery was not necessary, so benchmark v1 did not isolate the intended
> T2 capability.

The full observation is in [`notes/STAGE2-FINDINGS.md`](./notes/STAGE2-FINDINGS.md) §1.

**The correction was made to the benchmark, not to the matcher.** No rule,
window, bound or tolerance in `src/finrecon/` changed. Weakening the
deterministic core to leave room for an agent would trade correctly
reconciled money for a better-looking ablation, which DESIGN.md §1 rules
out. The ordering also matters: v2 was built and frozen **before any
Stage-3 model, agent or prompt existed**, so no corruption, seed or
threshold could have been tuned against one.

#### The v2 T2 construct

Each T2 case now builds two settlements that are indistinguishable to every
declared deterministic rule:

```text
bank credit  = net,  value date within the declared window
   settlement A   net, same date, sound break-up, utr = <UTR_A>   <- true
   settlement B   net, same date, sound break-up, utr = <UTR_B>   <- decoy
   narration      carries a degraded fragment of UTR_A only

structured evidence alone  ->  A and B both plausible  ->  unresolved
reference correctly recovered ->  A  ->  uniquely resolvable
```

Both settlements carry the same gross, therefore the same fee/GST/net to
the paise; each has its own order and captured payment; each carries a UTR,
so the decoy is not identifiable by the absence of one. Which chain is
built first is randomised, so record-ID order carries no signal. The
degradation categories are unchanged from v1 — left/right truncation,
masking, separator alteration, reordering, and embedding in noisy narration
— still drawn from the frozen Stage-0 ladder.

This keeps T2 and T3 cleanly apart:

| Tier | Structured evidence alone | Recoverable discriminator | Correct outcome |
|---|---|---|---|
| T1 | unique answer | — | resolve |
| T2 | multiple candidates | yes, exactly one | resolve *after* recovery |
| T3 | multiple candidates | none | escalate |

#### The generator proves it, per case

The generator does not trust the tier it declared. Every T2 case is
re-derived from its own records — case-locally at build time, then again
batch-wide across the whole split — and generation fails unless: no whole
narration token is a usable direct key; at least two settlement groups are
structurally plausible; the true one is among them; the surviving fragment
is consistent with the true settlement's UTR and with no other candidate's;
and deleting the narration entirely leaves the case ambiguous. See
`src/finrecon/benchmark/generator/t2_invariants.py`, whose plausibility
model is reimplemented from DESIGN.md §4.3 rather than imported from
`finrecon.matchers`, so the check is independent of the code it checks.

#### DEV engineering diagnostic (not a benchmark result)

DEV only, produced by the unchanged Stage-2 core against benchmark v2.
FROZEN-EVAL outcomes are not inspected, and no match rate, precision or
coverage number is claimed anywhere — that needs the Stage-4 harness,
which does not exist.

| Tier | Total | Resolved | Unresolved | Incorrect |
|---|---:|---:|---:|---:|
| T0 | 350 | 350 | 0 | 0 |
| T1 | 300 | 300 | 0 | 0 |
| T2 | 200 | 0 | 200 | 0 |
| T3 | 40 | 0 | 40 | 0 |

For all 200 DEV T2 cases: candidate count min 2 / max 2 / mean 2.00; 200
with ≥2 candidates; 200 with the true settlement present in the candidate
set; 0 uniquely resolved by structured rules. All 40 T3 cases remain
correctly unresolved with two candidates each.

Counts: 890 cases in both splits (T0 350, T1 300, T2 200, T3 40), unchanged
from v1. Records rise to 4,580 per split (from 3,980) because each T2 case
now needs a second order, payment and settlement.

Seeds are unchanged from v1 — `DEV_SEED = 42`, `FROZEN_EVAL_SEED = 1337`.
The generator-version bump already separates the artifacts, and keeping the
seeds makes it plain none was chosen for a matcher outcome.

### Benchmark v4 — the compositional-evidence pilot

**Not frozen. Not a reporting artifact.** Full account in
[`benchmark/V4-PILOT.md`](benchmark/V4-PILOT.md); findings in
[`notes/BENCHMARK-V4-FINDINGS.md`](notes/BENCHMARK-V4-FINDINGS.md).

Benchmark v3's T2 tier turned out not to be a strong test of AI reasoning.
Exhaustive enumeration of every narration substring of length ≥ 4, with no
model anywhere in the loop, identifies the correct settlement in **200 of 200**
DEV T2 cases and misidentifies none. The degraded reference is causally
necessary — v2 established that, and structured evidence alone still resolves
none of them — but necessary did not imply hard to recover.

So v3 keeps three uses and loses one. It remains a safety-regression
benchmark, a deterministic validator/policy regression benchmark, and a
tool-contract regression benchmark. It **must not** be presented as evidence
that a language model contributes reasoning a substring loop does not.

The v4 pilot (`v4-pilot` split, 64 cases, 778 records, seed 4242) builds cases
whose resolvable answers require *composing* evidence — a reference head in one
narration field and its tail in another; a reference reach set intersected with
a break-up line amount or a settlement date — with 16 of 64 intentionally
unresolvable. Two results came out of it, both deterministic and both for zero
tokens:

**The Stage-3 decision layer could not express a conjunction.** Under
`validator.v1` the resolution predicate was one discriminating fragment plus
financial exactness, and financial exactness is uniform within a case because
Stage 2's candidate generator has already filtered on it. So every
compositional case escalated, and the pilot's match rate under v1 was 8/48. The
pilot was built without touching the validator, the gate, the tools or the
prompt — the gap was measured first. It has since been closed by
`validator.v2`; see
[Validator v2](#validator-v2--safe-conjunctive-reference-evidence).

**The pilot is nonetheless fully solvable without a model.** Five deterministic
arms, zero provider calls:

| Arm | Resolved | Correct | **Wrong** | Match rate |
|---|---:|---:|---:|---:|
| A · Stage-2 rules only | 0 | 0 | 0 | 0.000 |
| B1 · `validator.v1` semantics (the before-column) | 12 | 8 | **4** | 0.167 |
| B · the shipped gate, exhaustively fed (`validator.v3`) | 48 | 48 | **0** | 1.000 |
| C1 · lexical composition | 38 | 34 | **4** | 0.708 |
| C2 · lexical + structural composition | 48 | 48 | 0 | 1.000 |
| C3 · first subset that isolates | 52 | 48 | **4** | 1.000 |

`B1` is the rule that saturated v3 T2; on v4 it reaches only the 8-case
positive control, which is the measurement that justified `validator.v2`. `B`
is the shipped gate: v2 supplied closed reference conjunction and v3 supplies
closed structural consistency. Arm C2 also solves the pilot completely — which is close to a tautology, because
C2 composes exactly the feature vocabulary the generator uses to *define* its
cases. Raising the conjunction arity raises an exponent, not a complexity
class. That is reported rather than hidden, and it is why the recommendation is
not to freeze a full v4 yet.

Every wrong resolution outside the shipped gate and conservative C2/S3 rules comes from one four-case archetype whose
correct outcome is escalation: a stale reference from a different settlement
plus a contradicting value-date field. Only C2's "exactly one candidate is
consistent with *everything*" rule escapes it — C3 has identical features and
resolves all four wrongly. Adding composition without a consistency rule made
the arms broader, not safer.

```bash
make generate-v4-pilot   # build and write the pilot (verifies every case twice)
make verify-v4-pilot     # recompute its fingerprint against its manifest
make baselines-v4-pilot  # the five arms; zero provider calls
make test-v4             # the pilot's own tests
make test-validator-v3   # structural closure and adversarial decision fixtures
make verify-frozen       # benchmark v3, still frozen
```

### Validator v2 — safe conjunctive reference evidence

Full account: [`notes/VALIDATOR-V2-FINDINGS.md`](notes/VALIDATOR-V2-FINDINGS.md).

The v4 pilot found that the decision layer could not combine evidence: it
resolved a case only when some *one* narration fragment reached exactly one
candidate. Two clues that were each inconclusive but jointly decisive were
unreachable, and — worse — a clue reaching two candidates could
*contradict* a discriminating one and was silently discarded. `validator.v2`
fixes both.

**The unsafe version of this fix, and why it was rejected.** Intersecting the
reach sets of the fragments *the agent tested* looks obvious and is not safe.
With three references arranged the way same-bank same-day references actually
are:

```
"AXISCN11" -> {A, B}      "863727" -> {A, C}      "Q7K4" -> {B, C}
```

one narration carrying all three spans proves **A** (head + tail), **B** (head
+ hinge) or **C** (tail + hinge) depending on which pair the agent happened to
test, and proves nothing when read completely. That is the model choosing the
winner by choosing where not to look — the fishing-by-omission channel
`DESIGN.md` §4.1 exists to close, reappearing inside the conjunction. It is a
fixture in `benchmark/baselines/adversarial.py`, not a hypothesis: the rule
resolves all three ways on the same snapshot.

**What shipped instead.** A candidate is identified when it is the only one
consistent with **every** informative claim in the narration's *deterministic
closure* — every substring standing in a declared relation to any candidate
reference, whether the agent asked about it or not
(`src/finrecon/evidence/closure.py`). Under a closed evidence set, looking away
cannot help, because nothing can be left out.

The agent's evidence is a **seed**, not the proof: the closure is consulted only
once the investigation has surfaced at least one admissible fragment. *Which*
fragment does not change the conclusion; only *whether* one exists changes
whether the path runs. So the omission attack has nothing to work with, while a
case nobody investigated still cannot move money — an invariant this repository
already asserted at two layers, and the criterion that ruled out the
unconditional-closure variant.

Four properties come free from the rule being a set intersection rather than a
vote, and are tested anyway: **order invariance**, **duplicate invariance**,
**overlap invariance** (`"ABC123"`, `"BC12"` and `"C123"` are one claim read
three ways, grouped into one evidence atom), and **contradiction monotonicity**
(adding a claim can only shrink an intersection, so valid contradicting
evidence can never leave a match standing).

**Results.** 14 adversarial fixtures, the v4 pilot, and the 240-case DEV
residual:

| | `validator.v1` | **`validator.v2`** |
|---|---:|---:|
| Adversarial fixtures passed | 11 / 14 | **14 / 14** |
| v4 pilot resolved / correct / **wrong** | 12 / 8 / **4** | 38 / 34 / **4** |
| v4 pilot match rate | 0.167 | **0.708** |
| DEV correct / **wrong** | 171 / **0** | 171 / **0** |
| DEV T3 resolved | 0 | **0** |

**+26 correct resolutions on the pilot, zero new wrong ones, and no benchmark
v3 regression.** All 26 are conjunctive by the strict definition — no single
claim would have sufficed. v2 also *closes* two v1 safety failures that only
the adversarial suite made visible.

**What v2 does not fix, stated plainly.** The four `conflict_stale_reference`
cases still resolve wrongly, exactly as under v1, and the ₹1.34 crore of
value at risk on the pilot is entirely theirs. Their reference closure contains
one informative claim and nothing contradicts it, because what refutes them is
a *value date*. **No reference-only rule can escalate them** — requiring
otherwise would require the impossible, so the harness records it as explicitly
not a shipping criterion. Fixing them needs the second capability
`benchmark/V4-PILOT.md` §9 names: a declared narration-date-to-settlement-date
relation. That is the strongest remaining argument for it.

**What it cost.** Only the validator moved:

```
investigator.v4   tools.v3   loop.v2   trajectory-cache.v3   validator.v2   policy.v1
```

The prompt, the tools, the loop, the trajectory record format, the candidate
generator and every integer-paise rule are untouched. The policy gate keeps
`policy.v1` and its exact blocker vocabulary — a contradiction reaches it as the
*union* of the contradicting claims, firing the existing
`ambiguous_reference_link`, which is what that blocker already means. Because
`validator_version` is part of the trajectory cache key, every v1 artifact now
**fails replay closed** with the versions named rather than being silently
rescored, and `validator_version` is already a `compare` dimension so a
v1-vs-v2 comparison is attributed to the validator and not to a model.

The closure is exhaustive over every substring, with no sampling: 1.4 ms per
DEV case, 4.3 ms per pilot case. Above a declared 240-character narration bound
it refuses rather than truncating, because a partially searched narration
cannot support a claim about what the narration does *not* contain.

**This is a deterministic decision-layer capability, not an LLM-accuracy
feature.** The numbers above were produced by a non-linguistic fake; no model
has run against the pilot. The trade is deliberate and worth naming: the agent
no longer selects the reference evidence, and its fragment selection is now a
*measured* quantity (`agent_atom_coverage` in the Stage-4 report) rather than an
input to any predicate. There is no reason to let an untrusted component choose
the evidence when the complete evidence costs four milliseconds.

```bash
make conjunction-rules    # the five-rule comparison that chose the rule
make test-validator-v2    # the closure's equivalence claims + the safety suite
```

### Validator v3 — structural evidence hardening

Full account: [`notes/VALIDATOR-V3-FINDINGS.md`](notes/VALIDATOR-V3-FINDINGS.md).

`validator.v2` could prove reference conjunctions but could not refute a stale
reference with trusted structural context. `validator.v3` adds two closed,
exact relations: an explicit `VALDT DDMONYY` field must agree with the normalized
bank value date and candidate settlement date, and an explicit `RFND rrr.pp`
field must equal a signed refund breakup line in integer paise. Both are
evaluated over the complete immutable candidate snapshot. All recognized
tokens participate; duplicates add no weight and conflicting tokens empty the
intersection. Date or amount alone never resolves because v2's admissible
reference seed remains mandatory.

The v4 pilot moves from **38 resolved / 34 correct / 4 wrong / 26 escalated**
to **48 resolved / 48 correct / 0 wrong / 16 escalated**. All 16 intentionally
ambiguous cases escalate; unsafe auto-match rate and value at risk are zero.
The frozen v3 cohort remains **171 correct / 0 wrong / 0 T3 resolved** and its
fingerprint is unchanged.

Current identities:

```
investigator.v4   tools.v3   loop.v2   trajectory-cache.v3   validator.v3   policy.v1
```

### Frozen-eval SHA-256

Benchmark v3 (current):

```
f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b
```

Benchmark v2 (superseded, retained in `manifests/v2.json` and the
CHANGELOG):

```
d130c42c4bb52b6dc6b88e24f89257f4586c72423a22fdc4606440e53545b897
```

Benchmark v1 (superseded, retained in `manifests/v1.json` and the
CHANGELOG):

```
cda267318d215040a401bc413296015296f0d720eda09d6cd12503085fe88243
```

This is `sha256` of a manifest string built from
`"<relative_path>\t<sha256(file_bytes)>\n"` lines — one per FROZEN-EVAL
dataset file plus the FROZEN-EVAL ground-truth file, in fixed alphabetical
order — never ZIP metadata or filesystem timestamps. See
`src/finrecon/benchmark/generator/hashing.py` for the exact algorithm.

### Regenerating / verifying the benchmark

```bash
make generate-dev       # writes benchmark/datasets/dev/, benchmark/ground_truth/dev.jsonl
make generate-frozen    # writes benchmark/datasets/frozen-eval/, ground_truth/frozen-eval.jsonl, refreshes the hash
make verify-frozen      # recomputes the FROZEN-EVAL hash and checks it against manifests/v3.json
```

Or directly:

```bash
python -m finrecon.benchmark.generator.generate --split dev
python -m finrecon.benchmark.generator.generate --split frozen-eval
python -m finrecon.benchmark.generator.generate --verify-frozen
```

### Running the deterministic core

```bash
make reconcile-dev      # one deterministic pass over DEV; prints decision counts by rule
make test-idempotency   # process the same batch twice; assert no duplicate rows
make test-isolation     # assert reconciliation code cannot read hidden ground truth
```

`reconcile-dev` reports operational facts only — how many cases resolved,
under which rule, and how many produced candidate snapshots. It reports no
accuracy: a production system has no ground truth, and accuracy belongs to
the benchmark harness (`DESIGN.md` §7).

### Running the investigation layer

```bash
make test-stage3             # every Stage-3 test, deterministic fake providers
make investigate-dev         # LIVE, 4 DEV cases; needs a provider credential
make investigate-dev-replay  # replay from fixtures; zero provider calls, no key
```

Credentials come from the environment only — see `.env.example`. Nothing in
this repository writes a key to a trajectory, a fixture, a log line or an
exception message, and `.env` is gitignored.

`investigate-dev` prints the provider order, the exact model IDs and which
credentials are present before it runs anything, because a run that quietly
fell back to a different model is a run whose numbers mean something else.
Like `reconcile-dev`, it reports no accuracy.

#### The safety invariant, and how it is enforced

> The agent may enrich the case. It may not shrink it. (`DESIGN.md` §4.1)

This is structural, not a prompt instruction:

| The agent cannot… | Because |
|---|---|
| delete, hide or replace a candidate | the snapshot is a frozen model of tuples; tools receive it and nothing writable |
| rank or score candidates | no tool output has a score, rank, confidence or `is_match` field |
| mark a winner or resolve a case | the gate's signature takes no agent input; its inputs are the snapshot, the policy and raw tool outputs |
| investigate a settlement outside the case | candidate and settlement IDs are checked against the snapshot before any handler runs |
| have its summary believed | the validator reads tool results only; the extraction function cannot return prose |
| hide a contradicting candidate | the validator re-tests every fragment against **all** candidates, including ones the agent never asked about |
| fabricate a reference | a fragment must be found in the immutable narration — re-checked by the validator, not taken from the tool's own boolean |
| write anything | no tool imports sqlite3, the ledger, the loader or the pipeline |

#### Value-aware policy (DESIGN.md §4.5)

Two declared rungs, in `src/finrecon/decide/config.py`, both stated as
configuration rather than attributed to a source they did not come from:

| Rung | Threshold | Effect |
|---|---|---|
| ordinary | — | base evidence floor: 4 pinned reference characters |
| elevated scrutiny | > ₹1,00,000 | floor doubles to 8 pinned characters |
| above ceiling | > ₹5,00,000 | escalate regardless of evidence |

**Neither threshold binds on this benchmark** — the synthetic data tops out
in the tens of thousands of rupees — so the value gate is exercised by
construction in `tests/test_policy.py`, not by the dataset, and no count
anywhere is a product of it. Saying that is better than picking a ceiling
that makes the mechanism look active.

### Methodological limitations (stated plainly, per DESIGN.md §5.1/§10)

- This is synthetic data, authored by the same person building the system
  it will later evaluate. It does not equal production data.
- Freezing the eval set prevents overfitting to *instances*, not to the
  *distribution* the taxonomy encodes — the taxonomy still came from one
  person's judgment.
- The narration template library (`narration_library.py`) mixes two
  verbatim, citable UTR examples from Razorpay's own public API/webhook
  documentation with source-informed synthetic templates modelled on
  documented NEFT/RTGS/IMPS/UPI narration conventions — it is not a
  capture of any real bank's actual statement format.
- Not every real-world bank narration format, or every real degradation a
  bank statement can inflict on a reference, is represented here.
- No benchmark result, match rate, precision, coverage, or ablation number
  exists yet — Stage 2 reconciles; nothing here evaluates it.
- Benchmark v1's T2 tier did not isolate the capability it named; v2
  corrects the construct. The correction was made before any model
  existed, but it is still an author-authored benchmark being repaired by
  its author, and should be read as such — see below and
  `notes/STAGE2-FINDINGS.md`.
- v2's T2 ambiguity is a *pair* of plausible settlements. Real
  reconciliation queues contain larger and messier contention sets; the
  benchmark does not model those, and `notes/STAGE2-FINDINGS.md` records
  the other archetypes deliberately left out of scope.
- Hosted-model Stage-3 runs so far are limited to smoke tests and one
  five-case DEV diagnostic. They are not benchmark results, are not committed
  as replay fixtures, and do not establish model capability over T2/T3. The
  deterministic fake-provider diagnostics still describe plumbing only; see
  `notes/STAGE3-FINDINGS.md` §§1 and 8–9.
- Benchmark v3's T2 tier is not a strong test of AI reasoning: a model-free
  exhaustive substring matcher solves all 200 DEV T2 cases at zero risk. v3 is
  a safety and regression benchmark, and saying otherwise would overclaim. See
  `benchmark/V4-PILOT.md` §1.
- The v4 pilot is a pilot. It is not frozen, no number from it is a benchmark
  result, and no model has run against it. Its resolvable families are
  unreachable by the shipped decision layer *by construction*, so its 8/48
  match rate is a statement about a missing capability rather than about
  reconciliation quality.
- The v4 pilot is also fully solvable by a deterministic composition baseline
  (48/48, zero at risk), because its difficulty is assembled from the same
  declared feature vocabulary such a baseline can enumerate. A benchmark on
  which a model beats a deterministic composer would need evidence drawn from
  outside an author's declared vocabulary — real narrations with real
  degradations — which is a data-sourcing problem, not a generator problem.
- Three of the v4 pilot's narration shapes appear only in resolvable cases, so
  "this line has a refund-amount field, therefore this case has an answer" is
  true over 24 of its 64 cases. It scores nothing — every metric requires
  naming a settlement — but it is a real correlation and it is measured in the
  leakage audit rather than omitted.
- `DESIGN.md` is frozen at v4 and its §4.3 states the resolution predicate as
  "a strong reference link exists". `validator.v2` implements that as *exactly
  one candidate consistent with every informative claim in the narration's
  deterministic closure*, which is narrower than v1's reading in the safety
  direction and wider in the coverage direction. The design document was not
  edited to match; the divergence is recorded here and in
  `notes/VALIDATOR-V2-FINDINGS.md` §10, because rewriting a frozen design
  document to agree with later code is how a design document stops being
  evidence of anything.
- `validator.v2`'s reference path is fully deterministic. The agent seeds it and
  cannot select within it, so on the reference families the model contributes
  investigation efficiency and nothing to correctness. That is measured
  (`agent_atom_coverage`) rather than claimed either way, and it is a
  continuation of `notes/STAGE3-FINDINGS.md` §1 rather than a new finding.

### Stage 4 — offline benchmark evaluation

Accuracy lives in exactly one place, and it is not the shipped system.

```bash
make test-stage4                                  # the evaluator's own tests
make eval TRAJECTORIES=fixtures/trajectories      # score a recorded corpus
make eval-compare A=report-a.json B=report-b.json # same-cohort comparison
```

Full documentation: [`benchmark/eval/README.md`](benchmark/eval/README.md).

| Layer | Reads ground truth? | Reports accuracy? | Can call a model? | Decides? |
|---|---|---|---|---|
| Stage 2 — deterministic core | no (structurally) | **no** | no | yes |
| Stage 3 — investigation agent | no (structurally) | **no** | yes | yes |
| **Stage 4 — `benchmark/eval/`** | **yes** | **yes** | **no** | **no** |
| `benchmark/baselines/` | yes, **after** deciding | yes | **no** | for measurement only |

The baselines row is the one that needs a sentence. Those arms do decide, and
they do read truth — but never in the same call frame: `arms.py` and
`features.py` see a case snapshot and nothing else, and `report.py` loads truth
only once every arm has returned. Both halves are asserted by parsing the
package (`tests/test_v4_baselines.py`), and nothing there is installed with
`finrecon` or can reach a provider.

Reports are sliced by tier, archetype, family, required composition and
candidate-set size. A v1–v3 cohort has no families, so its family block comes
back empty rather than zero-filled — an absent key would read as "not
measured", and a zero as "measured, none found".

Four properties hold that table up:

- **Stage 3 itself has no ground truth.** `tests/test_benchmark_isolation.py`
  parses every module on the reconciliation path and fails if any of them
  names `ground_truth` in an import, a string literal or a path expression.
- **The production controller reports no accuracy.** `reconcile-dev` and
  `investigate-dev` print resolution counts, blockers, steps and models, and
  no correctness number at all — a production controller has no answer key.
- **Accuracy exists only in the offline evaluation layer.** `benchmark/eval/`
  sits outside `src/`, so it is never installed with the package. The
  dependency arrow runs one way: the evaluator imports `finrecon`, and nothing
  under `src/finrecon` imports the evaluator.
- **Evaluation can never influence a reconciliation decision.** It runs after
  the fact over recorded artifacts and hands numbers to a human. It has no
  provider, no write path into the ledger's decision columns, and no way to
  feed a score back into the gate that decides whether money moves.

The evaluator is **offline by construction**: it replays recorded trajectories
through the real validator and policy with `replay_only=True` and
`chain=None`, and a missing trajectory fails the run rather than triggering a
live one. That is asserted three ways — structurally (no provider module is
imported anywhere in the package), at runtime (`provider_calls_made()` stays
false), and with a provider that raises if it is ever contacted.

Cohorts are pinned explicitly. `--limit` ordering is not a cohort: two runs
that both say "50 cases" can cover different fifties. Before scoring, the
evaluator reconciles requested against found and reports duplicates, missing,
extra, tier counts and contamination; an incomplete exact cohort aborts rather
than scoring the subset that happened to be present.

Comparison mode verifies identical case IDs and identical tier composition
before emitting a delta, then counts how many configuration dimensions differ
— provider/model, prompt, tools, loop, validator, policy — and **withholds
causal attribution whenever more than one moved**. See
`notes/STAGE3-FINDINGS.md` §11 for the worked example.

## Development setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
make install
```

## Operations console

FinRecon includes a React + TypeScript operations console backed by a thin
FastAPI layer over the existing orchestration and SQLite ledger boundaries.
No matching, validation, policy, or human-resolution authority is implemented
in the browser.

For local development, run these in separate terminals:

```bash
uv run --extra dev uvicorn finrecon.api.app:app --reload --host 127.0.0.1 --port 8000
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. To serve one production-built process instead:

```bash
cd web
npm ci
npm run build
cd ..
uv run uvicorn finrecon.api.app:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000`. The default durable ledger is
`var/finrecon.sqlite3`; override it with `FINRECON_LEDGER_PATH`.

## Docker self-hosting

The single FinRecon container serves both the FastAPI API and the built web
dashboard at `http://localhost:8000`. It is replay/demo-first by default: no
provider credential is needed to load the dashboard, run the operational demo,
browse benchmarks, or replay committed bounded-search trajectories.

```powershell
git clone <your-fork-or-repository-url> finrecon
cd finrecon
Copy-Item .env.example .env
# Optional: add your own provider credential to .env for Live investigations.
docker compose up --build
```

Open `http://localhost:8000`; health is available at
`http://localhost:8000/api/health`. Docker persists the SQLite ledger in the
named `finrecon-data` volume at `/app/var/finrecon.sqlite3`. Set
`FINRECON_LEDGER_PATH` to use a different server-side SQLite location.

Live investigation is intentionally unavailable in a public/demo deployment
unless the server operator configures a provider. Credentials are backend
environment variables only—never browser inputs. Supported variables are
`OPENROUTER_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`, and
`GOROUTER_API_KEY`, with optional provider-specific `*_MODEL` and `*_BASE_URL`
settings documented in [`.env.example`](.env.example). With no configured
credential, Live returns self-host configuration guidance while Replay, Demo,
and Benchmarks remain fully usable.

Benchmark replay reads persisted trajectory artifacts and makes zero provider
calls. v3 and the v4 pilot remain report/case-explorer views unless a real
persisted replay artifact exists.

## Running tests

```bash
make test            # full suite
make test-stage3     # investigation layer, deterministic fake providers
make test-stage4     # offline evaluator
make test-v4         # benchmark v4 pilot, its leakage audit and its baselines
make verify-frozen   # FROZEN-EVAL SHA-256 against the manifest
make verify-v4-pilot # v4 pilot fingerprint against its own manifest
```
