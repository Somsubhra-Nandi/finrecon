"""RAW BANK CSV -> profile-driven -> CANONICAL BankRecord[] + MANIFEST.

Deterministically transforms one bank's CSV export into canonical
:class:`finrecon.models.BankRecord` records, driven entirely by an explicit
:class:`~finrecon.adapters.bank.csv_profile.BankCsvProfile` -- see that
module for the declaration this parser consumes, and
``notes/RAZORPAY-INPUT-GAP.md`` §4 for the design this continues.

**Absolutely no guessing.** Three specific temptations this module refuses,
each because guessing wrong here corrupts financial evidence silently:

* **No date-format sniffing.** ``03/07/2026`` parses without error under
  both ``DD/MM/YYYY`` and ``MM/DD/YYYY`` -- and disagrees on which day it
  names. :meth:`~finrecon.adapters.bank.csv_profile.BankCsvProfile.value_date_format`
  is matched exactly, once, never retried under a second format.
* **No float money math.** Bank CSV money is rupee decimal text; converting
  it goes through :meth:`finrecon.models.money.Paise.from_rupees`, the same
  exact-decimal boundary conversion the rest of the codebase uses -- never
  ``float(text) * 100``.
* **No direction inference from amount sign/magnitude.** Two-column
  (debit/credit) and one-column-plus-marker (amount/direction) sources are
  both supported, but which column(s) mean what, and which marker strings
  mean credit vs. debit, are exactly what the profile declares -- see
  :func:`_resolve_direction_and_amount`.

**Row-scoped, not group-scoped.** Unlike the Razorpay settlement recon
adapter (which reconstructs one aggregate ``Settlement`` from many rows and
so must decide whether to quarantine the *whole aggregate*), one bank CSV
row maps to at most one canonical ``BankRecord`` with no aggregation step.
So a bad row is simply rejected and the batch continues (task brief §10) --
there is no blocking/non-blocking conflict classification to make, because
nothing here ever contaminates a larger object. The one exception is
duplicate identity resolution (see "Row identity" below), which can affect
more than one row at once, and is still handled by exclusion, never by
guessing which copy is authoritative.

**Row identity.** Two disjoint regimes, deliberately treated differently
(see :func:`_resolve_row_identities`):

* **Reference-identified rows** -- ``profile.reference_id_column``
  declared and non-empty for the row -- get a *trustworthy* identity,
  ``f"{profile_id}:ref:{reference}"``. Rows sharing one are assumed to be
  the same real-world transaction: byte-identical copies (a re-fetch or
  pagination overlap) collapse to one record; copies that *disagree* on
  content are a genuine contradiction and are excluded, fail-closed,
  never resolved by guessing which copy is right.
* **Fallback rows** -- no reference column declared, or empty for this
  row -- get an identity derived from a SHA-256 over the row's declared
  identity fields (value date, narration, money-column raw text), but a
  content match here is deliberately **never** treated as evidence of
  being the same transaction. Two bank rows with identical value date,
  narration, amount and direction (e.g. two separate ₹500 UPI payments
  posted the same day with the same narration) are a completely ordinary,
  legitimate occurrence -- collapsing them would silently delete a real
  transaction. So every fallback row survives as its own ``BankRecord``;
  rows sharing a content digest are distinguished only by an explicit
  occurrence index (``f"{profile_id}:content:{digest}:{occurrence:04d}"``,
  assigned in row order) so ids stay unique within the statement, never by
  asserting they are duplicates of each other. A profile should still
  declare ``reference_id_column`` whenever the source provides one --
  content-hash identity is a fallback of last resort, not a substitute.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum

from finrecon.models import BankRecord, BankRecordDirection
from finrecon.models.money import MoneyError, Paise

from .csv_profile import (
    AmountDirectionColumns,
    BankCsvProfile,
    DebitCreditColumns,
    InactiveSideMarker,
)
from .manifest import BankIngestConflict, BankIngestManifest, BankRowProvenance


class BankCsvDecodeError(RuntimeError):
    """The file could not even be read into rows.

    Reserved for failures that are not about any one row's data -- the
    declared ``encoding`` cannot decode the bytes, there is no header row
    to map columns against, or the profile declares a column the header
    does not actually have. Any of these means row-by-row processing
    cannot meaningfully begin, so this is raised rather than folded into
    per-row rejection.
    """


class _RowRejected(Exception):
    """Internal control-flow signal: this one row cannot produce a
    canonical ``BankRecord``. Carries the ``(kind, detail)`` recorded on
    the resulting :class:`RejectedBankRow`."""

    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class RejectedBankRow:
    """One CSV data row that did not become a canonical ``BankRecord``.

    Always row-scoped (see the module docstring) except for
    ``conflicting_duplicate_bank_record_id``, whose ``reason`` is shared by
    every row in the conflicting group -- each still gets its own entry
    here, keyed by its own ``row_index``.
    """

    row_index: int
    row_fingerprint: str
    reason: str
    detail: str
    raw_fields: tuple[tuple[str, str | None], ...]
    """The row's raw column values (sorted by column name), for audit."""


