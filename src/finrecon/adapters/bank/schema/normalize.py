"""Conservative header normalization -- representation only, never meaning.

This module exists to answer exactly one question: *are these two header
tuples the same header, written down slightly differently?* It is allowed
to look past a UTF-8 BOM, stray whitespace, and letter case, because none
of those change which column a bank named. It is **not** allowed to look
past punctuation, abbreviations, synonyms, edit distance, reordering, or a
missing/extra column -- every one of those is a claim about *meaning*, and
a wrong claim there silently re-maps somebody's debit column.

That boundary is the whole safety argument for automatic profile
selection: the detector may only recognise a schema a human already
reviewed and versioned. Anything weaker belongs to the (not yet built)
unknown-schema proposal layer, where a human confirms the mapping.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable

BOM = "\ufeff"
"""The UTF-8 byte-order mark, as a character.

``utf-8-sig`` strips it while decoding; a file decoded as plain ``utf-8``
(which is what a profile declaring ``encoding="utf-8"`` asks for) keeps it
glued to the first header. Stripping it here is a representation fix, not
an encoding guess -- see :mod:`finrecon.json_text` for the same reasoning
at the JSON boundary.
"""

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_header(header: str) -> str:
    """Normalize one header cell: BOM, whitespace shape, and case only.

    Order of operations matters: the BOM is removed first (otherwise it
    survives as a leading non-space character), then internal whitespace
    runs collapse to one space, then the result is trimmed, then case is
    folded. ``casefold`` rather than ``lower`` so non-ASCII headers compare
    the way Unicode says they should.
    """
    return _WHITESPACE_RUN.sub(" ", header.replace(BOM, "")).strip().casefold()


def normalize_headers(headers: Iterable[str]) -> tuple[str, ...]:
    """Normalize a header row, **preserving order**.

    Order is preserved deliberately: two profiles whose columns differ only
    in order are two different schemas, and this layer must never let them
    match. See :mod:`finrecon.adapters.bank.schema.signature`.
    """
    return tuple(normalize_header(header) for header in headers)


def encoding_family(encoding: str) -> str:
    """Canonical codec name, so ``utf-8``/``UTF_8``/``utf-8-sig`` agree.

    A BOM-tolerant codec and its plain sibling read the same bytes into the
    same characters once :func:`normalize_header` has dropped the mark, so
    treating them as one family avoids a spurious mismatch. Unknown codec
    names are returned casefolded rather than raising: this value is only
    ever compared for equality and reported for audit, and a profile whose
    declared encoding does not exist will fail loudly at decode time
    anyway.
    """
    try:
        canonical = codecs.lookup(encoding).name
    except LookupError:
        return encoding.strip().casefold()
    return "utf-8" if canonical in {"utf-8", "utf-8-sig"} else canonical


__all__ = ["BOM", "encoding_family", "normalize_header", "normalize_headers"]
