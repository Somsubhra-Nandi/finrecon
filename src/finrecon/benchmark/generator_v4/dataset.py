"""Full-split orchestration for the v4 pilot: plan -> cases -> verified bundle.

The split-wide verification pass at the end is not a formality. Every v4 case
claims something about its *whole* search space -- "no single fragment
identifies a candidate", "exactly this composition does" -- and candidates are
drawn from the entire batch, so another case's settlement landing on the same
net inside the same value-date window changes the claim. A wider pool can only
add candidates, so a case that passed case-locally still has to pass
batch-wide, and a failure here is a generation failure rather than a committed
artifact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from finrecon.models import BankRecord, Order, Payment, Refund, Settlement

from finrecon.benchmark.generator.record_factory import RecordFactory
from finrecon.benchmark.generator_v4.case_builder import CaseBundle, build_case
from finrecon.benchmark.generator_v4.config import V4_PILOT_SPLIT, v4_split_slug
from finrecon.benchmark.generator_v4.ground_truth import V4GroundTruthCase
from finrecon.benchmark.generator_v4.invariants import (
    CaseVerification,
    PlausibilityInputs,
    verify_case,
)
from finrecon.benchmark.generator_v4.plan import build_case_plan


@dataclass
class V4DatasetBundle:
    split: str
    seed: int
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    bank_records: list[BankRecord] = field(default_factory=list)
    ground_truth: list[V4GroundTruthCase] = field(default_factory=list)
    cases: list[CaseBundle] = field(default_factory=list)

    def archetype_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.ground_truth:
            counts[entry.archetype] = counts.get(entry.archetype, 0) + 1
        return dict(sorted(counts.items()))

    def family_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.ground_truth:
            for family in entry.families:
                counts[family] = counts.get(family, 0) + 1
        return dict(sorted(counts.items()))

    def candidate_count_buckets(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.ground_truth:
            key = str(entry.expected_candidate_count)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.ground_truth:
            counts[entry.required_outcome] = counts.get(entry.required_outcome, 0) + 1
        return dict(sorted(counts.items()))

    def composition_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.ground_truth:
            key = entry.required_composition
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

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


def verify_split_wide(bundle: V4DatasetBundle) -> dict[str, CaseVerification]:
    """Re-check every case's invariants against the whole split's records."""
    pool = PlausibilityInputs(
        settlements=tuple(bundle.settlements),
        payments=tuple(bundle.payments),
        refunds=tuple(bundle.refunds),
    )
    bank_by_id = {record.bank_record_id: record for record in bundle.bank_records}

    verifications: dict[str, CaseVerification] = {}
    for case in bundle.cases:
        bank_record = bank_by_id[case.records.bank_records[0].bank_record_id]
        verifications[case.case_id] = verify_case(
            case_id=case.case_id,
            bank_record=bank_record,
            pool=pool,
            expectation=case.expectation,
        )
    return verifications


def build_v4_dataset(
    seed: int,
    archetype_counts: dict[str, int],
    split: str = V4_PILOT_SPLIT,
) -> V4DatasetBundle:
    plan = build_case_plan(seed, split, archetype_counts)
    factory = RecordFactory(id_slug=v4_split_slug(split))
    bundle = V4DatasetBundle(split=split, seed=seed)

    for case_index, planned in enumerate(plan):
        case_id = f"case-{v4_split_slug(split)}-{case_index:05d}"
        case = build_case(
            case_id=case_id,
            archetype=planned.archetype,
            candidate_count=planned.candidate_count,
            seed=seed,
            factory=factory,
        )
        records = case.records
        bundle.orders.extend(records.orders)
        bundle.payments.extend(records.payments)
        bundle.settlements.extend(records.settlements)
        bundle.refunds.extend(records.refunds)
        bundle.bank_records.extend(records.bank_records)
        bundle.ground_truth.append(case.ground_truth)
        bundle.cases.append(case)

    verify_split_wide(bundle)
    return bundle


__all__ = ["V4DatasetBundle", "build_v4_dataset", "verify_split_wide"]