@dataclass(frozen=True)
class BankCsvAdapterResult:
    """The adapter's output. ``records`` is the decision-eligible set --
    safe to feed to ``loader.py``-shaped consumers. ``rejected_rows`` and
    ``manifest`` are ingestion-review-only, exactly as with the Razorpay
    adapter's ``quarantined_settlements``/``manifest``: never read by the
    reconciliation path.
    """

    records: tuple[BankRecord, ...]
    rejected_rows: tuple[RejectedBankRow, ...]
    manifest: BankIngestManifest
    conflicts: tuple[BankIngestConflict, ...] = field(default=())


def _clean_money_text(profile: BankCsvProfile, raw: str | None) -> str:
    text = (raw or "").strip()
    if profile.thousands_separator:
        text = text.replace(profile.thousands_separator, "")
    return text


class _SideState(Enum):
    """What one debit/credit column says about its own side, for a row.

    Classification is separated from direction resolution on purpose: a
    side's *populated-ness* and its *value* are two different questions,
    and conflating them is exactly the bug this distinction closes. Under
    :attr:`~finrecon.adapters.bank.csv_profile.InactiveSideMarker.EMPTY_OR_ZERO`,
    ``"0.0"`` is populated text but an inactive side, which can only be
    decided after the money text is exactly parsed -- never before.
    """

    ABSENT = "absent"
    """Textually empty after cleaning."""
    ZERO = "zero"
    """Parsed exactly, to zero paise."""
    ACTIVE = "active"
    """Parsed exactly, to a non-zero amount."""
    MALFORMED = "malformed"
    """Not a value this parser will convert to exact paise."""


@dataclass(frozen=True)
class _SideReading:
    """One side's classification plus the exact ``Paise`` it produced.

    ``amount`` is carried so the direction resolver reuses this object
    rather than parsing the same text a second time (a second parse is
    both wasted work and a place for the two reads to disagree).
    """

    state: _SideState
    amount: Paise | None
    text: str
    error: str | None = None


def _classify_money_side(profile: BankCsvProfile, raw: str | None) -> _SideReading:
    """Classify one debit/credit column value, using the exact money parser.

    No ``float`` anywhere: rupee text goes through
    :meth:`finrecon.models.money.Paise.from_rupees`, the same exact-decimal
    boundary conversion the rest of the codebase uses.

    ``ArithmeticError`` is caught alongside ``MoneyError`` because
    ``Decimal("Infinity")`` is a *valid* decimal that then overflows on
    conversion to ``int``; text like that is malformed money as far as this
    boundary is concerned, and quarantining the row is the correct answer.
    Malformed text is never silently coerced into "inactive".
    """
    text = _clean_money_text(profile, raw)
    if text == "":
        return _SideReading(_SideState.ABSENT, None, text)
    try:
        amount = Paise.from_rupees(text)
    except MoneyError as exc:
        return _SideReading(_SideState.MALFORMED, None, text, str(exc))
    except ArithmeticError as exc:
        return _SideReading(
            _SideState.MALFORMED,
            None,
            text,
            f"{text!r} is not a finite amount representable as integer paise: {exc}",
        )
    state = _SideState.ZERO if int(amount) == 0 else _SideState.ACTIVE
    return _SideReading(state, amount, text)


