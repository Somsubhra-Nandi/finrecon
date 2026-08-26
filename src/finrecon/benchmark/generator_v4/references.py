r"""Structured UTR construction with deliberately controlled reach sets.

A v4 conjunction case needs, for a given clue fragment, an exactly known set
of candidates the declared relations reach from it. That is only tractable if
the references have structure, so this module gives them the structure real
ones have:

.. code-block:: text

    A X I S C N 1 1 | 3 7 | 8 6 3 7 2 7
    \_____________/   \_/   \_________/
       head (8)       mid     tail (6)
    bank(4)+channel(2)+2 digits

That is the shape of ``AXISCN1153863727`` -- the literal ``utr`` value from
Razorpay's own published ``settlement.processed`` webhook example, already
captured verbatim in the frozen Stage-0 narration library. Same-bank,
same-day references genuinely share a leading run; trailing runs collide by
chance. So "two settlements whose references share a head but not a tail" is
an ordinary fact about a settlement file, not a puzzle constructed to defeat
a matcher.

The no-zero rule
----------------

Every digit in a generated v4 reference is drawn from ``1-9``; no ``0``
appears anywhere in a head, mid or tail. This is not cosmetic. Generated
record identifiers are zero-padded six-digit ordinals
(``setl_v4pilot_000123``), so **every** suffix of an identifier that is four
characters or longer contains a ``0`` at this scale. A digit run from a v4
narration therefore cannot be a suffix of any record identifier, which
removes an entire class of accidental discriminators -- a numeric fragment
that was meant to be a partial reference silently standing in a
``suffix_of_reference`` relation to exactly one settlement ID.

The invariant checker verifies reach sets against the real
:mod:`finrecon.evidence.reference` relations over the real Stage-2 candidate
set regardless. This module only makes violations rare; it is not what makes
them impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

BANK_CODES: tuple[str, ...] = (
    "AXIS",
    "HDFC",
    "ICIC",
    "SBIN",
    "KKBK",
    "UTIB",
    "PUNB",
    "YESB",
    "IDIB",
    "BARB",
)
"""Four-letter bank identifiers of the shape Indian rails actually use.

