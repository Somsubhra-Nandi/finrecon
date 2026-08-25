"""Recorded Stage-3 artifacts, read from one or more sources into one cohort.

Two source kinds, because two exist in practice:

``cache`` directory
    A :class:`finrecon.agent.cache.TrajectoryCache` directory of
    ``<cache_key>.json`` files -- the committed-fixture shape, and what
    ``make eval`` will normally point at.

``dump`` file
    The console transcript of an ``investigate_cli --show-trajectory`` run,
    which interleaves a summary header with ``====``-separated trajectory
    JSON. These are what a live experiment actually leaves behind, and a
    cohort is often split across several of them (one batch, plus a
    follow-up run covering the cases the batch missed). Supporting them
    directly is what removes the ad-hoc "paste it into a scratch script"
    step.

Multi-source precedence is **first source wins**. Later sources fill gaps
they alone cover; they never silently override an earlier one. Every case
records which source it came from, and any case offered by more than one
source is reported as a duplicate rather than quietly de-duplicated -- two
sources disagreeing about one case is a fact the operator needs to see.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from benchmark.eval.errors import EvaluationInputError

_SEPARATOR = re.compile(r"^={20,}\s*$", re.M)

_REQUIRED_FIELDS = ("case_id", "termination_reason", "cache_key", "snapshot_hash")


@dataclass(frozen=True)
class TrajectoryRecord:
    """One recorded investigation, plus where it was read from."""

    case_id: str
    cache_key: str
    payload: dict
    source: str

    @property
    def versions(self) -> dict[str, str]:
        """The version quadrant that decides whether this record is replayable."""
        return {
            key: self.payload.get(key, "")
            for key in (
                "prompt_version",
                "tool_schema_version",
                "agent_loop_version",
                "cache_schema_version",
                "validator_version",
                "policy_version",
            )
        }


def decode_text(raw: bytes) -> str:
    """Decode a dump file whose encoding is not known in advance.

    BOM-first, deliberately. A UTF-16LE transcript decodes *successfully* as
    UTF-8 -- every second byte is a NUL, and NUL is valid UTF-8 -- so a
    try-UTF-8-first ladder silently yields text full of NULs that then fails
    to parse for reasons that look like corruption. Checking the BOM removes
    the guess entirely; the NUL heuristic covers a BOM-less UTF-16 file.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if b"\x00" in raw[:4096]:
        return raw.decode("utf-16", errors="strict")
    return raw.decode("utf-8")


def _validate(payload: object, origin: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    if not all(field in payload for field in _REQUIRED_FIELDS):
        return None
    if not payload.get("cache_key"):
        raise EvaluationInputError(
            f"{origin}: trajectory for case {payload.get('case_id')!r} has no "
            "cache_key; it cannot be replayed, and the evaluator will not guess one"
        )
    return payload


def load_cache_dir(directory: Path | str) -> list[TrajectoryRecord]:
    """Read every ``<cache_key>.json`` in a trajectory cache directory."""
    path = Path(directory)
    if not path.is_dir():
        raise EvaluationInputError(f"trajectory directory not found: {path}")
    records: list[TrajectoryRecord] = []
    for entry in sorted(path.glob("*.json")):
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except ValueError as error:
            raise EvaluationInputError(f"{entry}: not valid JSON ({error})") from error
        checked = _validate(payload, str(entry))
        if checked is None:
            raise EvaluationInputError(
                f"{entry}: not a trajectory record (missing one of {_REQUIRED_FIELDS})"
            )
        records.append(
            TrajectoryRecord(
                case_id=checked["case_id"],
                cache_key=checked["cache_key"],
                payload=checked,
                source=f"cache:{path.name}",
            )
        )
    if not records:
        raise EvaluationInputError(f"no trajectory fixtures found in {path}")
    return records


def load_run_dump(file: Path | str) -> list[TrajectoryRecord]:
    """Read every trajectory embedded in an ``investigate_cli`` transcript."""
    path = Path(file)
    if not path.is_file():
        raise EvaluationInputError(f"run dump not found: {path}")
    text = decode_text(path.read_bytes())
    decoder = json.JSONDecoder()
    records: list[TrajectoryRecord] = []
    for chunk in _SEPARATOR.split(text):
        chunk = chunk.strip()
        if not chunk.startswith("{"):
            continue
        try:
            # raw_decode, not loads: a chunk may carry trailing summary prose
            # after the JSON object, which is normal in a console transcript.
            payload, _ = decoder.raw_decode(chunk)
        except ValueError:
            continue
        checked = _validate(payload, str(path))
        if checked is None:
            continue
        records.append(
            TrajectoryRecord(
                case_id=checked["case_id"],
                cache_key=checked["cache_key"],
                payload=checked,
                source=f"dump:{path.name}",
            )
        )
    if not records:
        raise EvaluationInputError(
            f"{path}: no trajectory records found. A transcript only contains them "
            "when the run was made with --show-trajectory."
        )
    return records


@dataclass(frozen=True)
class AssembledSources:
    """Every case one or more sources offered, and how they overlapped."""

    records: dict[str, TrajectoryRecord]
    duplicates: dict[str, tuple[str, ...]]
    """case_id -> every source that offered it, for cases offered more than once."""
    per_source_counts: dict[str, int]
    """source label -> how many cases it *contributed* (won), not how many it held."""

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.records))


