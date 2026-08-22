"""Integer-paise money representation.

DESIGN.md §4.6 / §7: all money in the financial path is an ``int`` count of
paise. No ``float`` may enter this path at any point, including from
Pydantic validation, because binary floats cannot represent every rupee
amount exactly and a silently-rounded value is a silently wrong ledger.

``Paise`` is the only type financial-record fields should use for amounts.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

_PAISE_PER_RUPEE = 100


class MoneyError(ValueError):
    """Raised when a value cannot be safely represented as integer paise."""


def _reject_float(value: Any) -> None:
    if isinstance(value, float):
        raise MoneyError(
            f"floating-point value {value!r} is not accepted as money; "
            "convert to integer paise (or a Decimal/str rupee amount via "
            "Paise.from_rupees) before it enters the financial path"
        )


class Paise(int):
    """A whole number of paise. Subclasses ``int``; never accepts ``float``.

    Construct directly from an integer count of paise (``Paise(100000)``),
    or from an external decimal-rupee boundary value via
    :meth:`from_rupees`, which is the only sanctioned float-adjacent entry
    point and immediately converts to an exact integer.
    """

    def __new__(cls, value: int) -> "Paise":
        _reject_float(value)
        if isinstance(value, bool):
            raise MoneyError("bool is not a valid paise value")
        if not isinstance(value, int):
            raise MoneyError(
                f"Paise requires an int, got {type(value).__name__}: {value!r}"
            )
        return super().__new__(cls, value)

    @classmethod
    def from_rupees(cls, value: str | Decimal) -> "Paise":
        """Safely convert an external decimal-rupee amount into exact paise.

        This is the one sanctioned boundary conversion (e.g. a CSV column
        holding ``"41.50"``). It accepts ``str`` or ``Decimal`` only —
        never ``float`` — and rejects any value with sub-paise precision
        rather than silently rounding it, since silent rounding is exactly
        the failure mode this type exists to prevent.
        """
        _reject_float(value)
        if not isinstance(value, (str, Decimal)):
            raise MoneyError(
                "from_rupees accepts only str or Decimal, got "
                f"{type(value).__name__}: {value!r}"
            )
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise MoneyError(f"{value!r} is not a valid decimal amount") from exc

        scaled = decimal_value * _PAISE_PER_RUPEE
        if scaled != scaled.to_integral_value():
            raise MoneyError(
                f"{value!r} has sub-paise precision and cannot be "
                "represented exactly as integer paise"
            )
        return cls(int(scaled))

    def to_rupees(self) -> Decimal:
        """Render as a ``Decimal`` rupee amount, for display only."""
        return Decimal(int(self)) / _PAISE_PER_RUPEE

    def __repr__(self) -> str:
        return f"Paise({int(self)})"

    # --- Pydantic v2 integration -------------------------------------
    #
    # A plain `int` subclass would let pydantic coerce a float or a
    # numeric string straight through its default int handling. Instead
    # we route validation exclusively through this class's own
    # constructor so `MoneyError` (not a bare pydantic coercion) is what
    # rejects floats, and so schemas built from this type reject them too.

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        def validate(value: Any) -> "Paise":
            if isinstance(value, Paise):
                return value
            if isinstance(value, bool) or not isinstance(value, int):
                raise MoneyError(
                    f"Paise requires an int, got {type(value).__name__}: {value!r}"
                )
            return cls(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(int),
        )
