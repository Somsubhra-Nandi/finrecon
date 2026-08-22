from decimal import Decimal

import pytest
from pydantic import BaseModel

from finrecon.models.money import MoneyError, Paise


def test_paise_accepts_int():
    assert Paise(100) == 100
    assert int(Paise(100)) == 100


def test_paise_rejects_float():
    with pytest.raises(MoneyError):
        Paise(100.0)


def test_paise_rejects_bool():
    with pytest.raises(MoneyError):
        Paise(True)


def test_paise_rejects_non_numeric():
    with pytest.raises(MoneyError):
        Paise("100")


def test_from_rupees_exact_string():
    assert Paise.from_rupees("41.50") == 4150
    assert Paise.from_rupees("100") == 10000


def test_from_rupees_exact_decimal():
    assert Paise.from_rupees(Decimal("41.50")) == 4150


def test_from_rupees_rejects_float():
    with pytest.raises(MoneyError):
        Paise.from_rupees(41.5)


def test_from_rupees_rejects_sub_paise_precision():
    with pytest.raises(MoneyError):
        Paise.from_rupees("41.505")


def test_to_rupees_round_trip():
    assert Paise(4150).to_rupees() == Decimal("41.50")


def test_negative_paise_allowed_for_fee_lines():
    assert Paise(-500) == -500


class _Holder(BaseModel):
    amount: Paise


def test_pydantic_field_accepts_int():
    holder = _Holder(amount=1000)
    assert isinstance(holder.amount, Paise)
    assert holder.amount == 1000


def test_pydantic_field_rejects_float():
    with pytest.raises(Exception):
        _Holder(amount=1000.0)


def test_pydantic_field_rejects_numeric_string():
    with pytest.raises(Exception):
        _Holder(amount="1000")


def test_pydantic_round_trips_through_json():
    holder = _Holder(amount=1000)
    dumped = holder.model_dump_json()
    assert '"amount":1000' in dumped
    restored = _Holder.model_validate_json(dumped)
    assert restored.amount == 1000