def _resolve_zero_filled_debit_credit(
    profile: BankCsvProfile, row: Mapping[str, str | None], columns: DebitCreditColumns
) -> tuple[BankRecordDirection, Paise]:
    """Direction for a source that zero-fills its inactive side.

    Only reached when the profile declares
    :attr:`~finrecon.adapters.bank.csv_profile.InactiveSideMarker.EMPTY_OR_ZERO`.
    An explicit zero and an empty cell mean the same thing here -- "not
    this side" -- because that is what the profile says this source does.
    Everything else keeps the same fail-closed shape as the empty-only
    path: two active sides have no declared meaning, no active side is not
    a financial movement, and malformed text is rejected rather than
    reinterpreted.

    Note what is *not* relaxed: a non-zero value is active whatever its
    sign, exactly as under the empty-only reading. This declares how a
    source marks an inactive side; it does not redefine money.
    """
    debit = _classify_money_side(profile, row.get(columns.debit_column))
    credit = _classify_money_side(profile, row.get(columns.credit_column))

    for column, reading in ((columns.debit_column, debit), (columns.credit_column, credit)):
        if reading.state is _SideState.MALFORMED:
            raise _RowRejected("malformed_money", f"{column!r}: {reading.error}")

    debit_active = debit.state is _SideState.ACTIVE
    credit_active = credit.state is _SideState.ACTIVE

    if debit_active and credit_active:
        raise _RowRejected(
            "both_debit_and_credit_populated",
            f"both {columns.debit_column!r} ({debit.text!r}) and "
            f"{columns.credit_column!r} ({credit.text!r}) carry a non-zero "
            "amount; this profile declares that an inactive side is empty or "
            "zero, so it has no documented meaning for two active sides and "
            "the row is rejected rather than guessed at",
        )
    if debit_active:
        assert debit.amount is not None  # ACTIVE implies an exactly parsed amount
        return BankRecordDirection.DEBIT, debit.amount
    if credit_active:
        assert credit.amount is not None
        return BankRecordDirection.CREDIT, credit.amount
    raise _RowRejected(
        "neither_amount_populated",
        f"neither {columns.debit_column!r} ({debit.text!r}) nor "
        f"{columns.credit_column!r} ({credit.text!r}) carries a non-zero "
        "amount; under this profile's declared empty-or-zero inactive side "
        "that is not a financial movement this adapter can canonicalize",
    )


def _resolve_direction_and_amount(
    profile: BankCsvProfile, row: Mapping[str, str | None]
) -> tuple[BankRecordDirection, Paise]:
    """Task brief §4: deterministic direction, never guessed.

    Two-column case: exactly one of debit/credit populated determines
    direction; neither populated is not a financial movement this adapter
    can canonicalize; both populated has no profile-documented meaning, so
    it is rejected rather than resolved by preference. What counts as
    "populated" is the profile's ``inactive_side_marker`` declaration, not
    a guess: under the default ``EMPTY_ONLY`` any text at all is populated,
    while ``EMPTY_OR_ZERO`` routes to
    :func:`_resolve_zero_filled_debit_credit`, where an exactly-parsed zero
    marks the inactive side.

    One-column case: the direction marker must be exactly one of the
    profile's declared ``credit_values``/``debit_values`` -- never inferred
    from the amount's sign.
    """
    columns = profile.money_columns
    if isinstance(columns, DebitCreditColumns):
        if columns.inactive_side_marker is InactiveSideMarker.EMPTY_OR_ZERO:
            return _resolve_zero_filled_debit_credit(profile, row, columns)
        debit_text = _clean_money_text(profile, row.get(columns.debit_column))
        credit_text = _clean_money_text(profile, row.get(columns.credit_column))
        debit_populated = debit_text != ""
        credit_populated = credit_text != ""
        if debit_populated and credit_populated:
            raise _RowRejected(
                "both_debit_and_credit_populated",
                f"both {columns.debit_column!r} ({debit_text!r}) and "
                f"{columns.credit_column!r} ({credit_text!r}) are populated; "
                "this profile does not declare a documented meaning for "
                "that combination, so the row is rejected rather than "
                "guessed at",
            )
        if not debit_populated and not credit_populated:
            raise _RowRejected(
                "neither_amount_populated",
                f"neither {columns.debit_column!r} nor "
                f"{columns.credit_column!r} is populated; not a financial "
                "movement this adapter can canonicalize",
            )
        text = debit_text if debit_populated else credit_text
        direction = BankRecordDirection.DEBIT if debit_populated else BankRecordDirection.CREDIT
        try:
            return direction, Paise.from_rupees(text)
        except MoneyError as exc:
            raise _RowRejected("malformed_money", str(exc)) from exc

    amount_text = _clean_money_text(profile, row.get(columns.amount_column))
    if amount_text == "":
        raise _RowRejected(
            "missing_amount",
            f"{columns.amount_column!r} is empty; a canonical BankRecord "
            "requires an amount",
        )
    direction_raw = (row.get(columns.direction_column) or "").strip()
    is_credit = direction_raw in columns.credit_values
    is_debit = direction_raw in columns.debit_values
    if is_credit and is_debit:
        raise _RowRejected(
            "ambiguous_direction_value",
            f"{direction_raw!r} in {columns.direction_column!r} appears in "
            "both this profile's declared credit_values and debit_values; "
            "the profile is misconfigured, not the source row",
        )
    if not is_credit and not is_debit:
        raise _RowRejected(
            "unrecognized_direction_value",
            f"{direction_raw!r} in {columns.direction_column!r} is not one "
            "of this profile's declared credit_values or debit_values; "
            "never guessed from context",
        )
    try:
        amount = Paise.from_rupees(amount_text)
    except MoneyError as exc:
        raise _RowRejected("malformed_money", str(exc)) from exc
    return (BankRecordDirection.CREDIT if is_credit else BankRecordDirection.DEBIT), amount


