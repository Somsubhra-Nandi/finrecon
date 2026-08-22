# FinRecon

FinRecon is a deterministic financial reconciliation controller with a
bounded AI investigation agent attached to the cases deterministic rules
cannot settle — built for Razorpay's AI Buildathon, Track 04 (AI Finance
Controller).

Architecture, benchmark design, and the implementation plan are fully
specified in [`DESIGN.md`](./DESIGN.md) (frozen, v4). This README does not
restate that document.

## Implementation status

**Currently at Stage 1 (Benchmark generator)** of the plan in `DESIGN.md` §9.

Stage 0 delivered the foundations, reused as-is in Stage 1:

- Canonical financial record schemas (order, payment, settlement, refund,
  bank record) in `src/finrecon/models/`
- Integer-paise money representation (`src/finrecon/models/money.py`) —
  no float may enter the financial path
- The corruption taxonomy and UTR degradation ladder
  (`src/finrecon/benchmark/generator/`)
- The narration format library, with provenance labelling on every entry
  (`src/finrecon/benchmark/generator/narration_library.py`)

Stage 1 delivers, and only delivers, the synthetic benchmark generator and
its output: a DEV dataset, a FROZEN-EVAL dataset, hidden ground truth for
both, deterministic manifests, and a SHA-256 fingerprint for FROZEN-EVAL.
**No matcher, candidate generator, agent, validator, policy gate, ledger,
API, or UI exists yet** — those belong to Stage 2+ in `DESIGN.md` §9, and
nothing in this repository consumes the ground truth this stage emits.

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
  exists yet — Stage 1 only generates data; nothing here evaluates it.

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
