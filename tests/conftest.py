"""Shared Stage-2 test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finrecon.ledger.store import LedgerStore
from finrecon.loader import default_benchmark_dir
from finrecon.pipeline import case_id_for, process_batch


@pytest.fixture(scope="session")
def benchmark_dir() -> Path:
    return default_benchmark_dir()


@pytest.fixture(scope="session")
def dev_result(benchmark_dir):
    """One deterministic pass over DEV, shared across tests (it is read-only)."""
    store = LedgerStore(":memory:")
    result = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
    yield result, store
    store.close()


@pytest.fixture(scope="session")
def dev_ground_truth(benchmark_dir):
    """DEV ground truth, keyed by Stage-2 case ID.

    **Test-only.** DESIGN.md §9 keeps ground truth hidden from the system;
    it is loaded here so a development diagnostic can say whether the
    deterministic rules are *right*, not merely self-consistent. Nothing
    under ``src/finrecon`` may reach it, which
    ``test_benchmark_isolation.py`` asserts structurally.

    For FROZEN-EVAL truth, see ``frozen_eval_tier_labels`` — deliberately a
    separate, narrower fixture.
    """
    path = benchmark_dir / "ground_truth" / "dev.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {case_id_for(e["record_ids"]["bank_records"][0]): e for e in entries}


@pytest.fixture(scope="session")
def frozen_eval_tier_labels(benchmark_dir):
    """FROZEN-EVAL **tier labels only**, keyed by Stage-2 case ID.

    Deliberately narrow. This fixture exposes each case's ``tier`` and
    ``archetype`` and nothing else — not ``correct_relationship``, not
    ``required_outcome``, not ``true_reference``. It exists for one purpose:
    asserting that a tier is resolved by the *mechanism* its definition
    names (T0 by direct key, T1 by derivation), which is a benchmark-integrity
    property, not an accuracy measurement.

    **Why this is not a hole in the freeze protocol.** DESIGN.md §5.1 step 7
    says build against DEV and report against FROZEN, and the risk it
    guards against is tuning: repeatedly reading held-out *outcomes* and
    adjusting rules until they improve. Tier labels cannot support that —
    they say what a case is meant to test, not what answer it has. The
    benchmark v3 defect (``benchmark/manifests/CHANGELOG.md``) was invisible
    precisely because no test ever compared the two splits' mechanisms, so
    the fix has to include a test that does.

    **Do not widen this fixture during Stage 3.** If a Stage-3 change wants
    FROZEN-EVAL outcomes, that is the evaluation harness's job (Stage 4),
    run once against a frozen system — not a fixture consulted while
    iterating.
    """
    path = benchmark_dir / "ground_truth" / "frozen-eval.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {
        case_id_for(e["record_ids"]["bank_records"][0]): {
            "tier": e["tier"],
            "archetype": e["archetype"],
        }
        for e in entries
    }
