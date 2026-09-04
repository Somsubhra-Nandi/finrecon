"""Product-facing, offline replay for the complete Frozen Eval v3 benchmark.

The security boundary in this module is intentionally visible in the order of
operations.  Stage 2 and the cached Stage-3 replay finish before the Stage-4
section imports or reads hidden truth.  No truth-derived value is passed back
into reconciliation, validation, or policy.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import DecisionStatus
from finrecon.pipeline import process_batch

from benchmark.eval.errors import EvaluationInputError, ReplayIntegrityError
from benchmark.eval.replay import replay_cohort
from benchmark.eval.scoring import CaseVerdict, aggregate_scores, verdict_for
from benchmark.eval.sources import load_cache_dir


BENCHMARK_ID = "frozen-eval-v3"
SPLIT = "frozen-eval"
PROVIDER_ID = "gorouter"
MODEL = "claude-opus-5-thinking"
TRAJECTORY_DIRECTORY = "frozen-eval-v3-opus5-thinking-final"
ORIGINAL_FAILURE_DIRECTORY = "frozen-eval-v3-opus5-thinking-t2-provider-failures-original"
RECOVERED_REPORT = "frozen-eval-v3-opus5-thinking-provider-recovered-240.json"
OPERATIONAL_REPORT = "frozen-eval-v3-opus5-thinking-operational-raw-240.json"
HASH_MANIFEST = "frozen-eval-v3-opus5-thinking-hashes.txt"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvaluationInputError(f"{path.name} must contain a JSON object")
    return value


def _read_hash_manifest(path: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "_sha256=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ReplayIntegrityError(f"invalid SHA-256 value for {key!r}")
        hashes[key] = value
    if len(hashes) != 4:
        raise ReplayIntegrityError("the Frozen Eval v3 hash manifest is incomplete")
    return hashes


def _canonical_bytes(data: bytes) -> bytes:
    """Return ``data`` with line endings normalised to CRLF.

    The published Frozen Eval v3 hashes were computed against CRLF bytes.  How
    a machine materialises those same committed blobs is a delivery detail, not
    a change of content: a git checkout honours the ``eol=crlf`` pin in
    ``.gitattributes`` and produces CRLF, while a source tarball or archive
    export of the identical commit produces the stored LF bytes.  Hashing raw
    bytes therefore made the integrity check depend on how the code arrived
    rather than on whether an artifact had actually changed, and it failed
    closed on tarball-based deploys.  Normalising first removes that variable
    and leaves the published hash values unchanged; a real edit to an artifact
    still changes the digest.
    """

    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def _directory_hash(directory: Path) -> str:
    lines = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name):
        digest = hashlib.sha256(_canonical_bytes(path.read_bytes())).hexdigest()
        lines.append(f"{path.name}\t{digest}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(_canonical_bytes(path.read_bytes())).hexdigest()


def _stage2_verdict(decision: Any, truth: Any) -> CaseVerdict:
    predicted = tuple(decision.settlement_ids)
    correct = truth.correct_relationship is not None and predicted == truth.expected_settlement_ids
    return CaseVerdict(
        case_id=decision.case_id,
        tier=truth.tier,
        archetype=truth.archetype,
        resolved=True,
        correct=correct,
        wrong_reason=None if correct else "resolved to the wrong settlement",
        predicted_candidate_id=None,
        predicted_settlement_ids=predicted,
        truth_settlement_ids=truth.expected_settlement_ids,
        truth_reference=truth.true_reference,
        termination_reason="stage2_deterministic_resolution",
        blockers=(),
        evidence_relations=(),
        value_at_stake_paise=truth.value_at_stake_paise,
        is_uniquely_resolvable=truth.is_uniquely_resolvable,
        escalation_correct=None,
    )


def _provenance(report: dict[str, Any], *, label: str) -> dict[str, Any]:
    metrics = report["metrics"]
    terminations = report.get("agent", {}).get("termination_reasons", {})
    t2_cases = int(report.get("cohort", {}).get("tier_counts", {}).get("T2", 0))
    provider_failures = int(terminations.get("provider_infrastructure_failure", 0))
    # The operational report has 7 safe T3 provider failures as well as the
    # 13 T2 failures.  Derive T2 failures from its resolvable denominator.
    t2_provider_failures = t2_cases - int(metrics["correct_auto_resolutions"])
    return {
        "label": label,
        "t2_cases": t2_cases,
        "t2_correctly_resolved": int(metrics["correct_auto_resolutions"]),
        "t2_provider_infrastructure_failures": t2_provider_failures,
        "wrong_auto_resolutions": int(metrics["wrong_auto_resolutions"]),
        "termination_reasons": {
            "investigation_complete": int(terminations.get("investigation_complete", 0)),
            "provider_infrastructure_failure": provider_failures,
        },
    }


def run_frozen_v3_replay(project_root: Path) -> dict[str, Any]:
    """Execute the complete replay and return a truth-safe product projection."""
    benchmark_root = project_root / "benchmark"
    trajectory_root = project_root / "fixtures" / "trajectories"
    trajectory_dir = trajectory_root / TRAJECTORY_DIRECTORY

    # Reconciliation boundary: visible inputs and recorded trajectories only.
    with LedgerStore(":memory:") as store:
        stage2_batch = process_batch(
            store=store, benchmark_dir=benchmark_root, split=SPLIT
        )
    stage2_resolved = tuple(
        decision
        for decision in stage2_batch.decisions
        if decision.status is DecisionStatus.RESOLVED
    )
    residual_ids = tuple(sorted(snapshot.case_id for snapshot in stage2_batch.snapshots))

    records = load_cache_dir(trajectory_dir)
    by_case = {record.case_id: record for record in records}
    if len(records) != len(by_case):
        raise ReplayIntegrityError("the canonical trajectory corpus contains duplicate case IDs")
    if set(by_case) != set(residual_ids):
        missing = sorted(set(residual_ids) - set(by_case))
        extra = sorted(set(by_case) - set(residual_ids))
        raise EvaluationInputError(
            f"canonical trajectory cohort mismatch: {len(missing)} missing, {len(extra)} extra"
        )

    with tempfile.TemporaryDirectory(prefix="finrecon-frozen-v3-replay-") as staging:
        replay = replay_cohort(
            benchmark_dir=benchmark_root,
            split=SPLIT,
            records=by_case,
            cohort=residual_ids,
            provider_id=PROVIDER_ID,
            model=MODEL,
            staging_dir=Path(staging),
        )
    if replay.provider_calls_made or replay.cache_hits != len(residual_ids):
        raise ReplayIntegrityError("Frozen Eval v3 replay did not remain fully offline")

    # Stage-4 boundary starts here.  Reconciliation outcomes already exist.
    from benchmark.eval.groundtruth import load_ground_truth

    manifest = _read_json(benchmark_root / "manifests" / "v3.json")
    frozen_hash = compute_fingerprint(benchmark_root, SPLIT)
    if frozen_hash != manifest["frozen_eval_sha256"]:
        raise ReplayIntegrityError("Frozen Eval v3 benchmark fingerprint mismatch")
    truth = load_ground_truth(benchmark_root, SPLIT, allow_frozen_truth=True)

    stage2_verdicts = tuple(_stage2_verdict(item, truth[item.case_id]) for item in stage2_resolved)
    stage3_verdicts = tuple(
        verdict_for(outcome, truth[outcome.case_id]) for outcome in replay.stage3.outcomes
    )
    verdicts = stage2_verdicts + stage3_verdicts
    metrics = aggregate_scores(verdicts)
    by_tier = {
        tier: aggregate_scores(item for item in verdicts if item.tier == tier)
        for tier in ("T0", "T1", "T2", "T3")
    }

    hashes = _read_hash_manifest(benchmark_root / "reports" / HASH_MANIFEST)
    actual_hashes = {
        "canonical_240_trajectory_corpus_sha256": _directory_hash(trajectory_dir),
        "original_13_t2_provider_failures_sha256": _directory_hash(
            trajectory_root / ORIGINAL_FAILURE_DIRECTORY
        ),
        "operational_raw_report_sha256": _file_hash(
            benchmark_root / "reports" / OPERATIONAL_REPORT
        ),
        "provider_recovered_report_sha256": _file_hash(
            benchmark_root / "reports" / RECOVERED_REPORT
        ),
    }
    if actual_hashes != hashes:
        raise ReplayIntegrityError("one or more Frozen Eval v3 artifact hashes changed")

    recovered = _read_json(benchmark_root / "reports" / RECOVERED_REPORT)
    operational = _read_json(benchmark_root / "reports" / OPERATIONAL_REPORT)
    t3_outcomes = [
        outcome for outcome in replay.stage3.outcomes if truth[outcome.case_id].tier == "T3"
    ]
    t3_terminations = Counter(outcome.trajectory.termination_reason for outcome in t3_outcomes)

    total_cases = len(verdicts)
    residual_cases = len(stage3_verdicts)
    t2_cases = by_tier["T2"]["investigated"]
    t2_correctly_resolved = by_tier["T2"]["correct_auto_resolutions"]
    t3_cases = by_tier["T3"]["investigated"]
    t3_safely_escalated = by_tier["T3"]["correctly_escalated"]
    total_correct_auto_resolutions = len(stage2_resolved) + t2_correctly_resolved

    # Full-suite and residual metrics are deliberately kept separate.  Fail
    # closed if artifacts from incompatible benchmark cohorts are ever mixed.
    if total_cases != len(stage2_resolved) + t2_correctly_resolved + t3_safely_escalated:
        raise ReplayIntegrityError("full-suite case counts do not reconcile")
    if residual_cases != t2_cases + t3_cases:
        raise ReplayIntegrityError("Stage-3 residual cohort counts do not reconcile")
    if total_correct_auto_resolutions != metrics["correct_auto_resolutions"]:
        raise ReplayIntegrityError("full-suite automatic-resolution counts do not reconcile")

    return {
        "benchmark_id": BENCHMARK_ID,
        "mode": "offline_replay",
        "provider_calls": 0,
        "provider_calls_made": False,
        "total_cases": total_cases,
        "total_correct_auto_resolutions": total_correct_auto_resolutions,
        "stage2": {"cases": len(stage2_resolved), "resolved": len(stage2_resolved)},
        "stage3": {
            "residual_cases": residual_cases,
            "trajectory_cache_hits": replay.cache_hits,
            "t2": {
                "cases": t2_cases,
                "correctly_resolved": t2_correctly_resolved,
            },
            "t3": {
                "cases": t3_cases,
                "safely_escalated": t3_safely_escalated,
                "termination_reasons": dict(sorted(t3_terminations.items())),
            },
        },
        "evaluation": {
            "resolvable_cases": metrics["uniquely_resolvable_cases"],
            "correct_resolutions": metrics["correct_auto_resolutions"],
            "ambiguous_cases": metrics["truly_ambiguous_cases"],
            "safely_escalated": metrics["correctly_escalated"],
            "wrong_auto_resolutions": metrics["wrong_auto_resolutions"],
            "resolvable_match_rate": metrics["overall_match_rate"],
            "auto_resolution_precision": metrics["auto_resolution_accuracy"],
            "unsafe_auto_match_rate": metrics["unsafe_auto_match_rate"],
            "value_at_risk_paise": metrics["value_at_risk_paise"],
            "soundness_violations": int(recovered.get("soundness", {}).get("total_violations", 0)),
            "tool_validation_failures": int(recovered.get("agent", {}).get("tool_validation_failed", 0)),
            "validation_rejections": int(recovered.get("agent", {}).get("tool_validation_rejections_total", 0)),
            "budget_exhausted": int(recovered.get("agent", {}).get("tool_budget_exhausted_cases", 0)),
        },
        "phases": [
            {"id": "load", "label": "Loading frozen evaluation", "count": total_cases, "unit": "cases"},
            {"id": "stage2", "label": "Rules-based matching", "count": len(stage2_resolved), "unit": "resolved"},
            {"id": "trajectories", "label": "Loading frozen investigation trajectories", "count": replay.cache_hits, "total": len(residual_ids)},
            {"id": "validation", "label": "Deterministic validation", "count": by_tier["T2"]["correct_auto_resolutions"], "unit": "resolved"},
            {"id": "policy", "label": "Financial resolution policy", "count": by_tier["T3"]["correctly_escalated"], "unit": "escalated"},
            {"id": "evaluation", "label": "Offline benchmark verification", "status": "complete"},
        ],
        "provenance": {
            "provider_recovered": _provenance(recovered, label="Provider-recovered frozen corpus"),
            "operational": _provenance(operational, label="Original operational run"),
            "retry_contract": ["requested model", "prompt", "tools", "validator", "policy", "investigation budget"],
            "original_failed_trajectories_preserved": len(list((trajectory_root / ORIGINAL_FAILURE_DIRECTORY).glob("*.json"))),
        },
        "integrity": {
            "frozen": True,
            "verified": True,
            "benchmark_sha256": frozen_hash,
            **hashes,
        },
    }


__all__ = ["run_frozen_v3_replay"]
