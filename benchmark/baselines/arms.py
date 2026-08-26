"""The six deterministic arms, each producing decisions and nothing else.

Every arm returns :class:`ArmPrediction` objects. None of them consults
ground truth; scoring happens afterwards, in :mod:`benchmark.baselines.report`.

Arm B is the one to read carefully. It does not re-implement the shipped
decision layer -- it *drives* it, by synthesising the raw tool evidence a
maximally aggressive agent would have gathered and handing that to the real
:func:`finrecon.decide.validator.validate_case` and the real
:func:`finrecon.decide.policy.decide`. Its number is therefore the ceiling of
the architecture as it stands, not an estimate of one -- which is also why it
*moved* when ``validator.v2`` landed. Arm B1 exists to hold the old ceiling
still: it restates ``validator.v1``'s rule so the before-and-after of that
change stays measurable on one cohort.

Arms C1-C3 carry their own declared resolution rules, stated in each function.
Two of them are now partly redundant with the shipped gate and that is the
point: ``C1``'s lexical intersection is close to what ``validator.v2`` does,
so the remaining distance between ``B`` and ``C2`` is the distance still owed
to *structural* evidence -- break-up amounts and settlement dates -- which the
gate does not admit. They remain diagnostic solvers rather than shippable
ones.

What all five keep
------------------

No arm resolves a candidate that is not financially exact, none resolves a
candidate outside the immutable Stage-2 candidate set, and none resolves when
its rule leaves more than one survivor. Those are the properties that make a
coverage number comparable with the shipped system's; an arm that dropped
them would be measuring a different task.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from finrecon.agent.tools import TOOL_COMPARE_REFERENCE_FRAGMENT, TOOLS_BY_NAME, ToolContext
from finrecon.agent.trajectory import (
    TERMINATION_INVESTIGATION_COMPLETE,
    INVOCATION_SUCCEEDED,
    ToolInvocationRecord,
    Trajectory,
)
from finrecon.agent.version import (
    CACHE_SCHEMA_VERSION,
    POLICY_VERSION,
    TOOL_SCHEMA_VERSION,
    VALIDATOR_VERSION,
)
from finrecon.candidates.generator import BLOCKING_RULE_EXACT_TOTAL
from finrecon.candidates.snapshot import CaseSnapshot, SettlementFacts
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.decide.policy import applicable_min_pinned, decide
from finrecon.decide.validator import raw_tool_evidence, validate_case

from benchmark.baselines.features import (
    Feature,
    distinct_reach_sets,
    lexical_features,
    structural_features,
)

ARM_A = "A_rules_only"
ARM_B1 = "B1_validator_v1_semantics"
ARM_B = "B_shipped_gate_exhaustive"
ARM_C1 = "C1_lexical_composition"
ARM_C2 = "C2_lexical_and_structural_composition"
ARM_C3 = "C3_first_subset_that_isolates"

ARMS: tuple[str, ...] = (ARM_A, ARM_B1, ARM_B, ARM_C1, ARM_C2, ARM_C3)

MAX_SUBSET_SIZE = 4
"""How many feature reach sets arm C3 will intersect. One more than the highest
arity any v4 archetype claims, so an archetype solvable at a lower arity than
declared shows up rather than being missed."""


@dataclass(frozen=True)
class ArmPrediction:
    """One arm's decision on one case. Carries no notion of correctness."""

    case_id: str
    arm: str
    resolved: bool
    settlement_ids: tuple[str, ...]
    """Sorted, so it compares directly against a ground-truth relationship."""
    candidate_id: str | None
    reason: str
    """Why it stopped where it did. Declared vocabulary, not prose."""
    features_used: int
    minimal_arity: int | None
    """Smallest number of reach sets whose intersection is a singleton. Diagnostic."""