def _parse_value_date(profile: BankCsvProfile, row: Mapping[str, str | None]) -> date:
    """Task brief §2/§6: exact declared format, source-backed, never sniffed."""
    raw = (row.get(profile.value_date_column) or "").strip()
    if raw == "":
        raise _RowRejected(
            "missing_value_date",
            f"{profile.value_date_column!r} is empty; a canonical "
            "BankRecord requires a value_date",
        )
    try:
        return datetime.strptime(raw, profile.value_date_format).date()
    except ValueError as exc:
        raise _RowRejected(
            "invalid_value_date_format",
            f"{raw!r} does not match the declared format "
            f"{profile.value_date_format!r} exactly; never re-attempted "
            "under a different format",
        ) from exc


def _narration(profile: BankCsvProfile, row: Mapping[str, str | None]) -> str:
    """Task brief §5: copied byte/text-identically, never rewritten."""
    raw = row.get(profile.narration_column)
    if not raw:
        raise _RowRejected(
            "missing_narration",
            f"{profile.narration_column!r} is empty; a canonical "
            "BankRecord requires narration",
        )
    return raw


def _check_currency(profile: BankCsvProfile, row: Mapping[str, str | None]) -> None:
    if profile.currency_column is None:
        return
    raw = (row.get(profile.currency_column) or "").strip()
    if raw != profile.currency:
        raise _RowRejected(
            "unsupported_currency",
            f"{profile.currency_column!r} = {raw!r} does not match this "
            f"profile's declared currency {profile.currency!r}",
        )


