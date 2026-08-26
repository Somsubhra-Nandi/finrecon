"""The deterministic policy gate -- the last thing between evidence and money.

DESIGN.md 4.3, implemented literally:

.. code-block:: text

    AUTO-RESOLVE only if:
        exactly one surviving candidate
    AND amount reconciles exactly under known fee/tax/refund rules
    AND a strong reference link exists
    AND no hard blocker
    AND value policy permits

Everything else escalates, and escalation is a first-class *pass*
(DESIGN.md 4.4), not a failure to try hard enough.

Two properties are worth stating because they are what the gate is for.

**Model confidence is not an input.** There is no confidence field in this
module, no weight, no score, no threshold on one. If a model announced 99%
certainty about candidate B while the raw evidence reached candidate A, this
gate would resolve to A -- and if the evidence reached both, it would resolve
to neither. The agent's text is not in the call signature at all.

**Blockers are absolute.** Nothing overrides them: not strong evidence, not
a small amount, not the fact that the answer is probably obvious. A blocker
list is returned in full rather than short-circuiting at the first one, so an
exception queue can show an analyst every reason at once instead of
one-at-a-time as each is cleared.

The value ladder (DESIGN.md 4.5) has two rungs. Above the scrutiny
threshold the evidence floor doubles; above the ceiling nothing
auto-resolves at all. Both are declared in
:mod:`finrecon.decide.config` with their reasoning, and both are oriented so
that tightening them can only ever convert a match into an escalation.
"""

from __future__ import annotations

from typing import Literal

from finrecon.agent.trajectory import (
    TERMINATION_DETERMINISTIC_POLICY_RESOLVED,
    TERMINATION_INVESTIGATION_COMPLETE,
    TERMINATION_PROVIDER_CONFIGURATION_FAILURE,
    TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
    TERMINATION_STEP_BUDGET_EXHAUSTED,
    TERMINATION_TOOL_VALIDATION_FAILED,
    Trajectory,
)
from finrecon.agent.version import POLICY_VERSION
from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.decide.validator import (
    RawToolEvidence,
    ValidatorResult,
    raw_tool_evidence,
    validate_case,
)
from finrecon.normalize.provenance import FrozenModel

# --- Hard blockers --------------------------------------------------------

BLOCKER_SNAPSHOT_INTEGRITY = "snapshot_integrity_failure"
BLOCKER_TOOL_VALIDATION = "tool_validation_failure"
BLOCKER_STEP_BUDGET_EXHAUSTED = "step_budget_exhausted"
BLOCKER_PROVIDER_FAILURE = "provider_failure"
BLOCKER_INVESTIGATION_INCOMPLETE = "investigation_incomplete"
BLOCKER_NO_REFERENCE_LINK = "no_reference_link"
BLOCKER_AMBIGUOUS_REFERENCE_LINK = "ambiguous_reference_link"
BLOCKER_NO_SURVIVING_CANDIDATE = "no_surviving_candidate"
BLOCKER_MULTIPLE_SURVIVING_CANDIDATES = "multiple_surviving_candidates"
BLOCKER_UNEXPLAINED_DELTA = "unexplained_delta"
BLOCKER_FINANCIAL_MISMATCH = "financial_mismatch"
BLOCKER_COUNTERPARTY_ALREADY_RESOLVED = "counterparty_already_resolved"
BLOCKER_VALUE_ABOVE_CEILING = "value_above_auto_resolution_ceiling"

HARD_BLOCKERS: tuple[str, ...] = (
    BLOCKER_SNAPSHOT_INTEGRITY,
    BLOCKER_TOOL_VALIDATION,
    BLOCKER_STEP_BUDGET_EXHAUSTED,
    BLOCKER_PROVIDER_FAILURE,
    BLOCKER_INVESTIGATION_INCOMPLETE,
    BLOCKER_NO_REFERENCE_LINK,
    BLOCKER_AMBIGUOUS_REFERENCE_LINK,
    BLOCKER_NO_SURVIVING_CANDIDATE,
    BLOCKER_MULTIPLE_SURVIVING_CANDIDATES,
    BLOCKER_UNEXPLAINED_DELTA,
    BLOCKER_FINANCIAL_MISMATCH,
    BLOCKER_COUNTERPARTY_ALREADY_RESOLVED,
    BLOCKER_VALUE_ABOVE_CEILING,
)

RULE_RESOLVE_RECOVERED_REFERENCE = "stage3.resolve.recovered_reference_link"
RULE_ESCALATE = "stage3.escalate"

Outcome = Literal["RESOLVE", "ESCALATE"]


