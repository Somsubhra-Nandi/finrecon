"""Stage 1 tests: synthetic benchmark generator (DESIGN.md §5, §9 Stage 1).

These tests build datasets in-memory (never touching the committed
``benchmark/`` directory unless explicitly noted) and check the Stage 1
exit conditions: determinism, tier disjointness, ground-truth separation,
no leakage into system-visible records, and reproducible frozen hashing.
"""

from __future__ import annotations

import json
import random

import pytest

from finrecon.benchmark.generator.config import (
    DEV_SEED,
    FROZEN_EVAL_SEED,
    TARGET_TIER_COUNTS,
)
from finrecon.benchmark.generator.dataset import (
    DatasetBundle,
    build_dataset,
    verify_t2_invariants,
)
from finrecon.benchmark.generator.serialize import dataset_file_dicts, ground_truth_dicts
from finrecon.benchmark.generator.t2_evidence import SurvivingReference
from finrecon.benchmark.generator.t2_invariants import (
    PlausibilityInputs,
    T2ConstructError,
    plausible_settlement_groups,
)
from finrecon.benchmark.generator.templates import REFERENCELESS_NARRATIONS

SMALL_COUNTS = {"T0": 10, "T1": 10, "T2": 10, "T3": 4}


# --------------------------------------------------------------------------
# 1-2. DEV / FROZEN-EVAL generation succeeds
# --------------------------------------------------------------------------


def test_dev_generation_succeeds():
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    assert len(bundle.ground_truth) == sum(TARGET_TIER_COUNTS.values())


def test_frozen_eval_generation_succeeds():
    bundle = build_dataset("frozen-eval", FROZEN_EVAL_SEED, TARGET_TIER_COUNTS)
    assert len(bundle.ground_truth) == sum(TARGET_TIER_COUNTS.values())


# --------------------------------------------------------------------------
# 3. Exact target case counts
# --------------------------------------------------------------------------


def test_exact_target_case_counts_dev():
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    assert bundle.tier_counts() == TARGET_TIER_COUNTS


def test_exact_target_case_counts_frozen_eval():
    bundle = build_dataset("frozen-eval", FROZEN_EVAL_SEED, TARGET_TIER_COUNTS)
    assert bundle.tier_counts() == TARGET_TIER_COUNTS


def test_total_case_count_is_890():
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    assert len(bundle.ground_truth) == 890


# --------------------------------------------------------------------------
# 4. Financial record count exceeds the Track 4 minimum of 50
# --------------------------------------------------------------------------


def test_record_count_comfortably_exceeds_track_minimum():
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    assert bundle.total_record_count() > 50
    # comfortably: at least an order of magnitude past the 50-record bar
    assert bundle.total_record_count() > 500


def test_record_and_case_counts_are_not_conflated():
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    assert bundle.total_record_count() != len(bundle.ground_truth)
    assert bundle.total_record_count() > len(bundle.ground_truth)


# --------------------------------------------------------------------------
# 5. Tier disjointness (also asserted live during generation; here re-verified)
# --------------------------------------------------------------------------


def test_tier_disjointness_holds_for_full_dev_set():
    # build_dataset already raises TierDisjointnessError internally if any
    # case is inconsistent; reaching this point at all is the assertion.
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    assert len(bundle.ground_truth) == sum(SMALL_COUNTS.values())


def test_tier_disjointness_detector_rejects_a_broken_t1_case():
    from finrecon.benchmark.generator.assertions import CaseRecords, TierDisjointnessError, assert_tier_disjoint
    from finrecon.benchmark.generator.case_builder import build_t0_utr_intact
    from finrecon.benchmark.generator.record_factory import RecordFactory
    from finrecon.benchmark.generator.seeding import case_rng

    factory = RecordFactory(id_slug="test")
    rng = case_rng(1, "test", 0)
    bundle = build_t0_utr_intact("case-test-0", rng, factory)

    with pytest.raises(TierDisjointnessError):
        # A T0 case (usable direct key survives) mislabeled as T1 must be rejected.
        assert_tier_disjoint(bundle.records, "T1", "case-test-0")


