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
from datetime import datetime, time, timedelta

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
from finrecon.benchmark.generator.t2_evidence import SurvivingReference
from finrecon.benchmark.generator.t2_invariants import (
    PlausibilityInputs,
    T2ConstructError,
    verify_t2_case,
)
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

_T2_DECOY_UTR_ATTEMPTS = 64
"""Bounded, deterministic retries when a drawn decoy UTR would also fit the fragment.

A 4-character surviving suffix can coincidentally be a suffix of a freshly
drawn UTR. Redrawing the *decoy* (never the true reference, never the
degradation) keeps the case's difficulty exactly as the ladder category
specifies while guaranteeing invariant 6. The bound is declared rather
than open-ended so an exhausted search fails loudly instead of looping.
"""


def _t2_surviving_reference(utr: str, category_id: str, degrade_seed: int, rng) -> SurvivingReference:
    """Degrade ``utr`` per the ladder category and render the bank's narration around it.

    Returns both halves of the T2 evidence contract: the exact fragment
    that survived, and the narration it survived inside. For every
    category except ``embedded_in_narration`` the fragment is the degraded
    string itself, dropped into a noisy template. ``embedded_in_narration``
    degrades *retrievability* rather than characters — the reference is
    character-intact but glued to surrounding text inside a single token,
    so no whole-token comparison can reach it.
    """
    if category_id == "embedded_in_narration":
        template_id = "design_doc_example_neft"
        template = get_narration_template(template_id)
        result = degrade_embedded_in_narration(utr, degrade_seed, narration_template=template.template)
        return SurvivingReference(
            category_id=category_id,
            evidence=utr,
            narration=result.value,
            narration_template_id=template_id,
        )

    result = degrade(utr, category_id, degrade_seed)
    narration, template_id = render_t2_noisy(result.value, rng)
    return SurvivingReference(
        category_id=category_id,
        evidence=result.value,
        narration=narration,
        narration_template_id=template_id,
    )


def build_t2_degraded_reference(case_id: str, rng, factory: RecordFactory, category_id: str) -> CaseBundle:
    """One degraded-reference case in which the degraded reference actually matters.

    Benchmark v1 built T2 as a T1 case with a mangled narration bolted on:
    a single settlement, uniquely pinned by amount and date, so the
    deterministic core resolved all 200 of them without reading a
    character of narration (``notes/STAGE2-FINDINGS.md`` §1). The
    surviving reference was decorative.

    v2 makes it load-bearing. Two settlements are built — the true one and
    a structurally identical decoy — with the same gross, therefore the
    same fee/GST/net, on the same settlement date, each with its own order
    and captured payment and its own sound break-up. Both are equally
    plausible under every declared Stage-2 rule, so structured evidence
    alone cannot choose, and the deterministic core refuses. The only
    thing separating them is the degraded UTR fragment in the narration,
    which is drawn from the true settlement and verified inconsistent with
    the decoy's.

    Nothing here is tier-aware on the matcher's side; the case is simply
    built so that the tier-blind rules cannot settle it.
    """
    base_ts = case_base_timestamp(rng)
    gross = rng.randint(_GROSS_MIN, _GROSS_MAX)
    fee, gst, net = fee_breakup(gross)

    true_payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))
    decoy_payment_ts = base_ts + timedelta(seconds=rng.randint(1, 600))

    # Both settlements land on one calendar date at distinct times. Same
    # date keeps them jointly in the declared value-date window; distinct
    # times keep the case out of T3's "genuinely indistinguishable records"
    # construct, which pins identical timestamps.
    settlement_day = (max(true_payment_ts, decoy_payment_ts) + timedelta(days=rng.randint(1, 3))).date()
    day_start = datetime.combine(settlement_day, time.min)
    true_minute, decoy_minute = rng.sample(range(1440), 2)
    true_settlement_ts = day_start + timedelta(minutes=true_minute)
    decoy_settlement_ts = day_start + timedelta(minutes=decoy_minute)

    true_utr = synthetic_utr(rng)
    degrade_seed = rng.randint(0, 2**31 - 1)
    surviving = _t2_surviving_reference(true_utr, category_id, degrade_seed, rng)

    decoy_utr: str | None = None
    for _ in range(_T2_DECOY_UTR_ATTEMPTS):
        drawn = synthetic_utr(rng)
        if drawn != true_utr and not surviving.recovers(drawn):
            decoy_utr = drawn
            break
    if decoy_utr is None:
        raise T2ConstructError(
            f"T2 case {case_id!r}: could not draw a decoy UTR inconsistent with surviving "
            f"evidence {surviving.evidence!r} in {_T2_DECOY_UTR_ATTEMPTS} attempts"
        )

    def _chain(utr: str, payment_ts, settlement_ts):
        order = factory.make_order(amount=gross, created_at=base_ts, status=OrderStatus.PAID)
        payment = factory.make_payment(order_id=order.order_id, amount=gross, created_at=payment_ts)
        settlement = factory.make_settlement(
            utr=utr,
            net_amount=net,
            created_at=settlement_ts,
            breakup=(payment_line(gross, payment.payment_id), fee_line(fee), tax_line(gst)),
        )
        return order, payment, settlement

    # Which chain is built first is randomised, so the true settlement's
    # position in the sequential ID space carries no signal. Without this,
    # "the lower settlement ID is the answer" would hold for all 200 cases.
    true_first = rng.random() < 0.5
    if true_first:
        true_chain = _chain(true_utr, true_payment_ts, true_settlement_ts)
        decoy_chain = _chain(decoy_utr, decoy_payment_ts, decoy_settlement_ts)
        ordered = (true_chain, decoy_chain)
    else:
        decoy_chain = _chain(decoy_utr, decoy_payment_ts, decoy_settlement_ts)
        true_chain = _chain(true_utr, true_payment_ts, true_settlement_ts)
        ordered = (decoy_chain, true_chain)

    _, _, true_settlement = true_chain
    _, _, decoy_settlement = decoy_chain

    bank_record = factory.make_bank_record(
        amount=net,
        narration=surviving.narration,
        value_date=settlement_day + timedelta(days=rng.randint(0, 1)),
    )

    records = CaseRecords(
        orders=tuple(chain[0] for chain in ordered),
        payments=tuple(chain[1] for chain in ordered),
        settlements=tuple(chain[2] for chain in ordered),
        refunds=(),
        bank_records=(bank_record,),
    )

    # Independent re-derivation from the records just built. Case-local
    # only here; the full-split check runs in `dataset.py` once every
    # case exists, and a wider pool can only add candidates.
    verify_t2_case(
        case_id=case_id,
        bank_record=bank_record,
        pool=PlausibilityInputs(
            settlements=records.settlements,
            payments=records.payments,
            refunds=records.refunds,
        ),
        true_settlement_id=true_settlement.settlement_id,
        surviving_reference=surviving,
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
                settlement_ids=(true_settlement.settlement_id,),
                relationship="one_to_one",
            ),
            true_reference=true_utr,
            degradation=DegradationInfo(
                category_id=category_id,
                narration_template_id=surviving.narration_template_id,
                surviving_evidence=surviving.evidence,
            ),
            distractor_settlement_ids=(decoy_settlement.settlement_id,),
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
