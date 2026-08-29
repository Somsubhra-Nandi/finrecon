"""Oracle and bounded mechanical runs for ``bounded-search-v1``.

Inference happens before this module asks the evaluator to load hidden truth.
The mechanical provider sees the same case briefing, tool schemas, tool
outputs and controller as a hosted model will; its only special property is a
deterministic fragment-ranking strategy.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from finrecon.agent.cache import TrajectoryCache
from finrecon.agent.loop import LoopConfig
from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelResponse,
    ToolCallRequest,
    ToolSpec,
)
from finrecon.agent.providers.chain import ProviderChain
from finrecon.benchmark.generator_search.config import (
    BENCHMARK_NAME,
    MAX_MODEL_STEPS,
    MAX_TOOL_CALLS_PER_STEP,
    TOOL_CALL_BUDGET,
)
from finrecon.benchmark.generator_search.dataset import plausible_fragment_actions
from finrecon.benchmark.generator_search.manifest import compute_search_fingerprint
from finrecon.decide.config import DEFAULT_POLICY
from finrecon.decide.policy import adjudicate
from finrecon.evidence.closure import build_reference_closure
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch
from finrecon.stage3 import run_stage3

from benchmark.baselines.arms import exhaustive_fragment_trajectory
from benchmark.baselines.features import Feature
from benchmark.eval.evaluate import EvaluationConfig, evaluate
from benchmark.eval.groundtruth import load_ground_truth
from benchmark.eval.sources import read_cohort_file

MECHANICAL_PROVIDER = "mechanical"
MECHANICAL_MODEL = "bounded-mechanical-investigator-v1"


def _briefing_narration(messages: tuple[ConversationMessage, ...]) -> str:
    briefing = next(message.content for message in messages if message.role == "user")
    return next(
        (line[len("narration: ") :] for line in briefing.splitlines() if line.startswith("narration: ")),
        "",
    )


def _has_reference_reach(messages: tuple[ConversationMessage, ...]) -> bool:
    """Whether a previous deterministic comparison reached any candidate."""
    for message in reversed(messages):
        if message.role != "tool" or message.tool_name != "compare_reference_fragment":
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            return False
        for candidate in payload.get("candidate_comparisons", ()):
            for comparison in candidate.get("comparisons", ()):
                if comparison.get("max_pinned_reference_characters", 0) >= 4:
                    return True
        return False
    return False


_CANONICAL_CONTEXT = (
    "UTR",
    "REF",
    "TRACE",
    "RRN",
    "CLEARING",
    "ORIGIN",
    "BENEFICIARY",
    "RZRPAY",
)


def ranked_fragments(narration: str) -> tuple[str, ...]:
    """A credible fixed heuristic over visible narration only.

    It understands common bank-field vocabulary and reference shape, avoids
    pure prose where possible, and uses position only as a final stable
    tie-break.  It deliberately has no generator-family vocabulary and no
    access to canonical references or ground truth.
    """
    actions = plausible_fragment_actions(narration)
    scored: list[tuple[int, int, str]] = []
    search_from = 0
    for action in actions:
        position = narration.find(action, search_from)
        if position < 0:
            position = narration.find(action)
        search_from = max(search_from, position + len(action))
        left = narration[max(0, position - 28) : position].upper()
        upper = action.upper()
        has_alpha = any(char.isalpha() for char in action)
        has_digit = any(char.isdigit() for char in action)
        score = 0
        if has_alpha and has_digit:
            score += 8
        elif action.isdigit() and len(action) >= 6:
            score += 5
        elif has_alpha:
            score += 1
        if 6 <= len(action) <= 18:
            score += 2
        score += 4 * sum(1 for label in _CANONICAL_CONTEXT if label in left)
        if upper in {"RAZORPAY", "SETTLEMENT", "FRAGMENT", "RFND"}:
            score -= 8
        scored.append((score, -position, action))
    scored.sort(reverse=True)
    return tuple(action for _score, _position, action in scored)


class BoundedMechanicalInvestigator(ModelProvider):
    """Strong fixed heuristic under the exact hosted-model controller budget."""

    provider_id = MECHANICAL_PROVIDER

    @property
    def model(self) -> str:
        return MECHANICAL_MODEL

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        del tools
        # If a valid reference seed did not resolve the case, validator.v3's
        # complete closure has already shown that more seeds cannot create a
        # safe winner. Stop rather than burn calls on a known ambiguity.
        if _has_reference_reach(messages):
            return ModelResponse(
                self.provider_id,
                self.model,
                "A reference reached the closed evidence set without resolving; stop safely.",
                finish_reason="stop",
                reported_model=self.model,
            )

        narration = _briefing_narration(messages)
        tried = sum(
            message.role == "tool" and message.tool_name == "compare_reference_fragment"
            for message in messages
        )
        ranked = ranked_fragments(narration)
        if tried >= len(ranked):
            return ModelResponse(
                self.provider_id,
                self.model,
                "No further plausible fragment.",
                finish_reason="stop",
                reported_model=self.model,
            )
        fragment = ranked[tried]
        call = ToolCallRequest(
            call_id=f"mechanical-fragment-{tried}",
            tool_name="compare_reference_fragment",
            raw_arguments=json.dumps({"fragment": fragment}),
        )
        return ModelResponse(
            self.provider_id,
            self.model,
            "Testing the next highest-ranked visible fragment.",
            tool_calls=(call,),
            finish_reason="tool_calls",
            reported_model=self.model,
        )


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_mechanical(benchmark_dir: Path) -> dict:
    """Run production Stage 3 first, then score the completed trajectories."""
    cohort = read_cohort_file(benchmark_dir / "cohorts" / f"{BENCHMARK_NAME}.json")
    config = LoopConfig(
        max_steps=MAX_MODEL_STEPS,
        max_tool_calls_per_step=MAX_TOOL_CALLS_PER_STEP,
    )
    with tempfile.TemporaryDirectory(prefix="finrecon-bsearch-mechanical-") as temp:
        cache_dir = Path(temp) / "recorded"
        with LedgerStore(":memory:") as store:
            batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=BENCHMARK_NAME)
            assert len(batch.snapshots) == len(cohort) == 50
            run_stage3(
                store=store,
                batch_result=batch,
                chain=ProviderChain((BoundedMechanicalInvestigator(),)),
                cache=TrajectoryCache(cache_dir),
                config=config,
                case_ids=frozenset(cohort),
            )

        # Hidden truth is first loaded inside evaluate(), after every production
        # decision above is final and recorded.
        result = evaluate(
            EvaluationConfig(
                benchmark_dir=benchmark_dir,
                split=BENCHMARK_NAME,
                trajectory_dirs=(cache_dir,),
                cohort_ids=cohort,
                expected_tier="SEARCH",
                provider_id=MECHANICAL_PROVIDER,
                model=MECHANICAL_MODEL,
                max_steps=MAX_MODEL_STEPS,
                max_tool_calls_per_step=MAX_TOOL_CALLS_PER_STEP,
                label="bounded-search-v1-mechanical",
            ),
            staging_dir=Path(temp) / "replay",
        )
    report = result.report
    report["challenge"] = {
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_sha256": compute_search_fingerprint(benchmark_dir),
        "tool_call_budget": TOOL_CALL_BUDGET,
        "provider_calls_made": False,
        "ground_truth_read_during_inference": False,
    }
    return report


def run_oracle(benchmark_dir: Path) -> dict:
    """Unbounded evaluation-only closure check, never production code."""
    decisions: dict[str, tuple[bool, tuple[str, ...], int, int]] = {}
    with LedgerStore(":memory:") as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=BENCHMARK_NAME)
    assert len(batch.snapshots) == 50
    for snapshot in batch.snapshots:
        floor = DEFAULT_POLICY.value.min_pinned_for(
            snapshot.base_evidence.bank_record.amount_paise,
            DEFAULT_POLICY.evidence.min_pinned_reference_characters,
        )
        closure = build_reference_closure(
            snapshot,
            accepted_relation_ids=DEFAULT_POLICY.evidence.accepted_relation_ids,
            min_pinned_reference_characters=floor,
        )
        seed_features: tuple[Feature, ...] = ()
        if closure.atoms:
            atom = closure.atoms[0]
            seed_features = (
                Feature(kind="lexical", token=atom.fragment, reach=frozenset(atom.reach)),
            )
        trajectory = exhaustive_fragment_trajectory(
            snapshot, seed_features, DEFAULT_POLICY
        )
        _validator, decision = adjudicate(
            snapshot=snapshot, trajectory=trajectory, policy=DEFAULT_POLICY
        )
        decisions[snapshot.case_id] = (
            decision.resolved,
            tuple(sorted(decision.resolved_settlement_ids)),
            len(seed_features),
            closure.fragments_enumerated,
        )

    # The oracle's complete decisions exist before the answer key is opened.
    truth = load_ground_truth(benchmark_dir, BENCHMARK_NAME)
    per_family: dict[str, Counter] = {}
    correct = wrong = escalated = 0
    resolvable_passed = ambiguous_passed = 0
    tool_calls = 0
    fragments_enumerated = 0
    per_case = []
    for case_id in sorted(decisions):
        resolved, predicted, calls, enumerated = decisions[case_id]
        entry = truth[case_id]
        tool_calls += calls
        fragments_enumerated += enumerated
        is_correct = resolved and predicted == entry.expected_settlement_ids
        is_wrong = resolved and not is_correct
        if is_correct:
            correct += 1
        if is_wrong:
            wrong += 1
        if not resolved:
            escalated += 1
        if entry.is_uniquely_resolvable and is_correct:
            resolvable_passed += 1
        if not entry.is_uniquely_resolvable and not resolved:
            ambiguous_passed += 1
        slot = per_family.setdefault(entry.archetype, Counter())
        slot["cases"] += 1
        slot["correct"] += int(is_correct)
        slot["wrong"] += int(is_wrong)
        slot["escalated"] += int(not resolved)
        per_case.append(
            {
                "case_id": case_id,
                "family": entry.archetype,
                "resolved": resolved,
                "correct": is_correct,
                "tool_calls_in_unbounded_oracle": calls,
            }
        )
    return {
        "report_kind": "bounded_search_construction_oracle",
        "benchmark_name": BENCHMARK_NAME,
        "benchmark_sha256": compute_search_fingerprint(benchmark_dir),
        "evaluation_only": True,
        "production_code": False,
        "provider_calls_made": False,
        "ground_truth_read_during_search": False,
        "cases": len(decisions),
        "resolvable_cases_verified": resolvable_passed,
        "ambiguous_cases_verified": ambiguous_passed,
        "correct_auto_resolutions": correct,
        "wrong_auto_resolutions": wrong,
        "escalated": escalated,
        "unbounded_reference_tool_calls": tool_calls,
        "reference_fragments_enumerated": fragments_enumerated,
        "by_family": {
            family: dict(sorted(counts.items()))
            for family, counts in sorted(per_family.items())
        },
        "per_case": per_case,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded-search-v1 free validation harness")
    parser.add_argument("command", choices=("oracle", "mechanical"))
    parser.add_argument("--benchmark-dir", type=Path, default=Path("benchmark"))
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    report = (
        run_oracle(args.benchmark_dir)
        if args.command == "oracle"
        else run_mechanical(args.benchmark_dir)
    )
    if args.json_out:
        _write_json(args.json_out, report)
    if not args.quiet:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report.get("wrong_auto_resolutions", report.get("metrics", {}).get("wrong_auto_resolutions")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
