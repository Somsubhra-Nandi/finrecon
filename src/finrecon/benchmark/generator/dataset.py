"""Full-split orchestration: plan -> per-case builders -> collected dataset.

Produces a :class:`DatasetBundle` holding every system-visible record
(grouped by canonical type) and every hidden ground-truth entry for one
split (``dev`` or ``frozen-eval``). Nothing here writes to disk — that is
:mod:`finrecon.benchmark.generator.serialize`'s job — so the same bundle
can be built in-memory for tests without touching the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finrecon.models import BankRecord, Order, Payment, Refund, Settlement

from finrecon.benchmark.generator.assertions import CaseRecords
from finrecon.benchmark.generator.case_builder import (
    T1_ARCHETYPE_NAMES,
    T1_BUILDERS,
    build_t0_settlement_id_clean,
    build_t0_utr_intact,
    build_t2_degraded_reference,
    build_t3_ambiguous,
)
from finrecon.benchmark.generator.ground_truth import GroundTruthCase
from finrecon.benchmark.generator.plan import PlannedCase, build_case_plan
from finrecon.benchmark.generator.record_factory import RecordFactory
from finrecon.benchmark.generator.seeding import case_rng

_T1_BUILDER_BY_NAME = dict(zip(T1_ARCHETYPE_NAMES, T1_BUILDERS))


@dataclass
class DatasetBundle:
    split: str
    seed: int
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    bank_records: list[BankRecord] = field(default_factory=list)
    ground_truth: list[GroundTruthCase] = field(default_factory=list)

    def tier_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for gt in self.ground_truth:
            counts[gt.tier] = counts.get(gt.tier, 0) + 1
        return counts

    def record_counts(self) -> dict[str, int]:
        return {
            "orders": len(self.orders),
            "payments": len(self.payments),
            "settlements": len(self.settlements),
            "refunds": len(self.refunds),
            "bank_records": len(self.bank_records),
        }

    def total_record_count(self) -> int:
        return sum(self.record_counts().values())


def _build_case(planned: PlannedCase, case_index: int, seed: int, split: str, factory: RecordFactory):
    rng = case_rng(seed, split, case_index)
    case_id = f"case-{split}-{case_index:05d}"

    if planned.tier == "T0":
        if planned.archetype == "utr_intact":
            return build_t0_utr_intact(case_id, rng, factory)
        return build_t0_settlement_id_clean(case_id, rng, factory)

    if planned.tier == "T1":
        builder = _T1_BUILDER_BY_NAME[planned.archetype]
        return builder(case_id, rng, factory)

    if planned.tier == "T2":
        return build_t2_degraded_reference(case_id, rng, factory, planned.archetype)

    if planned.tier == "T3":
        return build_t3_ambiguous(case_id, rng, factory)

    raise ValueError(f"unknown tier in plan: {planned.tier!r}")


def build_dataset(split: str, seed: int, tier_counts: dict[str, int]) -> DatasetBundle:
    plan = build_case_plan(seed, split, tier_counts)
    factory = RecordFactory(split=split)
    bundle = DatasetBundle(split=split, seed=seed)

    for case_index, planned in enumerate(plan):
        case_bundle = _build_case(planned, case_index, seed, split, factory)
        records: CaseRecords = case_bundle.records
        bundle.orders.extend(records.orders)
        bundle.payments.extend(records.payments)
        bundle.settlements.extend(records.settlements)
        bundle.refunds.extend(records.refunds)
        bundle.bank_records.extend(records.bank_records)
        bundle.ground_truth.append(case_bundle.ground_truth)

    return bundle
