"""Stage 1 benchmark generator CLI.

    python -m finrecon.benchmark.generator.generate --split dev
    python -m finrecon.benchmark.generator.generate --split frozen-eval
    python -m finrecon.benchmark.generator.generate --split both
    python -m finrecon.benchmark.generator.generate --verify-frozen

Writes datasets/ground-truth for the requested split(s) under
``benchmark/`` at the repo root, and (when both splits are generated, or
explicitly on a frozen-eval-only run) refreshes ``manifests/v1.json``
including the frozen-eval SHA-256 fingerprint.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finrecon.benchmark.generator.config import (
    DEV_SEED,
    FROZEN_EVAL_SEED,
    TARGET_TIER_COUNTS,
    benchmark_dir,
)
from finrecon.benchmark.generator.dataset import build_dataset
from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.benchmark.generator.manifest import (
    build_manifest,
    read_manifest,
    update_manifest_for_split,
    write_manifest,
)
from finrecon.benchmark.generator.serialize import write_dataset


def _read_manifest_if_present(bdir: Path) -> dict | None:
    try:
        return read_manifest(bdir)
    except FileNotFoundError:
        return None


def generate_split(split: str, seed: int, base_dir: Path | None = None):
    """Write one split's dataset + ground truth, and merge its results into the manifest.

    A single-split run never overwrites the *other* split's fields in the
    manifest — it only updates the slice for ``split``, computing the
    frozen-eval SHA-256 whenever ``split == "frozen-eval"``.
    """
    bdir = benchmark_dir(base_dir)
    bundle = build_dataset(split, seed, TARGET_TIER_COUNTS)
    write_dataset(bundle, bdir)

    frozen_hash = compute_fingerprint(bdir, split="frozen-eval") if split == "frozen-eval" else None
    existing = _read_manifest_if_present(bdir)
    manifest = update_manifest_for_split(existing, bundle, seed, TARGET_TIER_COUNTS, frozen_hash)
    write_manifest(manifest, bdir)
    return bundle


def refresh_manifest(base_dir: Path | None = None) -> dict:
    """Rebuild both splits in-memory and rewrite the manifest, including the frozen-eval hash.

    Rebuilding in-memory (rather than trusting whatever is currently on
    disk) guarantees the manifest's tier/record counts always describe
    the generator's actual current output for the committed seeds.
    """
    dev_bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    frozen_bundle = build_dataset("frozen-eval", FROZEN_EVAL_SEED, TARGET_TIER_COUNTS)

    bdir = benchmark_dir(base_dir)
    write_dataset(dev_bundle, bdir)
    write_dataset(frozen_bundle, bdir)

    frozen_hash = compute_fingerprint(bdir, split="frozen-eval")
    manifest = build_manifest(
        dev_bundle, frozen_bundle, DEV_SEED, FROZEN_EVAL_SEED, TARGET_TIER_COUNTS, frozen_hash
    )
    write_manifest(manifest, bdir)
    return manifest


def verify_frozen(base_dir: Path | None = None) -> bool:
    bdir = benchmark_dir(base_dir)
    manifest = read_manifest(bdir)
    recorded_hash = manifest.get("frozen_eval_sha256")
    actual_hash = compute_fingerprint(bdir, split="frozen-eval")
    return recorded_hash == actual_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FinRecon Stage 1 benchmark generator")
    parser.add_argument("--split", choices=("dev", "frozen-eval", "both"), default=None)
    parser.add_argument("--verify-frozen", action="store_true")
    args = parser.parse_args(argv)

    if args.verify_frozen:
        ok = verify_frozen()
        if ok:
            print("frozen-eval SHA-256 matches manifest")
            return 0
        print("frozen-eval SHA-256 MISMATCH against manifest", file=sys.stderr)
        return 1

    if args.split is None:
        parser.error("--split is required unless --verify-frozen is given")

    if args.split == "both":
        manifest = refresh_manifest()
        print(f"generated dev + frozen-eval; frozen-eval sha256={manifest['frozen_eval_sha256']}")
        return 0

    seed = DEV_SEED if args.split == "dev" else FROZEN_EVAL_SEED
    bundle = generate_split(args.split, seed)
    print(
        f"generated split={args.split!r} seed={seed} cases={len(bundle.ground_truth)} "
        f"records={bundle.total_record_count()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
