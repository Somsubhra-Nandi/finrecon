"""Declarative bank CSV column mapping.

A :class:`BankCsvProfile` is a closed, explicit statement of how one bank's
CSV export maps onto canonical :class:`finrecon.models.BankRecord` --
nothing about it is inferred from the file's contents at parse time. See
``notes/RAZORPAY-INPUT-GAP.md`` §4.1's ``bank/csv_profile.py`` sketch,
which this module implements, and ``csv_parser.py`` for the adapter that
consumes it.

**Why a profile at all, and not one giant parser.** Every Indian bank's
statement export uses different column names, a different declared date
format, and a different debit/credit convention (two separate amount
columns vs. one amount column plus a direction marker). None of that can
be safely sniffed -- see :mod:`finrecon.adapters.bank.csv_parser`'s module
docstring, "Absolutely no guessing", for why. A profile makes every one of
those choices an explicit, reviewable, per-bank declaration instead.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DebitCreditColumns:
    """Separate debit/credit rupee-text columns.

    Per row, exactly one of ``debit_column``/``credit_column`` is expected
    to be populated -- see
    :func:`finrecon.adapters.bank.csv_parser._resolve_direction_and_amount`
    for the exact populated/neither/both semantics, which are never
    guessed.
    """

    debit_column: str
    credit_column: str


@dataclass(frozen=True)
class AmountDirectionColumns:
    """One amount column plus an explicit direction-marker column.

    ``credit_values``/``debit_values`` are the exact raw strings (compared
    verbatim, case-sensitive, no normalization) the source uses to mark
    each direction -- e.g. ``frozenset({"CR"})``/``frozenset({"DR"})``.
    Direction is never inferred from the amount's sign or magnitude: a
    profile must declare the marker vocabulary it has actually observed.
    """

    amount_column: str
    direction_column: str
    credit_values: frozenset[str]
    debit_values: frozenset[str]


MoneyColumns = DebitCreditColumns | AmountDirectionColumns


@dataclass(frozen=True)
class BankCsvProfile:
    """A closed mapping from one bank's CSV export to canonical fields.

    ``value_date_format`` is a :meth:`datetime.datetime.strptime` format
    string, matched exactly and never re-attempted under a different
    format on failure (task brief §2 -- "absolutely no date sniffing").

    ``reference_id_column`` is optional: when a row's value there is
    non-empty, it is the preferred source of :attr:`BankRecord.bank_record_id`
    (namespaced by ``profile_id``); when absent (column not declared, or
    empty for a given row), identity falls back to a deterministic hash of
    the row's declared identity fields -- see
    :mod:`finrecon.adapters.bank.csv_parser`'s "Row identity" section for
    the exact scheme and its documented limitation.

    ``currency_column`` is optional: when declared, every row's raw value
    there must equal ``currency`` exactly or the row is rejected
    (``unsupported_currency``). :class:`finrecon.models.BankRecord` itself
    carries no currency field (the canonical model assumes single-currency
    reconciliation throughout), so ``currency`` is enforced/recorded at the
    ingestion boundary only, never written into the canonical record.

    ``thousands_separator``, when declared (e.g. ``","``), is stripped
    literally from money text before decimal conversion -- an explicit,
    lossless formatting declaration, not a parsing guess. Left ``None`` by
    default: a profile must opt in only once its source format is known to
    use one.
    """

    profile_id: str
    currency: str
    value_date_column: str
    value_date_format: str
    narration_column: str
    money_columns: MoneyColumns
    reference_id_column: str | None = None
    currency_column: str | None = None
    thousands_separator: str | None = None
    delimiter: str = ","
    encoding: str = "utf-8"

    def declared_columns(self) -> frozenset[str]:
        """Every CSV header column this profile reads.

        Used by the parser both to fail fast on a profile/header mismatch
        (a declared column absent from the actual file) and to record
        ``dropped_fields`` -- every header column present in the file but
        outside this closed set.
        """
        columns = {self.value_date_column, self.narration_column}
        if isinstance(self.money_columns, DebitCreditColumns):
            columns.add(self.money_columns.debit_column)
            columns.add(self.money_columns.credit_column)
        else:
            columns.add(self.money_columns.amount_column)
            columns.add(self.money_columns.direction_column)
        if self.reference_id_column is not None:
            columns.add(self.reference_id_column)
        if self.currency_column is not None:
            columns.add(self.currency_column)
        return frozenset(columns)


__all__ = [
    "AmountDirectionColumns",
    "BankCsvProfile",
    "DebitCreditColumns",
    "MoneyColumns",
]
