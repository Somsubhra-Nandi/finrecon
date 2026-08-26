"""The single definition of correctness, plus soundness, agent and telemetry metrics.

Correctness
-----------

:func:`verdict_for` is the *only* place in the repository outside the tests
where "was this auto-resolution right?" is decided, and it encodes exactly the
predicate already asserted by::

    tests/test_stage3_dev_diagnostic.py::
        TestNoUnsafeAutoMatch::test_no_dev_case_is_auto_resolved_incorrectly

namely:

* an auto-resolution of a case whose ``correct_relationship`` is ``None`` is
  wrong -- there was no correct answer to reach;
* otherwise the resolved settlement set must equal
  ``tuple(sorted(correct_relationship["settlement_ids"]))`` exactly.

That test is deliberately left untouched. ``tests/test_stage4_evaluator.py``
re-derives its predicate inline and asserts the two agree case-for-case over a
full DEV Stage-3 run, so a second, drifting definition of correctness cannot
appear without a test failing.

Metrics
-------

The rates are DESIGN.md §5.3, defined once and used consistently. Two of them
carry definitional caveats that belong next to the code rather than in a
README footnote:

``overall_match_rate``
    The track-required number. Denominator is *cases with a uniquely
    resolvable ground truth*, so T3 is excluded by construction; numerator
    counts **automatic** reconciliations only, so a resolvable case the system
    escalates lowers it and a later human resolution does not repair it.
    Reported as ``None`` when the cohort contains no resolvable case at all,
    because 0/0 is not a score.

``auto_resolution_accuracy``
    §5.3's auto-resolution *precision*: correct auto-resolutions over all
    auto-resolutions. Also ``None`` on an empty denominator.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from finrecon.stage3 import CaseOutcome

from benchmark.eval.groundtruth import GroundTruthEntry

WRONG_NO_CORRECT_ANSWER = "resolved a case with no correct answer"
WRONG_SETTLEMENT = "resolved to the wrong settlement"

TOOL_VALIDATION_REASONS = (
    "unknown_tool",
    "malformed_arguments_json",
    "duplicate_argument_key",
    "schema_validation_failed",
    "unknown_candidate",
    "unknown_settlement",
    "tool_call_batch_limit_exceeded",
)
"""Every declared reason in :class:`finrecon.agent.tools.ToolValidationError`.

Listed exhaustively and always reported, zeros included, so that a reason
which never fires is visibly zero rather than absent -- an absent key reads as
"not measured", which is the wrong inference.
"""


@dataclass(frozen=True)
class CaseVerdict:
    """One case's scored outcome."""

    case_id: str
    tier: str
    archetype: str
    resolved: bool
    correct: bool | None
    """True/False for an auto-resolution; ``None`` for an escalation."""
    wrong_reason: str | None
    predicted_candidate_id: str | None
    predicted_settlement_ids: tuple[str, ...]
    truth_settlement_ids: tuple[str, ...]
    truth_reference: str | None
    termination_reason: str
    blockers: tuple[str, ...]
    evidence_relations: tuple[dict, ...]
    value_at_stake_paise: int
    is_uniquely_resolvable: bool
    escalation_correct: bool | None
    """For escalations: True when the case genuinely had no unique answer."""
    families: tuple[str, ...] = ()
    """Benchmark v4 analysis tags. Empty on v1-v3 cohorts, which have no families."""
    required_composition: str = ""
    """The evidence combination v4 says this case needs. Empty on v1-v3 cohorts."""
    candidate_count_bucket: str = "unknown"

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "tier": self.tier,
            "archetype": self.archetype,
            "families": list(self.families),
            "required_composition": self.required_composition,
            "candidate_count_bucket": self.candidate_count_bucket,
            "resolved": self.resolved,
            "correct": self.correct,
            "wrong_reason": self.wrong_reason,
            "predicted_candidate_id": self.predicted_candidate_id,
            "predicted_settlement_ids": list(self.predicted_settlement_ids),
            "truth_settlement_ids": list(self.truth_settlement_ids),
            "truth_reference": self.truth_reference,
            "termination_reason": self.termination_reason,
            "blockers": list(self.blockers),
            "evidence_relations": [dict(sorted(r.items())) for r in self.evidence_relations],
            "value_at_stake_paise": self.value_at_stake_paise,
            "is_uniquely_resolvable": self.is_uniquely_resolvable,
            "escalation_correct": self.escalation_correct,
        }


