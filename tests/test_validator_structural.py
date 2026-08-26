"""validator.v3 structural evidence: exact relations, closed over all candidates."""

from __future__ import annotations

import json
from datetime import date, timedelta

from finrecon.agent.tools import TOOL_COMPARE_REFERENCE_FRAGMENT, ToolContext, execute
from finrecon.candidates.snapshot import BaseEvidence, build_case_snapshot
from finrecon.decide.policy import adjudicate
from finrecon.decide.validator import RawToolEvidence
from finrecon.matchers.evidence import BreakupLineEvidence, SettlementDerivation
from tests.stage3_factories import FEE_PAISE, NET_PAISE, TAX_PAISE, VALUE_DATE, candidate, settlement_facts, snapshot_of
from tests.test_policy import trajectory_for


def _facts(settlement_id: str, utr: str, day: date, refunds: tuple[int, ...] = ()):
    facts = settlement_facts(settlement_id, utr)
    payment = NET_PAISE - FEE_PAISE - TAX_PAISE + sum(refunds)
    lines = [
        BreakupLineEvidence(line_type="payment", amount_paise=payment, reference_id=f"pay_{settlement_id}", reference_status="captured"),
        BreakupLineEvidence(line_type="fee", amount_paise=FEE_PAISE, reference_id=None, reference_status=None),
        BreakupLineEvidence(line_type="tax", amount_paise=TAX_PAISE, reference_id=None, reference_status=None),
    ]
    lines.extend(
        BreakupLineEvidence(line_type="refund", amount_paise=-amount, reference_id=f"rfnd_{settlement_id}_{i}", reference_status="processed")
        for i, amount in enumerate(refunds)
    )
    derivation = SettlementDerivation(
        settlement_id=settlement_id,
        settlement_amount_paise=NET_PAISE,
        breakup_total_paise=sum(line.amount_paise for line in lines),
        breakup_by_type=tuple(sorted({kind: sum(line.amount_paise for line in lines if line.line_type == kind) for kind in {line.line_type for line in lines}}.items())),
        lines=tuple(lines),
        unexplained_delta_paise=0,
        declared_adjustment_paise=0,
    )
    return facts.model_copy(update={"settlement_date_utc": day, "derivation": derivation})


def _snapshot(narration: str, specs: tuple[tuple[str, str, date, tuple[int, ...]], ...]):
    settlements = tuple(_facts(*spec) for spec in specs)
    draft = snapshot_of(narration=narration, settlements=settlements)
    candidates = tuple(
        candidate(draft.bank_record_id, (facts.settlement_id,)).model_copy(update={"settlement_dates": (facts.settlement_date_utc,)})
        for facts in settlements
    )
    return build_case_snapshot(
        case_id=draft.case_id, batch_id=draft.batch_id, bank_record_id=draft.bank_record_id,
        unresolved_rule_id=draft.unresolved_rule_id, unresolved_matcher_id=draft.unresolved_matcher_id,
        candidates=candidates,
        base_evidence=BaseEvidence(
            bank_record=draft.base_evidence.bank_record,
            settlement_facts=settlements,
            decision_evidence=draft.base_evidence.decision_evidence,
            blocking=draft.base_evidence.blocking,
        ),
    )


def _run(snapshot, fragment: str | None):
    evidence = ()
    if fragment is not None:
        arguments, output = execute(ToolContext(snapshot=snapshot), TOOL_COMPARE_REFERENCE_FRAGMENT, json.dumps({"fragment": fragment}))
        evidence = (RawToolEvidence(tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT, arguments=arguments.model_dump(mode="json"), output=output.model_dump(mode="json")),)
    return adjudicate(snapshot=snapshot, trajectory=trajectory_for(snapshot, evidence=evidence))


def test_stale_reference_and_wrong_date_escalates():
    snapshot = _snapshot("REF UTRA1111 VALDT 02APR26", (("setl_a", "UTRA1111", VALUE_DATE - timedelta(days=1), ()), ("setl_b", "UTRB2222", VALUE_DATE, ())))
    result, decision = _run(snapshot, "UTRA1111")
    assert not decision.resolved
    assert result.structural_evidence_state == "consistent"
    assert result.resolution_evidence_basis == "structural_contradiction"


