"""Score the arms, audit the split for leakage, and emit one machine-readable report.

Order matters here and mirrors :mod:`benchmark.eval.evaluate`: every arm makes
all of its decisions **before** any ground truth is loaded into the same call
frame as a decision. Truth arrives afterwards, and only to compare.

The correctness predicate
-------------------------

:func:`score_prediction` restates the predicate in
:func:`benchmark.eval.scoring.verdict_for` -- an auto-resolution of a case with
no correct answer is wrong; otherwise the resolved settlement set must equal
the expected one exactly. It is restated rather than reused because
``verdict_for`` needs a :class:`finrecon.stage3.CaseOutcome`, which arms A, C1,
C2 and C3 do not produce and cannot: they are not the shipped gate.

A second definition of correctness is exactly the drift risk the Stage-4
scorer's docstring warns about, so it is closed the same way that one closes
it -- ``tests/test_v4_baselines.py`` runs arm B, which *does* produce genuine
``CaseOutcome`` objects, through both predicates and requires them to agree
case for case.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import DecisionStatus
from finrecon.pipeline import BatchResult, process_batch

from benchmark.baselines import BASELINE_SUITE_VERSION
from benchmark.baselines.arms import (
    ARM_A,
    ARMS,
    SNAPSHOT_ARMS,
    ArmPrediction,
    arm_a_rules_only,
)
from benchmark.eval.groundtruth import GroundTruthEntry, load_ground_truth
from benchmark.eval.scoring import WRONG_NO_CORRECT_ANSWER, WRONG_SETTLEMENT


@dataclass(frozen=True)
class ScoredPrediction:
    prediction: ArmPrediction
    correct: bool | None
    wrong_reason: str | None
    escalation_correct: bool | None


def score_prediction(
    prediction: ArmPrediction, entry: GroundTruthEntry
) -> ScoredPrediction:
    """The section 5.3 correctness predicate, applied to an arm's decision."""
    if not prediction.resolved:
        return ScoredPrediction(
            prediction=prediction,
            correct=None,
            wrong_reason=None,
            escalation_correct=not entry.is_uniquely_resolvable,
        )
    if entry.correct_relationship is None:
        return ScoredPrediction(prediction, False, WRONG_NO_CORRECT_ANSWER, None)
    if entry.expected_settlement_ids != prediction.settlement_ids:
        return ScoredPrediction(prediction, False, WRONG_SETTLEMENT, None)
    return ScoredPrediction(prediction, True, None, None)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _arm_metrics(scored: list[ScoredPrediction], truth: dict[str, GroundTruthEntry]) -> dict:
    resolved = [s for s in scored if s.prediction.resolved]
    correct = [s for s in resolved if s.correct]
    wrong = [s for s in resolved if s.correct is False]
    resolvable = [s for s in scored if truth[s.prediction.case_id].is_uniquely_resolvable]
    ambiguous = [s for s in scored if not truth[s.prediction.case_id].is_uniquely_resolvable]
    correctly_escalated = [s for s in scored if not s.prediction.resolved and s.escalation_correct]
    falsely_escalated = [
        s
        for s in scored
        if not s.prediction.resolved and truth[s.prediction.case_id].is_uniquely_resolvable
    ]
    return {
        "cases": len(scored),
        "resolved": len(resolved),
        "correct": len(correct),
        "wrong": len(wrong),
        "escalated": len(scored) - len(resolved),
        "correct_escalations": len(correctly_escalated),
        "false_escalations": len(falsely_escalated),
        "match_rate": _ratio(len(correct), len(resolvable)),
        "auto_resolution_accuracy": _ratio(len(correct), len(resolved)),
        "unsafe_auto_match_rate": _ratio(len(wrong), len(scored)),
        "escalation_recall": _ratio(len(correctly_escalated), len(ambiguous)),
        "value_at_risk_paise": sum(
            truth[s.prediction.case_id].value_at_stake_paise for s in wrong
        ),
        "stop_reasons": dict(sorted(Counter(s.prediction.reason for s in scored).items())),
        "minimal_lexical_arity": dict(
            sorted(
                Counter(
                    "none" if s.prediction.minimal_arity is None else str(s.prediction.minimal_arity)
                    for s in scored
                ).items()
            )
        ),
    }