# --------------------------------------------------------------------------
# 6. T0 direct-key invariant
# --------------------------------------------------------------------------


def test_t0_cases_have_a_usable_direct_key():
    from finrecon.benchmark.generator.assertions import classify_case

    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    by_case_id = {gt.case_id: gt for gt in bundle.ground_truth}
    orders_by_id = {o.order_id: o for o in bundle.orders}
    payments_by_id = {p.payment_id: p for p in bundle.payments}
    settlements_by_id = {s.settlement_id: s for s in bundle.settlements}
    refunds_by_id = {r.refund_id: r for r in bundle.refunds}
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}

    from finrecon.benchmark.generator.assertions import CaseRecords

    t0_found = 0
    for gt in bundle.ground_truth:
        if gt.tier != "T0":
            continue
        t0_found += 1
        ids = gt.record_ids
        case = CaseRecords(
            orders=tuple(orders_by_id[i] for i in ids["orders"]),
            payments=tuple(payments_by_id[i] for i in ids["payments"]),
            settlements=tuple(settlements_by_id[i] for i in ids["settlements"]),
            refunds=tuple(refunds_by_id[i] for i in ids["refunds"]),
            bank_records=tuple(bank_by_id[i] for i in ids["bank_records"]),
        )
        assert classify_case(case) == "T0"
    assert t0_found == SMALL_COUNTS["T0"]


# --------------------------------------------------------------------------
# 7. T1 has no usable direct key
# --------------------------------------------------------------------------


def test_t1_cases_have_no_usable_direct_key():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    settlements_by_id = {s.settlement_id: s for s in bundle.settlements}
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}

    t1_found = 0
    for gt in bundle.ground_truth:
        if gt.tier != "T1":
            continue
        t1_found += 1
        for settlement_id in gt.record_ids["settlements"]:
            settlement = settlements_by_id[settlement_id]
            assert settlement.utr is None
            for bank_record_id in gt.record_ids["bank_records"]:
                assert settlement_id not in bank_by_id[bank_record_id].narration
    assert t1_found == SMALL_COUNTS["T1"]


# --------------------------------------------------------------------------
# 8. T2 uses degraded reference evidence
# --------------------------------------------------------------------------


def test_t2_cases_carry_a_degraded_but_recoverable_reference():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    settlements_by_id = {s.settlement_id: s for s in bundle.settlements}
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}

    t2_found = 0
    for gt in bundle.ground_truth:
        if gt.tier != "T2":
            continue
        t2_found += 1
        assert gt.true_reference is not None
        assert gt.degradation is not None
        assert gt.degradation.category_id != "intact"
        assert gt.degradation.surviving_evidence is not None

        true_settlement_id = gt.correct_relationship.settlement_ids[0]
        assert settlements_by_id[true_settlement_id].utr == gt.true_reference

        (bank_record_id,) = gt.record_ids["bank_records"]
        narration = bank_by_id[bank_record_id].narration
        assert gt.degradation.surviving_evidence in narration
        if gt.degradation.category_id == "embedded_in_narration":
            assert gt.true_reference in narration
        else:
            assert gt.true_reference not in narration
    assert t2_found == SMALL_COUNTS["T2"]


# --------------------------------------------------------------------------
# 8b. Benchmark v2: the degraded reference is *causally necessary*
#
# v1's T2 cases were uniquely resolvable from structured evidence alone
# (notes/STAGE2-FINDINGS.md §1), which made the degraded reference
# decorative. These tests pin the corrected construct so it cannot silently
# regress to v1's shape.
# --------------------------------------------------------------------------


def _t2_pool(bundle):
    return PlausibilityInputs(
        settlements=tuple(bundle.settlements),
        payments=tuple(bundle.payments),
        refunds=tuple(bundle.refunds),
    )


