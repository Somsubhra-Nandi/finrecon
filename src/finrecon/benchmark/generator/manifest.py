"""Generator manifest (DESIGN.md §5.1 step 5-6, Stage 1 exit condition).

The manifest records everything needed to know exactly what produced a
committed dataset, without embedding anything that would make the output
non-deterministic (no wall-clock timestamp is included in the hashed
dataset content; the manifest's own optional ``frozen_date`` field is
metadata about the freeze event, not an input to generation).
"""

from __future__ import annotations

import json
from pathlib import Path

from finrecon.benchmark.generator.config import GENERATOR_VERSION
from finrecon.benchmark.generator.dataset import DatasetBundle
from finrecon.benchmark.generator.corruptions import CORRUPTION_TAXONOMY
from finrecon.benchmark.generator.hashing import hashed_file_list
from finrecon.benchmark.generator.utr_degradation import DEGRADATION_LADDER


def _taxonomy_ids() -> list[str]:
    return [c.id for c in CORRUPTION_TAXONOMY]


def _degradation_ladder_ids() -> list[str]:
    return [c.id for c in DEGRADATION_LADDER]


_EMPTY_MANIFEST_TEMPLATE: dict = {
    "generator_version": GENERATOR_VERSION,
    "dev_seed": None,
    "frozen_eval_seed": None,
    "target_tier_counts": {},
    "target_total_cases": 0,
    "actual_tier_counts": {"dev": None, "frozen-eval": None},
    "case_counts": {"dev": None, "frozen-eval": None},
    "record_counts": {"dev": None, "frozen-eval": None},
    "total_record_counts": {"dev": None, "frozen-eval": None},
    "corruption_taxonomy_ids": [],
    "utr_degradation_ladder_ids": [],
    "frozen_eval_sha256": None,
    "frozen_eval_hashed_files": [],
    "frozen_eval_hash_algorithm": (
        "For each path listed in 'frozen_eval_hashed_files', in exactly that order, build the "
        "line '<relative_path>\\t<hex sha256 of that file's raw bytes>\\n' (paths are relative to "
        "benchmark/, forward-slash separated, and the order is the fixed lexicographic order "
        "emitted by finrecon.benchmark.generator.hashing.hashed_file_list). Concatenate those "
        "lines into one string and take sha256 of its UTF-8 encoding. The list covers the "
        "complete FROZEN-EVAL artifact: all five system-visible dataset files AND the hidden "
        "ground-truth file. DEV files are excluded (a separate split), and this manifest is "
        "excluded to avoid a circular self-reference, since the resulting digest is recorded in "
        "it. Depends only on file content plus a fixed path label -- never on mtimes, filesystem "
        "iteration order, or archive/ZIP metadata."
    ),
}


def _base_manifest(existing: dict | None, seed: int, target_tier_counts: dict[str, int]) -> dict:
    manifest = dict(existing) if existing else dict(_EMPTY_MANIFEST_TEMPLATE)
    manifest["generator_version"] = GENERATOR_VERSION
    manifest["target_tier_counts"] = target_tier_counts
    manifest["target_total_cases"] = sum(target_tier_counts.values())
    manifest["corruption_taxonomy_ids"] = _taxonomy_ids()
    manifest["utr_degradation_ladder_ids"] = _degradation_ladder_ids()
    manifest["frozen_eval_hashed_files"] = list(hashed_file_list("frozen-eval"))
    manifest["frozen_eval_hash_algorithm"] = _EMPTY_MANIFEST_TEMPLATE["frozen_eval_hash_algorithm"]
    return manifest


def update_manifest_for_split(
    existing: dict | None,
    bundle: DatasetBundle,
    seed: int,
    target_tier_counts: dict[str, int],
    frozen_eval_sha256: str | None = None,
) -> dict:
    """Merge one split's actual results into (a copy of) ``existing``, leaving the other split untouched."""
    manifest = _base_manifest(existing, seed, target_tier_counts)
    split = bundle.split
    seed_key = "dev_seed" if split == "dev" else "frozen_eval_seed"
    manifest[seed_key] = seed
    manifest["actual_tier_counts"][split] = bundle.tier_counts()
    manifest["case_counts"][split] = len(bundle.ground_truth)
    manifest["record_counts"][split] = bundle.record_counts()
    manifest["total_record_counts"][split] = bundle.total_record_count()
    if split == "frozen-eval":
        manifest["frozen_eval_sha256"] = frozen_eval_sha256
    return manifest


def build_manifest(
    dev_bundle: DatasetBundle,
    frozen_bundle: DatasetBundle,
    dev_seed: int,
    frozen_eval_seed: int,
    target_tier_counts: dict[str, int],
    frozen_eval_sha256: str | None,
) -> dict:
    manifest = update_manifest_for_split(None, dev_bundle, dev_seed, target_tier_counts)
    manifest = update_manifest_for_split(manifest, frozen_bundle, frozen_eval_seed, target_tier_counts, frozen_eval_sha256)
    return manifest


def write_manifest(manifest: dict, benchmark_dir: Path, filename: str = "v1.json") -> Path:
    path = benchmark_dir / "manifests" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def read_manifest(benchmark_dir: Path, filename: str = "v1.json") -> dict:
    path = benchmark_dir / "manifests" / filename
    return json.loads(path.read_text(encoding="utf-8"))
