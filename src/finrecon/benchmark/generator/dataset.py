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
from finrecon.benchmark.generator.config import split_id_slug
from finrecon.benchmark.generator.ground_truth import GroundTruthCase
from finrecon.benchmark.generator.plan import PlannedCase, build_case_plan
from finrecon.benchmark.generator.record_factory import RecordFactory
from finrecon.benchmark.generator.seeding import case_rng
from finrecon.benchmark.generator.t2_evidence import SurvivingReference
from finrecon.benchmark.generator.t2_invariants import (
    PlausibilityInputs,
    T2ConstructError,
    T2Verification,
    verify_t2_case,
)

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
    # Seeded on the split *name*, not the slug: the RNG stream is part of
    # the frozen construct, and v3 changes identifier text only. Keeping the
    # seeding input untouched means every amount, date, UTR and degradation
    # on FROZEN-EVAL is bit-for-bit what v2 produced, so the v2->v3 diff is
    # auditable as "IDs and the T0 narrations that embed them, nothing else".
    rng = case_rng(seed, split, case_index)
    case_id = f"case-{split_id_slug(split)}-{case_index:05d}"

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


def verify_t2_invariants(bundle: DatasetBundle) -> dict[str, T2Verification]:
    """Re-check every T2 case's causal-necessity invariants against the **whole split**.

    The case builder already checks each T2 case against its own records.
    That is not sufficient on its own: candidates are drawn from the entire
    batch, so another case's settlement landing on the same amount and date
    changes a T2 case's candidate set, and a coincidental UTR elsewhere in
    the split could make the surviving fragment ambiguous where it was
    unique case-locally. A wider pool can only add candidates, never remove
    them, so invariants 2-4 survive widening by construction — invariants
    1, 6 and 7 are the ones this pass actually re-earns.

    Runs on every generated split before anything is written, so a
    violation is a generation failure rather than a committed artifact.
    Returns the per-case verification records, which the DEV diagnostics
    and the test suite read.
    """
    pool = PlausibilityInputs(
        settlements=tuple(bundle.settlements),
        payments=tuple(bundle.payments),
        refunds=tuple(bundle.refunds),
    )
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}

    verifications: dict[str, T2Verification] = {}
    for gt in bundle.ground_truth:
        if gt.tier != "T2":
            continue
        if gt.degradation is None or gt.degradation.surviving_evidence is None:
            raise T2ConstructError(
                f"T2 case {gt.case_id!r} records no surviving reference evidence in ground truth"
            )
        if gt.correct_relationship is None:
            raise T2ConstructError(f"T2 case {gt.case_id!r} has no correct relationship")

        bank_record = bank_by_id[gt.correct_relationship.bank_record_id]
        surviving = SurvivingReference(
            category_id=gt.degradation.category_id,
            evidence=gt.degradation.surviving_evidence,
            narration=bank_record.narration,
            narration_template_id=gt.degradation.narration_template_id or "",
        )
        verifications[gt.case_id] = verify_t2_case(
            case_id=gt.case_id,
            bank_record=bank_record,
            pool=pool,
            true_settlement_id=gt.correct_relationship.settlement_ids[0],
            surviving_reference=surviving,
        )
    return verifications


def build_dataset(split: str, seed: int, tier_counts: dict[str, int]) -> DatasetBundle:
    plan = build_case_plan(seed, split, tier_counts)
    factory = RecordFactory(id_slug=split_id_slug(split))
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

    verify_t2_invariants(bundle)
    return bundle
