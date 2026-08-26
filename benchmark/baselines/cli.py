"""``python -m benchmark.baselines`` -- run the deterministic arms over a split.

Prints a compact summary and, optionally, writes the full JSON report. Makes
zero provider calls: no module in this package imports a provider, and
``tests/test_v4_baselines.py`` asserts that structurally rather than trusting
this sentence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from finrecon.loader import default_benchmark_dir

from benchmark.baselines import BASELINE_SUITE_VERSION
from benchmark.baselines.arms import ARMS
from benchmark.baselines.report import run_baselines, write_report


def _fmt(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def print_summary(report: dict, stream=sys.stdout) -> None:
    out = lambda line="": print(line, file=stream)  # noqa: E731
    out(f"\nfinrecon deterministic baselines ({BASELINE_SUITE_VERSION})")
    out(f"  split:                    {report['split']}")
    out(f"  provider calls:           {report['provider_calls_made']}")
    out(f"  cases:                    {report['cases']}"
        f"   resolvable {report['resolvable_cases']}"
        f"   intentionally ambiguous {report['intentionally_ambiguous_cases']}")

    stage2 = report["stage2"]
    out("\nStage 2")
    out(f"  resolved / unresolved:    {stage2['resolved']} / {stage2['unresolved']}")
    out(f"  unresolved rules:         {stage2['unresolved_rules']}")
    out(f"  candidate counts:         {stage2['candidate_count_distribution']}")
    out(f"  truth present in set:     {stage2['cases_where_truth_is_present_in_candidate_set']}"
        f" / {stage2['unresolved']}")
    out(f"  truth missing:            {stage2['cases_where_truth_is_missing'] or 'none'}")
    out(f"  exact-total candidates:   {stage2['candidates_from_exact_total_blocking']}"
        f" / {stage2['candidates_total']}")

    out(f"\n{'arm':<40}{'resolved':>9}{'correct':>9}{'WRONG':>7}{'escal':>7}"
        f"{'match':>9}{'at risk':>10}")
    for arm in ARMS:
        metrics = report["arms"][arm]["overall"]
        out(f"  {arm:<38}{metrics['resolved']:>9}{metrics['correct']:>9}"
            f"{metrics['wrong']:>7}{metrics['escalated']:>7}"
            f"{_fmt(metrics['match_rate']):>9}{metrics['value_at_risk_paise']:>10}")

    for arm in ARMS:
        wrong = report["arms"][arm]["wrong_resolutions"]
        if not wrong:
            continue
        out(f"\nWRONG auto-resolutions -- {arm}")
        for item in wrong:
            out(f"  {item['case_id']}  ({item['archetype']}): {item['reason']}")
            out(f"    predicted {item['predicted_settlement_ids']}"
                f"  truth {item['truth_settlement_ids'] or 'ESCALATE'}"
                f"  value {item['value_at_stake_paise']} paise")

    out("\nper required composition (resolved / cases)")
    header = f"  {'composition':<34}" + "".join(f"{arm.split('_')[0]:>8}" for arm in ARMS)
    out(header)
    compositions = sorted(report["arms"][ARMS[0]]["by_required_composition"])
    for composition in compositions:
        cells = ""
        for arm in ARMS:
            slice_metrics = report["arms"][arm]["by_required_composition"][composition]
            cells += f"{slice_metrics['resolved']:>4}/{slice_metrics['cases']:<3}"
        out(f"  {composition:<34}{cells}")

    audit = report["leakage_audit"]
    out("\nleakage audit")
    out(f"  truth position share:     {audit['truth_position_share']}")
    out(f"  truth is lowest ID:       {audit['truth_is_lowest_settlement_id']}"
        f" / {audit['resolvable_cases_audited']}")
    out(f"  truth is highest ID:      {audit['truth_is_highest_settlement_id']}"
        f" / {audit['resolvable_cases_audited']}")
    out(f"  outcome by cand. count:   {audit['required_outcome_by_candidate_count']}")
    out(f"  longest archetype run:    {audit['longest_same_archetype_run_in_case_id_order']}")
    out(f"  longest outcome run:      {audit['longest_same_outcome_run_in_case_id_order']}")
    out(f"  labels in visible files:  {audit['benchmark_labels_found_in_visible_files'] or 'none'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.baselines",
        description=(
            "Deterministic diagnostic baselines. Zero provider calls; ground truth "
            "is read only to score decisions that were already made."
        ),
    )
    parser.add_argument("--split", default="v4-pilot")
    parser.add_argument("--benchmark-dir", default=None, type=Path)
    parser.add_argument("--json-out", default=None, type=Path)
    parser.add_argument("--quiet", action="store_true", help="JSON only, no summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    benchmark_dir = args.benchmark_dir or default_benchmark_dir()
    report = run_baselines(benchmark_dir, args.split)

    if args.json_out:
        write_report(report, args.json_out)
        if not args.quiet:
            print(f"JSON report written to {args.json_out}")
    if args.quiet:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_summary(report)

    # The shipped gate and the candidate production rules must remain safe.
    # Historical/ablated arms intentionally retain the stale-reference failure
    # so the experiment still shows which capability closes it.
    conservative_wrong = sum(
        len(report["arms"][arm]["wrong_resolutions"])
        for arm in (
            "A_rules_only",
            "B_shipped_gate_exhaustive",
            "C2_lexical_and_structural_composition",
            "S1_reference_and_value_date",
            "S3_conservative_structural_composition",
        )
    )
    return 1 if conservative_wrong else 0


__all__ = ["build_parser", "main", "print_summary"]
