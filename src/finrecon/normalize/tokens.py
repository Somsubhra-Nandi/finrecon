"""Exact reference tokenization of bank narration.

**This is not narration parsing.** It performs one purely lexical
operation: split the raw narration on characters that cannot appear inside
a canonical identifier, and return the resulting substrings unchanged
(plus an upper-cased comparison key). It extracts no meaning, recognises
no reference *shape*, and has no notion of what a UTR looks like.

That distinction is the whole point. DESIGN.md §5.2 reserves degraded
reference recovery — truncated, masked, reordered or noise-embedded
tokens — for later stages, and Stage 2 is explicitly forbidden from
regex-extracting a reference out of degraded narration. Splitting on
delimiters and testing whole tokens for *exact equality* against known
identifiers cannot recover a degraded reference by construction: a
truncated, masked, reordered or prefixed token is simply not equal to the
identifier, so it never matches here.

The delimiter class deliberately keeps ``_`` inside tokens, because
canonical settlement IDs (``setl_dev_000123``) contain underscores while
narration separators in the frozen library are ``-``, ``/``, ``*``, ``:``
and spaces.
"""

from __future__ import annotations

import re

_DELIMITERS = re.compile(r"[^A-Za-z0-9_]+")


def tokenize_narration(narration: str) -> tuple[str, ...]:
    """Split ``narration`` into candidate identifier tokens, order preserved.

    Returns the substrings exactly as they appear in the source (no case
    folding, no character removal). Empty tokens are dropped.
    """
    return tuple(token for token in _DELIMITERS.split(narration) if token)


def token_key(token: str) -> str:
    """Case-insensitive comparison key for exact identifier matching.

    Upper-casing is the only transform applied. It is safe here because
    both sides of every comparison — narration tokens and canonical
    identifiers — go through this same function, and the canonical ID
    space contains no two identifiers that differ only by case.
    """
    return token.upper()
