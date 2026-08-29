"""Declared construction and fairness constants for bounded-search-v1."""

from __future__ import annotations

from dataclasses import dataclass

from finrecon.benchmark.generator.config import benchmark_dir, repo_root

BENCHMARK_NAME = "bounded-search-v1"
GENERATOR_VERSION = "bounded-search-generator.v1"
ID_SLUG = "bsearch"
SEARCH_SEED = 730241

TOOL_CALL_BUDGET = 4
MAX_MODEL_STEPS = 4
MAX_TOOL_CALLS_PER_STEP = 1

TOTAL_CASES = 50
RESOLVABLE_CASES = 40
AMBIGUOUS_CASES = 10

NOISE_TOKEN_COUNT = 21
MIN_PLAUSIBLE_EVIDENCE_ACTIONS = 30


@dataclass(frozen=True)
class FamilyPlan:
    family: str
    count: int
    source_archetypes: tuple[str, ...]
    candidate_counts: tuple[int, ...]
    required_outcome: str


FAMILY_PLANS: tuple[FamilyPlan, ...] = (
    FamilyPlan(
        "reference_prioritization",
        7,
        ("single_fragment_control",),
        (3,),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "noisy_reference_selection",
        7,
        ("conjunction_pair", "conjunction_wide"),
        (3, 4, 5),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "multi_evidence_composition",
        7,
        ("conjunction_pair", "conjunction_triple"),
        (3, 5),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "refund_linked_reasoning",
        6,
        ("amount_reference_hop",),
        (3,),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "conflicting_evidence",
        6,
        ("conjunction_pair",),
        (3,),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "decoy_heavy_candidate_search",
        7,
        ("single_fragment_control", "conjunction_wide"),
        (3, 4, 5),
        "AUTO_RESOLVABLE",
    ),
    FamilyPlan(
        "ambiguity_controls",
        10,
        (
            "ambiguity_no_discriminator",
            "ambiguity_conjunction_incomplete",
        ),
        (3, 4, 5),
        "ESCALATE",
    ),
)

FAMILY_COUNTS = {plan.family: plan.count for plan in FAMILY_PLANS}

assert sum(FAMILY_COUNTS.values()) == TOTAL_CASES
assert sum(p.count for p in FAMILY_PLANS if p.required_outcome == "AUTO_RESOLVABLE") == RESOLVABLE_CASES
assert sum(p.count for p in FAMILY_PLANS if p.required_outcome == "ESCALATE") == AMBIGUOUS_CASES

__all__ = [
    "AMBIGUOUS_CASES",
    "BENCHMARK_NAME",
    "FAMILY_COUNTS",
    "FAMILY_PLANS",
    "GENERATOR_VERSION",
    "ID_SLUG",
    "MAX_MODEL_STEPS",
    "MAX_TOOL_CALLS_PER_STEP",
    "MIN_PLAUSIBLE_EVIDENCE_ACTIONS",
    "NOISE_TOKEN_COUNT",
    "RESOLVABLE_CASES",
    "SEARCH_SEED",
    "TOTAL_CASES",
    "TOOL_CALL_BUDGET",
    "FamilyPlan",
    "benchmark_dir",
    "repo_root",
]
