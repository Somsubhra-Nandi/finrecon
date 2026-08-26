"""Deterministic v4 case plan: which archetype each case index builds.

Built once per split from the root seed, then shuffled with a seeded RNG, so
a case's position in the sequence -- and every record identifier derived from
that position -- carries no correlation with its archetype.

This matters more in v4 than it did in v3. In v3 only T3's tier determined
its required outcome; in v4 four of the nine archetypes determine it, and two
more determine which capability a solver needs. An unshuffled plan would put
all sixteen escalate-cases in one contiguous block of the identifier space,
and "the answer is escalate iff the settlement ordinal is above 200" would be
a perfectly good model of this benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from finrecon.benchmark.generator.seeding import derive_seed
from finrecon.benchmark.generator_v4.families import archetype_spec


@dataclass(frozen=True)
class PlannedV4Case:
    archetype: str
    candidate_count: int


def plan_rng(seed: int, split: str) -> Random:
    """A fresh, isolated RNG used only to build the shuffled plan."""
    return Random(derive_seed(seed, split, "v4-plan"))


def build_case_plan(
    seed: int, split: str, archetype_counts: dict[str, int]
) -> tuple[PlannedV4Case, ...]:
    """One entry per case, archetypes interleaved by a seeded shuffle.

    An archetype declaring several candidate-set sizes cycles through them in
    declared order, so the sizes are spread evenly rather than drawn -- a
    pilot small enough to inspect should not have its candidate-count
    distribution decided by sampling noise.
    """
    plan: list[PlannedV4Case] = []
    for archetype, count in archetype_counts.items():
        spec = archetype_spec(archetype)
        sizes = spec.candidate_counts
        for index in range(count):
            plan.append(
                PlannedV4Case(
                    archetype=archetype,
                    candidate_count=sizes[index % len(sizes)],
                )
            )

    rng = plan_rng(seed, split)
    rng.shuffle(plan)
    return tuple(plan)


__all__ = ["PlannedV4Case", "build_case_plan", "plan_rng"]
