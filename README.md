# FinRecon

FinRecon is a deterministic financial reconciliation controller with a
bounded AI investigation agent attached to the cases deterministic rules
cannot settle — built for Razorpay's AI Buildathon, Track 04 (AI Finance
Controller).

Architecture, benchmark design, and the implementation plan are fully
specified in [`DESIGN.md`](./DESIGN.md) (frozen, v4). This README does not
restate that document.

## Implementation status

**Currently at Stage 2 (Deterministic core)** of the plan in `DESIGN.md` §9.

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

**No LLM, Gemini call, narration parser, investigation agent, validator,
policy gate, evaluation harness, API or UI exists yet** — those belong to
Stage 3+ in `DESIGN.md` §9. Nothing on the reconciliation path reads the
hidden ground truth, which `tests/test_benchmark_isolation.py` asserts
structurally.

**No benchmark result is reported here.** Match rate, precision, coverage
and value at risk require the Stage-4 evaluation harness, which does not
exist. There is deliberately no `make eval` target.

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
│   ├── v3.json          ← current: generator version, seeds, tier counts, frozen-eval SHA-256
│   ├── v2.json          ← superseded, retained verbatim so the correction is auditable
│   ├── v1.json          ← superseded, retained verbatim
│   └── CHANGELOG.md      ← freeze record; generator changes are logged here, not silent
├── datasets/
│   ├── dev/              ← system-visible inputs only: orders, payments, settlements, refunds, bank_records
│   └── frozen-eval/       (same five files)
└── ground_truth/
    ├── dev.jsonl          ← hidden: tier, correct relationship, true reference, value at stake
    └── frozen-eval.jsonl
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

## Development setup

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
make install
```

## Running tests

```bash
make test
```
