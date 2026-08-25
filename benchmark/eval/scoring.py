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

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "tier": self.tier,
            "archetype": self.archetype,
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
    """The discriminating evidence that reached the *resolved* candidate.

    Only discriminating findings are returned, and only those whose match is
    the candidate the gate actually chose: that is the evidence which allowed
    the resolution, as distinct from everything the agent happened to look at.
    """
    relations: list[dict] = []
    chosen = outcome.decision.resolved_candidate_id
    if chosen is None:
        return ()
    for finding in outcome.validator_result.findings:
        if not finding.is_discriminating:
            continue
        for match in finding.matches:
            if match.candidate_id != chosen:
                continue
            relations.append(
                {
                    "fragment": finding.fragment,
                    "relation_id": match.relation_id,
                    "reference_kind": match.reference_kind,
                    "reference_value": match.reference_value,
                    "pinned_reference_characters": match.pinned_reference_characters,
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
        if not validator.discriminating_fragments:
            violations.append(
                SoundnessViolation(
                    outcome.case_id,
                    CHECK_NO_FABRICATION,
                    "resolved with no discriminating fragment",
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
    "soundness_violations",
    "telemetry",
    "telemetry_from_payloads",
    "trajectory_metrics",
    "verdict_for",
    "versions_from_payloads",
    "versions_of",
]