class PolicyDecision(FrozenModel):
    """One Stage-3 decision, with the full reasoning attached as data."""

    policy_version: str
    case_id: str
    bank_record_id: str
    outcome: Outcome
    rule_id: str
    resolved_candidate_id: str | None
    resolved_settlement_ids: tuple[str, ...]
    relationship: Literal["one_to_one", "many_to_one"] | None
    blockers: tuple[str, ...]
    """Every blocker that fired, not just the first. Empty on a resolution."""
    value_paise: int
    min_pinned_reference_characters_applied: int
    value_ladder_rung: str
    """``ordinary`` / ``elevated_scrutiny`` / ``above_ceiling``."""
    policy_declaration: dict
    """The declared thresholds this decision was taken under."""

    @property
    def resolved(self) -> bool:
        return self.outcome == "RESOLVE"


def value_ladder_rung(value_paise: int, policy: Stage3Policy) -> str:
    if policy.value.exceeds_ceiling(value_paise):
        return "above_ceiling"
    if value_paise > policy.value.elevated_scrutiny_threshold_paise:
        return "elevated_scrutiny"
    return "ordinary"


def applicable_min_pinned(value_paise: int, policy: Stage3Policy) -> int:
    """The evidence floor for this amount. Rises with value, never falls."""
    return policy.value.min_pinned_for(
        value_paise, policy.evidence.min_pinned_reference_characters
    )


def _investigation_blockers(trajectory: Trajectory) -> list[str]:
    blockers: list[str] = []
    if trajectory.had_validation_failure:
        blockers.append(BLOCKER_TOOL_VALIDATION)
    reason = trajectory.termination_reason
    if reason == TERMINATION_STEP_BUDGET_EXHAUSTED:
        blockers.append(BLOCKER_STEP_BUDGET_EXHAUSTED)
    elif reason == TERMINATION_TOOL_VALIDATION_FAILED:
        if BLOCKER_TOOL_VALIDATION not in blockers:
            blockers.append(BLOCKER_TOOL_VALIDATION)
    elif reason in (
        TERMINATION_PROVIDER_INFRASTRUCTURE_FAILURE,
        TERMINATION_PROVIDER_CONFIGURATION_FAILURE,
    ):
        blockers.append(BLOCKER_PROVIDER_FAILURE)
    elif reason not in (
        TERMINATION_INVESTIGATION_COMPLETE,
        TERMINATION_DETERMINISTIC_POLICY_RESOLVED,
    ):
        blockers.append(BLOCKER_INVESTIGATION_INCOMPLETE)
    return blockers


