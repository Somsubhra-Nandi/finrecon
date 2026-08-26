"""Benchmark v4 pilot generator CLI.

    python -m finrecon.benchmark.generator_v4.generate --write
    python -m finrecon.benchmark.generator_v4.generate --verify
    python -m finrecon.benchmark.generator_v4.generate --diagnostics

Writes ``benchmark/datasets/v4-pilot/``, ``benchmark/ground_truth/v4-pilot.jsonl``
and ``benchmark/manifests/v4-pilot.json``. It never opens v3's manifests,
datasets or ground truth, and ``--write`` refuses any split but the pilot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finrecon.benchmark.generator_v4.config import (
    TARGET_ARCHETYPE_COUNTS,
    V4_PILOT_SEED,
    V4_PILOT_SPLIT,
    benchmark_dir,
)
from finrecon.benchmark.generator_v4.dataset import V4DatasetBundle, build_v4_dataset
from finrecon.benchmark.generator_v4.manifest import (
    build_v4_manifest,
    compute_pilot_fingerprint,
    read_v4_manifest,
    write_v4_manifest,
)
from finrecon.benchmark.generator_v4.serialize import write_v4_dataset


def generate_pilot(base_dir: Path | None = None) -> tuple[V4DatasetBundle, dict]:
    """Build, verify and write the pilot. Returns the bundle and its manifest."""
    bundle = build_v4_dataset(V4_PILOT_SEED, TARGET_ARCHETYPE_COUNTS, V4_PILOT_SPLIT)
    directory = benchmark_dir(base_dir)
    write_v4_dataset(bundle, directory)
    fingerprint = compute_pilot_fingerprint(directory, V4_PILOT_SPLIT)
    manifest = build_v4_manifest(bundle, fingerprint)
    write_v4_manifest(manifest, directory)
    return bundle, manifest


def verify_pilot(base_dir: Path | None = None) -> bool:
    """Recompute the on-disk fingerprint and compare it with the manifest.

    A pilot is not frozen, so this is a *reproducibility* check rather than a
    freeze check: it answers "do the committed files still match the manifest
    that describes them", not "is anyone allowed to change them".
    """
    directory = benchmark_dir(base_dir)
    manifest = read_v4_manifest(directory)
    return manifest.get("pilot_sha256") == compute_pilot_fingerprint(
        directory, V4_PILOT_SPLIT
    )


def diagnostics(bundle: V4DatasetBundle) -> dict:
    """The section 12.A composition report, computed from the bundle itself."""
    resolvable = [
        entry for entry in bundle.ground_truth if entry.correct_relationship is not None
    ]
    ambiguous = [
        entry for entry in bundle.ground_truth if entry.correct_relationship is None
    ]
    per_archetype: dict[str, dict[str, int]] = {}
    for entry in bundle.ground_truth:
        slot = per_archetype.setdefault(
            entry.archetype, {"cases": 0, "resolvable": 0, "ambiguous": 0}
        )
        slot["cases"] += 1
        if entry.correct_relationship is not None:
            slot["resolvable"] += 1
        else:
            slot["ambiguous"] += 1

    return {
        "total_cases": len(bundle.ground_truth),
        "resolvable": len(resolvable),
        "intentionally_ambiguous": len(ambiguous),
        "records": bundle.record_counts(),
        "total_records": bundle.total_record_count(),
        "per_archetype": dict(sorted(per_archetype.items())),
        "per_family": bundle.family_counts(),
        "per_required_composition": bundle.composition_counts(),
        "per_candidate_count": bundle.candidate_count_buckets(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FinRecon benchmark v4 PILOT generator (additive; never touches v3)"
    )
    parser.add_argument("--write", action="store_true", help="generate and write the pilot")
    parser.add_argument(
        "--verify", action="store_true", help="recompute the pilot fingerprint"
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="build in memory and print the composition report; writes nothing",
    )
    args = parser.parse_args(argv)

    if not (args.write or args.verify or args.diagnostics):
        parser.error("pass one of --write, --verify or --diagnostics")

    if args.verify:
        if verify_pilot():
            print("v4-pilot fingerprint matches manifests/v4-pilot.json")
        else:
            print("v4-pilot fingerprint MISMATCH against manifest", file=sys.stderr)
            return 1

    if args.write:
        bundle, manifest = generate_pilot()
        print(
            f"generated split={V4_PILOT_SPLIT!r} seed={V4_PILOT_SEED} "
            f"cases={len(bundle.ground_truth)} records={bundle.total_record_count()}"
        )
        print(f"pilot sha256={manifest['pilot_sha256']}  (NOT frozen)")
        print(json.dumps(diagnostics(bundle), indent=2, sort_keys=True))
        return 0

    if args.diagnostics:
        bundle = build_v4_dataset(V4_PILOT_SEED, TARGET_ARCHETYPE_COUNTS, V4_PILOT_SPLIT)
        print(json.dumps(diagnostics(bundle), indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
