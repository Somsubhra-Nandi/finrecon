"""Construction, leakage, oracle, baseline and evaluator checks for bounded-search-v1."""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path

import pytest

from benchmark.eval.compare import compare
from benchmark.eval.scoring import CaseVerdict, aggregate_scores
from benchmark.search_challenge import run_mechanical, run_oracle
from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.benchmark.generator_search.config import (
    BENCHMARK_NAME,
    FAMILY_COUNTS,
    MAX_MODEL_STEPS,
    MAX_TOOL_CALLS_PER_STEP,
    MIN_PLAUSIBLE_EVIDENCE_ACTIONS,
    TOOL_CALL_BUDGET,
)
from finrecon.benchmark.generator_search.dataset import build_search_dataset
from finrecon.benchmark.generator_search.manifest import compute_search_fingerprint
from finrecon.benchmark.generator_search.serialize import write_search_dataset
from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import DecisionStatus
from finrecon.pipeline import process_batch

EXPECTED_SEARCH_SHA256 = "e2142a61275a681971cc6d14a02d9c3a8439cb797972a32e072518a09ebb9958"
EXPECTED_V3_SHA256 = "f9eb8770be6cc216d1c8b5486a10b74005382141f7c079844e2748444a44fc5b"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _longest_run(values) -> int:
    return max(len(list(group)) for _value, group in itertools.groupby(values))


@pytest.fixture(scope="session")
def search_truth(benchmark_dir) -> list[dict]:
    return _jsonl(benchmark_dir / "ground_truth" / f"{BENCHMARK_NAME}.jsonl")


@pytest.fixture(scope="session")
def search_batch(benchmark_dir):
    with LedgerStore(":memory:") as store:
        return process_batch(store=store, benchmark_dir=benchmark_dir, split=BENCHMARK_NAME)


@pytest.fixture(scope="session")
def search_oracle_report(benchmark_dir) -> dict:
    return run_oracle(benchmark_dir)


@pytest.fixture(scope="session")
def search_mechanical_report(benchmark_dir) -> dict:
    return run_mechanical(benchmark_dir)


