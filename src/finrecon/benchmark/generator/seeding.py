"""Deterministic seed derivation for the Stage 1 generator.

DESIGN.md's determinism requirements (Stage 1 exit condition; §4.6) demand
that every random operation uses an explicit seeded RNG instance, that no
hidden global randomness leaks in, and that the same seed always produces
the same output.

``random.Random`` instances are cheap and fully isolated from the global
``random`` module, so the pattern throughout the generator is: derive a
per-case integer seed deterministically from (root seed, split, case
index, purpose), then construct a fresh ``random.Random`` from it. Python's
own ``hash()`` is not used anywhere here — it is salted per-process unless
``PYTHONHASHSEED`` is fixed, which would silently break reproducibility.
"""

from __future__ import annotations

import hashlib
from random import Random


def derive_seed(*parts: object) -> int:
    """Deterministically fold ``parts`` into a single integer seed.

    Uses SHA-256 over the parts' string forms rather than Python's builtin
    ``hash()``, which is randomized per-process for strings unless
    ``PYTHONHASHSEED`` is pinned — an implicit dependency this generator
    must not have.
    """
    joined = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def case_rng(root_seed: int, split: str, case_index: int) -> Random:
    """A fresh, isolated RNG for one case, deterministic in (root_seed, split, case_index)."""
    return Random(derive_seed(root_seed, split, "case", case_index))


def plan_rng(root_seed: int, split: str) -> Random:
    """A fresh, isolated RNG used only to build the shuffled case-tier plan."""
    return Random(derive_seed(root_seed, split, "plan"))