def _slice(
    scored: list[ScoredPrediction],
    truth: dict[str, GroundTruthEntry],
    key,
) -> dict:
    grouped: dict[str, list[ScoredPrediction]] = {}
    for item in scored:
        for label in key(truth[item.prediction.case_id]):
            grouped.setdefault(label, []).append(item)
    return {label: _arm_metrics(items, truth) for label, items in sorted(grouped.items())}


# --- leakage audit ---------------------------------------------------------


def leakage_audit(
    batch: BatchResult, truth: dict[str, GroundTruthEntry], benchmark_dir: Path, split: str
) -> dict:
    """Does anything visible predict the answer without reading the evidence?

    Every check below is a *predictor* test, not a formatting test: it asks
    whether a rule stated purely over visible structure -- candidate order,
    identifier ordinals, candidate count, narration length -- would score above
    chance. A benchmark can be perfectly well formed and still be solvable by
    "pick the lowest settlement ID", and that is the failure this audit is for.
    """
    positions: Counter = Counter()
    position_by_count: dict[str, Counter] = {}
    lowest_id = 0
    highest_id = 0
    resolvable = 0

    for snapshot in batch.snapshots:
        entry = truth[snapshot.case_id]
        if entry.correct_relationship is None:
            continue
        resolvable += 1
        ordered = sorted(snapshot.candidates, key=lambda c: c.settlement_ids)
        expected = entry.expected_settlement_ids
        index = next(
            i for i, c in enumerate(ordered) if tuple(sorted(c.settlement_ids)) == expected
        )
        positions[index] += 1
        bucket = str(len(ordered))
        position_by_count.setdefault(bucket, Counter())[index] += 1
        if index == 0:
            lowest_id += 1
        if index == len(ordered) - 1:
            highest_id += 1

    outcome_by_count: dict[str, Counter] = {}
    outcome_by_narration_length: dict[str, Counter] = {}
    for snapshot in batch.snapshots:
        entry = truth[snapshot.case_id]
        outcome = entry.required_outcome
        outcome_by_count.setdefault(str(len(snapshot.candidates)), Counter())[outcome] += 1
        length = len(snapshot.base_evidence.bank_record.narration)
        bucket = f"{(length // 20) * 20}-{(length // 20) * 20 + 19}"
        outcome_by_narration_length.setdefault(bucket, Counter())[outcome] += 1

    outcome_by_shape: dict[str, Counter] = {}
    for snapshot in batch.snapshots:
        shape = narration_shape(snapshot.base_evidence.bank_record.narration)
        outcome_by_shape.setdefault(shape, Counter())[
            truth[snapshot.case_id].required_outcome
        ] += 1

    ordered_cases = sorted(truth.values(), key=lambda e: e.case_id)
    archetype_runs = _longest_run([entry.archetype for entry in ordered_cases])
    outcome_runs = _longest_run([entry.required_outcome for entry in ordered_cases])

    labels = set()
    for entry in truth.values():
        labels.update(entry.families)
        labels.add(entry.archetype)
        if entry.required_composition:
            labels.add(entry.required_composition)
    label_hits = _labels_in_visible_files(benchmark_dir, split, labels)

    return {
        "resolvable_cases_audited": resolvable,
        "truth_position_counts": dict(sorted(positions.items())),
        "truth_position_share": {
            str(index): round(count / resolvable, 4) for index, count in sorted(positions.items())
        }
        if resolvable
        else {},
        "truth_position_by_candidate_count": {
            bucket: dict(sorted(counter.items()))
            for bucket, counter in sorted(position_by_count.items())
        },
        "truth_is_lowest_settlement_id": lowest_id,
        "truth_is_highest_settlement_id": highest_id,
        "required_outcome_by_candidate_count": {
            bucket: dict(sorted(counter.items()))
            for bucket, counter in sorted(outcome_by_count.items())
        },
        "required_outcome_by_narration_length": {
            bucket: dict(sorted(counter.items()))
            for bucket, counter in sorted(outcome_by_narration_length.items())
        },
        "required_outcome_by_narration_shape": {
            shape: dict(sorted(counter.items()))
            for shape, counter in sorted(outcome_by_shape.items())
        },
        "cases_in_outcome_pure_narration_shapes": sum(
            sum(counter.values())
            for counter in outcome_by_shape.values()
            if len(counter) == 1
        ),
        "longest_same_archetype_run_in_case_id_order": archetype_runs,
        "longest_same_outcome_run_in_case_id_order": outcome_runs,
        "benchmark_labels_found_in_visible_files": sorted(label_hits),
    }


