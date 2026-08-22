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
manifests, and a SHA-256 fingerprint for FROZEN-EVAL. That generator and
those datasets are frozen and unchanged by Stage 2.

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
│   ├── v1.json          ← generator version, seeds, tier counts, frozen-eval SHA-256
│   └── CHANGELOG.md      ← freeze record; future generator changes are logged here, not silent
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
| T2 — Degraded reference | 200 | A UTR exists but survives only in truncated, masked, reordered, separator-mutated, or noise-embedded form |
| T3 — Truly ambiguous | 40 | No usable reference at all, plus a genuinely indistinguishable candidate; ground truth requires escalation |

Tiers are mutually exclusive by construction: the generator classifies
every case's actual generated records independently of the archetype that
built it and asserts the result matches the declared tier, failing loudly
on any mismatch (`src/finrecon/benchmark/generator/assertions.py`).

### Frozen-eval SHA-256

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
make verify-frozen      # recomputes the FROZEN-EVAL hash and checks it against manifests/v1.json
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
- The structured financial evidence the generator leaves behind (settlement
  amount, break-up, date) is as strong for T2 cases as for T1 ones, since
  T2 degrades only the *reference*. A tier-blind deterministic rule
  therefore reaches T2 cases too. That is a property of the frozen
  benchmark, recorded here rather than worked around — see
  `notes/STAGE2-FINDINGS.md`.

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
