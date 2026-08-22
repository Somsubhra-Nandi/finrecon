from datetime import date, datetime

import pytest
from pydantic import ValidationError

from finrecon.models import (
    BankRecord,
    BankRecordDirection,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Refund,
    RefundStatus,
    Settlement,
    SettlementLineItem,
    SettlementLineType,
)


def test_order_valid():
    order = Order(
        order_id="ORD-123",
        amount=100000,
        status=OrderStatus.PAID,
        created_at=datetime(2026, 1, 1),
    )
    assert order.amount == 100000
    assert order.currency == "INR"


def test_order_rejects_float_amount():
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-123",
            amount=1000.0,
            status=OrderStatus.PAID,
            created_at=datetime(2026, 1, 1),
        )


def test_payment_valid():
    payment = Payment(
        payment_id="pay_abc",
        order_id="ORD-123",
        amount=100000,
        status=PaymentStatus.CAPTURED,
        created_at=datetime(2026, 1, 1),
    )
    assert payment.status is PaymentStatus.CAPTURED


def test_refund_valid():
    refund = Refund(
        refund_id="rfnd_1",
        payment_id="pay_abc",
        amount=5000,
        status=RefundStatus.PROCESSED,
        created_at=datetime(2026, 1, 1),
    )
    assert refund.amount == 5000


def test_settlement_with_breakup():
    settlement = Settlement(
        settlement_id="setl_091",
        utr="928392123456",
        amount=415000,
        created_at=datetime(2026, 1, 1),
        breakup=(
            SettlementLineItem(type=SettlementLineType.PAYMENT, amount=425000),
            SettlementLineItem(type=SettlementLineType.FEE, amount=-8500),
            SettlementLineItem(type=SettlementLineType.TAX, amount=-1500),
        ),
    )
    assert sum(line.amount for line in settlement.breakup) == 415000


def test_settlement_utr_optional():
    settlement = Settlement(
        settlement_id="setl_092",
        amount=1000,
        created_at=datetime(2026, 1, 1),
    )
    assert settlement.utr is None


def test_settlement_line_item_rejects_float_amount():
    with pytest.raises(ValidationError):
        SettlementLineItem(type=SettlementLineType.FEE, amount=-85.0)


def test_bank_record_valid():
    record = BankRecord(
        bank_record_id="bnk_1",
        amount=415000,
        direction=BankRecordDirection.CREDIT,
        narration="NEFT CR-RZRPAY-SET98372-MUM",
        value_date=date(2026, 1, 2),
    )
    assert record.direction is BankRecordDirection.CREDIT


def test_canonical_records_are_frozen():
    order = Order(
        order_id="ORD-1",
        amount=1000,
        status=OrderStatus.PAID,
        created_at=datetime(2026, 1, 1),
    )
    with pytest.raises(ValidationError):
        order.amount = 2000


def test_canonical_records_forbid_extra_fields():
    with pytest.raises(ValidationError):
        Order(
            order_id="ORD-1",
            amount=1000,
            status=OrderStatus.PAID,
            created_at=datetime(2026, 1, 1),
            unexpected_field="nope",
        )
