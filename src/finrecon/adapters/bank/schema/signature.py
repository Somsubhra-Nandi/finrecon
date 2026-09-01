"""A deterministic, observable-structure-only schema signature.

What a signature is allowed to see: the header row as written, the same
row after :mod:`~finrecon.adapters.bank.schema.normalize`'s conservative
representation fixes, how many fields that row has, the delimiter it was
read under, and the encoding family it was decoded with. That is the whole
list.

What it deliberately does not see: any transaction value. No repository
evidence says a built-in profile needs row content to be recognised, and a
signature that folded values in would make recognition depend on which
statement period someone happened to export. There is also no similarity
score anywhere in this module -- a signature either equals another
signature or it does not.

``digest`` hashes the canonical form through
:func:`finrecon.ledger.audit.canonical_json`, the codebase's existing
byte-stable JSON, rather than a new serialization invented here.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass

from finrecon.ledger.audit import canonical_json

from .normalize import encoding_family, normalize_headers


class BankSchemaReadError(RuntimeError):
    """The header row could not be read at all under the given assumptions.

    Distinct from "did not match a profile": this means the bytes could not
    be decoded with the given encoding, or contain no header row, so there
    is nothing to compare.
    """


@dataclass(frozen=True)
class SchemaSignature:
    """One observed header row, under one declared read.

    ``raw_headers`` is kept alongside the normalized form so audit and the
    UI can always explain a decision in the file's own words -- the
    normalized tuple is a comparison key, never a substitute for what the
    file actually says.
    """

    raw_headers: tuple[str, ...]
    normalized_headers: tuple[str, ...]
    delimiter: str
    encoding: str
    """Encoding *family* (see :func:`~.normalize.encoding_family`), not the
    raw codec name the profile happened to spell."""

    @property
    def field_count(self) -> int:
        return len(self.raw_headers)

    def canonical_payload(self) -> dict:
        """The exact structure ``digest`` hashes -- normalized form only."""
        return {
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "field_count": self.field_count,
            "normalized_headers": list(self.normalized_headers),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            canonical_json(self.canonical_payload()).encode("utf-8")
        ).hexdigest()

    def matches_exactly(self, other: SchemaSignature) -> bool:
        """The header row as written is identical, under the same read.

        Compares ``raw_headers`` verbatim -- including case, spacing and
        punctuation -- so this tier asserts nothing at all beyond "these are
        the same characters in the same order".
        """
        return (
            self.raw_headers == other.raw_headers
            and self.delimiter == other.delimiter
            and self.encoding == other.encoding
        )

    def matches_normalized(self, other: SchemaSignature) -> bool:
        """Identical after BOM/whitespace/case fixes only, order preserved."""
        return (
            self.normalized_headers == other.normalized_headers
            and self.delimiter == other.delimiter
            and self.encoding == other.encoding
        )


def signature_from_headers(
    headers: tuple[str, ...], *, delimiter: str, encoding: str
) -> SchemaSignature:
    """Build a signature from an already-known header tuple.

    Used for the *expected* side: a registry entry declares its header row
    rather than having one read out of a file.
    """
    return SchemaSignature(
        raw_headers=tuple(headers),
        normalized_headers=normalize_headers(headers),
        delimiter=delimiter,
        encoding=encoding_family(encoding),
    )


def read_signature(
    raw_bytes: bytes, *, delimiter: str, encoding: str
) -> SchemaSignature:
    """Read the observed header row out of uploaded bytes. Read-only.

    Reads the *first* row only. Preamble/account-block scanning is
    deliberately absent: this phase recognises schemas whose header row is
    where the existing parser already expects it, and no built-in profile
    shipped here needs anything else. A statement with a preamble simply
    does not match, which is the correct fail-closed answer until
    header-location support exists.
    """
    try:
        text = raw_bytes.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise BankSchemaReadError(
            f"could not decode statement bytes as {encoding!r}: {exc}"
        ) from exc
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        headers = next(reader)
    except StopIteration:
        raise BankSchemaReadError(
            "no header row found; schema recognition maps columns by header "
            "name and requires one"
        ) from None
    return signature_from_headers(
        tuple(headers), delimiter=delimiter, encoding=encoding
    )


__all__ = [
    "BankSchemaReadError",
    "SchemaSignature",
    "read_signature",
    "signature_from_headers",
]