Chosen to be recognisable IFSC-style prefixes rather than invented strings,
and long enough (ten) that a case needing four mutually distinct banks never
runs out.
"""

CHANNEL_CODES: tuple[str, ...] = ("CN", "NB", "IM", "UP", "RT")
"""Rail/product markers: NEFT credit, net-banking, IMPS, UPI, RTGS."""

DIGITS_NO_ZERO: tuple[str, ...] = tuple("123456789")

HEAD_LENGTH = 8
MID_LENGTH = 2
TAIL_LENGTH = 6
UTR_LENGTH = HEAD_LENGTH + MID_LENGTH + TAIL_LENGTH


@dataclass(frozen=True)
class StructuredUtr:
    """One reference, kept in its three parts so clues can be cut precisely."""

    bank: str
    channel: str
    head_digits: str
    """The two digits that complete the eight-character head."""
    mid: str
    tail: str

    @property
    def head(self) -> str:
        return f"{self.bank}{self.channel}{self.head_digits}"

    @property
    def value(self) -> str:
        return f"{self.head}{self.mid}{self.tail}"

    @property
    def digits(self) -> str:
        return f"{self.head_digits}{self.mid}{self.tail}"

    def long_prefix(self, length: int) -> str:
        """The first ``length`` characters. A right-truncated reference."""
        if not HEAD_LENGTH < length < UTR_LENGTH:
            raise ValueError(
                f"a long prefix must be longer than the head ({HEAD_LENGTH}) and shorter "
                f"than the whole reference ({UTR_LENGTH}); got {length}"
            )
        return self.value[:length]

    def reordered(self, rng: Random, *, avoid: frozenset[str] = frozenset()) -> str:
        """The reference with its ten digits permuted, bank and channel kept.

        A character-multiset-preserving rendering, which is the ``reordered``
        category of the frozen Stage-0 degradation ladder read at digit
        granularity rather than chunk granularity. Bank and channel stay in
        place because a permutation that moved them would produce a string no
        bank would print.

        ``avoid`` holds every reference in the case. The rendering must equal
        none of them: a permutation that landed exactly on a decoy's UTR would
        stand in the ``exact`` relation to it and pin sixteen characters,
        turning a clue meant to reach three candidates into one that
        discriminates the wrong one.

        The first digit is also kept away from every candidate's seventh
        character, so that no prefix of this field extending past the
        bank-and-channel run reaches anything at all.
        """
        forbidden_seventh = {value[6] for value in avoid if len(value) > 6}
        digits = list(self.digits)
        for _ in range(_MAX_DRAWS):
            rng.shuffle(digits)
            permuted = "".join(digits)
            if permuted == self.digits:
                continue
            if permuted[0] in forbidden_seventh:
                continue
            rendered = f"{self.bank}{self.channel}{permuted}"
            if rendered in avoid:
                continue
            if permuted[-3:] in {value[-3:] for value in avoid}:
                continue
            return rendered
        raise ReferenceConstructionError(
            f"could not permute the digits of {self.value!r} into a rendering distinct "
            f"from every reference in the case"
        )


class ReferenceConstructionError(RuntimeError):
    """A reference set with the requested reach structure could not be built."""


def _digits(rng: Random, count: int) -> str:
    return "".join(rng.choice(DIGITS_NO_ZERO) for _ in range(count))


def make_utr(rng: Random, *, bank: str | None = None, channel: str | None = None) -> StructuredUtr:
    return StructuredUtr(
        bank=bank if bank is not None else rng.choice(BANK_CODES),
        channel=channel if channel is not None else rng.choice(CHANNEL_CODES),
        head_digits=_digits(rng, 2),
        mid=_digits(rng, MID_LENGTH),
        tail=_digits(rng, TAIL_LENGTH),
    )


_MAX_DRAWS = 128
"""Bounded, declared redraw budget. An exhausted budget raises rather than looping."""


def distinct_bank(rng: Random, used: set[str]) -> str:
    """A bank code no other reference in this case uses.

    Distinct banks are how a decoy is kept *out* of a head clue's reach: the
    head clue's own four-character prefix is the bank code, so a decoy at a
    different bank cannot be reached by the head clue or by any of its
    sub-fragments.
    """
    available = [code for code in BANK_CODES if code not in used]
    if not available:
        raise ReferenceConstructionError(
            f"every bank code is already in use ({sorted(used)}); a case cannot need more"
        )
    return rng.choice(available)


def distinct_tail(rng: Random, avoid: set[str], *, min_distinct_suffix: int = 3) -> str:
    """A tail whose every suffix of length >= ``min_distinct_suffix`` is unused.

    A four-character fragment is the shortest the evidence floor admits, so
    two tails agreeing in their last three characters are already close
    enough to matter: the tail clue is cut at six characters, and its
    sub-fragments run down to four. Requiring the last three to differ keeps
    every admissible sub-fragment of one tail out of every other tail.
    """
    for _ in range(_MAX_DRAWS):
        candidate = _digits(rng, TAIL_LENGTH)
        suffix = candidate[-min_distinct_suffix:]
        if all(existing[-min_distinct_suffix:] != suffix for existing in avoid):
            return candidate
    raise ReferenceConstructionError(
        f"could not draw a tail whose last {min_distinct_suffix} digits differ from "
        f"{sorted(avoid)} in {_MAX_DRAWS} attempts"
    )


def distinct_digit_pair(rng: Random, avoid: set[str]) -> str:
    """Two digits no other reference in this case uses in the same slot.

    Used for both the two head digits and the two mid digits, which are the
    only two-digit slots a reference has. Distinctness matters most in the mid:
    two candidates agreeing on head, mid and tail would be the same reference.
    """
    for _ in range(_MAX_DRAWS):
        candidate = _digits(rng, 2)
        if candidate not in avoid:
            return candidate
    raise ReferenceConstructionError(
        f"could not draw a digit pair outside {sorted(avoid)} in {_MAX_DRAWS} attempts"
    )


def non_anagram_mid(rng: Random, reference_mid: str) -> str:
    """A mid whose digits are not a permutation of ``reference_mid``.

    Used to keep the head-and-tail sharing decoy out of the reordered clue's
    reach: that decoy already agrees with the true reference everywhere
    except the mid, so if its mid were an anagram the whole reference would
    be one, and the three-clue construction would collapse to two.
    """
    target = sorted(reference_mid)
    for _ in range(_MAX_DRAWS):
        candidate = _digits(rng, MID_LENGTH)
        if sorted(candidate) != target:
            return candidate
    raise ReferenceConstructionError("could not draw a non-anagram mid")


def anagram_of(
    rng: Random,
    reference: StructuredUtr,
    *,
    keep_head: bool,
    keep_tail: bool,
) -> StructuredUtr:
    """A reference with the same character multiset, sharing head or tail as asked.

    Exactly one of ``keep_head`` / ``keep_tail`` is expected to be true. The
    ten digits are re-dealt so that the kept part is preserved verbatim and
    the other part is guaranteed to differ, which is what places the result
    in two of the three clue reach sets and outside the third.
    """
    if keep_head == keep_tail:
        raise ValueError("an anagram decoy keeps exactly one of the head and the tail")

    pool = sorted(reference.digits)
    for _ in range(_MAX_DRAWS):
        if keep_head:
            fixed = reference.head_digits
            remaining = _remove_multiset(pool, fixed)
            if remaining is None:
                break
            rest = list(remaining)
            rng.shuffle(rest)
            mid = "".join(rest[:MID_LENGTH])
            tail = "".join(rest[MID_LENGTH:])
            if tail[-3:] == reference.tail[-3:]:
                continue
            return StructuredUtr(
                bank=reference.bank,
                channel=reference.channel,
                head_digits=fixed,
                mid=mid,
                tail=tail,
            )

        fixed = reference.tail
        remaining = _remove_multiset(pool, fixed)
        if remaining is None:
            break
        rest = list(remaining)
        rng.shuffle(rest)
        head_digits = "".join(rest[:2])
        mid = "".join(rest[2:])
        if head_digits == reference.head_digits:
            continue
        return StructuredUtr(
            bank=reference.bank,
            channel=reference.channel,
            head_digits=head_digits,
            mid=mid,
            tail=fixed,
        )

    raise ReferenceConstructionError(
        f"could not build an anagram of {reference.value!r} "
        f"(keep_head={keep_head}, keep_tail={keep_tail})"
    )


def _remove_multiset(pool: list[str], taken: str) -> list[str] | None:
    """``pool`` minus one copy of each character of ``taken``; ``None`` if impossible."""
    remaining = list(pool)
    for character in taken:
        if character not in remaining:
            return None
        remaining.remove(character)
    return remaining


__all__ = [
    "BANK_CODES",
    "CHANNEL_CODES",
    "DIGITS_NO_ZERO",
    "HEAD_LENGTH",
    "MID_LENGTH",
    "TAIL_LENGTH",
    "UTR_LENGTH",
    "ReferenceConstructionError",
    "StructuredUtr",
    "anagram_of",
    "distinct_bank",
    "distinct_digit_pair",
    "distinct_tail",
    "make_utr",
    "non_anagram_mid",
]