def test_correct_reference_and_correct_date_resolves():
    snapshot = _snapshot("REF UTRA1111 VALDT 02APR26", (("setl_a", "UTRA1111", VALUE_DATE, ()), ("setl_b", "UTRB2222", VALUE_DATE - timedelta(days=1), ())))
    result, decision = _run(snapshot, "UTRA1111")
    assert decision.resolved
    assert result.resolution_evidence_basis == "reference+date"


def test_date_alone_does_not_resolve():
    snapshot = _snapshot("VALDT 02APR26", (("setl_a", "UTRA1111", VALUE_DATE, ()), ("setl_b", "UTRB2222", VALUE_DATE - timedelta(days=1), ())))
    _result, decision = _run(snapshot, None)
    assert not decision.resolved


def test_same_date_for_multiple_candidates_does_not_create_uniqueness():
    snapshot = _snapshot("REF HDFCCN1234 VALDT 02APR26", (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, ()), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE, ())))
    _result, decision = _run(snapshot, "HDFCCN1234")
    assert not decision.resolved


def test_wrong_reference_and_right_date_fails_closed():
    snapshot = _snapshot("REF UTRA1111 VALDT 02APR26", (("setl_a", "UTRA1111", VALUE_DATE - timedelta(days=1), ()), ("setl_b", "UTRB2222", VALUE_DATE, ())))
    _result, decision = _run(snapshot, "UTRA1111")
    assert not decision.resolved


def test_shared_breakup_amount_remains_ambiguous():
    snapshot = _snapshot("REF HDFCCN1234 RFND 10.00", (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, (1000,)), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE, (1000,))))
    _result, decision = _run(snapshot, "HDFCCN1234")
    assert not decision.resolved


def test_unique_breakup_amount_composes_with_reference():
    snapshot = _snapshot("REF HDFCCN1234 RFND 10.00", (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, (1000,)), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE, (2000,)), ("setl_c", "OTHERCCC", VALUE_DATE, (1000,))))
    result, decision = _run(snapshot, "HDFCCN1234")
    assert decision.resolved_settlement_ids == ("setl_a",)
    assert result.resolution_evidence_basis == "reference+amount"


def test_multiple_amounts_cannot_select_whichever_candidate_is_convenient():
    snapshot = _snapshot("REF HDFCCN1234 RFND 10.00 RFND 20.00", (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, (1000,)), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE, (2000,))))
    result, decision = _run(snapshot, "HDFCCN1234")
    assert not decision.resolved
    assert result.structural_evidence_state == "contradictory"


def test_duplicate_amount_tokens_do_not_strengthen_evidence():
    specs = (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, (1000,)), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE, (2000,)), ("setl_c", "OTHERCCCCC", VALUE_DATE, (2000,)))
    one = _snapshot("REF HDFCCN1234 RFND 10.00", specs)
    two = _snapshot("REF HDFCCN1234 RFND 10.00 RFND 10.00", specs)
    first, first_decision = _run(one, "HDFCCN1234")
    second, second_decision = _run(two, "HDFCCN1234")
    assert first_decision.resolved_settlement_ids == second_decision.resolved_settlement_ids == ("setl_a",)
    assert len(first.structural_closure.breakup_amount_facts) == len(second.structural_closure.breakup_amount_facts) == 1


def test_rupees_are_normalized_to_integer_paise_without_float():
    snapshot = _snapshot("REF UTRA1111 RFND 41874.50", (("setl_a", "UTRA1111", VALUE_DATE, (4_187_450,)), ("setl_b", "UTRB2222", VALUE_DATE, (1000,))))
    result, decision = _run(snapshot, "UTRA1111")
    assert result.structural_closure.breakup_amount_facts[0].parsed_amount_paise == 4_187_450
    assert decision.resolved_settlement_ids == ("setl_a",)


def test_contradictory_structural_facts_leave_no_survivor():
    snapshot = _snapshot("REF HDFCCN1234 VALDT 02APR26 RFND 10.00", (("setl_a", "HDFCCN1234AAAA", VALUE_DATE, (2000,)), ("setl_b", "HDFCCN1234BBBB", VALUE_DATE - timedelta(days=1), (1000,))))
    result, decision = _run(snapshot, "HDFCCN1234")
    assert result.combined_consistent_candidate_ids == ()
    assert not decision.resolved