def accepted_relations_for(outcome: CaseOutcome) -> tuple[dict, ...]:
    """The evidence that allowed the resolution, with full source provenance.

    Since ``validator.v2`` this reads the deterministic reference *closure*
    rather than the agent's own findings, because the closure is what the
    decision was made over. Every informative claim in a resolved case is
    consistent with the chosen candidate -- that is what "the intersection is
    this candidate" means -- so each contributes exactly one row, and a
    conjunctive resolution reports one row per clue it needed.

    Reading the agent's findings instead would have made a conjunctive
    resolution look unevidenced: none of its clues is discriminating on its
    own, which is the whole point of it.

    Each row carries the narration offsets and the atom identity alongside the
    relation, so a reader can point at the characters a resolution rests on
    without trusting anything a model wrote.
    """
    chosen = outcome.decision.resolved_candidate_id
    if chosen is None:
        return ()
    relations: list[dict] = []
    for atom in outcome.validator_result.reference_closure.informative_atoms():
        for match in atom.matches:
            if match.candidate_id != chosen:
                continue
            relations.append(
                {
                    "evidence_kind": "reference",
                    "atom_id": atom.atom_id,
                    "fragment": atom.fragment,
                    "narration_span": list(atom.span),
                    "narration_offsets": list(atom.occurrences),
                    "member_fragment_count": atom.member_fragment_count,
                    "candidates_reached": len(atom.reach),
                    "relation_id": match.relation_id,
                    "reference_kind": match.reference_kind,
                    "reference_value": match.reference_value,
                    "pinned_reference_characters": match.pinned_reference_characters,
                }
            )
    structural = outcome.validator_result.structural_closure
    for fact in structural.value_date_facts:
        if chosen not in fact.reached_candidate_ids:
            continue
        candidate = next(r for r in fact.candidate_results if r.candidate_id == chosen)
        relations.append(
            {
                "evidence_kind": "value_date",
                "source_span": fact.raw_source_span,
                "narration_offsets": list(fact.source_offsets),
                "relation_id": fact.relation_id,
                "bank_value_date": fact.bank_value_date.isoformat(),
                "parsed_value_date": fact.parsed_value_date.isoformat(),
                "candidate_settlement_dates": [d.isoformat() for d in candidate.candidate_settlement_dates],
                "candidates_reached": len(fact.reached_candidate_ids),
            }
        )
    for fact in structural.breakup_amount_facts:
        if chosen not in fact.reached_candidate_ids:
            continue
        matches = [match for match in fact.matches if match.candidate_id == chosen]
        relations.append(
            {
                "evidence_kind": "breakup_amount",
                "source_span": fact.raw_source_span,
                "raw_amount_token": fact.raw_amount_token,
                "narration_offsets": list(fact.source_offsets),
                "parsed_amount_paise": fact.parsed_amount_paise,
                "relation_id": fact.relation_id,
                "settlement_ids": sorted({match.settlement_id for match in matches}),
                "signed_line_amounts_paise": sorted({match.signed_amount_paise for match in matches}),
                "candidates_reached": len(fact.reached_candidate_ids),
            }
        )
    return tuple(relations)


