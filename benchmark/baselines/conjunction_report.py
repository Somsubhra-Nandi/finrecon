"""The harness that decides whether a conjunctive rule may be shipped.

Three populations, and a rule has to survive all three:

**Adversarial fixtures** (:mod:`benchmark.baselines.adversarial`) -- hand-built
attacks on the rule itself: cherry-picking by omission, duplicate evidence,
overlapping slices of one span, a generic wrapper, a stale strong reference,
contradiction monotonicity, and fabricated evidence. Each fixture declares the
only safe outcome, so a rule either matches it or fails.

**The v4 pilot** -- 64 cases, 48 with a unique answer, 16 intentionally
unresolvable. This is where a rule's *coverage* is measured.

**Benchmark v3 DEV** -- 240 cases the deterministic core left unresolved (200
T2, 40 T3). This is the regression population: a rule that loses T2 loses 200
correct reconciliations, and a rule that resolves any T3 case has broken
abstention.

Agent evidence comes from :func:`benchmark.baselines.conjunction.
bounded_agent_fragments` -- the same two mechanical narration splits that drive
the repository's fake investigator, restated inside this package so the harness
needs no provider chain and no Stage-3 run. It is not a model and nothing here
is a model result; what it provides is a *fixed* per-case fragment set, so five
rules are compared on identical agent evidence rather than on five different
investigations.

How the *real* pipeline behaves under a chosen rule is a different question,
answered where it belongs: by a Stage-3 pass through the actual loop, validator
and gate in ``tests/test_v4_stage4_integration.py``.

Ground truth is loaded only after every rule has produced every decision.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch

from benchmark.baselines import BASELINE_SUITE_VERSION
from benchmark.baselines.adversarial import ADVERSARIAL_BY_NAME, ADVERSARIAL_CASES
from benchmark.baselines.conjunction import (
    RULES,
    RULE_V1,
    bounded_agent_fragments,
    evaluate_rules,
)
from benchmark.eval.groundtruth import load_ground_truth
from benchmark.eval.scoring import WRONG_NO_CORRECT_ANSWER, WRONG_SETTLEMENT


def collect_agent_evidence(benchmark_dir: Path, split: str) -> dict:
    """One deterministic Stage-2 pass, plus the stand-in agent's fragments per case.

    Stage 2 is the real pipeline; the agent is the stand-in. No provider is
    constructed, no trajectory is recorded, and no model is consulted -- which
    is why this module can live beside the rules it judges without weakening
    the structural guarantee that nothing in this package can reach one.
    """
    store = LedgerStore(":memory:")
    try:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)
        return {
            snapshot.case_id: (
                snapshot,
                bounded_agent_fragments(snapshot.base_evidence.bank_record.narration),
            )
            for snapshot in batch.snapshots
        }
    finally:
        store.close()


def _score(resolved_settlements: tuple[str, ...] | None, entry) -> tuple[bool | None, str | None]:
    """The section 5.3 correctness predicate. Restated once, tested against Stage 4."""
    if resolved_settlements is None:
        return None, None
    if entry.correct_relationship is None:
        return False, WRONG_NO_CORRECT_ANSWER
    if entry.expected_settlement_ids != resolved_settlements:
        return False, WRONG_SETTLEMENT
    return True, None


def _settlements_of(snapshot, candidate_id: str) -> tuple[str, ...]:
    candidate = next(c for c in snapshot.candidates if c.candidate_id == candidate_id)
    return tuple(sorted(candidate.settlement_ids))


def run_split(benchmark_dir: Path, split: str) -> dict:
    """Score all five rules over one split. Truth is read only after deciding."""
    evidence = collect_agent_evidence(benchmark_dir, split)

    decisions: dict[str, dict[str, tuple[str, ...] | None]] = {rule: {} for rule in RULES}
    states: dict[str, Counter] = {rule: Counter() for rule in RULES}
    for case_id, (snapshot, fragments) in sorted(evidence.items()):
        outcomes = evaluate_rules(snapshot, fragments)
        for rule, outcome in outcomes.items():
            states[rule][outcome.state] += 1
            decisions[rule][case_id] = (
                None
                if outcome.resolved_candidate_id is None
                else _settlements_of(snapshot, outcome.resolved_candidate_id)
            )

    truth = load_ground_truth(benchmark_dir, split)
    per_rule: dict[str, dict] = {}
    for rule in RULES:
        rows = []
        for case_id, resolved in sorted(decisions[rule].items()):
            entry = truth[case_id]
            correct, reason = _score(resolved, entry)
            rows.append((case_id, entry, resolved, correct, reason))

        resolvable = [row for row in rows if row[1].is_uniquely_resolvable]
        ambiguous = [row for row in rows if not row[1].is_uniquely_resolvable]
        auto = [row for row in rows if row[2] is not None]
        correct_rows = [row for row in auto if row[3]]
        wrong_rows = [row for row in auto if row[3] is False]
        escalated_ambiguous = [row for row in ambiguous if row[2] is None]

        per_rule[rule] = {
            "cases": len(rows),
            "resolved": len(auto),
            "correct": len(correct_rows),
            "wrong": len(wrong_rows),
            "escalated": len(rows) - len(auto),
            "false_escalations": len([r for r in resolvable if r[2] is None]),
            "match_rate": (
                None if not resolvable else round(len(correct_rows) / len(resolvable), 6)
            ),
            "unsafe_auto_match_rate": round(len(wrong_rows) / len(rows), 6) if rows else None,
            "escalation_recall": (
                None
                if not ambiguous
                else round(len(escalated_ambiguous) / len(ambiguous), 6)
            ),
            "value_at_risk_paise": sum(row[1].value_at_stake_paise for row in wrong_rows),
            "states": dict(sorted(states[rule].items())),
            "by_required_composition": _by(rows, lambda e: (e.required_composition,)),
            "by_archetype": _by(rows, lambda e: (e.archetype,)),
            "by_tier": _by(rows, lambda e: (e.tier,)),
            "wrong_cases": [
                {
                    "case_id": row[0],
                    "archetype": row[1].archetype,
                    "reason": row[4],
                    "predicted_settlement_ids": list(row[2] or ()),
                    "truth_settlement_ids": list(row[1].expected_settlement_ids),
                    "value_at_stake_paise": row[1].value_at_stake_paise,
                }
                for row in wrong_rows
            ],
        }
    return per_rule


def _by(rows, key) -> dict:
    grouped: dict[str, list] = {}
    for row in rows:
        for label in key(row[1]):
            if label:
                grouped.setdefault(label, []).append(row)
    out = {}
    for label, items in sorted(grouped.items()):
        auto = [r for r in items if r[2] is not None]
        out[label] = {
            "cases": len(items),
            "resolved": len(auto),
            "correct": len([r for r in auto if r[3]]),
            "wrong": len([r for r in auto if r[3] is False]),
        }
    return out


def run_adversarial() -> dict:
    """Every attack against every rule. A rule fails if any fixture disagrees."""
    per_rule: dict[str, dict] = {
        rule: {"cases": 0, "matched": 0, "failures": []} for rule in RULES
    }
    for case in ADVERSARIAL_CASES:
        snapshot = case.snapshot()
        outcomes = evaluate_rules(snapshot, case.model_fragments)
        for rule, outcome in outcomes.items():
            per_rule[rule]["cases"] += 1
            if outcome.resolved_candidate_id == case.must_resolve_to:
                per_rule[rule]["matched"] += 1
            else:
                per_rule[rule]["failures"].append(
                    {
                        "fixture": case.name,
                        "attack": case.attack,
                        "expected": case.must_resolve_to,
                        "actual": outcome.resolved_candidate_id,
                        "state": outcome.state,
                        "why_it_matters": case.why,
                    }
                )
    for rule in RULES:
        per_rule[rule]["safe"] = not per_rule[rule]["failures"]
    return per_rule


def run_invariance() -> dict:
    """Order, duplicate and overlap invariance, measured on the fixtures.

    Not a proof -- the intersection is a set operation, so these hold by
    construction -- but a rule that reintroduced counting would fail here, and
    a claim nobody checks is a claim nobody can rely on.
    """
    import itertools

    results: dict[str, dict] = {rule: {"order": True, "duplicate": True} for rule in RULES}
    for case in ADVERSARIAL_CASES:
        snapshot = case.snapshot()
        fragments = case.model_fragments
        baseline = evaluate_rules(snapshot, fragments)
        for permutation in itertools.permutations(fragments):
            permuted = evaluate_rules(snapshot, permutation)
            for rule in RULES:
                if permuted[rule].resolved_candidate_id != baseline[rule].resolved_candidate_id:
                    results[rule]["order"] = False
        doubled = evaluate_rules(snapshot, fragments + fragments)
        for rule in RULES:
            if doubled[rule].resolved_candidate_id != baseline[rule].resolved_candidate_id:
                results[rule]["duplicate"] = False
    return results


def run_no_evidence_invariant() -> dict:
    """Does a rule still refuse a case the investigation never gathered evidence for?

    An invariant this repository has asserted since ``validator.v1``, at two
    layers -- ``tests/test_validator.py::test_no_evidence_at_all_leaves_no_survivor``
    and ``tests/test_policy.py::test_no_evidence_at_all_escalates``. It is the
    property that separates the otherwise indistinguishable safe rules, so it is
    measured here rather than left to whoever writes the summary.

    The probe is a fixture whose closure *does* identify a candidate, offered to
    each rule with no agent evidence at all. A rule that resolves it has decided
    that money may move on a case nobody investigated.
    """
    probe = ADVERSARIAL_BY_NAME["conjunction_clean_resolution"]
    snapshot = probe.snapshot()
    with_evidence = evaluate_rules(snapshot, probe.model_fragments)
    without_evidence = evaluate_rules(snapshot, ())
    return {
        rule: {
            "resolves_when_investigated": with_evidence[rule].resolved_candidate_id is not None,
            "refuses_when_uninvestigated": (
                without_evidence[rule].resolved_candidate_id is None
            ),
            "state_when_uninvestigated": without_evidence[rule].state,
        }
        for rule in RULES
    }


def run_harness(benchmark_dir: Path) -> dict:
    """The whole comparison, as one machine-readable report."""
    adversarial = run_adversarial()
    invariance = run_invariance()
    no_evidence = run_no_evidence_invariant()
    v4 = run_split(benchmark_dir, "v4-pilot")
    dev = run_split(benchmark_dir, "dev")

    # The regression bar is "no worse than the rule we already ship", not a
    # round number. Benchmark v3 T2 has 200 cases and the *closure* reaches all
    # 200, but validator.v1 driven by a bounded agent reaches 171 -- so
    # demanding 200 would have quietly disqualified every rule that keeps the
    # agent in the loop, in favour of the one that removes it. Measuring
    # against R0 keeps the comparison honest.
    v1_t2_correct = dev[RULE_V1]["by_tier"].get("T2", {}).get("correct", 0)

    verdicts = {}
    for rule in RULES:
        t2_correct = dev[rule]["by_tier"].get("T2", {}).get("correct", 0)
        t3_resolved = dev[rule]["by_tier"].get("T3", {}).get("resolved", 0)
        verdicts[rule] = {
            "adversarially_safe": adversarial[rule]["safe"],
            "order_invariant": invariance[rule]["order"],
            "duplicate_invariant": invariance[rule]["duplicate"],
            "v4_resolved": v4[rule]["resolved"],
            "v4_correct": v4[rule]["correct"],
            "v4_wrong": v4[rule]["wrong"],
            "dev_wrong": dev[rule]["wrong"],
            "dev_t2_correct": t2_correct,
            "dev_t2_correct_vs_v1": t2_correct - v1_t2_correct,
            "dev_t3_resolved": t3_resolved,
            "refuses_uninvestigated_case": no_evidence[rule]["refuses_when_uninvestigated"],
            "shippable": (
                adversarial[rule]["safe"]
                and invariance[rule]["order"]
                and invariance[rule]["duplicate"]
                and dev[rule]["wrong"] == 0
                and t3_resolved == 0
                and t2_correct >= v1_t2_correct
                and no_evidence[rule]["refuses_when_uninvestigated"]
            ),
        }

    return {
        "baseline_suite_version": BASELINE_SUITE_VERSION,
        "report_kind": "conjunctive_rule_comparison",
        "provider_calls_made": False,
        "agent_evidence_source": (
            "benchmark.baselines.conjunction.bounded_agent_fragments -- two "
            "mechanical narration splits, deterministic and non-linguistic; not a "
            "model and not a model result"
        ),
        "shippability_criteria": (
            "adversarially safe on every fixture; order- and duplicate-invariant; "
            "zero wrong resolutions on DEV; zero DEV T3 resolutions; and no fewer "
            "correct DEV T2 resolutions than the rule already shipped (R0). The v4 "
            "stale-reference archetype is deliberately NOT a criterion: separating "
            "it needs a narration-date-to-settlement-date relation, which this "
            "change does not add, so no reference-only rule can escalate it and "
            "requiring one to would be requiring the impossible. A rule must also "
            "still refuse a case the investigation gathered no evidence for, which "
            "is what separates the otherwise equally safe closure rules."
        ),
        "verdicts": verdicts,
        "adversarial": adversarial,
        "invariance": invariance,
        "no_evidence_invariant": no_evidence,
        "v4_pilot": v4,
        "dev": dev,
    }


def write_report(report: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "collect_agent_evidence",
    "run_adversarial",
    "run_harness",
    "run_invariance",
    "run_no_evidence_invariant",
    "run_split",
    "write_report",
]


def main(argv: list[str] | None = None) -> int:
    """``python -m benchmark.baselines.conjunction_report``.

    Exits non-zero when the rule the tree currently ships would not pass its
    own shippability criteria -- so the harness is a check, not just a report.
    """
    import argparse

    from finrecon.agent.version import VALIDATOR_VERSION
    from finrecon.loader import default_benchmark_dir

    parser = argparse.ArgumentParser(
        prog="python -m benchmark.baselines.conjunction_report",
        description=(
            "Compare candidate conjunctive-reference rules against the adversarial "
            "fixtures, the v4 pilot and the DEV residual. Zero provider calls."
        ),
    )
    parser.add_argument("--benchmark-dir", default=None, type=Path)
    parser.add_argument("--json-out", default=None, type=Path)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = run_harness(args.benchmark_dir or default_benchmark_dir())
    if args.json_out:
        write_report(report, args.json_out)

    if args.quiet:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print()
        print(f"conjunctive-rule comparison  (shipped: {VALIDATOR_VERSION})")
        print(f"  provider calls:   {report['provider_calls_made']}")
        print(f"  agent evidence:   {report['agent_evidence_source']}")
        header = (
            f"  {'rule':<34}{'adv':>5}{'ord':>5}{'dup':>5}{'noEv':>6}"
            f"{'devWrong':>10}{'devT2':>7}{'vs v1':>7}{'T3res':>7}"
            f"{'v4 res/corr/wrong':>20}{'SHIP':>6}"
        )
        print()
        print(header)
        for rule, verdict in report["verdicts"].items():
            v4_cell = (
                f"{verdict['v4_resolved']}/{verdict['v4_correct']}/{verdict['v4_wrong']}"
            )
            print(
                f"  {rule:<34}"
                f"{str(verdict['adversarially_safe'])[0]:>5}"
                f"{str(verdict['order_invariant'])[0]:>5}"
                f"{str(verdict['duplicate_invariant'])[0]:>5}"
                f"{str(verdict['refuses_uninvestigated_case'])[0]:>6}"
                f"{verdict['dev_wrong']:>10}"
                f"{verdict['dev_t2_correct']:>7}"
                f"{verdict['dev_t2_correct_vs_v1']:>+7}"
                f"{verdict['dev_t3_resolved']:>7}"
                f"{v4_cell:>20}"
                f"{str(verdict['shippable']):>6}"
            )
        for rule, block in report["adversarial"].items():
            if block["safe"]:
                continue
            print()
            print(f"  adversarial failures -- {rule}")
            for failure in block["failures"]:
                print(
                    f"    {failure['fixture']:<38} ({failure['attack']}) "
                    f"expected={failure['expected']} actual={failure['actual']}"
                )
        print()
        print(f"  criteria: {report['shippability_criteria']}")

    shipped = {
        "validator.v2": "R2_seeded_closure_intersection",
    }.get(VALIDATOR_VERSION)
    if shipped is None:
        print(
            f"no rule is mapped to {VALIDATOR_VERSION}; add one before trusting "
            "this exit code",
            file=sys.stderr,
        )
        return 2
    if not report["verdicts"][shipped]["shippable"]:
        print(
            f"the shipped rule ({shipped}) does not pass its own criteria",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
