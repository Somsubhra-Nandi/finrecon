"""Submission evaluation: deterministic recording followed by offline replay.

This module belongs to the evaluation boundary, never to ``src/finrecon``.
It deliberately records a small, deterministic investigator and then feeds
those records through the real replay evaluator.  The investigator is not an
LLM and its output is never represented as model quality; it exists so a
clean clone can exercise the full orchestration, validator, and policy path
without credentials or network access.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from finrecon.agent.cache import TrajectoryCache
from finrecon.agent.providers.base import ModelProvider, ModelResponse, ToolCallRequest, ToolSpec, ConversationMessage
from finrecon.agent.providers.chain import ProviderChain
from finrecon.benchmark.generator.hashing import compute_fingerprint
from finrecon.benchmark.generator_v4.manifest import compute_pilot_fingerprint
from finrecon.ledger.store import LedgerStore
from finrecon.matchers.result import DecisionStatus
from finrecon.pipeline import process_batch
from finrecon.stage3 import run_stage3

from benchmark.eval.evaluate import EvaluationConfig, evaluate
from benchmark.eval.groundtruth import load_ground_truth
from benchmark.eval.scoring import aggregate_scores, verdict_for


MECHANICAL_PROVIDER = "mechanical"
MECHANICAL_MODEL = "mechanical-investigator-v1"
SUBMISSION_EVALUATOR_VERSION = "submission-eval.v1"


class MechanicalInvestigator(ModelProvider):
    """Deterministic, non-linguistic investigator used only by this harness."""

    provider_id = MECHANICAL_PROVIDER

    @property
    def model(self) -> str:
        return MECHANICAL_MODEL

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        briefing = next(message.content for message in messages if message.role == "user")
        narration = next((line[11:] for line in briefing.splitlines() if line.startswith("narration: ")), "")
        candidates = [
            line.strip().lstrip("- ")[14:]
            for line in briefing.splitlines()
            if line.strip().lstrip("- ").startswith("candidate_id: ")
        ]
        prior_tools = sum(message.role == "tool" for message in messages)
        if prior_tools == 0:
            call = ToolCallRequest("mechanical-lookup", "lookup_candidate_records", json.dumps({"candidate_id": candidates[0]}))
            return ModelResponse(self.provider_id, self.model, "Deterministic candidate lookup.", (call,), finish_reason="tool_calls", reported_model=self.model)
        fragments = _fragments(narration)
        index = prior_tools - 1
        if index < len(fragments):
            call = ToolCallRequest(f"mechanical-fragment-{index}", "compare_reference_fragment", json.dumps({"fragment": fragments[index]}))
            return ModelResponse(self.provider_id, self.model, "Deterministic fragment comparison.", (call,), finish_reason="tool_calls", reported_model=self.model)
        return ModelResponse(self.provider_id, self.model, "No assertion; the deterministic gate decides.", finish_reason="stop", reported_model=self.model)


def _fragments(narration: str) -> tuple[str, ...]:
    import re

    values = re.findall(r"[A-Za-z0-9_*#-]+", narration) + re.findall(r"[A-Za-z0-9_]+", narration)
    unique = list(dict.fromkeys(value for value in values if len(value) >= 4))
    return tuple(sorted(unique, key=lambda value: (not any(char in value for char in "*#"), not (any(char.isalpha() for char in value) and any(char.isdigit() for char in value)), -len(value), value))[:6])


def _record(split: str, benchmark_dir: Path, directory: Path) -> tuple[int, int]:
    """Produce transient records using only visible data; no truth is loaded."""
    with LedgerStore(":memory:") as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)
        unresolved = frozenset(snapshot.case_id for snapshot in batch.snapshots)
        result = run_stage3(
            store=store, batch_result=batch, chain=ProviderChain((MechanicalInvestigator(),)),
            cache=TrajectoryCache(directory), case_ids=unresolved, write_cache=True,
        )
        return len(batch.decisions), len(result.outcomes)


def _stage3_report(split: str, benchmark_dir: Path, directory: Path, allow_frozen_truth: bool) -> dict:
    config = EvaluationConfig(
        benchmark_dir=benchmark_dir, split=split, trajectory_dirs=(directory,),
        provider_id=MECHANICAL_PROVIDER, model=MECHANICAL_MODEL,
        allow_frozen_truth=allow_frozen_truth, label=f"{split}-mechanical-replay",
    )
    with tempfile.TemporaryDirectory(prefix="finrecon-submission-replay-") as staging:
        return evaluate(config, staging_dir=Path(staging)).report


def _core_report(benchmark_dir: Path, replay_report: dict) -> dict:
    """Score all frozen decisions after production decisions have been made."""
    with LedgerStore(":memory:") as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split="frozen-eval")
    truth = load_ground_truth(benchmark_dir, "frozen-eval", allow_frozen_truth=True)
    replay_by_case = {item["case_id"]: item for item in replay_report["per_case"]}
    items = []
    for decision in batch.decisions:
        entry = truth[decision.case_id]
        if decision.status is DecisionStatus.RESOLVED:
            predicted = tuple(decision.settlement_ids)
            resolved = True
            mode = "deterministic"
        else:
            replay = replay_by_case[decision.case_id]
            predicted = tuple(replay["predicted_settlement_ids"])
            resolved = replay["resolved"]
            mode = "ai_assisted" if resolved else "escalated"
        correct = None
        wrong_reason = None
        escalation_correct = None
        if resolved:
            correct = entry.correct_relationship is not None and entry.expected_settlement_ids == predicted
            wrong_reason = None if correct else "wrong settlement or resolved ambiguous case"
        else:
            escalation_correct = not entry.is_uniquely_resolvable
        # A tiny adapter gives aggregate_scores exactly the fields it owns.
        from benchmark.eval.scoring import CaseVerdict
        items.append((mode, CaseVerdict(decision.case_id, entry.tier, entry.archetype, resolved, correct, wrong_reason, None, predicted, entry.expected_settlement_ids, entry.true_reference, "stage2" if mode == "deterministic" else replay_by_case[decision.case_id]["termination_reason"], (), (), entry.value_at_stake_paise, entry.is_uniquely_resolvable, escalation_correct)))
    all_verdicts = [verdict for _, verdict in items]
    by_tier = {}
    for tier in ("T0", "T1", "T2", "T3"):
        by_tier[tier] = aggregate_scores([verdict for _, verdict in items if verdict.tier == tier])
    return {
        "cohort": "frozen-eval", "cases": len(items), "metrics": aggregate_scores(all_verdicts),
        "resolution_outcomes": dict(Counter(mode for mode, _ in items)), "metrics_by_tier": by_tier,
    }


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _markdown(report: dict) -> str:
    core = report["frozen_core"]
    metrics = core["metrics"]
    lines = [
        "# FinRecon Evaluation", "", "## Reproducibility", "",
        "Command: `uv run python -m benchmark.final_eval` (or `make eval` where Make is available)", f"Frozen benchmark hash: `{report['integrity']['frozen_eval_sha256']}`",
        "Mode: deterministic trajectory recording followed by offline replay; zero network and zero live provider calls.",
        "Replay validates orchestration, validator, and policy against recorded investigator outputs. It is not a fresh measurement of hosted-model quality.", "",
        "## Frozen benchmark", "", "| Tier | Cases | Auto resolved | Correct auto | Wrong auto | Escalated |", "|---|---:|---:|---:|---:|---:|",
    ]
    for tier, value in core["metrics_by_tier"].items():
        lines.append(f"| {tier} | {value['investigated']} | {value['auto_resolved']} | {value['correct_auto_resolutions']} | {value['wrong_auto_resolutions']} | {value['escalated']} |")
    lines += ["", "## Resolution outcomes", "", f"- Deterministic (Stage 2): {core['resolution_outcomes'].get('deterministic', 0)}", f"- AI-assisted (Stage 3 evidence search + validator/policy acceptance): {core['resolution_outcomes'].get('ai_assisted', 0)}", f"- Escalated: {core['resolution_outcomes'].get('escalated', 0)}", "", "## Safety", "", f"- Unsafe auto-resolutions: {metrics['wrong_auto_resolutions']}", f"- Unsafe auto-match rate: {metrics['unsafe_auto_match_rate']}", f"- Value at risk: {metrics['value_at_risk_paise']} paise (sum of `value_at_stake_paise` for incorrect automatic resolutions).", "", "## Stage-3 replay evaluation", "", f"Exact frozen residual cohort: {report['stage3_replay']['metrics']['investigated']} cases; correct auto {report['stage3_replay']['metrics']['correct_auto_resolutions']}, wrong auto {report['stage3_replay']['metrics']['wrong_auto_resolutions']}, escalated {report['stage3_replay']['metrics']['escalated']}.", "Provider/model metadata: deterministic non-LLM `mechanical:mechanical-investigator-v1` (requested and reported); no token/cost telemetry is fabricated.", "", "## Experimental adversarial pilot — NON-PRODUCTION", "", f"v4 pilot: {report['v4_pilot']['metrics']['investigated']} cases; correct auto {report['v4_pilot']['metrics']['correct_auto_resolutions']}, wrong auto {report['v4_pilot']['metrics']['wrong_auto_resolutions']}, escalated {report['v4_pilot']['metrics']['escalated']}. This is synthetic safety/capability coverage, not Razorpay production coverage.", "", "## Limitations", "", "All benchmark data is synthetic. Replay is reproducible but is not current hosted-model quality. Razorpay/bank adapter fixtures are conformance tests, not production-accuracy benchmarks.", ""]
    return "\n".join(lines)


def run(benchmark_dir: Path, json_out: Path, markdown_out: Path) -> dict:
    frozen_hash = compute_fingerprint(benchmark_dir, "frozen-eval")
    manifest = json.loads((benchmark_dir / "manifests" / "v3.json").read_text(encoding="utf-8"))
    if frozen_hash != manifest["frozen_eval_sha256"]:
        raise RuntimeError(f"frozen benchmark integrity failure: {frozen_hash}")
    pilot_manifest = json.loads((benchmark_dir / "manifests" / "v4-pilot.json").read_text(encoding="utf-8"))
    pilot_hash = compute_pilot_fingerprint(benchmark_dir)
    if pilot_hash != pilot_manifest["pilot_sha256"]:
        raise RuntimeError(f"v4 pilot integrity failure: {pilot_hash}")
    with tempfile.TemporaryDirectory(prefix="finrecon-submission-recording-") as root:
        root_path = Path(root)
        frozen_dir, v4_dir = root_path / "frozen", root_path / "v4"
        frozen_cases, frozen_replay_cases = _record("frozen-eval", benchmark_dir, frozen_dir)
        v4_cases, v4_replay_cases = _record("v4-pilot", benchmark_dir, v4_dir)
        frozen_replay = _stage3_report("frozen-eval", benchmark_dir, frozen_dir, True)
        v4_replay = _stage3_report("v4-pilot", benchmark_dir, v4_dir, False)
        report = {
            "report_kind": "finrecon_submission_evaluation", "submission_evaluator_version": SUBMISSION_EVALUATOR_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(), "git_commit": _git_commit(),
            "integrity": {"frozen_eval_sha256": frozen_hash, "frozen_hash_verified": True, "v4_pilot_sha256": pilot_hash, "v4_pilot_hash_verified": True},
            "architecture_versions": frozen_replay["recorded_versions"],
            "mode": {"replay": True, "network_access_required": False, "provider_calls_made": False, "recording_provider": MECHANICAL_PROVIDER, "recording_model_requested": MECHANICAL_MODEL, "recording_model_reported": MECHANICAL_MODEL, "note": "Transient deterministic trajectories are recorded from visible inputs and replayed through production Stage 3; no hosted model is invoked."},
            "replay_coverage": {"frozen-eval": {"all_cases": frozen_cases, "stage3_residual_cases": frozen_replay_cases, "covered": frozen_replay_cases, "uncovered": 0, "reason": "Stage-2 resolved the remaining cases."}, "v4-pilot": {"all_cases": v4_cases, "stage3_residual_cases": v4_replay_cases, "covered": v4_replay_cases, "uncovered": 0, "reason": "All Stage-3 residuals recorded deterministically during this run."}},
            "frozen_core": _core_report(benchmark_dir, frozen_replay), "stage3_replay": frozen_replay, "v4_pilot": v4_replay,
            "limitations": ["Synthetic data only.", "Replay tests recorded deterministic investigator outputs and is not a fresh hosted-model measurement.", "v4 is an unfrozen experimental pilot, not production Razorpay coverage.", "Adapters are conformance/integration tested, not production-accuracy benchmarked."],
        }
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.write_text(_markdown(report), encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Full API-key-free FinRecon submission evaluation.")
    parser.add_argument("--benchmark-dir", type=Path, default=Path("benchmark"))
    parser.add_argument("--json-out", type=Path, default=Path("benchmark/reports/final-eval.json"))
    parser.add_argument("--markdown-out", type=Path, default=Path("benchmark/reports/final-eval.md"))
    args = parser.parse_args(argv)
    report = run(args.benchmark_dir, args.json_out, args.markdown_out)
    print(f"Evaluation complete: {report['frozen_core']['cases']} frozen cases; reports at {args.json_out} and {args.markdown_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
