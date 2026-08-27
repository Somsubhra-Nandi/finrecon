"""Structured, narration-independent value-date evidence.

Covers the promotion of ``BankRecord.value_date`` into deterministic,
provenance-backed structural evidence (``build_structured_value_date_fact``),
distinct from the legacy narration-``VALDT``-token path it sits beside. See
``src/finrecon/evidence/structural.py`` module docstring for why this fact is
audit-only and not folded into ``StructuralClosure.intersection_candidate_ids``
this session.
"""

from __future__ import annotations

from datetime import timedelta

from finrecon.evidence.structural import (
    EvidenceSource,
    RELATION_BANK_VALUE_DATE_EXACT,
    build_structural_closure,
    build_structured_value_date_fact,
)
from tests.stage3_factories import VALUE_DATE, candidate, settlement_facts, snapshot_of


def _snapshot_two_candidates(*, no_valdt_token: bool = True):
    """Two single-settlement candidates, one on the bank value date, one not.

    Narration deliberately carries no ``VALDT`` token by default, so the
    legacy narration-driven path produces nothing -- isolating what the
    structured path alone can prove.
    """
    a = settlement_facts("setl_a", "UTRA1111")
    b = settlement_facts("setl_b", "UTRB2222")
    settlements = (a, b)
    narration = "REF UTRA1111 UTRB2222" if no_valdt_token else "REF UTRA1111 VALDT 02APR26"
    draft = snapshot_of(narration=narration, settlements=settlements)
    candidates = (
        candidate(draft.bank_record_id, ("setl_a",)).model_copy(
            update={"settlement_dates": (VALUE_DATE,)}
        ),
        candidate(draft.bank_record_id, ("setl_b",)).model_copy(
            update={"settlement_dates": (VALUE_DATE + timedelta(days=1),)}
        ),
    )
    from finrecon.candidates.snapshot import BaseEvidence, build_case_snapshot

    return build_case_snapshot(
        case_id=draft.case_id,
        batch_id=draft.batch_id,
        bank_record_id=draft.bank_record_id,
        unresolved_rule_id=draft.unresolved_rule_id,
        unresolved_matcher_id=draft.unresolved_matcher_id,
        candidates=candidates,
        base_evidence=BaseEvidence(
            bank_record=draft.base_evidence.bank_record,
            settlement_facts=settlements,
            decision_evidence=draft.base_evidence.decision_evidence,
            blocking=draft.base_evidence.blocking,
        ),
    )


def test_structured_fact_supports_exact_matching_candidate_without_any_narration_token():
    """A. BankRecord.value_date alone produces structured deterministic evidence."""
    snapshot = _snapshot_two_candidates()
    fact = build_structured_value_date_fact(snapshot)
    assert fact.bank_value_date == VALUE_DATE
    assert fact.relation_id == RELATION_BANK_VALUE_DATE_EXACT
    a_id = snapshot.candidates[0].candidate_id
    assert fact.reached_candidate_ids == (a_id,)


def test_narration_is_never_mutated():
    """B. No narration mutation occurs anywhere in the structured path."""
    narration = "REF UTRA1111 UTRB2222"
    snapshot = _snapshot_two_candidates()
    before = snapshot.base_evidence.bank_record.narration
    build_structured_value_date_fact(snapshot)
    build_structural_closure(snapshot)
    after = snapshot.base_evidence.bank_record.narration
    assert before == after == narration


def test_structured_fact_is_source_backed_to_bank_record_not_narration():
    """C. Structured fact is provenance-tagged to BankRecord, carries no narration span."""
    snapshot = _snapshot_two_candidates()
    fact = build_structured_value_date_fact(snapshot)
    assert fact.source is EvidenceSource.STRUCTURED_BANK_FIELD
    assert fact.bank_record_id == snapshot.base_evidence.bank_record.bank_record_id
    assert not hasattr(fact, "raw_source_span")
    assert not hasattr(fact, "source_offsets")


