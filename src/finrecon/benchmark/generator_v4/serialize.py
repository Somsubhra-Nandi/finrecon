"""Deterministic JSONL serialization for the v4 pilot.

Byte-identical to what benchmark v3's serializer would produce for the same
records: same ID sort, same ``sort_keys=True``, same separators, same
newline. That is deliberate -- the two splits are read by the same loader, and
a formatting difference between them would be a difference the loader could
in principle see.

The v3 serializer is not reused directly only because it is typed against
``DatasetBundle`` and its ``ground_truth_dicts`` calls
``GroundTruthCase.to_json_dict``. The formatting primitives below are the
same three lines; the sorting rules are stated once, here, for v4's own
bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

from finrecon.benchmark.generator_v4.dataset import V4DatasetBundle

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
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for entry in dicts:
            handle.write(_dumps(entry))
            handle.write("\n")


def dataset_file_dicts(bundle: V4DatasetBundle) -> dict[str, list[dict]]:
    """Record-type name -> sorted, JSON-ready dicts. Pure; does not touch disk."""
    out: dict[str, list[dict]] = {}
    for record_type, id_field in _ID_FIELD.items():
        records = getattr(bundle, record_type)
        ordered = sorted(records, key=lambda record: getattr(record, id_field))
        out[record_type] = [record.model_dump(mode="json") for record in ordered]
    return out


def ground_truth_dicts(bundle: V4DatasetBundle) -> list[dict]:
    ordered = sorted(bundle.ground_truth, key=lambda entry: entry.case_id)
    return [entry.to_json_dict() for entry in ordered]


def write_v4_dataset(bundle: V4DatasetBundle, benchmark_dir: Path) -> dict[str, Path]:
    """Write ``datasets/<split>/*.jsonl`` and ``ground_truth/<split>.jsonl``."""
    dataset_dir = benchmark_dir / "datasets" / bundle.split
    written: dict[str, Path] = {}

    for record_type, dicts in dataset_file_dicts(bundle).items():
        path = dataset_dir / f"{record_type}.jsonl"
        _write_jsonl(path, dicts)
        written[record_type] = path

    ground_truth_path = benchmark_dir / "ground_truth" / f"{bundle.split}.jsonl"
    _write_jsonl(ground_truth_path, ground_truth_dicts(bundle))
    written["ground_truth"] = ground_truth_path
    return written


def dataset_file_names() -> tuple[str, ...]:
    return tuple(f"{name}.jsonl" for name in sorted(_ID_FIELD))


__all__ = [
    "dataset_file_dicts",
    "dataset_file_names",
    "ground_truth_dicts",
    "write_v4_dataset",
]