def _t2_surviving(gt, bank_record):
    return SurvivingReference(
        category_id=gt.degradation.category_id,
        evidence=gt.degradation.surviving_evidence,
        narration=bank_record.narration,
        narration_template_id=gt.degradation.narration_template_id or "",
    )


@pytest.fixture(scope="module")
def t2_bundle():
    return build_dataset("dev", DEV_SEED, SMALL_COUNTS)


def _t2_entries(bundle):
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}
    for gt in bundle.ground_truth:
        if gt.tier == "T2":
            yield gt, bank_by_id[gt.correct_relationship.bank_record_id]


def test_t2_has_no_usable_direct_key(t2_bundle):
    """Invariant 1: no whole narration token equals any settlement's UTR or ID."""
    checked = 0
    for gt, bank_record in _t2_entries(t2_bundle):
        checked += 1
        surviving = _t2_surviving(gt, bank_record)
        for settlement in t2_bundle.settlements:
            for identifier in (settlement.settlement_id, settlement.utr):
                if identifier:
                    assert not surviving.is_directly_usable(identifier), gt.case_id
    assert checked == SMALL_COUNTS["T2"]


def test_t2_structured_evidence_alone_leaves_at_least_two_candidates(t2_bundle):
    """Invariants 2-4: two or more plausible groups, the true one among them, no unique pick."""
    pool = _t2_pool(t2_bundle)
    for gt, bank_record in _t2_entries(t2_bundle):
        groups = plausible_settlement_groups(bank_record, pool)
        assert len(groups) >= 2, gt.case_id
        assert (gt.correct_relationship.settlement_ids[0],) in groups, gt.case_id


def test_t2_degraded_evidence_maps_to_exactly_the_true_candidate(t2_bundle):
    """Invariants 5-6: the surviving fragment fits the true UTR and no competitor's."""
    pool = _t2_pool(t2_bundle)
    by_id = {s.settlement_id: s for s in t2_bundle.settlements}
    for gt, bank_record in _t2_entries(t2_bundle):
        surviving = _t2_surviving(gt, bank_record)
        candidate_ids = {
            sid for group in plausible_settlement_groups(bank_record, pool) for sid in group
        }
        recovered = {sid for sid in candidate_ids if surviving.recovers(by_id[sid].utr)}
        assert recovered == {gt.correct_relationship.settlement_ids[0]}, gt.case_id


def test_t2_stays_ambiguous_when_the_degraded_evidence_is_removed(t2_bundle):
    """Invariant 7: delete the narration entirely and the case is still not decidable."""
    pool = _t2_pool(t2_bundle)
    for gt, bank_record in _t2_entries(t2_bundle):
        stripped = bank_record.model_copy(update={"narration": ""})
        assert len(plausible_settlement_groups(stripped, pool)) >= 2, gt.case_id


def test_t2_and_t3_stay_semantically_distinct(t2_bundle):
    """T2 = ambiguity plus one recoverable discriminator. T3 = ambiguity with none."""
    by_id = {s.settlement_id: s for s in t2_bundle.settlements}
    bank_by_id = {b.bank_record_id: b for b in t2_bundle.bank_records}

    for gt in t2_bundle.ground_truth:
        settlements = [by_id[i] for i in gt.record_ids["settlements"]]
        if gt.tier == "T2":
            assert gt.required_outcome == "AUTO_RESOLVABLE"
            assert gt.correct_relationship is not None
            assert gt.distractor_settlement_ids
            assert all(s.utr is not None for s in settlements)
            # distinct timestamps: T2's ambiguity comes from the declared
            # window, not from T3's identical-record construct.
            assert len({s.created_at for s in settlements}) == len(settlements)
        elif gt.tier == "T3":
            assert gt.required_outcome == "ESCALATE"
            assert gt.correct_relationship is None
            assert gt.distractor_settlement_ids == ()
            assert all(s.utr is None for s in settlements)
            narration = bank_by_id[gt.record_ids["bank_records"][0]].narration
            assert narration in REFERENCELESS_NARRATIONS


