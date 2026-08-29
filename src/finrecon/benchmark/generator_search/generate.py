"""CLI for generating and verifying bounded-search-v1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finrecon.benchmark.generator_search.config import BENCHMARK_NAME, benchmark_dir
from finrecon.benchmark.generator_search.dataset import build_search_dataset
from finrecon.benchmark.generator_search.manifest import (
    build_manifest,
    compute_search_fingerprint,
    manifest_path,
    write_manifest,
)
from finrecon.benchmark.generator_search.serialize import write_search_dataset


def generate(base_dir: Path | None = None) -> dict:
    directory = benchmark_dir(base_dir)
    bundle = build_search_dataset()
    write_search_dataset(bundle, directory)
    fingerprint = compute_search_fingerprint(directory)
    manifest = build_manifest(bundle, fingerprint)
    write_manifest(directory, manifest)
    return manifest


def verify(base_dir: Path | None = None) -> bool:
    directory = benchmark_dir(base_dir)
    manifest = json.loads(manifest_path(directory).read_text(encoding="utf-8"))
    return manifest["benchmark_sha256"] == compute_search_fingerprint(directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic bounded-search challenge")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    if not args.write and not args.verify:
        parser.error("pass --write or --verify")
    if args.write:
        manifest = generate()
        print(json.dumps(manifest, indent=2, sort_keys=True))
    if args.verify and not verify():
        print(f"{BENCHMARK_NAME} fingerprint mismatch", file=sys.stderr)
        return 1
    if args.verify:
        print(f"{BENCHMARK_NAME} fingerprint matches manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