def narration_shape(narration: str) -> str:
    """A coarse label for which *kind* of narration this is.

    Derived from the narration alone, with no reference to the generator's
    templates, because that is the only way a solver could derive it too. It
    exists so the audit can answer an uncomfortable question out loud: does
    the *shape* of a bank line predict whether its case is resolvable?

    On the v4 pilot the answer is partly yes, and the report says so rather
    than omitting the check. The mitigation is not that the correlation is
    absent -- it is that knowing a case is resolvable scores nothing on its
    own. Every metric here requires naming a settlement, and no shape label
    names one.
    """
    if "RFND " in narration:
        return "reference_head_and_refund_amount"
    if "VALDT " in narration:
        return "reference_head_and_value_date"
    if "REV" in narration and "PGSETL" in narration:
        return "long_truncated_reference_with_decoys"
    if "BATCH" in narration:
        return "reference_split_across_fields"
    return "no_reference_field"


def _longest_run(values: list[str]) -> int:
    best = current = 0
    previous: str | None = None
    for value in values:
        current = current + 1 if value == previous else 1
        previous = value
        best = max(best, current)
    return best


def _labels_in_visible_files(benchmark_dir: Path, split: str, labels: set[str]) -> set[str]:
    """Any benchmark-only label appearing in the system-visible dataset files.

    A direct search rather than an inference: family, archetype and composition
    names are hidden metadata, and a single one of them leaking into a
    narration or an identifier would hand the answer to a string search.
    """
    found: set[str] = set()
    directory = benchmark_dir / "datasets" / split
    for path in sorted(directory.glob("*.jsonl")):
        text = path.read_text(encoding="utf-8")
        for label in labels:
            if label and label in text:
                found.add(label)
    return found


# --- the run ---------------------------------------------------------------