def _content_identity_key(profile: BankCsvProfile, row: Mapping[str, str | None]) -> str:
    """A deterministic hash of one row's declared identity fields.

    This is a **grouping key**, not a transaction identity: two rows
    producing the same key merely *look* financially identical (same
    value date, narration and money-column text) -- that is not evidence
    they are the same physical transaction, so this key alone must never
    be used to collapse rows. See :func:`_resolve_row_identities`, which
    disambiguates same-key rows with an occurrence index instead of
    treating them as duplicates.
    """
    columns = profile.money_columns
    if isinstance(columns, DebitCreditColumns):
        money_fields = (
            row.get(columns.debit_column) or "",
            row.get(columns.credit_column) or "",
        )
    else:
        money_fields = (
            row.get(columns.amount_column) or "",
            row.get(columns.direction_column) or "",
        )
    identity_fields = (
        row.get(profile.value_date_column) or "",
        row.get(profile.narration_column) or "",
        *money_fields,
    )
    payload = json.dumps(list(identity_fields), ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{profile.profile_id}:content:{digest[:32]}"


def _reference_identity(profile: BankCsvProfile, row: Mapping[str, str | None]) -> str | None:
    """The row's trustworthy, source-provided identity, or ``None`` when
    the profile declares no reference column, or this row's value there is
    empty -- either way, the row falls back to content-keyed handling in
    :func:`_resolve_row_identities`."""
    if profile.reference_id_column is None:
        return None
    raw_ref = row.get(profile.reference_id_column)
    ref = raw_ref.strip() if raw_ref else ""
    if not ref:
        return None
    return f"{profile.profile_id}:ref:{ref}"


def _row_fingerprint(row: Mapping[str, str | None]) -> str:
    """SHA-256 over every raw column value in the row (sorted keys) --
    the row's stable content identity for exact-duplicate collapse, over
    the *entire* row, not just the columns the profile projects (same
    convention as ``RazorpayReconRow.fingerprint``)."""
    payload = json.dumps({str(k): v for k, v in row.items()}, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve_row_identities(
    profile: BankCsvProfile, rows: list[tuple[int, dict[str, str | None]]]
) -> tuple[dict[int, str], tuple[str, ...], tuple[BankIngestConflict, ...], set[int]]:
    """Assign a ``bank_record_id`` to every row that should be built into a
    ``BankRecord``, and decide which rows (if any) must be excluded
    entirely. See the module docstring's "Row identity" section.

    Returns ``(assigned_ids, duplicate_fingerprints, conflicts,
    excluded_conflict_indices)``. A row index absent from ``assigned_ids``
    and absent from ``excluded_conflict_indices`` was a collapsed exact
    reference-duplicate copy -- fully accounted for via its surviving
    copy's id and ``duplicate_fingerprints``, no separate handling needed.

    Reference-identified rows (task brief §3) are grouped by their
    trustworthy identity: a group with one distinct row-fingerprint is the
    same transaction observed twice and collapses to its first (by row
    order) copy; a group with more than one distinct fingerprint is a
    genuine contradiction and every row in it is excluded (fail closed,
    never resolved by preference).

    Fallback rows (task brief §4) are **never** grouped or collapsed by
    content -- each gets its own id, disambiguated from same-content
    siblings purely by an occurrence index assigned in row order, so ids
    stay unique within this statement without asserting any two rows are
    the same transaction. This is the fix for the bug this task exists to
    close: previously, fallback rows were run through the *same*
    identity-collapsing logic as reference rows, so two genuinely distinct
    transactions that merely looked alike (same date/narration/amount/
    direction) were silently merged into one canonical record.
    """
    reference_groups: dict[str, list[tuple[int, dict[str, str | None], str]]] = defaultdict(list)
    fallback_rows: list[tuple[int, dict[str, str | None]]] = []

    for row_index, row in rows:
        reference = _reference_identity(profile, row)
        if reference is not None:
            reference_groups[reference].append((row_index, row, _row_fingerprint(row)))
        else:
            fallback_rows.append((row_index, row))

    assigned_ids: dict[int, str] = {}
    duplicate_fingerprints: list[str] = []
    conflicts: list[BankIngestConflict] = []
    excluded_conflict_indices: set[int] = set()

    for reference, group in sorted(reference_groups.items()):
        fingerprints = {fp for _, _, fp in group}
        if len(fingerprints) == 1:
            ordered = sorted(group, key=lambda item: item[0])
            first_index, _, _ = ordered[0]
            assigned_ids[first_index] = reference
            duplicate_fingerprints.extend(fp for _, _, fp in ordered[1:])
        else:
            excluded_conflict_indices.update(idx for idx, _, _ in group)
            conflicts.append(
                BankIngestConflict(
                    kind="conflicting_duplicate_bank_record_id",
                    detail=(
                        f"bank_record_id {reference!r} appears {len(group)} times "
                        f"with {len(fingerprints)} distinct row contents; excluded "
                        "from output rather than guessing which copy is "
                        "authoritative"
                    ),
                    row_indices=tuple(sorted(idx for idx, _, _ in group)),
                )
            )

    occurrence_counts: dict[str, int] = defaultdict(int)
    for row_index, row in sorted(fallback_rows, key=lambda item: item[0]):
        content_key = _content_identity_key(profile, row)
        occurrence = occurrence_counts[content_key]
        occurrence_counts[content_key] += 1
        assigned_ids[row_index] = f"{content_key}:{occurrence:04d}"

    return assigned_ids, tuple(duplicate_fingerprints), tuple(conflicts), excluded_conflict_indices


def _build_record(
    profile: BankCsvProfile, row: Mapping[str, str | None], bank_record_id: str
) -> BankRecord:
    _check_currency(profile, row)
    narration = _narration(profile, row)
    value_date = _parse_value_date(profile, row)
    direction, amount = _resolve_direction_and_amount(profile, row)
    return BankRecord(
        bank_record_id=bank_record_id,
        amount=amount,
        direction=direction,
        narration=narration,
        value_date=value_date,
    )


def parse_bank_csv(
    profile: BankCsvProfile, raw_bytes: bytes, source_id: str
) -> BankCsvAdapterResult:
    """Transform one bank CSV export into canonical ``BankRecord`` s.

    Deterministic in the sense that matters financially: reordering the
    same input rows never changes *how many* records come out or *what*
    each one says (see :func:`_resolve_row_identities`) -- the resulting
    financial multiset is row-order-independent, even though the exact
    ``bank_record_id`` string a given occurrence-index-disambiguated
    fallback row receives can depend on where it falls among same-content
    siblings in row order (that index exists only to keep ids unique
    within one statement, never to assert transaction equality).
    """
    try:
        text = raw_bytes.decode(profile.encoding)
    except UnicodeDecodeError as exc:
        raise BankCsvDecodeError(
            f"could not decode source bytes as {profile.encoding!r}: {exc}"
        ) from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=profile.delimiter)
    if reader.fieldnames is None:
        raise BankCsvDecodeError(
            "no header row found; this parser maps columns by declared "
            "header name and requires one"
        )

    header_fields = set(reader.fieldnames)
    declared_columns = profile.declared_columns()
    missing_columns = declared_columns - header_fields
    if missing_columns:
        raise BankCsvDecodeError(
            f"profile {profile.profile_id!r} declares columns "
            f"{sorted(missing_columns)} not present in the CSV header "
            f"{list(reader.fieldnames)!r}"
        )
    dropped_columns = tuple(sorted(header_fields - declared_columns))

    all_rows = [(idx, dict(row)) for idx, row in enumerate(reader)]
    assigned_ids, duplicate_fingerprints, dedupe_conflicts, excluded_conflict_indices = (
        _resolve_row_identities(profile, all_rows)
    )

    rejected: list[RejectedBankRow] = []
    provenance: list[BankRowProvenance] = []
    built_by_id: dict[str, BankRecord] = {}

    for row_index, row in sorted(all_rows, key=lambda item: item[0]):
        fingerprint = _row_fingerprint(row)

        if row_index in excluded_conflict_indices:
            rejected.append(
                RejectedBankRow(
                    row_index=row_index,
                    row_fingerprint=fingerprint,
                    reason="conflicting_duplicate_bank_record_id",
                    detail="excluded as part of a conflicting-identity group; see manifest.conflicts",
                    raw_fields=tuple(sorted(row.items())),
                )
            )
            provenance.append(
                BankRowProvenance(
                    source_id=source_id,
                    row_index=row_index,
                    row_fingerprint=fingerprint,
                    produced=(),
                    source_fields_used=(),
                    dropped_fields=dropped_columns,
                )
            )
            continue

        bank_record_id = assigned_ids.get(row_index)
        if bank_record_id is None:
            # A collapsed exact reference-duplicate copy: fully accounted
            # for via its surviving copy and `duplicate_rows_dropped`, no
            # separate provenance entry -- it produced nothing beyond what
            # that surviving copy already did.
            continue

        try:
            record = _build_record(profile, row, bank_record_id)
        except _RowRejected as exc:
            rejected.append(
                RejectedBankRow(
                    row_index=row_index,
                    row_fingerprint=fingerprint,
                    reason=exc.kind,
                    detail=exc.detail,
                    raw_fields=tuple(sorted(row.items())),
                )
            )
            provenance.append(
                BankRowProvenance(
                    source_id=source_id,
                    row_index=row_index,
                    row_fingerprint=fingerprint,
                    produced=(),
                    source_fields_used=(),
                    dropped_fields=dropped_columns,
                )
            )
            continue

        built_by_id[record.bank_record_id] = record
        provenance.append(
            BankRowProvenance(
                source_id=source_id,
                row_index=row_index,
                row_fingerprint=fingerprint,
                produced=(f"bank_record:{record.bank_record_id}",),
                source_fields_used=tuple(sorted(declared_columns)),
                dropped_fields=dropped_columns,
            )
        )

    records = tuple(built_by_id[key] for key in sorted(built_by_id))

    manifest = BankIngestManifest(
        source_id=source_id,
        rows=tuple(provenance),
        duplicate_rows_dropped=duplicate_fingerprints,
        conflicts=dedupe_conflicts,
    )

    return BankCsvAdapterResult(
        records=records,
        rejected_rows=tuple(rejected),
        manifest=manifest,
        conflicts=dedupe_conflicts,
    )


__all__ = [
    "BankCsvAdapterResult",
    "BankCsvDecodeError",
    "RejectedBankRow",
    "parse_bank_csv",
]