def verdict_for(outcome: CaseOutcome, entry: GroundTruthEntry) -> CaseVerdict:
    """Score one case. See the module docstring for the predicate's provenance."""
    decision = outcome.decision
    resolved = decision.resolved

    correct: bool | None = None
    wrong_reason: str | None = None
    escalation_correct: bool | None = None

    if resolved:
        if entry.correct_relationship is None:
            correct, wrong_reason = False, WRONG_NO_CORRECT_ANSWER
        elif entry.expected_settlement_ids != tuple(decision.resolved_settlement_ids):
            correct, wrong_reason = False, WRONG_SETTLEMENT
        else:
            correct = True
    else:
        # An escalation is "correct" exactly when there was no unique answer to
        # find. Escalating a resolvable case is not an error -- the system is
        # allowed to decline -- but it is not a correct escalation either, and
        # it costs match rate, which is where that shows up.
        escalation_correct = not entry.is_uniquely_resolvable

    return CaseVerdict(
        case_id=outcome.case_id,
        tier=entry.tier,
        archetype=entry.archetype,
        resolved=resolved,
        correct=correct,
        wrong_reason=wrong_reason,
        predicted_candidate_id=decision.resolved_candidate_id,
        predicted_settlement_ids=tuple(decision.resolved_settlement_ids),
        truth_settlement_ids=entry.expected_settlement_ids,
        truth_reference=entry.true_reference,
        termination_reason=outcome.trajectory.termination_reason,
        blockers=tuple(decision.blockers),
        evidence_relations=accepted_relations_for(outcome) if resolved else (),
        value_at_stake_paise=entry.value_at_stake_paise,
        is_uniquely_resolvable=entry.is_uniquely_resolvable,
        escalation_correct=escalation_correct,
        families=entry.families,
        required_composition=entry.required_composition,
        candidate_count_bucket=entry.candidate_count_bucket,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    """A rate, or ``None`` when the denominator is empty. Never 0/0 as 0.0."""
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def aggregate_scores(verdicts: Iterable[CaseVerdict]) -> dict:
    """The §5.3 metric block for a cohort."""
    items = list(verdicts)
    investigated = len(items)
    auto_resolved = sum(1 for v in items if v.resolved)
    correct = sum(1 for v in items if v.resolved and v.correct)
    wrong = sum(1 for v in items if v.resolved and v.correct is False)
    escalated = investigated - auto_resolved
    resolvable = sum(1 for v in items if v.is_uniquely_resolvable)
    ambiguous = investigated - resolvable
    correctly_escalated = sum(1 for v in items if not v.resolved and v.escalation_correct)
    value_at_risk = sum(v.value_at_stake_paise for v in items if v.correct is False)

    return {
        "investigated": investigated,
        "auto_resolved": auto_resolved,
        "correct_auto_resolutions": correct,
        "wrong_auto_resolutions": wrong,
        "escalated": escalated,
        "auto_resolution_accuracy": _ratio(correct, auto_resolved),
        "overall_match_rate": _ratio(correct, resolvable),
        "auto_resolution_coverage": _ratio(auto_resolved, investigated),
        "unsafe_auto_match_rate": _ratio(wrong, investigated),
        "escalation_recall": _ratio(correctly_escalated, ambiguous),
        "uniquely_resolvable_cases": resolvable,
        "truly_ambiguous_cases": ambiguous,
        "correctly_escalated": correctly_escalated,
        "value_at_risk_paise": value_at_risk,
        "match_rate_denominator": "cases with a uniquely resolvable ground truth",
        "match_rate_numerator": "correct automatic reconciliations only",
        "scoring_available": True,
        "scoring_unavailable_reason": None,
    }


def _slice_metrics(items: list[CaseVerdict]) -> dict:
    """The subset of the section 5.3 block that survives being sliced.

    Deliberately narrower than :func:`aggregate_scores`. A slice of six cases
    has a match rate, but quoting its escalation recall next to the cohort's
    invites reading one small denominator as if it were the other. So a slice
    reports counts, the two rates whose denominators it actually owns, and
    value at risk -- and nothing that would be mistaken for a headline.
    """
    resolvable = sum(1 for v in items if v.is_uniquely_resolvable)
    auto = sum(1 for v in items if v.resolved)
    correct = sum(1 for v in items if v.resolved and v.correct)
    wrong = sum(1 for v in items if v.resolved and v.correct is False)
    correctly_escalated = sum(1 for v in items if not v.resolved and v.escalation_correct)
    return {
        "cases": len(items),
        "uniquely_resolvable": resolvable,
        "truly_ambiguous": len(items) - resolvable,
        "auto_resolved": auto,
        "correct_auto_resolutions": correct,
        "wrong_auto_resolutions": wrong,
        "escalated": len(items) - auto,
        "correctly_escalated": correctly_escalated,
        "match_rate": _ratio(correct, resolvable),
        "auto_resolution_accuracy": _ratio(correct, auto),
        "value_at_risk_paise": sum(
            v.value_at_stake_paise for v in items if v.correct is False
        ),
    }


def _group_metrics(
    verdicts: Iterable[CaseVerdict], key
) -> dict:
    grouped: dict[str, list[CaseVerdict]] = {}
    for verdict in verdicts:
        for label in key(verdict):
            grouped.setdefault(label, []).append(verdict)
    return {label: _slice_metrics(items) for label, items in sorted(grouped.items())}


def metrics_by_family(verdicts: Iterable[CaseVerdict]) -> dict:
    """Per-family metrics. A case in several families is counted in each.

    Overlapping by design: v4's families are descriptive tags, not a partition,
    so the family counts deliberately do not sum to the cohort size. The block
    is empty for a v1-v3 cohort, which is the honest rendering of "this
    benchmark generation has no families" -- distinct from a family with zero
    cases.
    """
    return _group_metrics(verdicts, lambda v: v.families)


def metrics_by_required_composition(verdicts: Iterable[CaseVerdict]) -> dict:
    """Per-composition metrics. Exactly one composition per case, so this partitions."""
    return _group_metrics(
        verdicts, lambda v: (v.required_composition,) if v.required_composition else ()
    )


def metrics_by_candidate_count(verdicts: Iterable[CaseVerdict]) -> dict:
    """Per-candidate-set-size metrics. ``unknown`` for splits that record no count."""
    return _group_metrics(verdicts, lambda v: (v.candidate_count_bucket,))


def metrics_by_tier(verdicts: Iterable[CaseVerdict]) -> dict:
    """Per-tier metrics -- the DESIGN.md 5.4 results table, as data."""
    return _group_metrics(verdicts, lambda v: (v.tier,))


def metrics_by_archetype(verdicts: Iterable[CaseVerdict]) -> dict:
    return _group_metrics(verdicts, lambda v: (v.archetype,))


def conjunction_metrics(outcomes: Iterable[CaseOutcome]) -> dict:
    """How much of the cohort needed conjunctive reference evidence, and how.

    New with ``validator.v2``. Every figure is provenance rather than accuracy:
    it describes the *shape* of the evidence a decision rested on, which is the
    thing a reader of an audit trail wants and the thing a version bump makes
    incomparable across runs if it is not reported.

    ``agent_atom_coverage`` is the one to read for the C-vs-D question. Since v2
    the decision does not depend on which claims the agent surfaced, so how many
    it surfaced is free to measure -- and it is the honest form of "did the
    investigation earn its tokens?", now that it cannot be confused with "did
    the investigation decide?".
    """
    items = list(outcomes)
    resolved = [o for o in items if o.decision.resolved]
    conjunctive = [o for o in resolved if o.validator_result.resolved_conjunctively]

    states: Counter = Counter()
    atom_counts: Counter = Counter()
    span_counts: Counter = Counter()
    intersection_sizes: Counter = Counter()
    surfaced = 0
    informative_total = 0
    incomplete_closures = 0

    for outcome in items:
        validator = outcome.validator_result
        closure = validator.reference_closure
        states[validator.reference_evidence_state] += 1
        if not closure.is_complete:
            incomplete_closures += 1
            continue
        atom_counts[str(len(validator.informative_atom_ids))] += 1
        span_counts[str(closure.independent_span_count())] += 1
        intersection_sizes[str(len(validator.reference_intersection_candidate_ids))] += 1
        informative_total += len(validator.informative_atom_ids)
        surfaced += len(validator.agent_surfaced_atom_ids)

    return {
        "resolutions_total": len(resolved),
        "resolutions_needing_conjunction": len(conjunctive),
        "resolutions_from_a_single_claim": len(resolved) - len(conjunctive),
        "reference_evidence_states": dict(sorted(states.items())),
        "informative_atoms_per_case": dict(sorted(atom_counts.items())),
        "independent_narration_spans_per_case": dict(sorted(span_counts.items())),
        "final_intersection_size": dict(sorted(intersection_sizes.items())),
        "informative_atoms_total": informative_total,
        "informative_atoms_surfaced_by_agent": surfaced,
        "agent_atom_coverage": _ratio(surfaced, informative_total),
        "cases_with_incomplete_closure": incomplete_closures,
        "closure_is_the_decision_input": True,
        "note": (
            "The agent seeds the reference path and does not select within it, so "
            "agent_atom_coverage measures investigation efficiency and nothing "
            "about correctness. See src/finrecon/decide/validator.py."
        ),
    }


def structural_metrics(outcomes: Iterable[CaseOutcome]) -> dict:
    """Decision-basis and provenance counts for validator.v3 structural facts."""
    items = list(outcomes)
    bases: Counter = Counter()
    states: Counter = Counter()
    value_date_facts = 0
    amount_facts = 0
    contradiction_escalations = 0
    for outcome in items:
        validator = outcome.validator_result
        bases[validator.resolution_evidence_basis] += 1
        states[validator.structural_evidence_state] += 1
        value_date_facts += len(validator.structural_closure.value_date_facts)
        amount_facts += len(validator.structural_closure.breakup_amount_facts)
        if not outcome.decision.resolved and validator.resolution_evidence_basis == "structural_contradiction":
            contradiction_escalations += 1
    return {
        "resolution_evidence_basis": dict(sorted(bases.items())),
        "structural_evidence_states": dict(sorted(states.items())),
        "reference_only_resolutions": sum(1 for o in items if o.decision.resolved and o.validator_result.resolution_evidence_basis == "reference-only"),
        "reference_plus_date_resolutions": sum(1 for o in items if o.decision.resolved and o.validator_result.resolution_evidence_basis == "reference+date"),
        "reference_plus_amount_resolutions": sum(1 for o in items if o.decision.resolved and o.validator_result.resolution_evidence_basis == "reference+amount"),
        "structural_contradiction_escalations": contradiction_escalations,
        "value_date_facts": value_date_facts,
        "breakup_amount_facts": amount_facts,
        "complete_snapshot_is_structural_axis": True,
    }


# --- Soundness -------------------------------------------------------------


@dataclass(frozen=True)
class SoundnessViolation:
    case_id: str
    check: str
    detail: str

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "check": self.check, "detail": self.detail}


