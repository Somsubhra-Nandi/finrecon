"""Deterministic case plan: which (tier, archetype) each case index builds.

The plan is built once per split from the split's root seed, then shuffled
with a seeded RNG so that a case's position in the sequence — and
therefore every record ID derived from that position — carries no
correlation with its tier. Without this, T3's tier would leak through the
ID space, since tier fully determines ``required_outcome`` for T3.
"""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.benchmark.generator.case_builder import T1_ARCHETYPE_NAMES
from finrecon.benchmark.generator.seeding import plan_rng
from finrecon.benchmark.generator.templates import T2_DEGRADATION_CATEGORY_IDS


@dataclass(frozen=True)
class PlannedCase:
    tier: str
    archetype: str
    """For T1: the builder function name. For T2: the degradation category id. Unused for T0/T3."""


def build_case_plan(seed: int, split: str, tier_counts: dict[str, int]) -> tuple[PlannedCase, ...]:
    plan: list[PlannedCase] = []

    t0_count = tier_counts["T0"]
    for i in range(t0_count):
        archetype = "utr_intact" if i % 2 == 0 else "settlement_id_clean"
        plan.append(PlannedCase(tier="T0", archetype=archetype))

    for i in range(tier_counts["T1"]):
        plan.append(PlannedCase(tier="T1", archetype=T1_ARCHETYPE_NAMES[i % len(T1_ARCHETYPE_NAMES)]))

    for i in range(tier_counts["T2"]):
        category_id = T2_DEGRADATION_CATEGORY_IDS[i % len(T2_DEGRADATION_CATEGORY_IDS)]
        plan.append(PlannedCase(tier="T2", archetype=category_id))

    for i in range(tier_counts["T3"]):
        plan.append(PlannedCase(tier="T3", archetype="ambiguous_same_amount_same_date"))

    rng = plan_rng(seed, split)
    rng.shuffle(plan)
    return tuple(plan)
