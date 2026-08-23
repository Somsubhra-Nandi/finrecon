"""Stage 1 generator configuration — frozen constants (DESIGN.md §5.1, §9 Stage 1).

Seeds and target tier counts are committed here, once, per the freeze
protocol: DEV is for tuning, FROZEN-EVAL is for final reporting, and both
come from the same taxonomy with different deterministic seeds.
"""

from __future__ import annotations

from pathlib import Path

GENERATOR_VERSION = "2.0.0"
"""Benchmark v2. See ``benchmark/manifests/CHANGELOG.md`` for why v1 was superseded.

Major bump, not a patch: v2 changes what a T2 case *is*. v1's T2 records
are not a subset of v2's, and the two frozen-eval fingerprints are not
comparable.
"""

MANIFEST_FILENAME = "v2.json"
"""The manifest this generator writes and verifies against.

``v1.json`` stays on disk untouched, holding v1's seeds, counts and
frozen-eval SHA-256, so the correction is auditable rather than a silent
rewrite.
"""

DEV_SEED = 42
FROZEN_EVAL_SEED = 1337
"""Unchanged from v1, deliberately.

The v2 construct changes the *data*, so generator-version separation is
already sufficient to distinguish the artifacts — a seed change would add
churn without adding independence. Keeping them also removes any
suspicion that seeds were shopped for: they were fixed before Stage 2
ran, and no matcher result influenced them (DESIGN.md §5.1).
"""

# DESIGN.md §5.2 target case counts (v4 benchmark shape).
TARGET_TIER_COUNTS: dict[str, int] = {
    "T0": 350,
    "T1": 300,
    "T2": 200,
    "T3": 40,
}

TOTAL_TARGET_CASES = sum(TARGET_TIER_COUNTS.values())

SPLITS = ("dev", "frozen-eval")


def repo_root() -> Path:
    """Walk up from this file until a directory containing ``pyproject.toml`` is found."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("could not locate repo root (no pyproject.toml found above generator module)")


def benchmark_dir(base_dir: Path | None = None) -> Path:
    return (base_dir or repo_root()) / "benchmark"
