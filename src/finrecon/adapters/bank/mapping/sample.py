"""A hard-bounded, read-only excerpt of a clean bank CSV.

This is the *only* thing a mapping proposal is allowed to see. Every bound
below is a constant in this module rather than a caller's argument, so no
call site can widen what leaves the process by passing a bigger number.

Scope, stated plainly: the input is assumed to be an already-clean
transaction table -- row 1 is the header, the rows after it are
transactions. There is no preamble scan, no footer scan, no account-block
detection and no delimiter sniffing. A statement that is not already in
that shape simply produces a sample that does not support a mapping, which
is the correct fail-closed answer while header-location support does not
exist.

**Why bound it at all.** A column mapping is inferable from the header row
plus a handful of rows showing each column's *shape*; nothing about the
statement's hundredth transaction helps. Sending more would put an
operator's whole transaction history through a third-party endpoint to
answer a question that a five-row excerpt already answers, which is a
privacy cost with no corresponding benefit.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from ..schema.normalize import normalize_headers
from ..schema.signature import BankSchemaReadError

MAX_SAMPLE_ROWS = 5
"""Data rows included in a proposal sample. Five, and not configurable.

Enough to show each column's shape (a date's separator, whether a money
column is zero-filled or blank on its inactive side, whether a reference
column is populated), and small enough that the excerpt stays reviewable by
eye in the audit record.
"""

MAX_CELL_CHARS = 80
"""Per-cell truncation. A narration's *shape* is visible well inside this."""

MAX_HEADERS = 64
"""Header cells forwarded. A wider table is refused rather than silently cut."""

MAX_SAMPLE_CHARS = 6000
"""Total characters across headers and sampled cells, after truncation.

The last bound, enforced by dropping whole sample rows from the end (never
by cutting a row in half, which would misrepresent a column as empty).
"""

TRUNCATION_MARK = "…"


def _truncate(cell: str) -> str:
    text = cell.replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= MAX_CELL_CHARS:
        return text
    return text[: MAX_CELL_CHARS - 1] + TRUNCATION_MARK


@dataclass(frozen=True)
class BankCsvSample:
    """Headers plus a bounded row excerpt, and how it was bounded."""

    raw_headers: tuple[str, ...]
    normalized_headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    """Sampled data rows, padded/trimmed to ``len(raw_headers)`` so a row
    and the header tuple can always be zipped without a length check."""
    delimiter: str
    encoding: str
    total_data_rows_scanned: int
    """Data rows read while collecting the sample -- never the file's total."""
    rows_dropped_for_size: int = 0
    cells_truncated: int = 0

    def column(self, header: str) -> tuple[str, ...]:
        """Every sampled value for one header, by exact header name."""
        try:
            index = self.raw_headers.index(header)
        except ValueError:
            return ()
        return tuple(row[index] for row in self.rows)

    def prompt_payload(self) -> dict:
        """The exact structure handed to a model. Nothing else is sent."""
        return {
            "headers": list(self.raw_headers),
            "sample_rows": [list(row) for row in self.rows],
        }

    def bounds_payload(self) -> dict:
        """What was withheld, for the proposal's audit record."""
        return {
            "max_sample_rows": MAX_SAMPLE_ROWS,
            "max_cell_chars": MAX_CELL_CHARS,
            "max_sample_chars": MAX_SAMPLE_CHARS,
            "sample_rows_sent": len(self.rows),
            "data_rows_scanned": self.total_data_rows_scanned,
            "rows_dropped_for_size": self.rows_dropped_for_size,
            "cells_truncated": self.cells_truncated,
        }

    @property
    def character_count(self) -> int:
        return sum(len(h) for h in self.raw_headers) + sum(
            len(cell) for row in self.rows for cell in row
        )


class BankCsvSampleError(BankSchemaReadError):
    """The file is not a clean tabular CSV this phase can sample."""


def read_sample(
    raw_bytes: bytes, *, delimiter: str = ",", encoding: str = "utf-8-sig"
) -> BankCsvSample:
    """Read the header row and a bounded excerpt of data rows. Read-only.

    Reads at most :data:`MAX_SAMPLE_ROWS` non-empty data rows and stops --
    it does not scan to the end of the file, so a large upload costs the
    same as a small one here.
    """
    try:
        text = raw_bytes.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        raise BankCsvSampleError(
            f"could not decode statement bytes as {encoding!r}: {exc}"
        ) from exc

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header_row = next(reader)
    except StopIteration:
        raise BankCsvSampleError(
            "no header row found; a mapping proposal maps columns by header "
            "name and requires one"
        ) from None

    headers = tuple(_truncate(cell) for cell in header_row)
    if not headers or all(h == "" for h in headers):
        raise BankCsvSampleError("the header row is empty; there are no columns to map")
    if len(headers) > MAX_HEADERS:
        raise BankCsvSampleError(
            f"the header row declares {len(headers)} columns; this phase maps "
            f"at most {MAX_HEADERS}"
        )

    width = len(headers)
    rows: list[tuple[str, ...]] = []
    truncated = 0
    scanned = 0
    for record in reader:
        if len(rows) >= MAX_SAMPLE_ROWS:
            break
        scanned += 1
        if not any(cell.strip() for cell in record):
            continue
        cells = []
        for index in range(width):
            original = record[index] if index < len(record) else ""
            cell = _truncate(original)
            if cell != original.strip():
                truncated += 1
            cells.append(cell)
        rows.append(tuple(cells))

    dropped = 0
    sample = BankCsvSample(
        raw_headers=headers,
        normalized_headers=normalize_headers(headers),
        rows=tuple(rows),
        delimiter=delimiter,
        encoding=encoding,
        total_data_rows_scanned=scanned,
        cells_truncated=truncated,
    )
    # Enforced by dropping whole rows from the end. Cutting a row short
    # would present a populated column as empty, which is exactly the kind
    # of false observation a debit/credit proposal must never be handed.
    while sample.character_count > MAX_SAMPLE_CHARS and sample.rows:
        dropped += 1
        sample = BankCsvSample(
            raw_headers=sample.raw_headers,
            normalized_headers=sample.normalized_headers,
            rows=sample.rows[:-1],
            delimiter=sample.delimiter,
            encoding=sample.encoding,
            total_data_rows_scanned=scanned,
            rows_dropped_for_size=dropped,
            cells_truncated=truncated,
        )
    if sample.character_count > MAX_SAMPLE_CHARS:
        raise BankCsvSampleError(
            "the header row alone exceeds the proposal sample size bound; "
            "this statement is too wide to map automatically"
        )
    return sample


__all__ = [
    "MAX_CELL_CHARS",
    "MAX_HEADERS",
    "MAX_SAMPLE_CHARS",
    "MAX_SAMPLE_ROWS",
    "BankCsvSample",
    "BankCsvSampleError",
    "read_sample",
]
