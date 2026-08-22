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
    ``test_benchmark_isolation.py`` asserts structurally. FROZEN-EVAL truth
    is never loaded by any test.
    """
    path = benchmark_dir / "ground_truth" / "dev.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {case_id_for(e["record_ids"]["bank_records"][0]): e for e in entries}
