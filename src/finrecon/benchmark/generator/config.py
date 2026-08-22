"""Stage 1 generator configuration — frozen constants (DESIGN.md §5.1, §9 Stage 1).

Seeds and target tier counts are committed here, once, per the freeze
protocol: DEV is for tuning, FROZEN-EVAL is for final reporting, and both
come from the same taxonomy with different deterministic seeds.
"""

from __future__ import annotations

from pathlib import Path

GENERATOR_VERSION = "1.0.0"

DEV_SEED = 42
FROZEN_EVAL_SEED = 1337

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