def test_t2_decoys_carry_no_accidental_distinguishing_structure(t2_bundle):
    """The wrong candidate must look exactly as good as the right one, structurally."""
    by_id = {s.settlement_id: s for s in t2_bundle.settlements}
    for gt, _ in _t2_entries(t2_bundle):
        true_settlement = by_id[gt.correct_relationship.settlement_ids[0]]
        for decoy_id in gt.distractor_settlement_ids:
            decoy = by_id[decoy_id]
            assert decoy.amount == true_settlement.amount
            assert decoy.created_at.date() == true_settlement.created_at.date()
            assert decoy.utr is not None and decoy.utr != true_settlement.utr
            assert [line.type for line in decoy.breakup] == [
                line.type for line in true_settlement.breakup
            ]
            assert [int(line.amount) for line in decoy.breakup] == [
                int(line.amount) for line in true_settlement.breakup
            ]


def test_the_true_t2_settlement_is_not_systematically_the_lower_id(t2_bundle):
    """Record-ID order must carry no signal about which candidate is correct."""
    bundle = build_dataset("dev", DEV_SEED, TARGET_TIER_COUNTS)
    true_is_lower = 0
    total = 0
    for gt in bundle.ground_truth:
        if gt.tier != "T2":
            continue
        total += 1
        true_id = gt.correct_relationship.settlement_ids[0]
        if true_id < min(gt.distractor_settlement_ids):
            true_is_lower += 1
    assert total == TARGET_TIER_COUNTS["T2"]
    # Both orderings must actually occur; a constant would be a leak.
    assert 0 < true_is_lower < total


def test_generated_t2_cases_pass_the_batch_wide_invariant_check():
    """`build_dataset` runs this itself; asserting it here makes the guard explicit."""
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    verifications = verify_t2_invariants(bundle)
    assert len(verifications) == SMALL_COUNTS["T2"]
    for verification in verifications.values():
        assert verification.candidate_count >= 2
        assert len(verification.recovered_settlement_ids) == 1


def test_a_t2_case_without_a_decoy_is_rejected():
    """The invariant check must actually fail on v1's shape, not just pass on v2's."""
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    doomed = next(gt for gt in bundle.ground_truth if gt.tier == "T2")
    decoy_ids = set(doomed.distractor_settlement_ids)

    stripped = DatasetBundle(split=bundle.split, seed=bundle.seed)
    stripped.orders = list(bundle.orders)
    stripped.payments = list(bundle.payments)
    stripped.refunds = list(bundle.refunds)
    stripped.bank_records = list(bundle.bank_records)
    stripped.settlements = [s for s in bundle.settlements if s.settlement_id not in decoy_ids]
    stripped.ground_truth = [doomed]

    with pytest.raises(T2ConstructError, match="at least two"):
        verify_t2_invariants(stripped)


# --------------------------------------------------------------------------
# 9-10. T3 has no uniquely distinguishing reference and requires escalation
# --------------------------------------------------------------------------


def test_t3_cases_have_no_distinguishing_reference_and_require_escalation():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    settlements_by_id = {s.settlement_id: s for s in bundle.settlements}
    bank_by_id = {b.bank_record_id: b for b in bundle.bank_records}

    t3_found = 0
    for gt in bundle.ground_truth:
        if gt.tier != "T3":
            continue
        t3_found += 1
        assert gt.required_outcome == "ESCALATE"
        assert gt.correct_relationship is None
        assert gt.true_reference is None
        settlements = [settlements_by_id[i] for i in gt.record_ids["settlements"]]
        assert len(settlements) >= 2
        assert all(s.utr is None for s in settlements)
        # genuinely ambiguous: at least two settlements share amount + timestamp
        amounts_times = [(s.amount, s.created_at) for s in settlements]
        assert len(set(amounts_times)) < len(amounts_times)
        for settlement in settlements:
            for bank_record_id in gt.record_ids["bank_records"]:
                assert settlement.settlement_id not in bank_by_id[bank_record_id].narration
    assert t3_found == SMALL_COUNTS["T3"]


