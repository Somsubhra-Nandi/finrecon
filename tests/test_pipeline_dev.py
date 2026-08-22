"""End-to-end Stage-2 pipeline behaviour, plus a DEV engineering diagnostic.

The DEV-truth assertions here are a **development diagnostic**, not a
benchmark result. They exist so a regression in the deterministic rules
fails a test instead of quietly producing confident wrong answers, and
they read DEV only — FROZEN-EVAL outcomes are never inspected
(DESIGN.md §5.1: build against DEV, report against FROZEN).
"""

from __future__ import annotations

from collections import Counter

from finrecon.matchers.result import DecisionStatus
from finrecon.matchers.rules import (
    RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING,
    RULE_DIRECT_KEY_EXACT_TOKEN,
)


class TestPipelineShape:
    def test_one_case_per_bank_credit(self, dev_result):
        result, _ = dev_result
        assert len(result.decisions) == len(result.batch.bank_records) == 890
        assert len({d.case_id for d in result.decisions}) == 890

    def test_case_ids_derive_from_the_visible_bank_record(self, dev_result):
        result, _ = dev_result
        for decision in result.decisions:
            assert decision.case_id == f"case:{decision.bank_record_id}"

    def test_decisions_are_returned_in_deterministic_order(self, dev_result):
        result, _ = dev_result
        assert list(result.decisions) == sorted(result.decisions, key=lambda d: d.case_id)

    def test_no_settlement_is_linked_to_two_cases(self, dev_result):
        result, _ = dev_result
        linked = [sid for d in result.resolved() for sid in d.settlement_ids]
        assert len(linked) == len(set(linked))

    def test_every_case_is_either_resolved_or_has_a_snapshot(self, dev_result):
        result, _ = dev_result
        snapshot_cases = {s.case_id for s in result.snapshots}
        for decision in result.decisions:
            if decision.status is DecisionStatus.UNRESOLVED:
                assert decision.case_id in snapshot_cases

    def test_the_ledger_agrees_with_the_in_memory_result(self, dev_result):
        result, store = dev_result
        assert store.status_counts(result.batch_id) == {
            "resolved": len(result.resolved()),
            "unresolved": len(result.unresolved()),
        }
        assert len(store.audit_rows(result.batch_id)) == len(result.decisions)


class TestDevDiagnostic:
    """DEV-only engineering diagnostic. Not a benchmark result."""

    def test_dev_decision_mix(self, dev_result):
        result, _ = dev_result
        assert Counter(d.rule_id for d in result.decisions) == {
            RULE_DIRECT_KEY_EXACT_TOKEN: 350,
            RULE_DERIVED_EXACT_SETTLEMENT_ACCOUNTING: 500,
            "unresolved.multiple_derived_candidates": 40,
        }

    def test_no_dev_case_is_auto_resolved_incorrectly(self, dev_result, dev_ground_truth):
        """The metric DESIGN.md §1 says matters most: zero unsafe auto-matches."""
        result, _ = dev_result
        wrong = []
        for decision in result.resolved():
            expected = dev_ground_truth[decision.case_id]["correct_relationship"]
            if expected is None or tuple(sorted(expected["settlement_ids"])) != decision.settlement_ids:
                wrong.append(decision.case_id)
        assert wrong == []

    def test_every_dev_case_requiring_escalation_is_refused(self, dev_result, dev_ground_truth):
        result, _ = dev_result
        by_case = {d.case_id: d for d in result.decisions}
        must_escalate = [
            case_id
            for case_id, entry in dev_ground_truth.items()
            if entry["required_outcome"] == "ESCALATE"
        ]
        assert len(must_escalate) == 40
        for case_id in must_escalate:
            assert by_case[case_id].status is DecisionStatus.UNRESOLVED, case_id

    def test_unresolved_dev_cases_are_exactly_the_ones_requiring_escalation(
        self, dev_result, dev_ground_truth
    ):
        result, _ = dev_result
        unresolved = {d.case_id for d in result.unresolved()}
        required = {
            case_id
            for case_id, entry in dev_ground_truth.items()
            if entry["required_outcome"] == "ESCALATE"
        }
        assert unresolved == required

    def test_dev_tier_breakdown(self, dev_result, dev_ground_truth):
        """Records what the deterministic core does per benchmark tier."""
        result, _ = dev_result
        by_case = {d.case_id: d for d in result.decisions}
        tally: Counter = Counter()
        for case_id, entry in dev_ground_truth.items():
            decision = by_case[case_id]
            expected = entry["correct_relationship"]
            if decision.status is DecisionStatus.RESOLVED:
                correct = expected and tuple(sorted(expected["settlement_ids"])) == decision.settlement_ids
                outcome = "resolved_correct" if correct else "resolved_incorrect"
            else:
                outcome = "unresolved_correct" if expected is None else "unresolved"
            tally[(entry["tier"], outcome)] += 1

        assert dict(tally) == {
            ("T0", "resolved_correct"): 350,
            ("T1", "resolved_correct"): 300,
            ("T2", "resolved_correct"): 200,
            ("T3", "unresolved_correct"): 40,
        }
