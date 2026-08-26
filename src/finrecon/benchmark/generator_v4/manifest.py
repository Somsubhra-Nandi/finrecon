"""The v4 pilot manifest, and its deliberately non-frozen fingerprint.

The manifest records what produced this pilot and what the pilot contains.
It carries a ``pilot_sha256`` computed exactly the way benchmark v3 computes
``frozen_eval_sha256`` -- same algorithm, same file ordering, same
git-tree-hash construction -- so that a later decision to freeze v4 does not
need a new hashing scheme.

What it does **not** carry is any claim that the pilot is frozen. The field is
named ``pilot_sha256`` rather than ``frozen_sha256`` and the manifest states
``frozen: false``, because DESIGN.md 5.1's freeze is a promise about a
reporting artifact and this is a diagnostic one. A pilot whose manifest looked
frozen would invite exactly the mistake the whole protocol exists to prevent:
someone reporting a headline number against a set that is still being
designed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finrecon.benchmark.generator_v4.config import (
    GENERATOR_V4_VERSION,
    V4_PILOT_MANIFEST_FILENAME,
    V4_PILOT_SEED,
    V4_PILOT_SPLIT,
)
from finrecon.benchmark.generator_v4.dataset import V4DatasetBundle
from finrecon.benchmark.generator_v4.families import ARCHETYPES, COMPOSITIONS, FAMILIES
from finrecon.benchmark.generator_v4.serialize import dataset_file_names

HASH_ALGORITHM_NOTE = (
    "For each path listed in 'hashed_files', in exactly that order, build the line "
    "'<relative_path>\\t<hex sha256 of that file's raw bytes>\\n' (paths are relative to "
    "benchmark/, forward-slash separated). Concatenate those lines into one string and "
    "take sha256 of its UTF-8 encoding. Identical in construction to benchmark v3's "
    "frozen_eval_sha256, so a later freeze needs no new scheme. Depends only on file "
    "content plus a fixed path label -- never on mtimes, filesystem iteration order, or "
    "archive metadata."
)


def hashed_file_list(split: str = V4_PILOT_SPLIT) -> tuple[str, ...]:
    dataset_paths = tuple(f"datasets/{split}/{name}" for name in dataset_file_names())
    return dataset_paths + (f"ground_truth/{split}.jsonl",)


def compute_pilot_fingerprint(benchmark_dir: Path, split: str = V4_PILOT_SPLIT) -> str:
    lines = []
    for relative_path in hashed_file_list(split):
        digest = hashlib.sha256((benchmark_dir / relative_path).read_bytes()).hexdigest()
        lines.append(f"{relative_path}\t{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def build_v4_manifest(bundle: V4DatasetBundle, pilot_sha256: str | None) -> dict:
    return {
        "generator_version": GENERATOR_V4_VERSION,
        "frozen": False,
        "status": (
            "PILOT -- not frozen, not a reporting artifact. No match rate, precision or "
            "coverage number may be presented from this split until a freeze decision is "
            "taken and recorded in benchmark/manifests/CHANGELOG.md."
        ),
        "split": bundle.split,
        "seed": V4_PILOT_SEED,
        "case_count": len(bundle.ground_truth),
        "record_counts": bundle.record_counts(),
        "total_record_count": bundle.total_record_count(),
        "archetype_counts": bundle.archetype_counts(),
        "family_counts": bundle.family_counts(),
        "required_composition_counts": bundle.composition_counts(),
        "required_outcome_counts": bundle.outcome_counts(),
        "candidate_count_buckets": bundle.candidate_count_buckets(),
        "declared_families": list(FAMILIES),
        "declared_compositions": list(COMPOSITIONS),
        "declared_archetypes": [
            {
                "archetype": spec.archetype,
                "families": list(spec.families),
                "required_composition": spec.required_composition,
                "required_outcome": spec.required_outcome,
                "candidate_counts": list(spec.candidate_counts),
            }
            for spec in ARCHETYPES
        ],
        "pilot_sha256": pilot_sha256,
        "hashed_files": list(hashed_file_list(bundle.split)),
        "hash_algorithm": HASH_ALGORITHM_NOTE,
        "v3_untouched": (
            "This manifest is a separate file. benchmark/manifests/v1.json, v2.json and "
            "v3.json are never opened by the v4 generator, and the v3 FROZEN-EVAL "
            "fingerprint f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b "
            "is unchanged by anything in this package."
        ),
    }


def write_v4_manifest(
    manifest: dict,
    benchmark_dir: Path,
    filename: str = V4_PILOT_MANIFEST_FILENAME,
) -> Path:
    path = benchmark_dir / "manifests" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def read_v4_manifest(
    benchmark_dir: Path, filename: str = V4_PILOT_MANIFEST_FILENAME
) -> dict:
    return json.loads((benchmark_dir / "manifests" / filename).read_text(encoding="utf-8"))


__all__ = [
    "HASH_ALGORITHM_NOTE",
    "build_v4_manifest",
    "compute_pilot_fingerprint",
    "hashed_file_list",
    "read_v4_manifest",
    "write_v4_manifest",
]
