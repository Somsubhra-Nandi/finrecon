"""One JSON wire shape for :class:`BankCsvProfile`, read in exactly one place.

Before this module the same profile payload was decoded twice -- once in
:mod:`finrecon.api.app` for the browser upload, once in
:mod:`finrecon.orchestrate_cli` for ``--bank-profile`` -- and the built-in
profile registry would have been a third copy. Three readers of one wire
shape is three chances for them to drift on a detail that silently changes
what a statement says (``inactive_side_marker`` being the obvious one: a
reader that quietly defaults an unrecognised value re-reads a zero-filled
statement under the wrong semantics).

So the decoding lives here once and the callers keep only their own error
type: both existing entry points still raise exactly what they always did
(``HTTPException``/``invalid_bank_profile`` and ``OrchestrationInputError``
respectively) by catching :class:`BankProfileFormatError`.

Deliberately *not* a validator of bank semantics -- it only turns a mapping
into the frozen declaration. Whether the declared columns exist in a given
CSV is still, as before, the parser's fail-fast check.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .csv_profile import (
    AmountDirectionColumns,
    BankCsvProfile,
    DebitCreditColumns,
    InactiveSideMarker,
    MoneyColumns,
)


class BankProfileFormatError(ValueError):
    """A profile payload is not a well-formed :class:`BankCsvProfile`."""


def inactive_side_marker_from_payload(money_payload: Mapping[str, Any]) -> InactiveSideMarker:
    """Read ``money_columns.inactive_side_marker`` off the wire.

    Omitted means :attr:`InactiveSideMarker.EMPTY_ONLY`, which is the
    behaviour every profile written before this field existed already has.
    An unrecognised value is invalid profile input and says so -- it is
    never quietly folded back into the default, because that would silently
    parse a zero-filled statement under the wrong semantics.
    """
    raw = money_payload.get("inactive_side_marker", InactiveSideMarker.EMPTY_ONLY.value)
    try:
        return InactiveSideMarker(raw)
    except ValueError as exc:
        valid = [member.value for member in InactiveSideMarker]
        raise BankProfileFormatError(
            f"money_columns.inactive_side_marker must be one of {valid}, got {raw!r}"
        ) from exc


def money_columns_from_payload(money_payload: Mapping[str, Any]) -> MoneyColumns:
    kind = money_payload.get("kind")
    if kind == "debit_credit":
        return DebitCreditColumns(
            debit_column=money_payload["debit_column"],
            credit_column=money_payload["credit_column"],
            inactive_side_marker=inactive_side_marker_from_payload(money_payload),
        )
    if kind == "amount_direction":
        return AmountDirectionColumns(
            amount_column=money_payload["amount_column"],
            direction_column=money_payload["direction_column"],
            credit_values=frozenset(money_payload["credit_values"]),
            debit_values=frozenset(money_payload["debit_values"]),
        )
    raise BankProfileFormatError(
        f"money_columns.kind must be 'debit_credit' or 'amount_direction', got {kind!r}"
    )


def profile_from_payload(payload: Mapping[str, Any]) -> BankCsvProfile:
    """Build the frozen profile declaration from its JSON mapping.

    Raises :class:`BankProfileFormatError` for anything malformed --
    including the ``KeyError``/``TypeError``/``ValueError`` the underlying
    construction can raise -- so a caller has exactly one exception type to
    translate into its own boundary error.
    """
    try:
        money_columns = money_columns_from_payload(payload["money_columns"])
        return BankCsvProfile(
            profile_id=payload["profile_id"],
            currency=payload["currency"],
            value_date_column=payload["value_date_column"],
            value_date_format=payload["value_date_format"],
            narration_column=payload["narration_column"],
            money_columns=money_columns,
            reference_id_column=payload.get("reference_id_column"),
            currency_column=payload.get("currency_column"),
            thousands_separator=payload.get("thousands_separator"),
            delimiter=payload.get("delimiter", ","),
            encoding=payload.get("encoding", "utf-8"),
        )
    except BankProfileFormatError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise BankProfileFormatError(str(exc)) from exc


__all__ = [
    "BankProfileFormatError",
    "inactive_side_marker_from_payload",
    "money_columns_from_payload",
    "profile_from_payload",
]