# --------------------------------------------------------------------------
# 11. No hidden-ground-truth leakage into system-visible records
# --------------------------------------------------------------------------


def test_no_leakage_of_tier_or_outcome_into_visible_record_ids():
    # Record IDs are generator-constructed from a fixed pattern (no random
    # content), so an exact-word check here is meaningful. Narration text
    # is excluded: it legitimately contains randomly generated UTR-like
    # strings that can coincidentally contain a substring like "t3" with
    # zero connection to tier or outcome, which would make a substring
    # scan there a false-positive generator, not a real leakage check.
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    forbidden_words = {"t0", "t1", "t2", "t3", "escalate", "ambiguous", "resolvable", "auto_resolve"}

    visible_ids = []
    for order in bundle.orders:
        visible_ids.append(order.order_id)
    for payment in bundle.payments:
        visible_ids.append(payment.payment_id)
    for settlement in bundle.settlements:
        visible_ids.append(settlement.settlement_id)
    for refund in bundle.refunds:
        visible_ids.append(refund.refund_id)
    for bank_record in bundle.bank_records:
        visible_ids.append(bank_record.bank_record_id)

    for record_id in visible_ids:
        tokens = set(record_id.lower().replace("-", "_").split("_"))
        leaked = tokens & forbidden_words
        assert not leaked, f"leaked {leaked!r} into visible record id {record_id!r}"


def test_no_leakage_of_case_grouping_into_visible_records():
    # Canonical record models forbid extra fields (CanonicalRecord's
    # extra="forbid"), so a case_id/tier/outcome field could never be
    # added to a visible record even by accident — verified structurally
    # in test_ground_truth_is_a_separate_file_from_system_visible_datasets.
    # Here we check the narration content specifically for the *fixed*,
    # non-random boilerplate strings the generator itself writes, which
    # must never spell out an answer.
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    forbidden_literal_phrases = ("escalate", "ambiguous", "should_", "correct_settlement", "resolvable")
    for bank_record in bundle.bank_records:
        lowered = bank_record.narration.lower()
        for phrase in forbidden_literal_phrases:
            assert phrase not in lowered, f"leaked {phrase!r} into narration {bank_record.narration!r}"


def test_ground_truth_is_a_separate_file_from_system_visible_datasets():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    dataset_dicts = dataset_file_dicts(bundle)
    for record_type, dicts in dataset_dicts.items():
        for d in dicts:
            assert "tier" not in d
            assert "required_outcome" not in d
            assert "case_id" not in d


# --------------------------------------------------------------------------
# 12-13. Determinism: same seed -> identical, different seed -> different
# --------------------------------------------------------------------------


def test_same_seed_produces_identical_dataset():
    b1 = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    b2 = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    assert dataset_file_dicts(b1) == dataset_file_dicts(b2)
    assert ground_truth_dicts(b1) == ground_truth_dicts(b2)


def test_different_seeds_produce_different_datasets():
    b1 = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    b2 = build_dataset("dev", FROZEN_EVAL_SEED, SMALL_COUNTS)
    assert dataset_file_dicts(b1) != dataset_file_dicts(b2)


def test_dev_and_frozen_eval_seeds_differ():
    assert DEV_SEED != FROZEN_EVAL_SEED


def test_generation_does_not_mutate_global_random_state():
    random.seed(20260822)
    before = random.random()

    random.seed(20260822)
    build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    after = random.random()

    assert before == after


def test_case_ids_are_not_reused_across_splits():
    dev = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    frozen = build_dataset("frozen-eval", FROZEN_EVAL_SEED, SMALL_COUNTS)
    dev_ids = {gt.case_id for gt in dev.ground_truth}
    frozen_ids = {gt.case_id for gt in frozen.ground_truth}
    assert dev_ids.isdisjoint(frozen_ids)


