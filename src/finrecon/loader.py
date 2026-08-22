"""Loader for system-visible benchmark inputs.

This is the **only** door through which the deterministic reconciliation
pipeline reads benchmark data, and it reads exactly five files:

    datasets/<split>/{bank_records,orders,payments,refunds,settlements}.jsonl

``benchmark/ground_truth/`` is hidden evaluation data (DESIGN.md §9,
Stage 1: "Ground truth emitted alongside, hidden from the system"). No
function here can reach it: the path is built from
:data:`VISIBLE_RECORD_FILES` under ``datasets/<split>/`` only, and
:func:`load_visible_split` refuses a split name that is not a plain
directory component. A structural test asserts that no module under the
reconciliation path imports or opens anything under ``ground_truth``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from finrecon.models import BankRecord, Order, Payment, Refund, Settlement

VISIBLE_RECORD_FILES: tuple[str, ...] = (
    "bank_records",
    "orders",
    "payments",
    "refunds",
    "settlements",
)
"""The five system-visible record files, in fixed alphabetical order."""

@dataclass(frozen=True)
class VisibleSplit:
    """Raw canonical records for one split, plus a content fingerprint."""

    split: str
    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund]
    settlements: list[Settlement]
    bank_records: list[BankRecord]
    content_fingerprint: str
    """SHA-256 over the visible file bytes; batch identity is checked against it."""


def _read_jsonl_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as fh:
        return [line for line in fh if line.strip()]


def _parse(model, path: Path) -> list:
    """Parse each JSONL line straight into ``model``.

    ``model_validate_json`` is used rather than ``model_validate`` over a
    ``json.loads`` dict on purpose: the canonical models are
    ``strict=True`` (see :mod:`finrecon.models.base`), and only pydantic's
    JSON-input mode accepts the wire representations the generator emits —
    ISO-8601 timestamp strings and enum *values* — while still refusing
    the lax coercions strict mode exists to block, floats into ``Paise``
    above all.
    """
    return [model.model_validate_json(line) for line in _read_jsonl_lines(path)]


def _fingerprint(paths: dict[str, Path]) -> str:
    manifest = "".join(
        f"{name}\t{hashlib.sha256(paths[name].read_bytes()).hexdigest()}\n"
        for name in VISIBLE_RECORD_FILES
    )
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def default_benchmark_dir() -> Path:
    """Locate ``benchmark/`` by walking up to the directory holding ``pyproject.toml``."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate / "benchmark"
    raise RuntimeError("could not locate the repository root")


def visible_split_dir(benchmark_dir: Path, split: str) -> Path:
    if not split or "/" in split or "\\" in split or split in {".", ".."}:
        raise ValueError(f"invalid split name: {split!r}")
    return benchmark_dir / "datasets" / split


def load_visible_split(benchmark_dir: Path, split: str) -> VisibleSplit:
    """Load one split's system-visible records. Never touches ground truth."""
    directory = visible_split_dir(benchmark_dir, split)
    paths = {name: directory / f"{name}.jsonl" for name in VISIBLE_RECORD_FILES}
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing visible dataset files: {missing}")

    return VisibleSplit(
        split=split,
        orders=_parse(Order, paths["orders"]),
        payments=_parse(Payment, paths["payments"]),
        refunds=_parse(Refund, paths["refunds"]),
        settlements=_parse(Settlement, paths["settlements"]),
        bank_records=_parse(BankRecord, paths["bank_records"]),
        content_fingerprint=_fingerprint(paths),
    )
