"""Stage-2 Phase F: base idempotency.

DESIGN.md §9 puts this test in Stage 2 on purpose — "idempotency is a
storage-layer invariant, and retrofitting it after the agent exists is far
more painful than asserting it the day the store is written."

Scope note: this covers *reprocessing the same batch*. The later
human-resolution case (a resolved exception must not re-raise) belongs to
Stage 5 and is deliberately not asserted here.
"""

from __future__ import annotations

import pytest

from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch


def _run_twice(benchmark_dir, split="dev", db=":memory:"):
    store = LedgerStore(db)
    first = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)
    first_digest = store.digest(first.batch_id)
    first_counts = {
        table: store.count(table)
        for table in ("batches", "cases", "case_links", "case_candidates", "case_snapshots", "audit_log")
    }

    second = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)
    second_digest = store.digest(second.batch_id)
    second_counts = {table: store.count(table) for table in first_counts}
    return store, (first, first_digest, first_counts), (second, second_digest, second_counts)


@pytest.fixture(scope="module")
def run_twice_result(benchmark_dir):
    """DEV processed twice into one ledger. Shared: every test below reads it."""
    store, first, second = _run_twice(benchmark_dir)
    yield store, first, second
    store.close()


class TestSameBatchTwice:
    def test_the_ledger_is_byte_identical_after_a_rerun(self, run_twice_result):
        store, (_, digest_1, _), (_, digest_2, _) = run_twice_result
        assert digest_1 == digest_2

    def test_no_table_grows_on_the_second_run(self, run_twice_result):
        store, (_, _, counts_1), (_, _, counts_2) = run_twice_result
        assert counts_1 == counts_2

    def test_no_duplicate_cases(self, run_twice_result):
        store, (first, _, _), _ = run_twice_result
        rows = store.case_rows(first.batch_id)
        assert len(rows) == 890
        assert len({row["case_id"] for row in rows}) == 890

    def test_no_duplicate_links(self, run_twice_result):
        store, (first, _, _), _ = run_twice_result
        rows = store.link_rows(first.batch_id)
        keys = {(row["case_id"], row["settlement_id"]) for row in rows}
        assert len(keys) == len(rows)

    def test_no_duplicate_candidate_rows(self, run_twice_result):
        store, (first, _, _), _ = run_twice_result
        rows = store.candidate_rows(first.batch_id)
        keys = {(row["case_id"], row["candidate_id"]) for row in rows}
        assert len(keys) == len(rows)

    def test_no_duplicate_logical_audit_decisions(self, run_twice_result):
        store, (first, _, _), _ = run_twice_result
        rows = store.audit_rows(first.batch_id)
        assert len(rows) == 890
        assert len({(row["case_id"], row["sequence"]) for row in rows}) == 890
        assert len({row["audit_id"] for row in rows}) == 890

    def test_the_same_logical_resolutions_are_reported_both_times(self, run_twice_result):
        store, (first, _, _), (second, _, _) = run_twice_result
        assert first.decisions == second.decisions
        assert first.snapshots == second.snapshots
        assert first.candidates_by_case == second.candidates_by_case

    def test_snapshot_hashes_are_stable_across_runs(self, run_twice_result):
        store, (first, _, _), (second, _, _) = run_twice_result
        assert [s.content_hash for s in first.snapshots] == [
            s.content_hash for s in second.snapshots
        ]


class TestIdempotencyOnDisk:
    def test_a_reopened_on_disk_ledger_still_deduplicates(self, benchmark_dir, tmp_path):
        db = tmp_path / "ledger.sqlite3"

        with LedgerStore(db) as store:
            first = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
            digest_1 = store.digest(first.batch_id)
            counts_1 = {t: store.count(t) for t in ("cases", "case_links", "audit_log")}

        with LedgerStore(db) as store:
            second = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
            assert store.digest(second.batch_id) == digest_1
            assert {t: store.count(t) for t in counts_1} == counts_1
