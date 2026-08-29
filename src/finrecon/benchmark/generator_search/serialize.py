"""Deterministic, deliberately shuffled serialization for bounded-search-v1."""

from __future__ import annotations

import json
from pathlib import Path
from random import Random

from finrecon.benchmark.generator.seeding import derive_seed
from finrecon.benchmark.generator_search.config import BENCHMARK_NAME, SEARCH_SEED
from finrecon.benchmark.generator_search.dataset import SearchDatasetBundle

_ID_FIELD = {
    "bank_records": "bank_record_id",
    "orders": "order_id",
    "payments": "payment_id",
    "refunds": "refund_id",
    "settlements": "settlement_id",
}


def _dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(_dumps(row) + "\n" for row in rows), encoding="utf-8")


def dataset_file_dicts(bundle: SearchDatasetBundle) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    for name, id_field in _ID_FIELD.items():
        records = list(getattr(bundle, name))
        # Begin from a stable order, then shuffle with a file-specific seed so
        # source placement is reproducible but not an ID-order shortcut.
        records.sort(key=lambda record: getattr(record, id_field))
        Random(derive_seed(SEARCH_SEED, BENCHMARK_NAME, "source-order", name)).shuffle(records)
        result[name] = [record.model_dump(mode="json") for record in records]
    return result


def write_search_dataset(bundle: SearchDatasetBundle, benchmark_dir: Path) -> dict[str, Path]:
    written: dict[str, Path] = {}
    root = benchmark_dir / "datasets" / BENCHMARK_NAME
    for name, rows in dataset_file_dicts(bundle).items():
        path = root / f"{name}.jsonl"
        _write_jsonl(path, rows)
        written[name] = path

    truth_path = benchmark_dir / "ground_truth" / f"{BENCHMARK_NAME}.jsonl"
    truth_rows = sorted(bundle.ground_truth, key=lambda row: row["case_id"])
    _write_jsonl(truth_path, truth_rows)
    written["ground_truth"] = truth_path

    cohort_path = benchmark_dir / "cohorts" / f"{BENCHMARK_NAME}.json"
    cohort_path.parent.mkdir(parents=True, exist_ok=True)
    case_ids = sorted(
        f"case:{row['record_ids']['bank_records'][0]}" for row in bundle.ground_truth
    )
    cohort_path.write_text(json.dumps(case_ids, indent=2) + "\n", encoding="utf-8")
    written["cohort"] = cohort_path
    return written


__all__ = ["dataset_file_dicts", "write_search_dataset"]