def test_record_ids_are_not_reused_across_splits():
    dev = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    frozen = build_dataset("frozen-eval", FROZEN_EVAL_SEED, SMALL_COUNTS)
    dev_ids = {o.order_id for o in dev.orders}
    frozen_ids = {o.order_id for o in frozen.orders}
    assert dev_ids.isdisjoint(frozen_ids)


# --------------------------------------------------------------------------
# 14. Stable serialization ordering
# --------------------------------------------------------------------------


def test_serialized_records_are_sorted_by_id():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    dicts = dataset_file_dicts(bundle)
    assert [d["order_id"] for d in dicts["orders"]] == sorted(d["order_id"] for d in dicts["orders"])
    assert [d["bank_record_id"] for d in dicts["bank_records"]] == sorted(
        d["bank_record_id"] for d in dicts["bank_records"]
    )


def test_serialized_ground_truth_is_sorted_by_case_id():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    gt_dicts = ground_truth_dicts(bundle)
    assert [d["case_id"] for d in gt_dicts] == sorted(d["case_id"] for d in gt_dicts)


def test_repeated_serialization_is_byte_stable():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    first = json.dumps(dataset_file_dicts(bundle), sort_keys=True)
    second = json.dumps(dataset_file_dicts(build_dataset("dev", DEV_SEED, SMALL_COUNTS)), sort_keys=True)
    assert first == second


# --------------------------------------------------------------------------
# 15. All monetary values remain integer paise
# --------------------------------------------------------------------------


def test_all_monetary_values_are_integers():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    for order in bundle.orders:
        assert isinstance(int(order.amount), int)
    for payment in bundle.payments:
        assert isinstance(int(payment.amount), int)
    for settlement in bundle.settlements:
        assert isinstance(int(settlement.amount), int)
        for line in settlement.breakup:
            assert isinstance(int(line.amount), int)
    for refund in bundle.refunds:
        assert isinstance(int(refund.amount), int)
    for bank_record in bundle.bank_records:
        assert isinstance(int(bank_record.amount), int)
    for gt in bundle.ground_truth:
        assert isinstance(gt.value_at_stake_paise, int)


def test_settlement_breakup_reconciles_exactly_to_net_amount():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    for settlement in bundle.settlements:
        assert sum(int(line.amount) for line in settlement.breakup) == int(settlement.amount)


# --------------------------------------------------------------------------
# 16. Ground-truth IDs reference real generated records
# --------------------------------------------------------------------------


def test_ground_truth_record_ids_reference_real_records():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    valid = {
        "orders": {o.order_id for o in bundle.orders},
        "payments": {p.payment_id for p in bundle.payments},
        "settlements": {s.settlement_id for s in bundle.settlements},
        "refunds": {r.refund_id for r in bundle.refunds},
        "bank_records": {b.bank_record_id for b in bundle.bank_records},
    }
    for gt in bundle.ground_truth:
        for record_type, ids in gt.record_ids.items():
            for record_id in ids:
                assert record_id in valid[record_type]


def test_correct_relationship_ids_reference_real_records():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    bank_ids = {b.bank_record_id for b in bundle.bank_records}
    settlement_ids = {s.settlement_id for s in bundle.settlements}
    for gt in bundle.ground_truth:
        if gt.correct_relationship is None:
            continue
        assert gt.correct_relationship.bank_record_id in bank_ids
        for settlement_id in gt.correct_relationship.settlement_ids:
            assert settlement_id in settlement_ids


# --------------------------------------------------------------------------
# 17. Every generated record belongs to a valid case/batch relationship
# --------------------------------------------------------------------------


def test_every_record_belongs_to_exactly_the_cases_that_declare_it():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    referenced_orders = {oid for gt in bundle.ground_truth for oid in gt.record_ids["orders"]}
    referenced_bank_records = {bid for gt in bundle.ground_truth for bid in gt.record_ids["bank_records"]}
    assert referenced_orders == {o.order_id for o in bundle.orders}
    assert referenced_bank_records == {b.bank_record_id for b in bundle.bank_records}


