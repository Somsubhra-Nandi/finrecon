"""What survives of a T2 reference, and what it would take to recover it.

Benchmark v2 makes T2 mean one thing precisely: *structured financial
evidence alone leaves more than one plausible settlement, and only the
degraded reference separates them*. Enforcing that needs an explicit,
generator-side answer to a question v1 never had to ask — **could a
correct recovery of this surviving fragment identify this UTR, and only
this UTR?**

This module is that answer, and nothing more.

Each ladder category gets one ``recovers`` predicate: given the fragment
the bank statement preserved and a candidate settlement's UTR, is the
fragment *consistent with* that UTR having produced it? The predicates are
deliberately **permissive** — an anagram test rather than a permutation
solver, a prefix test rather than a length-aware one — because they are
used to prove a negative. A T2 case is only accepted when the true UTR is
the sole candidate the fragment is consistent with, so a predicate that
errs towards saying "yes, consistent" makes that acceptance *harder*, not
easier. A stricter predicate could accidentally certify a case as
unambiguous when a real recovery step would still be torn between two
settlements.

Three things this module is not:

* It is not a recovery *algorithm*. It answers "could recovery work here",
  not "here is the recovered reference". Nothing implements extraction.
* It is not reachable from the reconciliation path. It lives under
  ``benchmark/generator/`` and is imported only by the generator and its
  tests; Stage-2 code has no dependency on it, and must not gain one.
* It is not tuned against any model. No model exists (DESIGN.md §9,
  Stage 3), and benchmark v2 is frozen before one does.
"""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.benchmark.generator.token_contract import (
    fold as _fold,
    is_usable_direct_key,
    narration_tokens,
)

MASK_CHAR = "*"
SEPARATOR = "-"

__all__ = [
    "MASK_CHAR",
    "SEPARATOR",
    "SurvivingReference",
    "narration_tokens",
    "recovery_is_consistent",
]


# --------------------------------------------------------------------------
# Per-category recovery-consistency predicates
# --------------------------------------------------------------------------


def _recovers_truncated_left(fragment: str, utr: str) -> bool:
    """A trailing suffix survived: consistent with any UTR ending in it."""
    return bool(fragment) and _fold(utr).endswith(_fold(fragment))


def _recovers_truncated_right(fragment: str, utr: str) -> bool:
    """A leading prefix survived: consistent with any UTR starting with it."""
    return bool(fragment) and _fold(utr).startswith(_fold(fragment))


def _recovers_masked(fragment: str, utr: str) -> bool:
    """Same length, and every unmasked position agrees."""
    if len(fragment) != len(utr):
        return False
    return all(f == MASK_CHAR or _fold(f) == _fold(u) for f, u in zip(fragment, utr))


def _recovers_separator_altered(fragment: str, utr: str) -> bool:
    """Separators were inserted or removed; stripping them must restore the UTR."""
    return _fold(fragment).replace(SEPARATOR, "") == _fold(utr)


def _recovers_reordered(fragment: str, utr: str) -> bool:
    """Chunks were permuted, so only the character multiset is guaranteed.

    Chunk boundaries are *not* recoverable from the permuted string — a
    trailing short chunk can land in the middle, after which re-chunking
    yields pieces the original never had. So the honest test is an anagram
    test, which is the widest predicate consistent with the transform and
    therefore the safest one for proving uniqueness.
    """
    return len(fragment) == len(utr) and sorted(_fold(fragment)) == sorted(_fold(utr))


def _recovers_embedded_in_narration(fragment: str, utr: str) -> bool:
    """The reference is intact but glued into a longer token: substring containment."""
    return bool(utr) and _fold(utr) in _fold(fragment)


_RECOVERY_PREDICATES = {
    "truncated_left": _recovers_truncated_left,
    "truncated_right": _recovers_truncated_right,
    "masked": _recovers_masked,
    "separator_altered": _recovers_separator_altered,
    "reordered": _recovers_reordered,
    "embedded_in_narration": _recovers_embedded_in_narration,
}


def recovery_is_consistent(category_id: str, surviving_evidence: str, utr: str | None) -> bool:
    """Could a correct recovery of ``surviving_evidence`` have produced ``utr``?

    ``utr is None`` (a settlement carrying no reference at all) is always
    ``False``: there is nothing for the evidence to be consistent with.
    """
    if utr is None:
        return False
    try:
        predicate = _RECOVERY_PREDICATES[category_id]
    except KeyError as exc:
        raise KeyError(
            f"no T2 recovery-consistency predicate for degradation category {category_id!r}"
        ) from exc
    return predicate(surviving_evidence, utr)


@dataclass(frozen=True)
class SurvivingReference:
    """The degraded reference evidence one T2 bank narration actually carries."""

    category_id: str
    evidence: str
    """The exact substring of the narration a recovery step would have to work from."""
    narration: str
    narration_template_id: str

    def is_present_in_narration(self) -> bool:
        return self.evidence in self.narration

    def is_directly_usable(self, identifier: str) -> bool:
        """True if some whole narration token already equals ``identifier`` — i.e. a T0 direct key.

        Delegates to the shared token contract so T2's "no direct key
        survives" invariant and T0's "a direct key survives" admission test
        are the same predicate read in opposite directions.
        """
        return is_usable_direct_key(self.narration, identifier)

    def recovers(self, utr: str | None) -> bool:
        return recovery_is_consistent(self.category_id, self.evidence, utr)
