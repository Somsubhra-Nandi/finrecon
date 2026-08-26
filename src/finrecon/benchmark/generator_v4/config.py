"""Frozen constants for the benchmark v4 **pilot**.

Deliberately small. A pilot exists to be read case by case, and 64 cases at
three to five candidates each is roughly 250 settlements — inspectable in an
afternoon, which a 900-case set is not.

Nothing here is shared with :mod:`finrecon.benchmark.generator.config`. The
v3 seeds, tier counts and manifest filename are untouched, and this module
never writes to ``v3.json``.
"""

from __future__ import annotations

from finrecon.benchmark.generator.config import benchmark_dir, repo_root
from finrecon.benchmark.generator.token_contract import is_token_safe

GENERATOR_V4_VERSION = "4.0.0-pilot"
"""Identity of the v4 pilot generator.

The ``-pilot`` suffix is load-bearing, not decorative: DESIGN.md 5.1's
freeze protocol makes "frozen" a claim about a reporting artifact, and this
is not one. A later frozen v4 would be ``4.1.0`` or ``5.0.0`` with its own
manifest, its own seed and its own fingerprint.
"""

V4_PILOT_SPLIT = "v4-pilot"
"""On-disk split name: ``benchmark/datasets/v4-pilot/``."""

V4_PILOT_SLUG = "v4pilot"
"""Token-safe slug interpolated into generated identifiers.

Same discipline as benchmark v3's ``frozeneval``: a slug carrying a
tokenizer delimiter would make every identifier built from it unreachable as
a whole token, which is the v3.0.0 defect. Validated below rather than
assumed.
"""

V4_PILOT_MANIFEST_FILENAME = "v4-pilot.json"
"""A file of its own. ``v1.json``, ``v2.json`` and ``v3.json`` are never opened."""

V4_PILOT_SEED = 4242
"""Fixed before any case was generated and before any baseline was run.

Chosen for being obviously arbitrary rather than for an outcome; the pilot's
diagnostics are reported for this seed and for the seed-stability check the
tests run, not for a seed selected after seeing results.
"""

TARGET_ARCHETYPE_COUNTS: dict[str, int] = {
    "single_fragment_control": 8,
    "conjunction_pair": 12,
    "conjunction_wide": 8,
    "conjunction_triple": 6,
    "amount_reference_hop": 10,
    "conflict_stale_reference": 4,
    "conflict_context_resolves": 4,
    "ambiguity_no_discriminator": 6,
    "ambiguity_conjunction_incomplete": 6,
}
"""64 cases across nine archetypes. See ``benchmark/V4-PILOT.md`` for the rationale."""

TOTAL_TARGET_CASES = sum(TARGET_ARCHETYPE_COUNTS.values())

GROSS_MIN = 50_000
"""Rs 500, in paise. Same floor as v3, so amount ranges are comparable."""

GROSS_MAX = 5_000_000
"""Rs 50,000, in paise. Same ceiling as v3 — and still far below the
Rs 1,00,000 elevated-scrutiny rung, so the value ladder does not bind on this
pilot any more than it binds on v3."""


def v4_split_slug(split: str = V4_PILOT_SPLIT) -> str:
    """Token-safe slug for ``split``, validated against the tokenization contract."""
    if split != V4_PILOT_SPLIT:
        raise ValueError(
            f"the v4 pilot generator only knows split {V4_PILOT_SPLIT!r}, not {split!r}"
        )
    if not is_token_safe(V4_PILOT_SLUG):
        raise ValueError(
            f"v4 split slug {V4_PILOT_SLUG!r} does not survive tokenization as one token"
        )
    return V4_PILOT_SLUG


__all__ = [
    "GENERATOR_V4_VERSION",
    "GROSS_MAX",
    "GROSS_MIN",
    "TARGET_ARCHETYPE_COUNTS",
    "TOTAL_TARGET_CASES",
    "V4_PILOT_MANIFEST_FILENAME",
    "V4_PILOT_SEED",
    "V4_PILOT_SLUG",
    "V4_PILOT_SPLIT",
    "benchmark_dir",
    "repo_root",
    "v4_split_slug",
]
