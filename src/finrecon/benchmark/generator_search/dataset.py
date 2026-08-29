"""Build the fifty-case synthetic bounded-search challenge.

The financial records come from the already-validated v4 construction
vocabulary.  This generator changes the question around those records: it
places the admissible clue among many plausible remittance fields, verifies
that the evidence closure is unchanged, and gives the investigator only four
tool calls in which to select a useful fragment.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from random import Random

from finrecon.benchmark.generator.assertions import CaseRecords
from finrecon.benchmark.generator.record_factory import RecordFactory
from finrecon.benchmark.generator.seeding import derive_seed
from finrecon.benchmark.generator_v4.case_builder import CaseBundle, build_case
from finrecon.benchmark.generator_v4.invariants import verify_case
from finrecon.benchmark.generator_search.config import (
    BENCHMARK_NAME,
    FAMILY_PLANS,
    ID_SLUG,
    MIN_PLAUSIBLE_EVIDENCE_ACTIONS,
    NOISE_TOKEN_COUNT,
    SEARCH_SEED,
)
from finrecon.models import BankRecord, Order, Payment, Refund, Settlement


@dataclass(frozen=True)
class PlannedSearchCase:
    family: str
    source_archetype: str
    candidate_count: int
    required_outcome: str


@dataclass
class SearchDatasetBundle:
    split: str
    seed: int
    orders: list[Order] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    settlements: list[Settlement] = field(default_factory=list)
    refunds: list[Refund] = field(default_factory=list)
    bank_records: list[BankRecord] = field(default_factory=list)
    ground_truth: list[dict] = field(default_factory=list)

    def record_counts(self) -> dict[str, int]:
        return {
            "orders": len(self.orders),
            "payments": len(self.payments),
            "refunds": len(self.refunds),
            "settlements": len(self.settlements),
            "bank_records": len(self.bank_records),
        }

    def total_record_count(self) -> int:
        return sum(self.record_counts().values())


def _expand_plan() -> list[PlannedSearchCase]:
    plan: list[PlannedSearchCase] = []
    for family in FAMILY_PLANS:
        if family.family == "noisy_reference_selection":
            sources = ("conjunction_pair",) * 4 + ("conjunction_wide",) * 3
            counts = (3, 3, 3, 3, 4, 5, 4)
        elif family.family == "multi_evidence_composition":
            sources = ("conjunction_pair",) * 3 + ("conjunction_triple",) * 4
            counts = (3, 3, 3, 5, 5, 5, 5)
        elif family.family == "decoy_heavy_candidate_search":
            sources = ("single_fragment_control",) * 4 + ("conjunction_wide",) * 3
            counts = (3, 3, 3, 3, 4, 5, 5)
        elif family.family == "ambiguity_controls":
            sources = (
                ("ambiguity_no_discriminator",) * 5
                + ("ambiguity_conjunction_incomplete",) * 5
            )
            counts = (3, 4, 5, 3, 5, 4, 4, 4, 4, 4)
        else:
            sources = tuple(family.source_archetypes[0] for _ in range(family.count))
            counts = tuple(family.candidate_counts[0] for _ in range(family.count))
        assert len(sources) == len(counts) == family.count
        plan.extend(
            PlannedSearchCase(family.family, source, count, family.required_outcome)
            for source, count in zip(sources, counts)
        )
    # IDs are assigned only after this shuffle, so reject accidental long
    # runs that would let case ordinal act as a weak outcome label. This is a
    # construction invariant fixed before any hosted-model result exists.
    for attempt in range(256):
        shuffled = list(plan)
        Random(
            derive_seed(SEARCH_SEED, BENCHMARK_NAME, "plan", attempt)
        ).shuffle(shuffled)
        outcome_run = family_run = 1
        max_outcome_run = max_family_run = 1
        for previous, current in zip(shuffled, shuffled[1:]):
            outcome_run = outcome_run + 1 if (
                previous.required_outcome == current.required_outcome
            ) else 1
            family_run = family_run + 1 if previous.family == current.family else 1
            max_outcome_run = max(max_outcome_run, outcome_run)
            max_family_run = max(max_family_run, family_run)
        if max_outcome_run <= 6 and max_family_run <= 2:
            return shuffled
    raise AssertionError("could not construct a leakage-resistant case order")


_DECOY_PREFIXES = (
    "AX", "BT", "CY", "LG", "MM", "OR", "PY", "PS",
    "RM", "RV", "RN", "TR", "AR", "LD", "GT", "AV",
)


def _decoy_token(rng: Random, index: int) -> str:
    # A zero makes the complete token unlike the v4 UTR vocabulary (digits 1-9),
    # while the verifier below remains the authority for every substring.
    suffix = "".join(rng.choice("ABCDEFGHJKLMNPQRTUVWXYZ123456789") for _ in range(3))
    return f"{_DECOY_PREFIXES[index % len(_DECOY_PREFIXES)]}0{suffix}"


def _interleave(rng: Random, fixed: list[str], decoys: list[str]) -> list[str]:
    fields = list(decoys)
    for value in fixed:
        fields.insert(rng.randrange(len(fields) + 1), value)
    return fields


def _clue_fields(family: str, raw_truth: dict) -> list[str]:
    clues = [item["fragment"] for item in raw_truth.get("clues", ())]
    structural = raw_truth.get("structural_discriminator")
    structural_token = None
    if structural:
        if structural["kind"] != "breakup_line_amount_paise":
            raise AssertionError(
                "bounded-search-v1 only admits source-backed refund amount facts"
            )
        structural_token = f"RFND {structural['narration_token']}"

    if family == "reference_prioritization":
        return [f"BANK-UTR-FRAGMENT={value}" for value in clues]
    if family == "noisy_reference_selection":
        return [f"INWARD-NOTE-RETAINED={value}" for value in clues]
    if family == "multi_evidence_composition":
        labels = ("ORIGIN-REF", "BENEFICIARY-REF", "CLEARING-REF")
        return [f"{labels[index]}={value}" for index, value in enumerate(clues)]
    if family == "refund_linked_reasoning":
        fields = [f"REVERSAL-LINK={value}" for value in clues]
        if structural_token:
            fields.append(structural_token)
        return fields
    if family == "conflicting_evidence":
        fields = [f"VALUE-ADVICE={value}" for value in clues]
        if structural_token:
            fields.append(structural_token)
        return fields
    if family == "decoy_heavy_candidate_search":
        return [f"GATEWAY-COMMENT-KEPT={value}" for value in clues]
    # Ambiguity controls retain any incomplete/stale reference evidence and
    # any structural contradiction. They never gain an answer token.
    fields = [f"REMITTANCE-NOTE={value}" for value in clues]
    if structural_token:
        fields.append(structural_token)
    return fields


def _narration_for(family: str, raw_truth: dict, rng: Random) -> tuple[str, tuple[str, ...]]:
    decoys = [_decoy_token(rng, index) for index in range(NOISE_TOKEN_COUNT)]
    fixed = _clue_fields(family, raw_truth)
    fields = _interleave(rng, fixed, decoys)
    rail = rng.choice(("NEFT CR", "RTGS CR", "IMPS P2A"))
    narration = f"{rail} RAZORPAY " + "|".join(fields)
    return narration, tuple(decoys)


def plausible_fragment_actions(narration: str) -> tuple[str, ...]:
    """A conservative visible action catalogue used only for difficulty audit."""
    tokens = re.findall(r"[A-Za-z0-9_.-]+", narration)
    ignored = {"NEFT", "RTGS", "IMPS", "P2A", "RAZORPAY"}
    return tuple(dict.fromkeys(t for t in tokens if len(t) >= 4 and t not in ignored))


def plausible_evidence_action_count(narration: str, candidate_count: int) -> int:
    # Each candidate supports a record lookup and expected-net computation;
    # each singleton settlement supports one breakup inspection. Current
    # challenge cases intentionally keep candidates singleton.
    return len(plausible_fragment_actions(narration)) + 3 * candidate_count


def _records_with_narration(records: CaseRecords, narration: str) -> CaseRecords:
    bank = records.bank_records[0].model_copy(update={"narration": narration})
    return CaseRecords(
        orders=records.orders,
        payments=records.payments,
        settlements=records.settlements,
        refunds=records.refunds,
        bank_records=(bank,),
    )


def _search_case(
    base: CaseBundle,
    planned: PlannedSearchCase,
    *,
    case_index: int,
) -> tuple[CaseRecords, dict]:
    raw_truth = base.ground_truth.model_dump(mode="json")
    last_error: Exception | None = None
    for attempt in range(64):
        rng = Random(
            derive_seed(SEARCH_SEED, base.case_id, planned.family, "noise", attempt)
        )
        narration, decoys = _narration_for(planned.family, raw_truth, rng)
        if len(narration) > 240:
            last_error = AssertionError(f"narration length {len(narration)} exceeds 240")
            continue
        action_count = plausible_evidence_action_count(narration, planned.candidate_count)
        if action_count < MIN_PLAUSIBLE_EVIDENCE_ACTIONS:
            last_error = AssertionError(f"only {action_count} plausible evidence actions")
            continue
        records = _records_with_narration(base.records, narration)
        try:
            verification = verify_case(
                case_id=base.case_id,
                bank_record=records.bank_records[0],
                pool=type("Pool", (), {
                    "settlements": records.settlements,
                    "payments": records.payments,
                    "refunds": records.refunds,
                })(),
                expectation=base.expectation,
            )
        except Exception as error:  # bounded redraw; verifier owns the invariant vocabulary
            last_error = error
            continue

        relationship = raw_truth["correct_relationship"]
        truth = {
            "case_id": base.case_id,
            "benchmark": BENCHMARK_NAME,
            "tier": "SEARCH",
            "archetype": planned.family,
            "families": [planned.family],
            "source_archetype": planned.source_archetype,
            "required_composition": raw_truth["required_composition"],
            "record_ids": raw_truth["record_ids"],
            "required_outcome": planned.required_outcome,
            "correct_relationship": relationship,
            "true_reference": raw_truth["true_reference"],
            "distractor_settlement_ids": raw_truth["distractor_settlement_ids"],
            "expected_candidate_count": planned.candidate_count,
            "clues": raw_truth["clues"],
            "structural_discriminator": raw_truth["structural_discriminator"],
            "value_at_stake_paise": raw_truth["value_at_stake_paise"],
            "plausible_fragment_actions": list(plausible_fragment_actions(narration)),
            "plausible_evidence_action_count": action_count,
            "irrelevant_evidence_tokens": list(decoys),
            "candidate_settlement_ids": list(verification.candidate_settlement_ids),
            "case_ordinal_after_seeded_shuffle": case_index,
        }
        return records, truth
    raise AssertionError(
        f"could not add safe search noise to {base.case_id} after 64 attempts: {last_error}"
    )


def build_search_dataset() -> SearchDatasetBundle:
    plan = _expand_plan()
    factory = RecordFactory(id_slug=ID_SLUG)
    bundle = SearchDatasetBundle(split=BENCHMARK_NAME, seed=SEARCH_SEED)

    for case_index, planned in enumerate(plan):
        case_id = f"case-bsearch-{case_index:05d}"
        base = build_case(
            case_id=case_id,
            archetype=planned.source_archetype,
            candidate_count=planned.candidate_count,
            seed=SEARCH_SEED,
            factory=factory,
        )
        records, truth = _search_case(base, planned, case_index=case_index)
        bundle.orders.extend(records.orders)
        bundle.payments.extend(records.payments)
        bundle.refunds.extend(records.refunds)
        bundle.settlements.extend(records.settlements)
        bundle.bank_records.extend(records.bank_records)
        bundle.ground_truth.append(truth)

    assert Counter(row["required_outcome"] for row in bundle.ground_truth) == {
        "AUTO_RESOLVABLE": 40,
        "ESCALATE": 10,
    }
    return bundle


__all__ = [
    "PlannedSearchCase",
    "SearchDatasetBundle",
    "build_search_dataset",
    "plausible_evidence_action_count",
    "plausible_fragment_actions",
]
