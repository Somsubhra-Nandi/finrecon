"""Deterministic JSONL serialization for datasets and ground truth.

Every record type is sorted by its ID before being written, independent of
generation order, so serialization is stable even if case-building order
ever changes. ``json.dumps`` uses ``sort_keys=True`` and a fixed separator
so byte output does not depend on dict insertion order or platform default
separators.
"""

from __future__ import annotations

import json
from pathlib import Path

from finrecon.benchmark.generator.dataset import DatasetBundle
from finrecon.benchmark.generator.ground_truth import GroundTruthCase

_ID_FIELD = {
    "orders": "order_id",
    "payments": "payment_id",
    "settlements": "settlement_id",
    "refunds": "refund_id",
    "bank_records": "bank_record_id",
}


def _dumps(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _write_jsonl(path: Path, dicts: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for d in dicts:
            fh.write(_dumps(d))
            fh.write("\n")


def dataset_file_dicts(bundle: DatasetBundle) -> dict[str, list[dict]]:
    """Record-type name -> sorted, JSON-ready dicts. Pure; does not touch disk."""
    out: dict[str, list[dict]] = {}
    for record_type, id_field in _ID_FIELD.items():
        records = getattr(bundle, record_type)
        sorted_records = sorted(records, key=lambda r: getattr(r, id_field))
        out[record_type] = [r.model_dump(mode="json") for r in sorted_records]
    return out


def ground_truth_dicts(bundle: DatasetBundle) -> list[dict]:
    sorted_gt = sorted(bundle.ground_truth, key=lambda gt: gt.case_id)
    return [gt.to_json_dict() for gt in sorted_gt]


def write_dataset(bundle: DatasetBundle, benchmark_dir: Path) -> dict[str, Path]:
    """Write datasets/<split>/*.jsonl and ground_truth/<split>.jsonl. Returns written paths."""
    dataset_dir = benchmark_dir / "datasets" / bundle.split
    written: dict[str, Path] = {}

    for record_type, dicts in dataset_file_dicts(bundle).items():
        path = dataset_dir / f"{record_type}.jsonl"
        _write_jsonl(path, dicts)
        written[record_type] = path

    gt_path = benchmark_dir / "ground_truth" / f"{bundle.split}.jsonl"
    _write_jsonl(gt_path, ground_truth_dicts(bundle))
    written["ground_truth"] = gt_path

    return written


def dataset_file_names() -> tuple[str, ...]:
    """Canonical, alphabetically ordered dataset file names (record type files only)."""
    return tuple(f"{name}.jsonl" for name in sorted(_ID_FIELD))
