"""Per-tier, per-archetype case construction (DESIGN.md §5.2, Stage 1).

Each ``build_*`` function constructs one reconciliation case: a small set
of canonical financial records (system-visible) plus one
:class:`~finrecon.benchmark.generator.ground_truth.GroundTruthCase` (hidden).
No function here reads wall-clock time, global ``random`` state, or a
non-seeded source of randomness — everything flows from the ``rng``
passed in, which callers derive deterministically per case
(:mod:`finrecon.benchmark.generator.seeding`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from finrecon.models import OrderStatus, PaymentStatus

from finrecon.benchmark.generator.assertions import CaseRecords, assert_tier_disjoint
from finrecon.benchmark.generator.ground_truth import (
    DegradationInfo,
    GroundTruthCase,
    ReconciliationRelationship,
)
from finrecon.benchmark.generator.record_factory import (
    RecordFactory,
    adjustment_line,
    case_base_timestamp,
    fee_breakup,
    fee_line,
    payment_line,
    refund_line,
    synthetic_utr,
    tax_line,
    transfer_line,
)
from finrecon.benchmark.generator.templates import (
    REFERENCELESS_NARRATIONS,
    render_t0_clean,
    render_t2_noisy,
)
from finrecon.benchmark.generator.narration_library import get_narration_template
from finrecon.benchmark.generator.utr_degradation import degrade, degrade_embedded_in_narration

_GROSS_MIN = 50_000  # ₹500
_GROSS_MAX = 5_000_000  # ₹50,000


@dataclass
class CaseBundle:
    case_id: str
    tier: str
    records: CaseRecords
    ground_truth: GroundTruthCase


def _record_ids(records: CaseRecords) -> dict[str, tuple[str, ...]]:
    return {
        "orders": tuple(o.order_id for o in records.orders),
        "payments": tuple(p.payment_id for p in records.payments),
        "settlements": tuple(s.settlement_id for s in records.settlements),
        "refunds": tuple(r.refund_id for r in records.refunds),
        "bank_records": tuple(b.bank_record_id for b in records.bank_records),
    }


def _finalize(case_id: str, tier: str, archetype: str, records: CaseRecords, gt_kwargs: dict) -> CaseBundle:
    ground_truth = GroundTruthCase(
        case_id=case_id,
        tier=tier,
        archetype=archetype,
        record_ids=_record_ids(records),
        **gt_kwargs,
    )
    assert_tier_disjoint(records, tier, case_id)
    return CaseBundle(case_id=case_id, tier=tier, records=records, ground_truth=ground_truth)


# --------------------------------------------------------------------------
# T0 — direct key
# --------------------------------------------------------------------------


def build_t0_utr_intact(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net = fee_breakup(gross)
    settlement_ts = payment_ts + timedelta(days=rng.randint(1, 3))
    utr = synthetic_utr(rng)
    settlement = factory.make_settlement(
        utr=utr,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
    )
    narration, template_id = render_t0_clean(utr, rng)
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,), payments=(payment,), settlements=(settlement,), refunds=(), bank_records=(bank_record,)
    )
    return _finalize(
        case_id,
        "T0",
        "utr_intact_direct_key",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=utr,
            degradation=DegradationInfo(category_id="intact", narration_template_id=template_id),
            value_at_stake_paise=net,
        ),
    )


def build_t0_settlement_id_clean(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net = fee_breakup(gross)
    settlement_ts = payment_ts + timedelta(days=rng.randint(1, 3))
    settlement = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
    )
    narration = f"RZPY/SETL/{settlement.settlement_id} CREDIT"
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,), payments=(payment,), settlements=(settlement,), refunds=(), bank_records=(bank_record,)
    )
    return _finalize(
        case_id,
        "T0",
        "settlement_id_clean_direct_key",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=settlement.settlement_id,
            degradation=None,
            value_at_stake_paise=net,
        ),
    )


# --------------------------------------------------------------------------
# T1 — derived (no usable direct join key; structured evidence suffices)
# --------------------------------------------------------------------------


def build_t1_fee_gst_arithmetic(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net = fee_breakup(gross)
    settlement_ts = payment_ts + timedelta(days=rng.randint(1, 3))
    settlement = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
    )
    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,), payments=(payment,), settlements=(settlement,), refunds=(), bank_records=(bank_record,)
    )
    return _finalize(
        case_id,
        "T1",
        "fee_gst_arithmetic",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=None,
            degradation=DegradationInfo(category_id="omitted", narration_template_id=None),
            value_at_stake_paise=net,
        ),
    )


def build_t1_refund_offset(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net_before_refund = fee_breakup(gross)
    refund_amount = rng.randint(1_00, max(1_00, net_before_refund // 4))
    refund_ts = payment_ts + timedelta(hours=rng.randint(1, 48))
    refund = factory.make_refund(payment_id=payment.payment_id, amount=refund_amount, created_at=refund_ts)
    net = net_before_refund - refund_amount
    settlement_ts = refund_ts + timedelta(days=rng.randint(1, 3))
    settlement = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(
            payment_line(gross, payment.payment_id),
            fee_line(fee),
            tax_line(gst),
            refund_line(refund_amount, refund.refund_id),
        ),
    )
    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,),
        payments=(payment,),
        settlements=(settlement,),
        refunds=(refund,),
        bank_records=(bank_record,),
    )
    return _finalize(
        case_id,
        "T1",
        "refund_offset",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=None,
            degradation=DegradationInfo(category_id="omitted", narration_template_id=None),
            value_at_stake_paise=net,
        ),
    )


def build_t1_batched_settlement(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)

    def _chain(hour_offset: int):
        gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
        order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
        payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
        payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
        fee, gst, net = fee_breakup(gross)
        settlement_ts = payment_ts + timedelta(days=2, hours=hour_offset)
        settlement = factory.make_settlement(
            utr=None,
            net_amount=net,
            created_at=settlement_ts,
            breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
        )
        return order, payment, settlement, net

    order1, payment1, settlement1, net1 = _chain(0)
    order2, payment2, settlement2, net2 = _chain(1)  # distinct hour -> distinct created_at, never ambiguous

    total_net = net1 + net2
    value_date = settlement1.created_at.date()
    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(amount=total_net, narration=narration, value_date=value_date)

    records = CaseRecords(
        orders=(order1, order2),
        payments=(payment1, payment2),
        settlements=(settlement1, settlement2),
        refunds=(),
        bank_records=(bank_record,),
    )
    return _finalize(
        case_id,
        "T1",
        "batched_settlement",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement1.settlement_id, settlement2.settlement_id),
                relationship="many_to_one",
            ),
            true_reference=None,
            degradation=DegradationInfo(category_id="omitted", narration_template_id=None),
            value_at_stake_paise=total_net,
        ),
    )


def build_t1_duplicate_disambiguation(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    order = factory.make_order(amount=rng.randint(_GROSS_MIN, _GROSS_MAX), created_at=base_ts, status=OrderStatus.PAID)

    failed_amount = rng.randint(_GROSS_MIN, _GROSS_MAX)
    failed_payment = factory.make_payment(
        order_id=order.order_id,
        amount=failed_amount,
        created_at=base_ts + timedelta(seconds=rng.randint(1, 60)),
        status=PaymentStatus.FAILED,
    )

    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    captured_ts = base_ts + timedelta(seconds=rng.randint(120, 600))
    captured_payment = factory.make_payment(
        order_id=order.order_id, amount=gross, created_at=captured_ts, status=PaymentStatus.CAPTURED
    )

    fee, gst, net = fee_breakup(gross)
    settlement_ts = captured_ts + timedelta(days=rng.randint(1, 3))
    settlement = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, captured_payment.payment_id), fee_line(fee), tax_line(gst)),
    )
    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )

    records = CaseRecords(
        orders=(order,),
        payments=(failed_payment, captured_payment),
        settlements=(settlement,),
        refunds=(),
        bank_records=(bank_record,),
    )
    return _finalize(
        case_id,
        "T1",
        "duplicate_disambiguation",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=None,
            degradation=DegradationInfo(category_id="omitted", narration_template_id=None),
            value_at_stake_paise=net,
        ),
    )


def build_t1_adjustment_and_transfer(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    """Exercises the ADJUSTMENT and TRANSFER settlement-breakup line types.

    A declared, deterministic rounding-style adjustment plus a sub-account
    transfer deduction, both structurally derivable from the breakup, with
    no UTR/settlement_id evidence anywhere.
    """
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net_before_transfer = fee_breakup(gross)

    transfer_amount = rng.randint(1_00, max(1_00, net_before_transfer // 10))
    adjustment_amount = rng.choice((-1, 0, 1))  # declared paise-level rounding correction

    net = net_before_transfer - transfer_amount + adjustment_amount
    settlement_ts = payment_ts + timedelta(days=rng.randint(1, 3))
    settlement = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(
            payment_line(gross, payment.payment_id),
            fee_line(fee),
            tax_line(gst),
            transfer_line(-transfer_amount),
            adjustment_line(adjustment_amount),
        ),
    )
    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,), payments=(payment,), settlements=(settlement,), refunds=(), bank_records=(bank_record,)
    )
    return _finalize(
        case_id,
        "T1",
        "adjustment_and_transfer",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=None,
            degradation=DegradationInfo(category_id="omitted", narration_template_id=None),
            value_at_stake_paise=net,
        ),
    )


T1_BUILDERS = (
    build_t1_fee_gst_arithmetic,
    build_t1_refund_offset,
    build_t1_batched_settlement,
    build_t1_duplicate_disambiguation,
    build_t1_adjustment_and_transfer,
)

T1_ARCHETYPE_NAMES = (
    "fee_gst_arithmetic",
    "refund_offset",
    "batched_settlement",
    "duplicate_disambiguation",
    "adjustment_and_transfer",
)


# --------------------------------------------------------------------------
# T2 — degraded reference
# --------------------------------------------------------------------------


def build_t2_degraded_reference(case_id: str, rng, factory: RecordFactory, category_id: str) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
    fee, gst, net = fee_breakup(gross)
    settlement_ts = payment_ts + timedelta(days=rng.randint(1, 3))
    utr = synthetic_utr(rng)
    settlement = factory.make_settlement(
        utr=utr,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
    )

    degrade_seed = rng.randint(0, 2**31 - 1)
    if category_id == "embedded_in_narration":
        template_id = rng.choice(("design_doc_example_neft",))
        template = get_narration_template(template_id)
        result = degrade_embedded_in_narration(utr, degrade_seed, narration_template=template.template)
        narration = result.value
    else:
        result = degrade(utr, category_id, degrade_seed)
        narration, template_id = render_t2_noisy(result.value, rng)

    bank_record = factory.make_bank_record(
        amount=net, narration=narration, value_date=(settlement_ts + timedelta(days=rng.randint(0, 1))).date()
    )
    records = CaseRecords(
        orders=(order,), payments=(payment,), settlements=(settlement,), refunds=(), bank_records=(bank_record,)
    )
    return _finalize(
        case_id,
        "T2",
        f"degraded_reference:{category_id}",
        records,
        dict(
            required_outcome="AUTO_RESOLVABLE",
            correct_relationship=ReconciliationRelationship(
                bank_record_id=bank_record.bank_record_id,
                settlement_ids=(settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=utr,
            degradation=DegradationInfo(category_id=category_id, narration_template_id=template_id),
            value_at_stake_paise=net,
        ),
    )


# --------------------------------------------------------------------------
# T3 — truly ambiguous
# --------------------------------------------------------------------------


def build_t3_ambiguous(case_id: str, rng, factory: RecordFactory) -> CaseBundle:
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    fee, gst, net = fee_breakup(gross)
    settlement_ts = base_ts + timedelta(days=rng.randint(1, 3))

    order1 = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment1 = factory.make_payment(order_id=order1.order_id, amount=gross, created_at=base_ts + timedelta(seconds=5))
    settlement1 = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,
        breakup=(payment_line(gross, payment1.payment_id), fee_line(fee), tax_line(gst)),
    )

    order2 = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
    payment2 = factory.make_payment(order_id=order2.order_id, amount=gross, created_at=base_ts + timedelta(seconds=5))
    settlement2 = factory.make_settlement(
        utr=None,
        net_amount=net,
        created_at=settlement_ts,  # identical to settlement1 -> genuinely indistinguishable
        breakup=(payment_line(gross, payment2.payment_id), fee_line(fee), tax_line(gst)),
    )

    narration = rng.choice(REFERENCELESS_NARRATIONS)
    bank_record = factory.make_bank_record(amount=net, narration=narration, value_date=settlement_ts.date())

    records = CaseRecords(
        orders=(order1, order2),
        payments=(payment1, payment2),
        settlements=(settlement1, settlement2),
        refunds=(),
        bank_records=(bank_record,),
    )
    return _finalize(
        case_id,
        "T3",
        "ambiguous_same_amount_same_date",
        records,
        dict(
            required_outcome="ESCALATE",
            correct_relationship=None,
            true_reference=None,
            degradation=DegradationInfo(category_id="ambiguous", narration_template_id=None),
            value_at_stake_paise=net,
        ),
    )
