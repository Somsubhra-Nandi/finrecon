"""Minimal CLI for one deterministic Stage-2 pass.

Prints counts of what was decided and under which rule. It deliberately
reports **no accuracy**: accuracy requires ground truth, the reconciliation
path cannot see ground truth, and DESIGN.md §7 draws exactly that line —
operational facts here, accuracy in the benchmark harness, which is a
later stage and does not exist yet.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from finrecon.ledger.store import open_ledger
from finrecon.loader import default_benchmark_dir
from finrecon.pipeline import process_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic reconciliation core.")
    parser.add_argument("--split", default="dev", help="dataset split to process (default: dev)")
    parser.add_argument("--db", default=":memory:", help="SQLite ledger path (default: in-memory)")
    parser.add_argument("--benchmark-dir", default=None, help="override the benchmark directory")
    args = parser.parse_args(argv)

    directory = Path(args.benchmark_dir) if args.benchmark_dir else default_benchmark_dir()

    with open_ledger(args.db) as store:
        result = process_batch(store=store, benchmark_dir=directory, split=args.split)

        print(f"batch            {result.batch_id}")
        print(f"records          {result.batch.record_count()}")
        print(f"cases            {len(result.decisions)}")
        print(f"resolved         {len(result.resolved())}")
        print(f"unresolved       {len(result.unresolved())}")
        print(f"case snapshots   {len(result.snapshots)}")
        print("decisions by rule:")
        for rule_id, count in sorted(Counter(d.rule_id for d in result.decisions).items()):
            print(f"  {count:>5}  {rule_id}")
        print(f"ledger digest    {store.digest(result.batch_id)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