REASON_RESOLVED = "resolved"
REASON_NO_EVIDENCE = "no_admissible_evidence"
REASON_NO_UNIQUE_SURVIVOR = "no_unique_survivor"
REASON_CONTRADICTORY_EVIDENCE = "contradictory_evidence"
REASON_AMBIGUOUS_ISOLATION = "several_candidates_isolable"
REASON_NOT_FINANCIALLY_EXACT = "survivor_not_financially_exact"
REASON_STAGE2_UNRESOLVED = "stage2_left_unresolved"


# --- shared safety predicate ----------------------------------------------


def _facts_by_id(snapshot: CaseSnapshot) -> dict[str, SettlementFacts]:
    return {facts.settlement_id: facts for facts in snapshot.base_evidence.settlement_facts}


def financially_exact_candidates(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> frozenset[str]:
    """Candidates whose money adds up, by the same clauses the validator applies.

    Kept as a separate function rather than folded into each arm so that every
    arm is held to one standard, and so a test can assert it agrees with
    :attr:`finrecon.decide.validator.ValidatorResult.financially_exact_candidate_ids`
    case for case.
    """
    facts_by_id = _facts_by_id(snapshot)
    exact: set[str] = set()
    for candidate in snapshot.candidates:
        settlements = [
            facts_by_id[sid] for sid in candidate.settlement_ids if sid in facts_by_id
        ]
        if len(settlements) != len(candidate.settlement_ids):
            continue
        if any(facts.derivation.unexplained_delta_paise != 0 for facts in settlements):
            continue
        if abs(candidate.unexplained_delta_paise) > policy.evidence.max_unexplained_delta_paise:
            continue
        if (
            policy.evidence.require_exact_total_blocking_rule
            and candidate.blocking_rule != BLOCKING_RULE_EXACT_TOTAL
        ):
            continue
        sound = True
        for facts in settlements:
            for line in facts.derivation.lines:
                if line.line_type == "payment" and (
                    line.reference_id is None or line.reference_status != "captured"
                ):
                    sound = False
                elif line.line_type == "refund" and (
                    line.reference_id is None or line.reference_status != "processed"
                ):
                    sound = False
        if sound:
            exact.add(candidate.candidate_id)
    return frozenset(exact)


def _settlement_ids_of(snapshot: CaseSnapshot, candidate_id: str) -> tuple[str, ...]:
    candidate = next(c for c in snapshot.candidates if c.candidate_id == candidate_id)
    return tuple(sorted(candidate.settlement_ids))


def _minimal_arity(reach_sets: tuple[frozenset[str], ...]) -> int | None:
    for arity in range(1, MAX_SUBSET_SIZE + 1):
        for combo in combinations(reach_sets, arity):
            intersection = combo[0]
            for member in combo[1:]:
                intersection = intersection & member
            if len(intersection) == 1:
                return arity
    return None


# --- arm A -----------------------------------------------------------------


def arm_a_rules_only(case_ids: tuple[str, ...], resolved_by_stage2: dict[str, tuple[str, ...]]) -> tuple[ArmPrediction, ...]:
    """The unmodified Stage-2 core: whatever it resolved, and nothing else.

    Takes the pipeline's own output rather than recomputing it, because
    recomputing it would be a second implementation of the deterministic core
    and the whole point of arm A is that it is the first one.
    """
    return tuple(
        ArmPrediction(
            case_id=case_id,
            arm=ARM_A,
            resolved=case_id in resolved_by_stage2,
            settlement_ids=tuple(sorted(resolved_by_stage2.get(case_id, ()))),
            candidate_id=None,
            reason=REASON_RESOLVED if case_id in resolved_by_stage2 else REASON_STAGE2_UNRESOLVED,
            features_used=0,
            minimal_arity=None,
        )
        for case_id in case_ids
    )


# --- arm B1: the pre-v2 rule, kept as the before-column --------------------


def arm_b1_validator_v1_semantics(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> ArmPrediction:
    """``validator.v1``'s rule, restated, over the exhaustive fragment set.

    Restated rather than driven, because the shipped validator no longer
    implements it -- and a before-column that silently tracked the after-column
    would measure nothing. Where the two rules still overlap they must agree: on
    a case whose evidence contains a discriminating fragment and nothing
    contradicting it, v1 and v2 reach the same candidate, and
    ``tests/test_validator_conjunction.py`` asserts that.
    """
    floor = applicable_min_pinned(snapshot.base_evidence.bank_record.amount_paise, policy)
    features = lexical_features(snapshot, floor)
    exact = financially_exact_candidates(snapshot, policy)

    identified = {
        next(iter(feature.reach)) for feature in features if len(feature.reach) == 1
    }
    minimal = _minimal_arity(distinct_reach_sets(features))

    if not features:
        reason = REASON_NO_EVIDENCE
    elif not identified:
        reason = REASON_NO_UNIQUE_SURVIVOR
    elif len(identified) > 1:
        reason = REASON_CONTRADICTORY_EVIDENCE
    else:
        candidate_id = next(iter(identified))
        if candidate_id not in exact:
            reason = REASON_NOT_FINANCIALLY_EXACT
        else:
            return ArmPrediction(
                case_id=snapshot.case_id,
                arm=ARM_B1,
                resolved=True,
                settlement_ids=_settlement_ids_of(snapshot, candidate_id),
                candidate_id=candidate_id,
                reason=REASON_RESOLVED,
                features_used=len(features),
                minimal_arity=minimal,
            )
    return ArmPrediction(
        case_id=snapshot.case_id,
        arm=ARM_B1,
        resolved=False,
        settlement_ids=(),
        candidate_id=None,
        reason=reason,
        features_used=len(features),
        minimal_arity=minimal,
    )


# --- arm B -----------------------------------------------------------------


def exhaustive_fragment_trajectory(
    snapshot: CaseSnapshot,
    features: tuple[Feature, ...],
    policy: Stage3Policy = DEFAULT_POLICY,
) -> Trajectory:
    """A trajectory holding the real tool's output for every reaching fragment.

    The tool handler is genuinely invoked, so what the validator reads here is
    byte-for-byte the payload a live investigation would have produced -- not a
    stand-in shaped like one. The version fields say plainly that no prompt and
    no bounded loop were involved; a baseline that borrowed the agent's
    identifiers would make a replayed report attribute this run to a model.
    """
    context = ToolContext(snapshot=snapshot)
    definition = TOOLS_BY_NAME[TOOL_COMPARE_REFERENCE_FRAGMENT]
    invocations: list[ToolInvocationRecord] = []

    for index, feature in enumerate(features):
        arguments = {"fragment": feature.token}
        output = definition.handler(
            context, definition.input_model.model_validate(arguments)
        )
        invocations.append(
            ToolInvocationRecord(
                step_index=1,
                call_index=index,
                tool_name=TOOL_COMPARE_REFERENCE_FRAGMENT,
                raw_arguments="",
                status=INVOCATION_SUCCEEDED,
                validated_arguments=arguments,
                validation_error_reason=None,
                validation_error_detail=None,
                output=output.model_dump(mode="json"),
            )
        )

    return Trajectory(
        case_id=snapshot.case_id,
        snapshot_hash=snapshot.content_hash,
        batch_id=snapshot.batch_id,
        prompt_version="baseline:no-prompt",
        tool_schema_version=TOOL_SCHEMA_VERSION,
        agent_loop_version="baseline:exhaustive-single-fragment",
        cache_schema_version=CACHE_SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
        policy_version=POLICY_VERSION,
        policy_declaration=policy.describe(),
        max_steps=1,
        max_tool_calls_per_step=len(invocations),
        provider_chain=(),
        steps=(),
        tool_invocations=tuple(invocations),
        termination_reason=TERMINATION_INVESTIGATION_COMPLETE,
        termination_detail="exhaustive deterministic enumeration; no model was consulted",
    )


def arm_b_single_fragment(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> ArmPrediction:
    """Every admissible fragment, through the real validator and the real gate."""
    floor = applicable_min_pinned(
        snapshot.base_evidence.bank_record.amount_paise, policy
    )
    features = lexical_features(snapshot, floor)
    trajectory = exhaustive_fragment_trajectory(snapshot, features, policy)
    result = validate_case(
        snapshot=snapshot,
        evidence=raw_tool_evidence(trajectory),
        policy=policy,
        min_pinned_reference_characters=floor,
    )
    decision = decide(
        snapshot=snapshot, trajectory=trajectory, validator_result=result, policy=policy
    )
    if decision.resolved:
        reason = REASON_RESOLVED
    elif not features:
        reason = REASON_NO_EVIDENCE
    elif len(result.reference_identified_candidate_ids) > 1:
        reason = REASON_CONTRADICTORY_EVIDENCE
    else:
        reason = REASON_NO_UNIQUE_SURVIVOR
    return ArmPrediction(
        case_id=snapshot.case_id,
        arm=ARM_B,
        resolved=decision.resolved,
        settlement_ids=tuple(sorted(decision.resolved_settlement_ids)),
        candidate_id=decision.resolved_candidate_id,
        reason=reason,
        features_used=len(features),
        minimal_arity=_minimal_arity(distinct_reach_sets(features)),
    )


# --- arms C ----------------------------------------------------------------


def _consistent_with_everything(
    snapshot: CaseSnapshot,
    features: tuple[Feature, ...],
    arm: str,
    policy: Stage3Policy,
) -> ArmPrediction:
    """Resolve iff exactly one candidate is in *every* feature's reach set.

    The declared rule for arms C1 and C2. Its conservatism is the property
    worth naming: a feature that contradicts the others empties the
    intersection and the case escalates, so evidence pointing two ways is
    never resolved by preferring the stronger-looking half. That is the same
    stance DESIGN.md 4.3 takes when it makes "more than one candidate
    satisfies the predicates" a hard blocker.
    """
    reach_sets = distinct_reach_sets(features)
    exact = financially_exact_candidates(snapshot, policy)

    if not features:
        return ArmPrediction(
            case_id=snapshot.case_id,
            arm=arm,
            resolved=False,
            settlement_ids=(),
            candidate_id=None,
            reason=REASON_NO_EVIDENCE,
            features_used=0,
            minimal_arity=None,
        )

    survivors: frozenset[str] = reach_sets[0]
    for reach in reach_sets[1:]:
        survivors = survivors & reach

    minimal = _minimal_arity(reach_sets)
    if len(survivors) != 1:
        return ArmPrediction(
            case_id=snapshot.case_id,
            arm=arm,
            resolved=False,
            settlement_ids=(),
            candidate_id=None,
            reason=(
                REASON_CONTRADICTORY_EVIDENCE if not survivors else REASON_NO_UNIQUE_SURVIVOR
            ),
            features_used=len(features),
            minimal_arity=minimal,
        )

    candidate_id = next(iter(survivors))
    if candidate_id not in exact:
        return ArmPrediction(
            case_id=snapshot.case_id,
            arm=arm,
            resolved=False,
            settlement_ids=(),
            candidate_id=None,
            reason=REASON_NOT_FINANCIALLY_EXACT,
            features_used=len(features),
            minimal_arity=minimal,
        )
    return ArmPrediction(
        case_id=snapshot.case_id,
        arm=arm,
        resolved=True,
        settlement_ids=_settlement_ids_of(snapshot, candidate_id),
        candidate_id=candidate_id,
        reason=REASON_RESOLVED,
        features_used=len(features),
        minimal_arity=minimal,
    )


def arm_c1_lexical_composition(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> ArmPrediction:
    floor = applicable_min_pinned(snapshot.base_evidence.bank_record.amount_paise, policy)
    return _consistent_with_everything(
        snapshot, lexical_features(snapshot, floor), ARM_C1, policy
    )


def arm_c2_lexical_and_structural(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> ArmPrediction:
    floor = applicable_min_pinned(snapshot.base_evidence.bank_record.amount_paise, policy)
    features = lexical_features(snapshot, floor) + structural_features(snapshot)
    return _consistent_with_everything(snapshot, features, ARM_C2, policy)


def arm_c3_first_subset_that_isolates(
    snapshot: CaseSnapshot, policy: Stage3Policy = DEFAULT_POLICY
) -> ArmPrediction:
    """Resolve if *any* subset of features isolates a candidate.

    The rule most people write first, and the reason it is measured separately:
    it treats "some combination points here" as sufficient, which is
    first-positive-match wearing a conjunction's clothes. Where two subsets
    isolate different candidates it declines, but a lone stale reference that
    isolates one candidate is enough for it, whatever else the narration says.
    """
    floor = applicable_min_pinned(snapshot.base_evidence.bank_record.amount_paise, policy)
    features = lexical_features(snapshot, floor) + structural_features(snapshot)
    reach_sets = distinct_reach_sets(features)
    exact = financially_exact_candidates(snapshot, policy)

    isolated: set[str] = set()
    for arity in range(1, MAX_SUBSET_SIZE + 1):
        for combo in combinations(reach_sets, arity):
            intersection = combo[0]
            for member in combo[1:]:
                intersection = intersection & member
            if len(intersection) == 1:
                isolated.add(next(iter(intersection)))

    minimal = _minimal_arity(reach_sets)
    if not features:
        reason = REASON_NO_EVIDENCE
    elif not isolated:
        reason = REASON_NO_UNIQUE_SURVIVOR
    elif len(isolated) > 1:
        reason = REASON_AMBIGUOUS_ISOLATION
    else:
        candidate_id = next(iter(isolated))
        if candidate_id not in exact:
            reason = REASON_NOT_FINANCIALLY_EXACT
        else:
            return ArmPrediction(
                case_id=snapshot.case_id,
                arm=ARM_C3,
                resolved=True,
                settlement_ids=_settlement_ids_of(snapshot, candidate_id),
                candidate_id=candidate_id,
                reason=REASON_RESOLVED,
                features_used=len(features),
                minimal_arity=minimal,
            )
    return ArmPrediction(
        case_id=snapshot.case_id,
        arm=ARM_C3,
        resolved=False,
        settlement_ids=(),
        candidate_id=None,
        reason=reason,
        features_used=len(features),
        minimal_arity=minimal,
    )


SNAPSHOT_ARMS = {
    ARM_B1: arm_b1_validator_v1_semantics,
    ARM_B: arm_b_single_fragment,
    ARM_C1: arm_c1_lexical_composition,
    ARM_C2: arm_c2_lexical_and_structural,
    ARM_C3: arm_c3_first_subset_that_isolates,
}


__all__ = [
    "ARMS",
    "ARM_A",
    "ARM_B",
    "ARM_B1",
    "ARM_C1",
    "ARM_C2",
    "ARM_C3",
    "MAX_SUBSET_SIZE",
    "REASON_AMBIGUOUS_ISOLATION",
    "REASON_CONTRADICTORY_EVIDENCE",
    "REASON_NOT_FINANCIALLY_EXACT",
    "REASON_NO_EVIDENCE",
    "REASON_NO_UNIQUE_SURVIVOR",
    "REASON_RESOLVED",
    "REASON_STAGE2_UNRESOLVED",
    "SNAPSHOT_ARMS",
    "ArmPrediction",
    "arm_a_rules_only",
    "arm_b1_validator_v1_semantics",
    "arm_b_single_fragment",
    "arm_c1_lexical_composition",
    "arm_c2_lexical_and_structural",
    "arm_c3_first_subset_that_isolates",
    "exhaustive_fragment_trajectory",
    "financially_exact_candidates",
]