CHECK_EXACT_PAISE = "exact_paise_reconciliation"
CHECK_FRAGMENT_IN_NARRATION = "fragment_present_in_narration"
CHECK_ACCEPTED_RELATIONS = "accepted_relation_ids_only"
CHECK_NO_FABRICATION = "no_fabricated_evidence"
CHECK_SNAPSHOT_INTEGRITY = "snapshot_integrity"

SOUNDNESS_CHECKS = (
    CHECK_EXACT_PAISE,
    CHECK_FRAGMENT_IN_NARRATION,
    CHECK_ACCEPTED_RELATIONS,
    CHECK_NO_FABRICATION,
    CHECK_SNAPSHOT_INTEGRITY,
)


def soundness_violations(outcomes: Iterable[CaseOutcome]) -> list[SoundnessViolation]:
    """Re-check every auto-resolution against the invariants that make it safe.

    These are the checks the Stage-3 DEV diagnostic already asserts, applied
    here to a *recorded* run rather than a fake-provider one. They answer a
    different question from correctness: a resolution can be right by luck.
    These say it was reached the declared way -- exact to the paise, on a
    fragment that really is in the bank narration, under a relation the policy
    declared, over a candidate set the agent could not shrink.
    """
    violations: list[SoundnessViolation] = []
    for outcome in outcomes:
        validator = outcome.validator_result

        if not validator.snapshot_integrity_ok:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_SNAPSHOT_INTEGRITY,
                    "snapshot content no longer matches its recorded hash",
                )
            )

        accepted = set(outcome.decision.policy_declaration["accepted_relation_ids"])
        for finding in validator.findings:
            for match in finding.matches:
                if match.relation_id not in accepted:
                    violations.append(
                        SoundnessViolation(
                            outcome.case_id,
                            CHECK_ACCEPTED_RELATIONS,
                            f"relation {match.relation_id!r} is not in the declared "
                            f"accepted set {sorted(accepted)}",
                        )
                    )

        narration = outcome.snapshot.base_evidence.bank_record.narration
        for fragment in validator.admissible_fragments:
            if fragment not in narration:
                violations.append(
                    SoundnessViolation(
                        outcome.case_id,
                        CHECK_FRAGMENT_IN_NARRATION,
                        f"admissible fragment {fragment!r} is absent from the "
                        "bank narration",
                    )
                )

        if not outcome.decision.resolved:
            continue

        chosen = outcome.decision.resolved_candidate_id
        if chosen not in validator.complete_candidate_ids:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_NO_FABRICATION,
                    f"resolved candidate {chosen!r} is not in the deterministic "
                    "candidate set",
                )
            )
        # validator.v2: a resolution rests on the deterministic closure, not on
        # a single discriminating fragment. Three things have to hold, and the
        # v1 check ("some fragment reached this candidate alone") holds for none
        # of them in a conjunctive case -- which is why it was replaced rather
        # than kept alongside.
        closure = validator.reference_closure
        if not closure.is_complete:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_NO_FABRICATION,
                    "resolved on an incomplete reference closure",
                )
            )
        if not closure.informative_atom_ids:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_NO_FABRICATION,
                    "resolved with no informative reference evidence",
                )
            )
        if validator.combined_consistent_candidate_ids != (chosen,):
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_NO_FABRICATION,
                    f"resolved {chosen!r} but combined closed evidence isolates "
                    f"{list(validator.combined_consistent_candidate_ids)}",
                )
            )
        for atom in closure.informative_atoms():
            if atom.fragment not in narration:
                violations.append(
                    SoundnessViolation(
                        outcome.case_id,
                        CHECK_FRAGMENT_IN_NARRATION,
                        f"closure atom {atom.atom_id} quotes {atom.fragment!r}, which "
                        "is absent from the bank narration",
                    )
                )
            for match in atom.matches:
                if match.relation_id not in accepted:
                    violations.append(
                        SoundnessViolation(
                            outcome.case_id,
                            CHECK_ACCEPTED_RELATIONS,
                            f"closure atom {atom.atom_id} rests on relation "
                            f"{match.relation_id!r}, which is not in the declared "
                            f"accepted set {sorted(accepted)}",
                        )
                    )

        assessment = next(
            (a for a in validator.financial_assessments if a.candidate_id == chosen),
            None,
        )
        if assessment is None:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_EXACT_PAISE,
                    f"no financial assessment for resolved candidate {chosen!r}",
                )
            )
            continue
        if assessment.group_unexplained_delta_paise != 0:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_EXACT_PAISE,
                    f"unexplained delta {assessment.group_unexplained_delta_paise} paise",
                )
            )
        if not assessment.every_breakup_is_exact:
            violations.append(
                SoundnessViolation(
                    outcome.case_id, CHECK_EXACT_PAISE, "a settlement breakup is inexact"
                )
            )
        if not assessment.breakup_references_are_sound:
            violations.append(
                SoundnessViolation(
                    outcome.case_id, CHECK_EXACT_PAISE, "breakup references are unsound"
                )
            )
    return violations


