# FinRecon

FinRecon is a deterministic financial reconciliation controller with a
bounded AI investigation agent attached to the cases deterministic rules
cannot settle — built for Razorpay's AI Buildathon, Track 04 (AI Finance
Controller).

Architecture, benchmark design, and the implementation plan are fully
specified in [`DESIGN.md`](./DESIGN.md) (frozen, v4). This README does not
restate that document.

## Implementation status

**Currently at Stage 0 (Foundations)** of the plan in `DESIGN.md` §9.

Stage 0 delivers, and only delivers:

- Canonical financial record schemas (order, payment, settlement, refund,
  bank record) in `src/finrecon/models/`
- Integer-paise money representation (`src/finrecon/models/money.py`) —
  no float may enter the financial path
- The corruption taxonomy and UTR degradation ladder
  (`src/finrecon/benchmark/generator/`)
- The narration format library, with provenance labelling on every entry
  (`src/finrecon/benchmark/generator/narration_library.py`)

No benchmark dataset, matcher, agent, ledger, API, or UI exists yet — those
belong to later stages in `DESIGN.md` §9.

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