def test_correct_candidate_date_relation_supports_the_candidate():
    """D. Exact settlement-date match supports that candidate."""
    snapshot = _snapshot_two_candidates()
    fact = build_structured_value_date_fact(snapshot)
    a_result = next(r for r in fact.candidate_results if r.candidate_id == snapshot.candidates[0].candidate_id)
    assert a_result.consistent is True


def test_contradictory_candidate_date_relation_is_not_reached():
    """E. A candidate whose settlement date differs is not supported by this fact.

    The structured fact only ever *supports*; refutation of the mismatched
    candidate is expressed as absence from ``reached_candidate_ids``, not as
    an empty-reach contradiction folded into the closed intersection -- see
    the module docstring for why the two are kept separate this session.
    """
    snapshot = _snapshot_two_candidates()
    fact = build_structured_value_date_fact(snapshot)
    b_id = snapshot.candidates[1].candidate_id
    b_result = next(r for r in fact.candidate_results if r.candidate_id == b_id)
    assert b_result.consistent is False
    assert b_id not in fact.reached_candidate_ids


def test_candidate_with_no_settlement_dates_produces_no_fabricated_match():
    """F. A candidate missing an authoritative settlement date is never guessed into reach."""
    snapshot = _snapshot_two_candidates()
    stripped = snapshot.model_copy(
        update={
            "candidates": tuple(
                c.model_copy(update={"settlement_dates": ()}) for c in snapshot.candidates
            )
        }
    )
    fact = build_structured_value_date_fact(stripped)
    assert fact.reached_candidate_ids == ()
    assert all(result.consistent is False for result in fact.candidate_results)


def test_agent_supplied_evidence_cannot_alter_the_structured_fact():
    """G. Nothing derived from raw_tool_evidence / agent trajectory feeds this builder.

    The builder's signature takes only the immutable snapshot, so there is no
    channel through which any agent output -- fragment text, tool output,
    self-reported confidence -- could reach or change ``bank_value_date`` or
    the relation evaluated against it.
    """
    import inspect

    signature = inspect.signature(build_structured_value_date_fact)
    assert list(signature.parameters) == ["snapshot"]

    snapshot = _snapshot_two_candidates()
    fact_first = build_structured_value_date_fact(snapshot)
    fact_second = build_structured_value_date_fact(snapshot)
    assert fact_first == fact_second


def test_two_candidates_with_different_dates_evaluated_deterministically():
    """H. Two candidates with different settlement dates are evaluated deterministically."""
    snapshot = _snapshot_two_candidates()
    first = build_structured_value_date_fact(snapshot)
    second = build_structured_value_date_fact(snapshot)
    assert first.candidate_results == second.candidate_results
    assert len(first.candidate_results) == 2


def test_legacy_valdt_narration_regression_path_is_unchanged():
    """I. The existing narration-VALDT synthetic path still produces its own fact,
    tagged as raw-narration provenance, independent of the structured fact.
    """
    snapshot = _snapshot_two_candidates(no_valdt_token=False)
    closure = build_structural_closure(snapshot)
    assert len(closure.value_date_facts) == 1
    legacy = closure.value_date_facts[0]
    assert legacy.source is EvidenceSource.RAW_NARRATION
    assert legacy.raw_source_span == "VALDT 02APR26"
    # The structured fact is still produced alongside it, independently.
    assert closure.structured_value_date_fact.source is EvidenceSource.STRUCTURED_BANK_FIELD


def test_structured_fact_does_not_change_closure_intersection_or_union():
    """The structured fact is audit-only: it must not perturb resolution this session."""
    snapshot = _snapshot_two_candidates()
    closure = build_structural_closure(snapshot)
    # No narration token in this snapshot, so the legacy facts are empty and
    # the closure has no evidence at all -- the structured fact must not
    # change that, even though it independently supports one candidate.
    assert closure.value_date_facts == ()
    assert closure.breakup_amount_facts == ()
    assert closure.has_evidence is False
    assert closure.intersection_candidate_ids == tuple(sorted(snapshot.candidate_ids()))
    assert closure.union_candidate_ids == ()