def decide(
    *,
    snapshot: CaseSnapshot,
    trajectory: Trajectory,
    validator_result: ValidatorResult,
    claimed_settlement_ids: frozenset[str] = frozenset(),
    policy: Stage3Policy = DEFAULT_POLICY,
) -> PolicyDecision:
    """Apply the deterministic gate. Takes no model prose and no confidence.

    ``claimed_settlement_ids`` are counterparties already linked in this run
    -- by Stage 2 or by an earlier Stage-3 resolution. Landing on one is
    DESIGN.md 4.3's "counterparty already resolved in this run" blocker, and
    it is passed in rather than looked up so the gate stays a pure function
    of its arguments.
    """
    value_paise = snapshot.base_evidence.bank_record.amount_paise
    rung = value_ladder_rung(value_paise, policy)
    floor = applicable_min_pinned(value_paise, policy)

    blockers = _investigation_blockers(trajectory)

    if not validator_result.snapshot_integrity_ok:
        blockers.append(BLOCKER_SNAPSHOT_INTEGRITY)

    # Since validator.v2 this is the candidate set the deterministic reference
    # closure identified -- one candidate when the closure isolates one, the
    # surviving set when several survive, and the *contradicting* set when none
    # does. The gate needs only "is this exactly one", so a contradiction
    # arrives here as a set of size two or more and fires
    # ``ambiguous_reference_link``: evidence pointing at more than one
    # candidate, and therefore at none. That is why v2 needed no new blocker
    # and this module is still ``policy.v1``.
    matched = validator_result.reference_identified_candidate_ids
    survivors = validator_result.surviving_candidate_ids

    # Each blocker names one distinct failure, so an exception queue can
    # show an analyst *why* rather than a pile of overlapping symptoms.
    if len(matched) == 0:
        blockers.append(BLOCKER_NO_REFERENCE_LINK)
    elif len(matched) > 1:
        blockers.append(BLOCKER_AMBIGUOUS_REFERENCE_LINK)
    elif not survivors:
        # A reference reached exactly one candidate but the money does not
        # add up. Name the arithmetic failure, not merely the absence.
        assessment = next(
            (a for a in validator_result.financial_assessments if a.candidate_id == matched[0]),
            None,
        )
        if assessment is not None and assessment.group_unexplained_delta_paise != 0:
            blockers.append(BLOCKER_UNEXPLAINED_DELTA)
        else:
            blockers.append(BLOCKER_FINANCIAL_MISMATCH)

    if len(survivors) > 1:
        blockers.append(BLOCKER_MULTIPLE_SURVIVING_CANDIDATES)
    elif not survivors and not blockers:
        # Defensive catch-all: nothing survived and nothing above explains
        # it. Reaching here would mean a predicate changed without its
        # blocker; escalating unexplained beats resolving unexplained.
        blockers.append(BLOCKER_NO_SURVIVING_CANDIDATE)

    if policy.value.exceeds_ceiling(value_paise):
        blockers.append(BLOCKER_VALUE_ABOVE_CEILING)

    resolved_candidate_id: str | None = None
    settlement_ids: tuple[str, ...] = ()
    if len(survivors) == 1:
        resolved_candidate_id = survivors[0]
        candidate = next(
            c for c in snapshot.candidates if c.candidate_id == resolved_candidate_id
        )
        settlement_ids = tuple(sorted(candidate.settlement_ids))
        if any(sid in claimed_settlement_ids for sid in settlement_ids):
            blockers.append(BLOCKER_COUNTERPARTY_ALREADY_RESOLVED)

    # Deterministic, de-duplicated blocker order: the declared order, always.
    ordered = tuple(b for b in HARD_BLOCKERS if b in set(blockers))

    if not ordered and resolved_candidate_id is not None:
        return PolicyDecision(
            policy_version=POLICY_VERSION,
            case_id=snapshot.case_id,
            bank_record_id=snapshot.bank_record_id,
            outcome="RESOLVE",
            rule_id=RULE_RESOLVE_RECOVERED_REFERENCE,
            resolved_candidate_id=resolved_candidate_id,
            resolved_settlement_ids=settlement_ids,
            relationship="one_to_one" if len(settlement_ids) == 1 else "many_to_one",
            blockers=(),
            value_paise=value_paise,
            min_pinned_reference_characters_applied=floor,
            value_ladder_rung=rung,
            policy_declaration=policy.describe(),
        )

    return PolicyDecision(
        policy_version=POLICY_VERSION,
        case_id=snapshot.case_id,
        bank_record_id=snapshot.bank_record_id,
        outcome="ESCALATE",
        rule_id=RULE_ESCALATE,
        resolved_candidate_id=None,
        resolved_settlement_ids=(),
        relationship=None,
        blockers=ordered,
        value_paise=value_paise,
        min_pinned_reference_characters_applied=floor,
        value_ladder_rung=rung,
        policy_declaration=policy.describe(),
    )


def adjudicate(
    *,
    snapshot: CaseSnapshot,
    trajectory: Trajectory,
    claimed_settlement_ids: frozenset[str] = frozenset(),
    policy: Stage3Policy = DEFAULT_POLICY,
) -> tuple[ValidatorResult, PolicyDecision]:
    """Validate then gate, with the value-aware evidence floor wired through.

    The one place the value ladder touches the validator: the floor is
    computed from the amount here and handed down, so the validator stays a
    pure evidence engine that knows nothing about money thresholds and the
    gate stays the only module that does.
    """
    value_paise = snapshot.base_evidence.bank_record.amount_paise
    evidence: tuple[RawToolEvidence, ...] = raw_tool_evidence(trajectory)
    result = validate_case(
        snapshot=snapshot,
        evidence=evidence,
        policy=policy,
        min_pinned_reference_characters=applicable_min_pinned(value_paise, policy),
    )
    decision = decide(
        snapshot=snapshot,
        trajectory=trajectory,
        validator_result=result,
        claimed_settlement_ids=claimed_settlement_ids,
        policy=policy,
    )
    return result, decision


__all__ = [
    "BLOCKER_AMBIGUOUS_REFERENCE_LINK",
    "BLOCKER_COUNTERPARTY_ALREADY_RESOLVED",
    "BLOCKER_FINANCIAL_MISMATCH",
    "BLOCKER_INVESTIGATION_INCOMPLETE",
    "BLOCKER_MULTIPLE_SURVIVING_CANDIDATES",
    "BLOCKER_NO_REFERENCE_LINK",
    "BLOCKER_NO_SURVIVING_CANDIDATE",
    "BLOCKER_PROVIDER_FAILURE",
    "BLOCKER_SNAPSHOT_INTEGRITY",
    "BLOCKER_STEP_BUDGET_EXHAUSTED",
    "BLOCKER_TOOL_VALIDATION",
    "BLOCKER_UNEXPLAINED_DELTA",
    "BLOCKER_VALUE_ABOVE_CEILING",
    "HARD_BLOCKERS",
    "RULE_ESCALATE",
    "RULE_RESOLVE_RECOVERED_REFERENCE",
    "Outcome",
    "PolicyDecision",
    "adjudicate",
    "applicable_min_pinned",
    "decide",
    "value_ladder_rung",
]