# --- Agent / tool quality --------------------------------------------------


SKIPPED_DUE_TO_BATCH_REJECTION = "skipped_due_to_batch_rejection"


def trajectory_metrics(payloads: Iterable[dict]) -> dict:
    """Termination and tool-validation aggregates, from raw trajectory dicts.

    Deliberately dict-based rather than model-based. A trajectory recorded
    under an older ``cache_schema_version`` cannot be parsed by today's
    :class:`finrecon.agent.trajectory.Trajectory` -- that is the whole point
    of versioning the record format -- but its termination reason and its
    tool-validation rejections are still plain, readable facts. Keeping this
    layer on dicts is what lets a historical baseline be measured at all,
    while :func:`agent_metrics` reuses the very same counting for a replayed
    run so the two can be compared without a second implementation.
    """
    terminations: Counter = Counter()
    reasons: Counter = Counter()
    cases_with_validation_failure = 0
    skipped_invocations = 0
    total_invocations = 0
    cases = 0

    for payload in payloads:
        cases += 1
        terminations[payload.get("termination_reason", "unknown")] += 1
        invocations = payload.get("tool_invocations") or []
        case_failed = False
        for invocation in invocations:
            total_invocations += 1
            reason = invocation.get("validation_error_reason")
            if reason:
                reasons[reason] += 1
                case_failed = True
            status = invocation.get("status")
            if status == SKIPPED_DUE_TO_BATCH_REJECTION or (
                status is None
                and invocation.get("output") is None
                and not reason
            ):
                skipped_invocations += 1
        if case_failed:
            cases_with_validation_failure += 1

    return {
        "cases": cases,
        "termination_reasons": dict(sorted(terminations.items())),
        "investigation_complete": terminations.get("investigation_complete", 0),
        "deterministic_policy_resolved": terminations.get("deterministic_policy_resolved", 0),
        "tool_validation_failed": terminations.get("tool_validation_failed", 0),
        "provider_configuration_failure": terminations.get(
            "provider_configuration_failure", 0
        ),
        "provider_infrastructure_failure": terminations.get(
            "provider_infrastructure_failure", 0
        ),
        "cases_with_any_validation_failure": cases_with_validation_failure,
        "tool_validation_reasons": {
            reason: reasons.get(reason, 0) for reason in TOOL_VALIDATION_REASONS
        },
        "tool_validation_rejections_total": sum(reasons.values()),
        "tool_invocations_total": total_invocations,
        "tool_invocations_skipped": skipped_invocations,
    }