def assemble(sources: list[list[TrajectoryRecord]]) -> AssembledSources:
    """Merge sources in priority order; first source to offer a case wins."""
    records: dict[str, TrajectoryRecord] = {}
    offered: dict[str, list[str]] = {}
    contributed: dict[str, int] = {}
    for group in sources:
        for record in group:
            offered.setdefault(record.case_id, []).append(record.source)
            if record.case_id not in records:
                records[record.case_id] = record
                contributed[record.source] = contributed.get(record.source, 0) + 1
    duplicates = {
        case_id: tuple(labels)
        for case_id, labels in sorted(offered.items())
        if len(labels) > 1
    }
    return AssembledSources(
        records=records,
        duplicates=duplicates,
        per_source_counts=dict(sorted(contributed.items())),
    )


def read_cohort_file(file: Path | str) -> tuple[str, ...]:
    """Read an explicit case-ID cohort.

    Accepts a JSON array, a JSON object with a ``case_ids`` key, or plain
    newline-delimited IDs with ``#`` comments. Order is preserved and
    duplicates are **kept**, so that a cohort file containing the same ID
    twice is reported as a duplicate rather than silently collapsing into a
    smaller cohort.
    """
    path = Path(file)
    if not path.is_file():
        raise EvaluationInputError(f"cohort file not found: {path}")
    text = decode_text(path.read_bytes()).strip()
    if not text:
        raise EvaluationInputError(f"cohort file is empty: {path}")
    if text.startswith(("[", "{")):
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("case_ids")
        if not isinstance(payload, list) or not all(isinstance(x, str) for x in payload):
            raise EvaluationInputError(
                f"{path}: expected a JSON array of case IDs, or an object with a "
                "'case_ids' array of strings"
            )
        return tuple(payload)
    ids = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not ids:
        raise EvaluationInputError(f"cohort file listed no case IDs: {path}")
    return tuple(ids)


def cohort_from_records(records: list[TrajectoryRecord]) -> tuple[str, ...]:
    """Derive a cohort from a baseline run's own trajectories, in sorted order.

    This is how a comparison gets pinned to "exactly the cases the historical
    run covered" without anyone transcribing fifty IDs by hand.
    """
    return tuple(sorted(r.case_id for r in records))


__all__ = [
    "AssembledSources",
    "TrajectoryRecord",
    "assemble",
    "cohort_from_records",
    "decode_text",
    "load_cache_dir",
    "load_run_dump",
    "read_cohort_file",
]
