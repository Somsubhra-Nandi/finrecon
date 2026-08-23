"""The benchmark's own statement of what makes a reference *usable* as a direct key.

DESIGN.md §5.2 grades the tiers on reference survival, and the top of the
ladder is stated in terms of usability, not presence:

    direct key survives          -> T0
    no key, structure survives   -> T1
    key survives only degraded   -> T2
    nothing distinguishing       -> T3

"Survives" has to mean *survives the normalization the reconciliation path
actually applies*. A reference that is present in a narration but cannot be
reached by the declared tokenization is not a usable direct key — it is a
degraded one, which is T2's definition, not T0's.

Benchmark v3 exists because that distinction was previously enforced with
substring containment on the T0 side, which is strictly weaker than the
whole-token equality the matcher requires. See
``benchmark/manifests/CHANGELOG.md`` v3.0.0.

**Deliberately reimplemented, not imported.** The delimiter class and the
case-folding rule below duplicate
:mod:`finrecon.normalize.tokens`. That duplication is the point: a
generator assertion that imported the production tokenizer would restate
the code it is trying to check, and the two agreeing would prove nothing.
Kept as an independent statement of the same contract, a divergence makes a
case fail to *generate* rather than silently change tier — which is how the
v3 defect should have surfaced and did not.
"""

from __future__ import annotations

import re
from functools import lru_cache

TOKEN_DELIMITERS = re.compile(r"[^A-Za-z0-9_]+")
"""Mirrors the delimiter class the Stage-2 tokenizer declares.

Note what is *not* in the character class: ``-`` is a delimiter, so a
hyphen inside an identifier splits it into two tokens and no whole-token
comparison can ever reach the original. That is exactly the v3 defect.
"""


@lru_cache(maxsize=4096)
def narration_tokens(narration: str) -> tuple[str, ...]:
    """Whole tokens of a narration. Cached: batch-wide checks re-split the same strings."""
    return tuple(token for token in TOKEN_DELIMITERS.split(narration) if token)


def fold(value: str) -> str:
    """Case-folding used for comparison, matching the production key derivation."""
    return value.upper()


@lru_cache(maxsize=4096)
def folded_token_set(narration: str) -> frozenset[str]:
    return frozenset(fold(token) for token in narration_tokens(narration))


def is_token_safe(identifier: str) -> bool:
    """True if ``identifier`` survives tokenization whole.

    An identifier is token-safe when splitting it on the declared delimiter
    class yields exactly itself — i.e. it contains no delimiter and is not
    empty. Generated identifiers that are expected to be reachable as a
    direct key must satisfy this; ones that never appear in a narration
    need not.
    """
    return narration_tokens(identifier) == (identifier,)


def is_usable_direct_key(narration: str, identifier: str | None) -> bool:
    """True if ``identifier`` is reachable from ``narration`` as one whole token.

    This is the T0 admission test. It is intentionally the same shape as
    the production direct-key lookup — tokenize the narration, fold, and
    require an exact whole-token hit — so a case the generator certifies as
    T0 is one the matcher can actually resolve by direct key.

    Substring containment is *not* sufficient and must not be substituted
    here: ``setl_frozen-eval_000042`` is a substring of its own narration
    while tokenizing to ``setl_frozen`` + ``eval_000042``, so containment
    certifies a T0 case the matcher then cannot reach.
    """
    if not identifier:
        return False
    return fold(identifier) in folded_token_set(narration)