def agent_metrics(outcomes: Iterable[CaseOutcome]) -> dict:
    """Termination, tool-validation and evidence-relation aggregates.

    The trajectory half is delegated to :func:`trajectory_metrics` so a
    replayed run and a recorded-only run are counted by identical code. The
    evidence and blocker halves need the *decision*, which only exists after
    replay, so they are added here.
    """
    items = list(outcomes)
    base = trajectory_metrics(o.trajectory.model_dump(mode="json") for o in items)

    relations: Counter = Counter()
    reference_kinds: Counter = Counter()
    blockers: Counter = Counter()
    for outcome in items:
        for blocker in outcome.decision.blockers:
            blockers[blocker] += 1
        for relation in accepted_relations_for(outcome):
            relations[relation["relation_id"]] += 1
            if relation.get("reference_kind") is not None:
                reference_kinds[relation["reference_kind"]] += 1

    return {
        **base,
        "accepted_evidence_relations": dict(sorted(relations.items())),
        "reference_kinds_used": dict(sorted(reference_kinds.items())),
        "escalation_blockers": dict(sorted(blockers.items())),
    }


# --- Provider / model telemetry --------------------------------------------


def telemetry_from_payloads(payloads: Iterable[dict]) -> dict:
    """Provider and model facts, with requested and reported kept apart.

    Conflating the two hides gateway aliasing: a run that asked for
    ``claude-opus-5-thinking`` and was answered by ``claude-opus-5`` produced
    its numbers on a model nobody named. Both are reported, always, and the
    report says explicitly whether they agreed.

    Dict-based for the same reason as :func:`trajectory_metrics`: token counts
    and model identities in a superseded record format are still readable
    facts, and a baseline that cannot be replayed can still be described.
    The per-case aggregation mirrors
    :class:`finrecon.agent.trajectory.Trajectory`'s own properties exactly --
    ``provider:model`` keys, first-seen order, ``None``-skipping sums.
    """
    requested: Counter = Counter()
    reported: Counter = Counter()
    chains: Counter = Counter()
    fallback_reasons: Counter = Counter()
    provider_attempt_outcomes: Counter = Counter()
    provider_error_classes: Counter = Counter()

    cases = 0
    cases_with_fallback = 0
    model_steps = 0
    failed_attempts = 0
    total_tokens = 0
    cases_with_tokens = 0
    input_tokens = 0
    output_tokens = 0
    latencies: list[int] = []
    step_counts: list[int] = []

    for payload in payloads:
        cases += 1
        chain = tuple(payload.get("provider_chain") or ())
        chains[":: ".join(chain) if chain else "none"] += 1
        steps = payload.get("steps") or []
        step_counts.append(len(steps))
        model_steps += len(steps)

        seen_requested: list[str] = []
        seen_reported: list[str] = []
        case_fallback = False
        case_tokens: list[int] = []
        for step in steps:
            provider = step.get("provider", "")
            key = f"{provider}:{step.get('model', '')}"
            if key not in seen_requested:
                seen_requested.append(key)
            if step.get("reported_model") is not None:
                reported_key = f"{provider}:{step['reported_model']}"
                if reported_key not in seen_reported:
                    seen_reported.append(reported_key)
            if step.get("fallback_used"):
                case_fallback = True
            if step.get("fallback_reason") is not None:
                fallback_reasons[step["fallback_reason"]] += 1
            for attempt in step.get("attempts") or []:
                provider_attempt_outcomes[attempt.get("outcome", "unknown")] += 1
                if attempt.get("outcome") != "success":
                    failed_attempts += 1
                    provider_error_classes[attempt.get("error_class") or "unknown"] += 1
            usage = step.get("usage") or {}
            if usage.get("total_tokens") is not None:
                case_tokens.append(usage["total_tokens"])
            if usage.get("input_tokens") is not None:
                input_tokens += usage["input_tokens"]
            if usage.get("output_tokens") is not None:
                output_tokens += usage["output_tokens"]

        for key in seen_requested:
            requested[key] += 1
        for key in seen_reported:
            reported[key] += 1
        if case_fallback:
            cases_with_fallback += 1
        if case_tokens:
            total_tokens += sum(case_tokens)
            cases_with_tokens += 1
        if payload.get("total_latency_ms") is not None:
            latencies.append(payload["total_latency_ms"])

    items_count = cases
    cases = cases or 1
    return {
        "cases": items_count,
        "provider_chains": dict(sorted(chains.items())),
        "models_requested": dict(sorted(requested.items())),
        "models_reported": dict(sorted(reported.items())),
        # None, not False, when the provider reported nothing: "we asked and it
        # disagreed" and "it never said" are different facts, and collapsing
        # them into False invents a mismatch that was never observed.
        "requested_matches_reported": (
            None if not reported else set(requested) == set(reported)
        ),
        "fallback_used_cases": cases_with_fallback,
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "provider_attempt_outcomes": dict(sorted(provider_attempt_outcomes.items())),
        "provider_failed_attempts": failed_attempts,
        "provider_error_classes": dict(sorted(provider_error_classes.items())),
        "model_steps_total": model_steps,
        "model_steps_mean_per_case": round(model_steps / cases, 4),
        "model_steps_max": max(step_counts) if step_counts else 0,
        "tokens_total": total_tokens,
        "tokens_input_total": input_tokens,
        "tokens_output_total": output_tokens,
        "tokens_mean_per_case": round(total_tokens / cases, 2),
        "cases_reporting_tokens": cases_with_tokens,
        "latency_total_ms": sum(latencies),
        "latency_mean_ms_per_case": round(sum(latencies) / len(latencies), 2)
        if latencies
        else None,
        "cases_reporting_latency": len(latencies),
    }


