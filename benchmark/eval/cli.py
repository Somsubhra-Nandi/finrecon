"""``python -m benchmark.eval`` — the Stage-4 offline evaluation CLI.

Two subcommands:

``evaluate``
    Score recorded Stage-3 trajectories against a split's ground truth.

``compare``
    Put two evaluation reports side by side over one identical cohort, and
    state the configuration differences without drawing a causal conclusion.

The banner printed before every run is not decoration. A reader who cannot
tell whether a number came from a live model or a replay cannot use the
number, so the mode, the cache identity and the zero-provider-call guarantee
are stated up front -- the same reasoning that makes ``investigate_cli`` print
its provider configuration before it runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from finrecon.agent.loop import DEFAULT_MAX_STEPS
from finrecon.loader import default_benchmark_dir

from benchmark.eval import EVALUATOR_VERSION
from benchmark.eval.compare import compare
from benchmark.eval.errors import EvaluationError
from benchmark.eval.evaluate import EvaluationConfig, EvaluationResult, evaluate
from benchmark.eval.sources import cohort_from_records, load_run_dump, read_cohort_file


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def print_summary(result: EvaluationResult, stream=sys.stdout) -> None:
    """Compact human-readable summary. The JSON report remains authoritative."""
    report = result.report
    cohort = report["cohort"]
    metrics = report["metrics"]
    agent = report["agent"]
    tele = report["telemetry"]
    sound = report["soundness"]
    out = lambda line="": print(line, file=stream)  # noqa: E731

    out(f"\ncohort: requested {cohort['requested_count']}  found {cohort['found_count']}"
        f"  complete={cohort['complete']}")
    if cohort["missing"]:
        out(f"  missing:   {len(cohort['missing'])}  {', '.join(cohort['missing'][:5])}")
    if cohort["extra"]:
        out(f"  extra (not scored): {len(cohort['extra'])}")
    if cohort["duplicate_sources"]:
        out(f"  offered by >1 source: {len(cohort['duplicate_sources'])}")
    out(f"  tiers:     {cohort['tier_counts']}"
        f"  expected={cohort['expected_tier']}  clean={cohort['all_expected_tier']}")
    if cohort["contamination"]:
        for item in cohort["contamination"][:5]:
            out(f"    contamination: {item['case_id']} is {item['tier']}")
    out(f"  sources:   {cohort['sources_contributing']}")

    out("\nscores")
    out(f"  investigated:              {metrics['investigated']}")
    if not metrics.get("scoring_available", True):
        out("  correctness:               NOT SCORED")
        out(f"    reason: {metrics['scoring_unavailable_reason']}")
    else:
        out(f"  auto-resolved:             {metrics['auto_resolved']}")
        out(f"  correct auto-resolutions:  {metrics['correct_auto_resolutions']}")
        out(f"  WRONG auto-resolutions:    {metrics['wrong_auto_resolutions']}")
        out(f"  escalated:                 {metrics['escalated']}")
        out(f"  auto-resolution accuracy:  {_fmt(metrics['auto_resolution_accuracy'])}")
        out(f"  overall match rate:        {_fmt(metrics['overall_match_rate'])}"
            f"   (n={metrics['uniquely_resolvable_cases']} resolvable)")
        out(f"  unsafe auto-match rate:    {_fmt(metrics['unsafe_auto_match_rate'])}")
        out(f"  value at risk (paise):     {metrics['value_at_risk_paise']}")

    if report["wrong_resolutions"]:
        out("\nWRONG auto-resolutions")
        for wrong in report["wrong_resolutions"]:
            out(f"  {wrong['case_id']}  ({wrong['tier']}, {wrong['wrong_reason']})")
            out(f"    predicted candidate : {wrong['predicted_candidate_id']}")
            out(f"    predicted settlements: {wrong['predicted_settlement_ids']}")
            out(f"    ground truth         : {wrong['truth_settlement_ids']}"
                f"  (true_reference={wrong['truth_reference']})")
            out(f"    termination          : {wrong['termination_reason']}")
            for relation in wrong["evidence_relations"]:
                out(f"    evidence relation    : {relation['relation_id']}"
                    f" on {relation['reference_kind']}={relation['reference_value']!r}"
                    f" via fragment {relation['fragment']!r}"
                    f" ({relation['pinned_reference_characters']} chars pinned)")

    out("\nsoundness")
    if not sound.get("checks_available", True):
        out("  NOT CHECKED (no replay, so no validator result to check)")
    else:
        out(f"  violations: {sound['total_violations']}"
            f"  {sound['violations_by_check'] or '{}'}")
        for violation in sound["violations"][:10]:
            out(f"    {violation['case_id']}  {violation['check']}: {violation['detail']}")

    out("\nagent / tools")
    out(f"  termination:               {agent['termination_reasons']}")
    out(f"  tool_validation_failed:    {agent['tool_validation_failed']}")
    out(f"  validation rejections:     {agent['tool_validation_rejections_total']}")
    nonzero = {k: v for k, v in agent["tool_validation_reasons"].items() if v}
    out(f"    by reason:               {nonzero or 'none'}")
    out(f"  accepted relations:        {agent['accepted_evidence_relations'] or 'none'}")
    out(f"  escalation blockers:       {agent['escalation_blockers'] or 'none'}")

    out("\nprovider / model telemetry")
    out(f"  models requested:          {tele['models_requested']}")
    out(f"  models reported (answered):{tele['models_reported']}")
    match = tele["requested_matches_reported"]
    out(f"  requested == reported:     "
        f"{'not reported by provider' if match is None else match}")
    out(f"  fallbacks:                 {tele['fallback_used_cases']} case(s)"
        f"  {tele['fallback_reasons'] or ''}")
    out(f"  provider failed attempts:  {tele['provider_failed_attempts']}"
        f"  {tele['provider_error_classes'] or ''}")
    out(f"  model steps:               total {tele['model_steps_total']}"
        f"  mean {tele['model_steps_mean_per_case']}  max {tele['model_steps_max']}")
    out(f"  tokens:                    total {tele['tokens_total']}"
        f"  mean/case {tele['tokens_mean_per_case']}")
    out(f"  latency:                   total {tele['latency_total_ms']} ms"
        f"  mean/case {_fmt(tele['latency_mean_ms_per_case'])} ms")


def print_comparison(comparison: dict, stream=sys.stdout) -> None:
    out = lambda line="": print(line, file=stream)  # noqa: E731
    labels = comparison["labels"]
    identity = comparison["cohort_identity"]
    out(f"\ncomparison: A={labels['a']}  B={labels['b']}")
    out(f"  identical case IDs:        {identity['identical_case_ids']}"
        f"  ({identity['count_a']} vs {identity['count_b']})")
    out(f"  identical tier composition:{identity['identical_tier_composition']}"
        f"  {identity['tier_counts_a']} vs {identity['tier_counts_b']}")
    if identity["only_in_a"] or identity["only_in_b"]:
        out(f"  only in A: {len(identity['only_in_a'])}"
            f"   only in B: {len(identity['only_in_b'])}")

    out("\nconfiguration")
    for dimension in ("provider_model", "prompt_version", "tool_schema_version",
                      "agent_loop_version", "validator_version", "policy_version"):
        a = comparison["configuration"]["a"].get(dimension)
        b = comparison["configuration"]["b"].get(dimension)
        flag = "CHANGED" if dimension in comparison["configuration"]["differing_dimensions"] else "same"
        out(f"  {dimension:<22} {flag:<8} A={a}  B={b}")

    if comparison["side_by_side"]:
        out(f"\n{'metric':<34}{'A':>14}{'B':>14}{'delta':>14}")
        for row in comparison["side_by_side"]:
            out(f"  {row['metric']:<32}{_fmt(row['a']):>14}{_fmt(row['b']):>14}"
                f"{_fmt(row['delta']):>14}")
    else:
        out("\n(no deltas: the cohorts are not comparable)")

    out("\nattribution")
    out(f"  causal claim: {comparison['attribution']['causal_claim']}")
    out(f"  {comparison['attribution']['statement']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.eval",
        description=(
            "Stage 4: offline benchmark evaluation. Scores recorded Stage-3 "
            "trajectories against hidden ground truth. Makes zero provider calls."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ev = subparsers.add_parser("evaluate", help="score recorded trajectories")
    ev.add_argument("--split", default="dev", help="dataset split (default: dev)")
    ev.add_argument("--benchmark-dir", default=None, help="benchmark directory")
    ev.add_argument("--trajectories", action="append", default=[], type=Path,
                    help="a trajectory cache directory (repeatable)")
    ev.add_argument("--run-dump", action="append", default=[], type=Path,
                    help="an investigate_cli --show-trajectory transcript (repeatable)")
    ev.add_argument("--cohort", default=None, type=Path,
                    help="file of case IDs pinning the exact cohort (JSON array or lines)")
    ev.add_argument("--cohort-from-dump", default=None, type=Path,
                    help="derive the exact cohort from a baseline run's transcript")
    ev.add_argument("--expected-tier", default=None,
                    help="assert every cohort case is this tier (e.g. T2)")
    ev.add_argument("--provider", default="gorouter", help="cache identity: provider")
    ev.add_argument("--model", default="claude-opus-5-thinking", help="cache identity: model")
    ev.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ev.add_argument("--label", default="evaluation", help="label recorded in the report")
    ev.add_argument("--json-out", default=None, type=Path, help="write the JSON report here")
    ev.add_argument("--allow-partial-cohort", action="store_true",
                    help="score the found subset when an exact cohort is incomplete")
    ev.add_argument("--allow-tier-contamination", action="store_true",
                    help="report contamination without failing")
    ev.add_argument("--allow-frozen-truth", action="store_true",
                    help="permit scoring against FROZEN-EVAL ground truth (deliberate)")
    ev.add_argument("--no-replay", action="store_true",
                    help=("describe recorded artifacts without replaying them. For a "
                          "baseline recorded under a superseded contract: reports "
                          "termination, tool-validation and telemetry facts, and NO "
                          "correctness (no decision is produced)."))
    ev.add_argument("--quiet", action="store_true", help="JSON only, no summary")

    cp = subparsers.add_parser("compare", help="compare two evaluation reports")
    cp.add_argument("report_a", type=Path)
    cp.add_argument("report_b", type=Path)
    cp.add_argument("--label-a", default="A")
    cp.add_argument("--label-b", default="B")
    cp.add_argument("--json-out", default=None, type=Path)
    cp.add_argument("--allow-cohort-mismatch", action="store_true",
                    help="reconcile without emitting deltas instead of failing")
    cp.add_argument("--quiet", action="store_true")
    return parser


def _run_evaluate(args: argparse.Namespace) -> int:
    benchmark_dir = Path(args.benchmark_dir) if args.benchmark_dir else default_benchmark_dir()

    cohort_ids: tuple[str, ...] | None = None
    if args.cohort and args.cohort_from_dump:
        print("error: pass --cohort or --cohort-from-dump, not both", file=sys.stderr)
        return 2
    if args.cohort:
        cohort_ids = read_cohort_file(args.cohort)
    elif args.cohort_from_dump:
        cohort_ids = cohort_from_records(load_run_dump(args.cohort_from_dump))

    config = EvaluationConfig(
        benchmark_dir=benchmark_dir,
        split=args.split,
        trajectory_dirs=tuple(args.trajectories),
        run_dumps=tuple(args.run_dump),
        cohort_ids=cohort_ids,
        expected_tier=args.expected_tier,
        provider_id=args.provider,
        model=args.model,
        max_steps=args.max_steps,
        require_exact_cohort=not args.allow_partial_cohort,
        require_expected_tier=not args.allow_tier_contamination,
        allow_frozen_truth=args.allow_frozen_truth,
        label=args.label,
        replay=not args.no_replay,
    )

    if not args.quiet:
        print(f"finrecon Stage 4 - offline benchmark evaluation ({EVALUATOR_VERSION})")
        mode = ("RECORDED-ONLY (no replay) - zero provider calls"
                if args.no_replay else "OFFLINE REPLAY - zero provider calls")
        print(f"  mode:           {mode}")
        print("  provider chain: none constructed")
        print(f"  cache identity: {args.provider}:{args.model}")
        print(f"  split:          {args.split}"
              + ("  [FROZEN TRUTH OPT-IN]" if args.allow_frozen_truth else ""))
        print("  accuracy source: hidden ground truth, read only here - never by "
              "the reconciliation path")

    with tempfile.TemporaryDirectory(prefix="finrecon-stage4-") as tmp:
        result = evaluate(config, staging_dir=Path(tmp) / "trajectories")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(result.report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        if not args.quiet:
            print(f"\nJSON report written to {args.json_out}")
    if args.quiet:
        print(json.dumps(result.report, indent=2, sort_keys=True))
    else:
        print_summary(result)

    # A wrong auto-resolution is the one outcome DESIGN.md 1 calls unacceptable,
    # so it is also the one that changes the exit code. Everything else is a
    # number to read, not a build failure.
    return 1 if result.report["metrics"].get("wrong_auto_resolutions") else 0


def _run_compare(args: argparse.Namespace) -> int:
    report_a = json.loads(Path(args.report_a).read_text(encoding="utf-8"))
    report_b = json.loads(Path(args.report_b).read_text(encoding="utf-8"))
    comparison = compare(
        report_a,
        report_b,
        label_a=args.label_a,
        label_b=args.label_b,
        require_identical_cohort=not args.allow_cohort_mismatch,
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.quiet:
        print(json.dumps(comparison, indent=2, sort_keys=True))
    else:
        print_comparison(comparison)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "evaluate":
            return _run_evaluate(args)
        return _run_compare(args)
    except EvaluationError as error:
        # Fail closed and say why. Never fall back to anything.
        print(f"\nevaluation failed: {error}", file=sys.stderr)
        return 2


__all__ = ["build_parser", "main", "print_comparison", "print_summary"]