def test_case_counts_families_outcomes_and_candidate_distribution(
    benchmark_dir, search_truth
):
    manifest = json.loads(
        (benchmark_dir / "manifests" / f"{BENCHMARK_NAME}.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(search_truth) == manifest["case_count"] == 50
    assert Counter(row["required_outcome"] for row in search_truth) == {
        "AUTO_RESOLVABLE": 40,
        "ESCALATE": 10,
    }
    assert Counter(row["archetype"] for row in search_truth) == FAMILY_COUNTS
    assert Counter(row["expected_candidate_count"] for row in search_truth) == {
        3: 32,
        4: 9,
        5: 9,
    }
    assert all(3 <= row["expected_candidate_count"] <= 5 for row in search_truth)


def test_stage_two_preserves_identical_unranked_candidate_facts(search_batch, search_truth):
    truth_by_case = {
        f"case:{row['record_ids']['bank_records'][0]}": row for row in search_truth
    }
    assert len(search_batch.snapshots) == len(search_batch.decisions) == 50
    assert all(
        decision.status is DecisionStatus.UNRESOLVED
        for decision in search_batch.decisions
    )

    truth_positions = []
    for snapshot in search_batch.snapshots:
        row = truth_by_case[snapshot.case_id]
        assert len(snapshot.candidates) == row["expected_candidate_count"]
        assert len({candidate.total_paise for candidate in snapshot.candidates}) == 1
        assert len({tuple(candidate.settlement_dates) for candidate in snapshot.candidates}) == 1
        relationship = row["correct_relationship"]
        if relationship is not None:
            wanted = tuple(relationship["settlement_ids"])
            truth_positions.append(
                next(
                    index
                    for index, candidate in enumerate(snapshot.candidates)
                    if tuple(candidate.settlement_ids) == wanted
                )
            )

    # Truth moves through the production-sorted candidate tuple; position is
    # not a usable winner rule even though the snapshot itself is immutable.
    positions = Counter(truth_positions)
    assert len(positions) >= 4
    assert max(positions.values()) < 20


def test_visible_data_has_no_answer_markers_or_hidden_labels(benchmark_dir, search_truth):
    visible_root = benchmark_dir / "datasets" / BENCHMARK_NAME
    visible = "\n".join(path.read_text(encoding="utf-8") for path in visible_root.glob("*.jsonl"))
    forbidden = (
        "VALDT",
        "TRUTH=",
        "WINNER=",
        "EXPECTED=",
        "AUTO_RESOLVABLE",
        "ESCALATE",
        "required_outcome",
        "correct_relationship",
        *FAMILY_COUNTS,
    )
    for marker in forbidden:
        assert marker not in visible

    assert _longest_run(row["required_outcome"] for row in search_truth) <= 6
    assert _longest_run(row["archetype"] for row in search_truth) <= 2


def test_source_order_and_irrelevant_evidence_are_seeded_and_shuffled(
    benchmark_dir, search_truth
):
    visible_root = benchmark_dir / "datasets" / BENCHMARK_NAME
    id_fields = {
        "bank_records": "bank_record_id",
        "orders": "order_id",
        "payments": "payment_id",
        "refunds": "refund_id",
        "settlements": "settlement_id",
    }
    for name, field in id_fields.items():
        ids = [row[field] for row in _jsonl(visible_root / f"{name}.jsonl")]
        assert ids != sorted(ids), name

    for row in search_truth:
        assert row["plausible_evidence_action_count"] >= MIN_PLAUSIBLE_EVIDENCE_ACTIONS
        assert len(row["irrelevant_evidence_tokens"]) == 21


def test_regeneration_is_byte_reproducible_and_matches_manifest(
    benchmark_dir, tmp_path
):
    bundle = build_search_dataset()
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_search_dataset(bundle, first)
    write_search_dataset(bundle, second)
    assert compute_search_fingerprint(first) == EXPECTED_SEARCH_SHA256
    assert compute_search_fingerprint(second) == EXPECTED_SEARCH_SHA256
    assert compute_search_fingerprint(benchmark_dir) == EXPECTED_SEARCH_SHA256

    for relative in (
        f"datasets/{BENCHMARK_NAME}/bank_records.jsonl",
        f"datasets/{BENCHMARK_NAME}/orders.jsonl",
        f"datasets/{BENCHMARK_NAME}/payments.jsonl",
        f"datasets/{BENCHMARK_NAME}/refunds.jsonl",
        f"datasets/{BENCHMARK_NAME}/settlements.jsonl",
        f"ground_truth/{BENCHMARK_NAME}.jsonl",
        f"cohorts/{BENCHMARK_NAME}.json",
    ):
        assert (first / relative).read_bytes() == (second / relative).read_bytes()
        assert (first / relative).read_bytes() == (benchmark_dir / relative).read_bytes()


def test_oracle_proves_resolvability_and_true_ambiguity(search_oracle_report):
    report = search_oracle_report
    assert report["benchmark_sha256"] == EXPECTED_SEARCH_SHA256
    assert report["resolvable_cases_verified"] == 40
    assert report["ambiguous_cases_verified"] == 10
    assert report["correct_auto_resolutions"] == 40
    assert report["wrong_auto_resolutions"] == 0
    assert report["escalated"] == 10
    assert report["ground_truth_read_during_search"] is False
    assert report["provider_calls_made"] is False


def test_mechanical_baseline_is_strong_safe_and_budget_bounded(
    search_mechanical_report,
):
    report = search_mechanical_report
    metrics = report["metrics"]
    agent = report["agent"]
    config = report["configuration"]
    assert report["challenge"]["benchmark_sha256"] == EXPECTED_SEARCH_SHA256
    assert 15 <= metrics["correct_auto_resolutions"] <= 25
    assert metrics["wrong_auto_resolutions"] == 0
    assert metrics["value_at_risk_paise"] == 0
    assert metrics["correctly_escalated"] == 10
    assert config["max_steps"] == MAX_MODEL_STEPS
    assert config["max_tool_calls_per_step"] == MAX_TOOL_CALLS_PER_STEP
    assert agent["tool_calls_executed_total"] <= 50 * TOOL_CALL_BUDGET
    assert agent["tool_calls_max_per_case"] <= TOOL_CALL_BUDGET
    assert agent["tool_budget_exhausted_cases"] > 0
    assert report["challenge"]["ground_truth_read_during_inference"] is False
    assert report["challenge"]["provider_calls_made"] is False


def test_evaluator_reports_family_tool_use_and_comparison(search_mechanical_report):
    families = search_mechanical_report["metrics_by_family"]
    assert set(families) == set(FAMILY_COUNTS)
    assert sum(slot["cases"] for slot in families.values()) == 50
    assert sum(slot["tool_calls_executed_total"] for slot in families.values()) == 151
    assert families["ambiguity_controls"]["correctly_escalated"] == 10

    comparison = compare(
        search_mechanical_report,
        search_mechanical_report,
        label_a="mechanical-a",
        label_b="mechanical-b",
    )
    assert comparison["cohort_identity"]["comparable"] is True
    assert comparison["configuration"]["differing_dimensions"] == []
    refund_rows = {
        row["metric"]: row
        for row in comparison["by_family_side_by_side"]["refund_linked_reasoning"]
    }
    assert refund_rows["tool_calls_executed_total"]["a"] == 22
    assert refund_rows["tool_calls_executed_total"]["delta"] == 0


def _verdict(*, case_id: str, correct: bool, stake: int) -> CaseVerdict:
    return CaseVerdict(
        case_id=case_id,
        tier="SEARCH",
        archetype="unit",
        resolved=True,
        correct=correct,
        wrong_reason=None if correct else "wrong",
        predicted_candidate_id="candidate",
        predicted_settlement_ids=("settlement",),
        truth_settlement_ids=("settlement",) if correct else ("other",),
        truth_reference=None,
        termination_reason="deterministic_policy_resolved",
        blockers=(),
        evidence_relations=(),
        value_at_stake_paise=stake,
        is_uniquely_resolvable=True,
        escalation_correct=None,
    )


def test_value_at_risk_counts_only_unsafe_automatic_resolutions():
    scores = aggregate_scores(
        (_verdict(case_id="correct", correct=True, stake=100),
         _verdict(case_id="wrong", correct=False, stake=725_019))
    )
    assert scores["wrong_auto_resolutions"] == 1
    assert scores["value_at_risk_paise"] == 725_019


def test_frozen_v3_hash_remains_unchanged(benchmark_dir):
    assert compute_fingerprint(benchmark_dir, "frozen-eval") == EXPECTED_V3_SHA256
