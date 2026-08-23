"""Benchmark v3: every tier is resolved by the mechanism its definition names.

DESIGN.md §5.2 grades the tiers on reference survival, and each tier's
placement on that gradient is a claim about *which rule settles it*:

    T0  a usable direct join key survives   -> direct-key matcher
    T1  no key, structure survives          -> derived reconciliation
    T2  key survives only degraded          -> neither; refuse pre-recovery
    T3  nothing distinguishing              -> neither; refuse

Benchmark v2 satisfied that on DEV and quietly violated it on FROZEN-EVAL:
175 of 350 T0 cases carried a settlement ID containing ``-``, which the
declared tokenization treats as a delimiter, so the direct-key matcher could
not reach them and derived reconciliation resolved them instead. The outcome
was right and the mechanism was wrong, so no accuracy assertion noticed.

Two lessons are encoded here as tests. First, the generator must reject a
T0 case whose direct key cannot survive tokenization
(``TestGeneratorRejectsUnusableDirectKeys``). Second — and this is the one
that would actually have caught v2 — the two splits must agree about which
rule resolves which tier (``TestCrossSplitRuleDistribution``). A defect
living only in the reporting split is the hardest kind to see, because that
split is the one nobody steps through case by case.

See ``benchmark/manifests/CHANGELOG.md`` v3.0.0.
"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from finrecon.benchmark.generator.assertions import TierDisjointnessError
from finrecon.benchmark.generator.case_builder import (
    build_t0_settlement_id_clean,
    build_t0_utr_intact,
)
from finrecon.benchmark.generator.config import (
    SPLIT_ID_SLUGS,
    SPLITS,
    split_id_slug,
)
from finrecon.benchmark.generator.record_factory import RecordFactory
from finrecon.benchmark.generator.token_contract import (
    is_token_safe,
    is_usable_direct_key,
    narration_tokens,
)
from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import (
    DIRECT_KEY_MATCHER_ID,
    RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
    RULE_DIRECT_KEY_EXACT_TOKEN,
)
from finrecon.pipeline import process_batch

T0_ARCHETYPES = ("utr_intact_direct_key", "settlement_id_clean_direct_key")


def _slug_free_factory(id_slug: str) -> RecordFactory:
    """A factory with the slug guard bypassed, to reproduce the v2 defect on purpose."""

    class Unguarded(RecordFactory):
        def __post_init__(self) -> None:  # noqa: D105 - deliberately no validation
            return None

    return Unguarded(id_slug=id_slug)


# --------------------------------------------------------------------------
# The token contract itself
# --------------------------------------------------------------------------


class TestTokenContract:
    def test_a_hyphen_breaks_an_identifier_into_two_tokens(self):
        """The exact mechanic behind the v3 defect, pinned so it cannot be forgotten."""
        assert narration_tokens("setl_frozen-eval_000042") == ("setl_frozen", "eval_000042")
        assert not is_token_safe("setl_frozen-eval_000042")

    def test_an_underscored_identifier_survives_whole(self):
        assert narration_tokens("setl_frozeneval_000042") == ("setl_frozeneval_000042",)
        assert is_token_safe("setl_frozeneval_000042")

    def test_substring_containment_is_not_usable_direct_key(self):
        """Containment was v2's test; whole-token equality is v3's. They disagree here."""
        narration = "RZPY/SETL/setl_frozen-eval_000042 CREDIT"
        identifier = "setl_frozen-eval_000042"
        assert identifier in narration, "precondition: v2's containment test passes"
        assert not is_usable_direct_key(narration, identifier), "v3's token test must fail"

    def test_a_clean_settlement_id_narration_is_a_usable_direct_key(self):
        narration = "RZPY/SETL/setl_frozeneval_000042 CREDIT"
        assert is_usable_direct_key(narration, "setl_frozeneval_000042")

    def test_the_direct_key_test_folds_case(self):
        assert is_usable_direct_key("ref abc123 cr", "ABC123")

    def test_an_absent_or_empty_identifier_is_never_usable(self):
        assert not is_usable_direct_key("NEFT CREDIT - SETTLEMENT", None)
        assert not is_usable_direct_key("NEFT CREDIT - SETTLEMENT", "")
        assert not is_usable_direct_key("NEFT CREDIT - SETTLEMENT", "setl_dev_000001")


# --------------------------------------------------------------------------
# Token-safe split slugs
# --------------------------------------------------------------------------


class TestTokenSafeSplitSlugs:
    @pytest.mark.parametrize("split", SPLITS)
    def test_every_split_has_a_token_safe_slug(self, split):
        assert is_token_safe(split_id_slug(split))

    @pytest.mark.parametrize("split", SPLITS)
    def test_generated_identifiers_expected_in_narration_survive_tokenization(self, split):
        """Settlement IDs are printed into T0 narrations, so they must be whole tokens."""
        factory = RecordFactory(id_slug=split_id_slug(split))
        assert is_token_safe(factory.settlement_id())

    def test_the_frozen_eval_slug_no_longer_carries_a_delimiter(self):
        assert SPLIT_ID_SLUGS["frozen-eval"] == "frozeneval"
        assert "-" not in SPLIT_ID_SLUGS["frozen-eval"]

    def test_split_names_keep_their_hyphen_for_paths_and_manifests(self):
        """Only identifiers are slugged; the on-disk split name is untouched."""
        assert "frozen-eval" in SPLITS
        assert split_id_slug("frozen-eval") != "frozen-eval"

    def test_an_unknown_split_is_refused_rather_than_guessed(self):
        with pytest.raises(ValueError, match="unknown split"):
            split_id_slug("staging")

    def test_the_record_factory_refuses_a_delimiter_bearing_slug(self):
        """The v2 defect is unrepresentable, not merely discouraged."""
        with pytest.raises(ValueError, match="does not survive tokenization"):
            RecordFactory(id_slug="frozen-eval")


# --------------------------------------------------------------------------
# Generator-side T0 admission
# --------------------------------------------------------------------------


class TestGeneratorRejectsUnusableDirectKeys:
    def test_a_settlement_id_that_is_only_a_substring_fails_generation(self):
        """The precise v2 defect: certified T0, unreachable by the matcher.

        With the hardened admission test the case classifies as T1 — no
        usable key, structure survives — and tier disjointness fails loudly
        instead of emitting a mislabelled case.
        """
        factory = _slug_free_factory("frozen-eval")
        with pytest.raises(TierDisjointnessError) as excinfo:
            build_t0_settlement_id_clean("case-probe-00001", random.Random(7), factory)
        message = str(excinfo.value)
        assert "declared tier 'T0'" in message
        assert "classify as 'T1'" in message
        assert "direct_key=False" in message

    def test_the_same_case_generates_cleanly_with_a_token_safe_slug(self):
        factory = RecordFactory(id_slug="frozeneval")
        bundle = build_t0_settlement_id_clean("case-probe-00002", random.Random(7), factory)
        assert bundle.tier == "T0"
        settlement = bundle.records.settlements[0]
        narration = bundle.records.bank_records[0].narration
        assert is_usable_direct_key(narration, settlement.settlement_id)

    def test_intact_utr_t0_cases_are_checked_with_the_same_token_semantics(self):
        factory = RecordFactory(id_slug="frozeneval")
        bundle = build_t0_utr_intact("case-probe-00003", random.Random(11), factory)
        settlement = bundle.records.settlements[0]
        narration = bundle.records.bank_records[0].narration
        assert is_usable_direct_key(narration, settlement.utr)

    def test_a_delimiter_bearing_utr_would_not_qualify_as_a_direct_key(self):
        """Guards the UTR half of the admission test against the same class of bug."""
        assert not is_usable_direct_key("NEFT-CR-AB12-CD34", "AB12-CD34")


# --------------------------------------------------------------------------
# Cross-split rule distribution — the test that would have caught v2
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen_eval_result(benchmark_dir):
    store = LedgerStore(":memory:")
    result = process_batch(store=store, benchmark_dir=benchmark_dir, split="frozen-eval")
    yield result
    store.close()


def _rule_distribution(decisions, labels) -> dict[str, Counter]:
    per_tier: dict[str, Counter] = {}
    for decision in decisions:
        tier = labels[decision.case_id]["tier"]
        key = (
            decision.rule_id
            if decision.status is DecisionStatus.RESOLVED
            else f"unresolved:{decision.rule_id}"
        )
        per_tier.setdefault(tier, Counter())[key] += 1
    return per_tier


class TestCrossSplitRuleDistribution:
    """Both splits must resolve each tier by the rule that tier is defined by.

    This is the assertion whose absence let v2 ship. Accuracy was identical
    across splits; only the mechanism differed.
    """

    def test_dev_t0_resolves_entirely_by_direct_key(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        per_tier = _rule_distribution(result.decisions, dev_ground_truth)
        assert per_tier["T0"] == Counter({RULE_DIRECT_KEY_EXACT_TOKEN: 350})

    def test_frozen_eval_t0_resolves_entirely_by_direct_key(
        self, frozen_eval_result, frozen_eval_tier_labels
    ):
        """v2 scored 175 here. Anything below 350 means the defect is back."""
        per_tier = _rule_distribution(frozen_eval_result.decisions, frozen_eval_tier_labels)
        assert per_tier["T0"] == Counter({RULE_DIRECT_KEY_EXACT_TOKEN: 350})

    def test_dev_t1_resolves_entirely_by_derivation(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        per_tier = _rule_distribution(result.decisions, dev_ground_truth)
        assert per_tier["T1"] == Counter({RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING: 300})

    def test_frozen_eval_t1_resolves_entirely_by_derivation(
        self, frozen_eval_result, frozen_eval_tier_labels
    ):
        per_tier = _rule_distribution(frozen_eval_result.decisions, frozen_eval_tier_labels)
        assert per_tier["T1"] == Counter({RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING: 300})

    @pytest.mark.parametrize("tier,count", [("T2", 200), ("T3", 40)])
    def test_dev_degraded_and_ambiguous_tiers_are_refused(
        self, dev_result, dev_ground_truth, tier, count
    ):
        result, _ = dev_result
        per_tier = _rule_distribution(result.decisions, dev_ground_truth)
        assert sum(per_tier[tier].values()) == count
        assert all(key.startswith("unresolved:") for key in per_tier[tier])

    @pytest.mark.parametrize("tier,count", [("T2", 200), ("T3", 40)])
    def test_frozen_eval_degraded_and_ambiguous_tiers_are_refused(
        self, frozen_eval_result, frozen_eval_tier_labels, tier, count
    ):
        per_tier = _rule_distribution(frozen_eval_result.decisions, frozen_eval_tier_labels)
        assert sum(per_tier[tier].values()) == count
        assert all(key.startswith("unresolved:") for key in per_tier[tier])

    def test_the_two_splits_agree_on_rule_distribution_per_tier(
        self, dev_result, dev_ground_truth, frozen_eval_result, frozen_eval_tier_labels
    ):
        """The general form of the invariant, not just the four cases above."""
        dev_res, _ = dev_result
        dev_dist = _rule_distribution(dev_res.decisions, dev_ground_truth)
        frozen_dist = _rule_distribution(frozen_eval_result.decisions, frozen_eval_tier_labels)
        assert set(dev_dist) == set(frozen_dist)
        for tier in sorted(dev_dist):
            assert dev_dist[tier] == frozen_dist[tier], tier

    @pytest.mark.parametrize("tier", ["T1", "T2", "T3"])
    def test_no_non_t0_case_is_reached_by_the_direct_key_matcher_on_dev(
        self, dev_result, dev_ground_truth, tier
    ):
        result, _ = dev_result
        for decision in result.decisions:
            if dev_ground_truth[decision.case_id]["tier"] != tier:
                continue
            assert decision.matcher_id != DIRECT_KEY_MATCHER_ID or (
                decision.status is DecisionStatus.UNRESOLVED
            ), decision.case_id

    @pytest.mark.parametrize("tier", ["T1", "T2", "T3"])
    def test_no_non_t0_case_is_reached_by_the_direct_key_matcher_on_frozen_eval(
        self, frozen_eval_result, frozen_eval_tier_labels, tier
    ):
        for decision in frozen_eval_result.decisions:
            if frozen_eval_tier_labels[decision.case_id]["tier"] != tier:
                continue
            assert decision.matcher_id != DIRECT_KEY_MATCHER_ID or (
                decision.status is DecisionStatus.UNRESOLVED
            ), decision.case_id


class TestBothT0ArchetypesAreExercised:
    """T0's two archetypes test different keys; both must be present and both must join."""

    def test_dev_exercises_both_archetypes(self, dev_ground_truth):
        counts = Counter(
            entry["archetype"] for entry in dev_ground_truth.values() if entry["tier"] == "T0"
        )
        assert counts == Counter({name: 175 for name in T0_ARCHETYPES})

    def test_frozen_eval_exercises_both_archetypes(self, frozen_eval_tier_labels):
        counts = Counter(
            entry["archetype"]
            for entry in frozen_eval_tier_labels.values()
            if entry["tier"] == "T0"
        )
        assert counts == Counter({name: 175 for name in T0_ARCHETYPES})

    @pytest.mark.parametrize("archetype", T0_ARCHETYPES)
    def test_each_dev_archetype_resolves_by_direct_key(
        self, dev_result, dev_ground_truth, archetype
    ):
        result, _ = dev_result
        seen = 0
        for decision in result.decisions:
            entry = dev_ground_truth[decision.case_id]
            if entry["tier"] != "T0" or entry["archetype"] != archetype:
                continue
            seen += 1
            assert decision.rule_id == RULE_DIRECT_KEY_EXACT_TOKEN, decision.case_id
        assert seen == 175

    @pytest.mark.parametrize("archetype", T0_ARCHETYPES)
    def test_each_frozen_eval_archetype_resolves_by_direct_key(
        self, frozen_eval_result, frozen_eval_tier_labels, archetype
    ):
        """``settlement_id_clean_direct_key`` is the archetype v2 broke here."""
        seen = 0
        for decision in frozen_eval_result.decisions:
            entry = frozen_eval_tier_labels[decision.case_id]
            if entry["tier"] != "T0" or entry["archetype"] != archetype:
                continue
            seen += 1
            assert decision.rule_id == RULE_DIRECT_KEY_EXACT_TOKEN, decision.case_id
        assert seen == 175