def run_baselines(benchmark_dir: Path, split: str) -> dict:
    """Run every arm over one split and return the full diagnostic report."""
    with LedgerStore(":memory:") as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)

    resolved_by_stage2 = {
        decision.case_id: decision.settlement_ids
        for decision in batch.decisions
        if decision.status is DecisionStatus.RESOLVED
    }
    snapshots = {snapshot.case_id: snapshot for snapshot in batch.snapshots}
    case_ids = tuple(sorted({decision.case_id for decision in batch.decisions}))

    predictions: dict[str, list[ArmPrediction]] = {
        ARM_A: list(arm_a_rules_only(case_ids, resolved_by_stage2))
    }
    for arm, runner in SNAPSHOT_ARMS.items():
        arm_predictions = []
        for case_id in case_ids:
            snapshot = snapshots.get(case_id)
            if snapshot is None:
                # Stage 2 already resolved it; there is no case file to
                # investigate, and an arm that "resolves" it would be taking
                # credit for arm A's work.
                arm_predictions.append(
                    ArmPrediction(
                        case_id=case_id,
                        arm=arm,
                        resolved=False,
                        settlement_ids=(),
                        candidate_id=None,
                        reason="resolved_before_investigation",
                        features_used=0,
                        minimal_arity=None,
                    )
                )
                continue
            arm_predictions.append(runner(snapshot))
        predictions[arm] = arm_predictions

    truth = load_ground_truth(benchmark_dir, split)

    arms_report: dict[str, dict] = {}
    for arm in ARMS:
        scored = [score_prediction(p, truth[p.case_id]) for p in predictions[arm]]
        arms_report[arm] = {
            "overall": _arm_metrics(scored, truth),
            "by_archetype": _slice(scored, truth, lambda e: (e.archetype,)),
            "by_family": _slice(scored, truth, lambda e: e.families),
            "by_required_composition": _slice(
                scored, truth, lambda e: (e.required_composition,) if e.required_composition else ()
            ),
            "by_candidate_count": _slice(scored, truth, lambda e: (e.candidate_count_bucket,)),
            "wrong_resolutions": [
                {
                    "case_id": item.prediction.case_id,
                    "archetype": truth[item.prediction.case_id].archetype,
                    "reason": item.wrong_reason,
                    "predicted_settlement_ids": list(item.prediction.settlement_ids),
                    "truth_settlement_ids": list(
                        truth[item.prediction.case_id].expected_settlement_ids
                    ),
                    "value_at_stake_paise": truth[
                        item.prediction.case_id
                    ].value_at_stake_paise,
                }
                for item in scored
                if item.correct is False
            ],
        }

    stage2 = {
        "cases": len(batch.decisions),
        "resolved": len(resolved_by_stage2),
        "unresolved": len(batch.snapshots),
        "unresolved_rules": dict(
            sorted(
                Counter(
                    d.rule_id
                    for d in batch.decisions
                    if d.status is DecisionStatus.UNRESOLVED
                ).items()
            )
        ),
        "candidate_count_distribution": dict(
            sorted(Counter(len(s.candidates) for s in batch.snapshots).items())
        ),
        "candidates_from_exact_total_blocking": sum(
            1
            for s in batch.snapshots
            for c in s.candidates
            if c.blocking_rule == "exact_total_in_window"
        ),
        "candidates_total": sum(len(s.candidates) for s in batch.snapshots),
        "cases_where_truth_is_present_in_candidate_set": sum(
            1
            for s in batch.snapshots
            if truth[s.case_id].correct_relationship is None
            or any(
                tuple(sorted(c.settlement_ids)) == truth[s.case_id].expected_settlement_ids
                for c in s.candidates
            )
        ),
        "cases_where_truth_is_missing": [
            s.case_id
            for s in batch.snapshots
            if truth[s.case_id].correct_relationship is not None
            and not any(
                tuple(sorted(c.settlement_ids)) == truth[s.case_id].expected_settlement_ids
                for c in s.candidates
            )
        ],
        "candidate_count_matches_generator_intent": sum(
            1
            for s in batch.snapshots
            if truth[s.case_id].expected_candidate_count in (None, len(s.candidates))
        ),
    }

    return {
        "baseline_suite_version": BASELINE_SUITE_VERSION,
        "report_kind": "deterministic_baselines",
        "split": split,
        "provider_calls_made": False,
        "reads_ground_truth_during_inference": False,
        "cases": len(case_ids),
        "resolvable_cases": sum(1 for e in truth.values() if e.is_uniquely_resolvable),
        "intentionally_ambiguous_cases": sum(
            1 for e in truth.values() if not e.is_uniquely_resolvable
        ),
        "stage2": stage2,
        "arms": arms_report,
        "leakage_audit": leakage_audit(batch, truth, benchmark_dir, split),
    }


def write_report(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "ScoredPrediction",
    "leakage_audit",
    "run_baselines",
    "score_prediction",
    "write_report",
]