def test_batch_is_the_full_set_of_records_across_all_cases():
    bundle = build_dataset("dev", DEV_SEED, SMALL_COUNTS)
    # the "batch" is everything generated for the split in one run
    assert bundle.total_record_count() == sum(bundle.record_counts().values())
    assert len(bundle.ground_truth) == sum(SMALL_COUNTS.values())


# --------------------------------------------------------------------------
# 18. Frozen-eval SHA-256 reproduces exactly
# --------------------------------------------------------------------------


def test_frozen_eval_fingerprint_reproduces_exactly(tmp_path):
    from finrecon.benchmark.generator.hashing import compute_fingerprint
    from finrecon.benchmark.generator.serialize import write_dataset

    bundle = build_dataset("frozen-eval", FROZEN_EVAL_SEED, SMALL_COUNTS)
    write_dataset(bundle, tmp_path)
    hash1 = compute_fingerprint(tmp_path, split="frozen-eval")

    bundle2 = build_dataset("frozen-eval", FROZEN_EVAL_SEED, SMALL_COUNTS)
    tmp2 = tmp_path / "second"
    write_dataset(bundle2, tmp2)
    hash2 = compute_fingerprint(tmp2, split="frozen-eval")

    assert hash1 == hash2
    assert len(hash1) == 64


def test_fingerprint_covers_the_complete_frozen_eval_artifact():
    """The digest must cover all five visible dataset files AND hidden ground truth.

    Locks the coverage contract itself, so a future edit cannot quietly
    drop ground truth (or slip DEV / the self-referential manifest) out of
    or into the fingerprint without failing here.
    """
    from finrecon.benchmark.generator.hashing import hashed_file_list
    from finrecon.benchmark.generator.serialize import dataset_file_names

    files = hashed_file_list("frozen-eval")
    expected = tuple(f"datasets/frozen-eval/{n}" for n in dataset_file_names()) + (
        "ground_truth/frozen-eval.jsonl",
    )
    assert files == expected
    assert "ground_truth/frozen-eval.jsonl" in files, "hidden ground truth must be fingerprinted"
    assert not any("/dev/" in f or f.endswith("dev.jsonl") for f in files), "DEV must not be fingerprinted"
    assert not any("manifest" in f for f in files), "manifest must be excluded (circular self-reference)"
    assert all("\\" not in f for f in files), "paths must be forward-slash for cross-platform stability"
    assert files == tuple(sorted(files)), "hashed order must be deterministic, not filesystem-dependent"


def test_manifest_documents_exactly_what_is_hashed():
    from finrecon.benchmark.generator.config import MANIFEST_FILENAME, benchmark_dir
    from finrecon.benchmark.generator.hashing import hashed_file_list
    from finrecon.benchmark.generator.manifest import read_manifest

    manifest_path = benchmark_dir() / "manifests" / MANIFEST_FILENAME
    if not manifest_path.exists():
        pytest.skip("benchmark not yet generated on disk")
    manifest = read_manifest(benchmark_dir())
    assert tuple(manifest["frozen_eval_hashed_files"]) == hashed_file_list("frozen-eval")


def test_committed_frozen_eval_matches_committed_manifest():
    from finrecon.benchmark.generator.config import MANIFEST_FILENAME, benchmark_dir
    from finrecon.benchmark.generator.hashing import compute_fingerprint
    from finrecon.benchmark.generator.manifest import read_manifest

    bdir = benchmark_dir()
    manifest_path = bdir / "manifests" / MANIFEST_FILENAME
    if not manifest_path.exists():
        pytest.skip("benchmark not yet generated on disk")
    manifest = read_manifest(bdir)
    actual = compute_fingerprint(bdir, split="frozen-eval")
    assert manifest["frozen_eval_sha256"] == actual
