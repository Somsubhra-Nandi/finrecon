"""Stage 1 generator configuration — frozen constants (DESIGN.md §5.1, §9 Stage 1).

Seeds and target tier counts are committed here, once, per the freeze
protocol: DEV is for tuning, FROZEN-EVAL is for final reporting, and both
come from the same taxonomy with different deterministic seeds.
"""

from __future__ import annotations

from pathlib import Path

from finrecon.benchmark.generator.token_contract import is_token_safe

GENERATOR_VERSION = "3.0.0"
"""Benchmark v3. See ``benchmark/manifests/CHANGELOG.md`` for the full history.

Major bump, not a patch: v3 changes the generated identifier text on
FROZEN-EVAL, so its dataset bytes and fingerprint are not comparable with
v2's. The *cases* are otherwise the same — same seeds, same RNG streams,
same amounts, dates and degradations — but a fingerprint either matches or
it does not, and a reader must not be invited to diff across the boundary.

v3 corrects a T0 admission defect: 175 of FROZEN-EVAL's 350 T0 cases
carried a settlement ID containing a ``-``, which the declared tokenization
treats as a delimiter, so no whole token ever equalled the ID and the
direct-key matcher could not reach them. DEV was unaffected because its
slug (``dev``) has no delimiter, which is precisely why the defect survived
a green test suite.
"""

MANIFEST_FILENAME = "v3.json"
"""The manifest this generator writes and verifies against.

``v1.json`` and ``v2.json`` stay on disk untouched, holding their own
seeds, counts and frozen-eval SHA-256, so each correction is auditable
rather than a silent rewrite.
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

SPLIT_ID_SLUGS: dict[str, str] = {
    "dev": "dev",
    "frozen-eval": "frozeneval",
}
"""Token-safe slug used inside generated record and case identifiers.

Split *names* are the on-disk directory names, the CLI argument and the
manifest keys, and they keep their human-readable hyphen. Split *slugs* are
what gets interpolated into identifiers, and they must survive the declared
tokenization whole — see
:mod:`finrecon.benchmark.generator.token_contract`.

Benchmark v3 introduced the distinction. Before it, ``frozen-eval`` went
straight into ``setl_frozen-eval_000042``, whose ``-`` is a tokenizer
delimiter, so a settlement ID printed into a T0 narration could never be
matched as a whole token. The slug is *not* derived by stripping
punctuation programmatically: it is an explicit committed mapping, so
adding a split is a deliberate decision about its identifier text rather
than a silent transformation.
"""


def split_id_slug(split: str) -> str:
    """Token-safe slug for ``split``, validated against the tokenization contract."""
    try:
        slug = SPLIT_ID_SLUGS[split]
    except KeyError as exc:
        raise ValueError(
            f"unknown split {split!r}; add an explicit token-safe slug to SPLIT_ID_SLUGS"
        ) from exc
    if not is_token_safe(slug):
        raise ValueError(
            f"split slug {slug!r} for split {split!r} does not survive tokenization as one "
            f"token; identifiers built from it could not be matched as a direct key"
        )
    return slug


def repo_root() -> Path:
    """Walk up from this file until a directory containing ``pyproject.toml`` is found."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("could not locate repo root (no pyproject.toml found above generator module)")


def benchmark_dir(base_dir: Path | None = None) -> Path:
    return (base_dir or repo_root()) / "benchmark"