def telemetry(outcomes: Iterable[CaseOutcome]) -> dict:
    """Provider/model telemetry for a replayed cohort. See the payload version."""
    return telemetry_from_payloads(
        o.trajectory.model_dump(mode="json") for o in outcomes
    )


def versions_from_payloads(payloads: Iterable[dict]) -> dict:
    """The contract the recorded artifacts were produced under."""
    fields = (
        "prompt_version",
        "tool_schema_version",
        "agent_loop_version",
        "cache_schema_version",
        "validator_version",
        "policy_version",
    )
    collected: dict[str, set[str]] = {name: set() for name in fields}
    for payload in payloads:
        for name in fields:
            value = payload.get(name)
            if value is not None:
                collected[name].add(value)
    return {name: sorted(values) for name, values in collected.items()}


def versions_of(outcomes: Iterable[CaseOutcome]) -> dict:
    """The contract every trajectory in the cohort was recorded under.

    A list per field, not a single value: a cohort assembled from several runs
    could legitimately be uniform, and if it is not, the report has to say so
    rather than pick one.
    """
    fields = (
        "prompt_version",
        "tool_schema_version",
        "agent_loop_version",
        "cache_schema_version",
        "validator_version",
        "policy_version",
    )
    collected: dict[str, set[str]] = {name: set() for name in fields}
    for outcome in outcomes:
        for name in fields:
            collected[name].add(getattr(outcome.trajectory, name))
    return {name: sorted(values) for name, values in collected.items()}


__all__ = [
    "CHECK_ACCEPTED_RELATIONS",
    "CHECK_EXACT_PAISE",
    "CHECK_FRAGMENT_IN_NARRATION",
    "CHECK_NO_FABRICATION",
    "CHECK_SNAPSHOT_INTEGRITY",
    "SOUNDNESS_CHECKS",
    "TOOL_VALIDATION_REASONS",
    "WRONG_NO_CORRECT_ANSWER",
    "WRONG_SETTLEMENT",
    "CaseVerdict",
    "SoundnessViolation",
    "accepted_relations_for",
    "agent_metrics",
    "aggregate_scores",
    "conjunction_metrics",
    "structural_metrics",
    "metrics_by_archetype",
    "metrics_by_candidate_count",
    "metrics_by_family",
    "metrics_by_required_composition",
    "metrics_by_tier",
    "soundness_violations",
    "telemetry",
    "telemetry_from_payloads",
    "trajectory_metrics",
    "verdict_for",
    "versions_from_payloads",
    "versions_of",
]
