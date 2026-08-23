"""The deterministic validator -- where evidence becomes a finding, or does not.

Three inputs, and the separation between them is the whole design
(DESIGN.md 4.1, 4.3):

1. **The immutable Stage-2 case snapshot.** Complete candidate set plus base
   facts, built before the agent existed and delivered here directly. The
   validator's notion of "all the candidates" comes from this and from
   nothing else.
2. **The declared policy.** Thresholds, accepted relations, the zero-paise
   rule.
3. **Raw tool outputs.** Only the payloads tools produced, extracted by
   :func:`raw_tool_evidence`, which reads tool-result records and is
   structurally incapable of returning a model's prose.

What is *not* an input, and why it matters
------------------------------------------

The agent's text never reaches this module. Not its summary, not its
reasoning, not its self-reported confidence, and above all not its opinion
about which candidate is right. An agent with tool access and an ambiguous
case will assemble a coherent story for the wrong candidate, because
constructing coherent stories is what it is optimized for. So the decision
layer reads the primary evidence instead.

And an agent that cannot lie about evidence can still *select* it -- the
fishing-by-omission channel. That is closed here, mechanically: the agent
chooses which **fragment** to test, and the validator then tests that
fragment against **every candidate in the snapshot**, including the ones the
agent never looked at. A model can no more hide a contradicting candidate
than it can invent a supporting one.

The predicate, in full
----------------------

A candidate survives when both hold:

*Reference link.* Some admissible fragment stands in a declared mechanical
relation to one of that candidate's references, pinning at least the
required number of characters. A fragment is admissible only if the
validator finds it literally present in the snapshot's narration -- checked
here, not taken from the tool's own boolean.

*Financial exactness.* The candidate's group total equals the bank credit to
the paise, every settlement's break-up accounts for its own amount to the
paise, every referencing line names a record in a terminal successful state,
and the candidate came from exact-total blocking.

Uniqueness, and what "discriminating" means
-------------------------------------------

Each fragment is evaluated **independently** against the complete candidate
set, and a fragment counts only when it *separates* them -- when exactly one
candidate is reachable from it. A fragment that reaches two candidates, or
none, is not probative: it carries no information about which of them is
right, and it is recorded and then set aside.

That distinction matters more than it looks. Canonical settlement IDs share
a prefix (``setl_dev_000023``, ``setl_dev_000024``), so a lazy fragment like
``"SETL"`` stands in a declared prefix relation to *every* candidate at once.
Treating that as contradicting a fragment that genuinely separates them
would let the noisiest probe in a trajectory veto the most informative one --
a rule under which a model is punished for looking around.

What is *not* set aside is disagreement. If two discriminating fragments
point at different candidates, that is a contradiction in the evidence and
the case escalates. There is no majority vote, no tie-break and no
preference for the stronger relation: DESIGN.md 4.3 makes "more than one
candidate satisfies the predicates" a hard blocker, and two fragments
disagreeing is that same state arrived at from a different direction.
"""

from __future__ import annotations

from typing import Any

from finrecon.agent.schemas import CompareReferenceFragmentOutput
from finrecon.agent.tools import TOOL_COMPARE_REFERENCE_FRAGMENT
from finrecon.agent.trajectory import Trajectory
from finrecon.agent.version import VALIDATOR_VERSION
from finrecon.candidates.generator import BLOCKING_RULE_EXACT_TOTAL
from finrecon.candidates.snapshot import CandidateRecord, CaseSnapshot, SettlementFacts
from finrecon.decide.config import DEFAULT_POLICY, EvidencePolicy, Stage3Policy
from finrecon.evidence.reference import (
    REFERENCE_KINDS,
    ReferenceComparison,
    compare,
    strongest_admissible_relation,
)
from finrecon.normalize.provenance import FrozenModel

SUCCESSFUL_PAYMENT_STATUS = "captured"
SUCCESSFUL_REFUND_STATUS = "processed"

FRAGMENT_INADMISSIBLE_EMPTY = "empty_fragment"
FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION = "fragment_not_present_in_narration"


class RawToolEvidence(FrozenModel):
    """One tool result, carried to the validator with nothing attached to it.

    Deliberately a three-field record. There is no room in this shape for a
    model's commentary, so "the validator ignores agent prose" is a property
    of the type rather than a discipline someone has to maintain.
    """

    tool_name: str
    arguments: dict
    output: dict


def raw_tool_evidence(trajectory: Trajectory) -> tuple[RawToolEvidence, ...]:
    """Extract the raw tool outputs from a trajectory. Reads no model text.

    Only invocations that passed validation *and* produced output are
    included -- a refused call has no evidence to contribute, and its
    refusal is handled by the policy gate as a blocker rather than smuggled
    in here as a fact.
    """
    return tuple(
        RawToolEvidence(
            tool_name=invocation.tool_name,
            arguments=dict(invocation.validated_arguments or {}),
            output=dict(invocation.output or {}),
        )
        for invocation in trajectory.tool_invocations
        if invocation.succeeded
    )


class CandidateReferenceMatch(FrozenModel):
    """One candidate a fragment reaches, and the strongest way it reaches it."""

    candidate_id: str
    settlement_id: str
    reference_kind: str
    reference_value: str
    relation_id: str
    pinned_reference_characters: int


class ReferenceFinding(FrozenModel):
    """What one admissible fragment proves, evaluated over the complete set."""

    fragment: str
    matches: tuple[CandidateReferenceMatch, ...]
    matched_candidate_ids: tuple[str, ...]
    candidates_evaluated: int
    """How many candidates this fragment was tested against. Always all of them."""
    is_discriminating: bool
    """True when exactly one candidate is reachable from this fragment.

    A fragment reaching zero or several candidates is recorded but carries
    no weight -- it says nothing about which candidate is right.
    """


class InadmissibleFragment(FrozenModel):
    fragment: str
    reason: str


class CandidateFinancialAssessment(FrozenModel):
    """Exact-paise accounting for one candidate, recomputed from snapshot facts."""

    candidate_id: str
    group_unexplained_delta_paise: int
    every_breakup_is_exact: bool
    breakup_references_are_sound: bool
    blocking_rule: str
    blocking_rule_is_exact_total: bool
    is_financially_exact: bool


class ValidatorResult(FrozenModel):
    """Everything the validator established, including what it refused to."""

    validator_version: str
    case_id: str
    snapshot_hash: str
    snapshot_integrity_ok: bool
    complete_candidate_ids: tuple[str, ...]
    """Every candidate in the immutable snapshot. Never a subset the agent chose."""
    narration_length: int
    fragments_tested_by_agent: tuple[str, ...]
    admissible_fragments: tuple[str, ...]
    inadmissible_fragments: tuple[InadmissibleFragment, ...]
    findings: tuple[ReferenceFinding, ...]
    reference_matched_candidate_ids: tuple[str, ...]
    """Union over *all* fragments, discriminating or not. Audit only."""
    discriminating_fragments: tuple[str, ...]
    reference_identified_candidate_ids: tuple[str, ...]
    """Candidates named by a fragment that separated the set. The decision input."""
    financial_assessments: tuple[CandidateFinancialAssessment, ...]
    financially_exact_candidate_ids: tuple[str, ...]
    surviving_candidate_ids: tuple[str, ...]
    min_pinned_reference_characters_applied: int

    @property
    def has_unique_survivor(self) -> bool:
        return len(self.surviving_candidate_ids) == 1


def _settlement_facts(snapshot: CaseSnapshot) -> dict[str, SettlementFacts]:
    return {f.settlement_id: f for f in snapshot.base_evidence.settlement_facts}


def _references_of(facts: SettlementFacts) -> tuple[tuple[str, str], ...]:
    values = {"utr": facts.utr, "settlement_id": facts.settlement_id}
    return tuple(
        (kind, values[kind]) for kind in REFERENCE_KINDS if values[kind] is not None
    )  # type: ignore[misc]


def _fragments_from(evidence: tuple[RawToolEvidence, ...]) -> tuple[str, ...]:
    """Every fragment the agent actually tested, read out of raw tool output.

    Read from the tool's *output* rather than its arguments because the
    output is the evidence record proper -- the thing DESIGN.md 4.1 requires
    the decision layer to consume. The two agree by construction; preferring
    the output keeps the rule literal.
    """
    seen: list[str] = []
    for item in evidence:
        if item.tool_name != TOOL_COMPARE_REFERENCE_FRAGMENT:
            continue
        fragment = item.output.get("fragment")
        if isinstance(fragment, str) and fragment not in seen:
            seen.append(fragment)
    return tuple(sorted(seen))


def _assess_finances(
    candidate: CandidateRecord,
    facts_by_id: dict[str, SettlementFacts],
    policy: EvidencePolicy,
) -> CandidateFinancialAssessment:
    settlements = [facts_by_id[sid] for sid in candidate.settlement_ids if sid in facts_by_id]

    every_exact = len(settlements) == len(candidate.settlement_ids) and all(
        facts.derivation.unexplained_delta_paise == 0 for facts in settlements
    )

    sound = True
    for facts in settlements:
        for line in facts.derivation.lines:
            if line.line_type == "payment":
                if line.reference_id is None or line.reference_status != SUCCESSFUL_PAYMENT_STATUS:
                    sound = False
            elif line.line_type == "refund":
                if line.reference_id is None or line.reference_status != SUCCESSFUL_REFUND_STATUS:
                    sound = False

    blocking_ok = candidate.blocking_rule == BLOCKING_RULE_EXACT_TOTAL
    delta_ok = abs(candidate.unexplained_delta_paise) <= policy.max_unexplained_delta_paise

    return CandidateFinancialAssessment(
        candidate_id=candidate.candidate_id,
        group_unexplained_delta_paise=candidate.unexplained_delta_paise,
        every_breakup_is_exact=every_exact,
        breakup_references_are_sound=sound,
        blocking_rule=candidate.blocking_rule,
        blocking_rule_is_exact_total=blocking_ok,
        is_financially_exact=(
            delta_ok
            and every_exact
            and sound
            and (blocking_ok or not policy.require_exact_total_blocking_rule)
        ),
    )


def validate_case(
    *,
    snapshot: CaseSnapshot,
    evidence: tuple[RawToolEvidence, ...],
    policy: Stage3Policy = DEFAULT_POLICY,
    min_pinned_reference_characters: int | None = None,
) -> ValidatorResult:
    """Adjudicate one case from the complete candidate set and raw evidence.

    ``min_pinned_reference_characters`` overrides the base evidence floor,
    which is how the value-aware policy raises the bar for a large amount
    without this module knowing anything about money thresholds.
    """
    narration = snapshot.base_evidence.bank_record.narration
    facts_by_id = _settlement_facts(snapshot)
    floor = (
        policy.evidence.min_pinned_reference_characters
        if min_pinned_reference_characters is None
        else min_pinned_reference_characters
    )

    tested = _fragments_from(evidence)
    admissible: list[str] = []
    inadmissible: list[InadmissibleFragment] = []
    for fragment in tested:
        if not fragment:
            inadmissible.append(
                InadmissibleFragment(fragment=fragment, reason=FRAGMENT_INADMISSIBLE_EMPTY)
            )
            continue
        # Presence is re-derived from the snapshot. The tool reports its own
        # view of this, and the validator deliberately does not use it: a
        # fabricated fragment must be inadmissible whatever any output says.
        if policy.evidence.require_fragment_present_in_narration and fragment not in narration:
            inadmissible.append(
                InadmissibleFragment(
                    fragment=fragment, reason=FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION
                )
            )
            continue
        admissible.append(fragment)

    findings: list[ReferenceFinding] = []
    for fragment in admissible:
        matches: list[CandidateReferenceMatch] = []
        # Every candidate in the snapshot, including any the agent never
        # asked about. This loop is the closure of the omission channel.
        for candidate in snapshot.candidates:
            best: CandidateReferenceMatch | None = None
            for settlement_id in candidate.settlement_ids:
                facts = facts_by_id.get(settlement_id)
                if facts is None:
                    continue
                for kind, value in _references_of(facts):
                    comparison: ReferenceComparison = compare(fragment, value, kind)  # type: ignore[arg-type]
                    relation = strongest_admissible_relation(
                        comparison,
                        accepted_relation_ids=policy.evidence.accepted_relation_ids,
                        min_pinned_reference_characters=floor,
                    )
                    if relation is None:
                        continue
                    contender = CandidateReferenceMatch(
                        candidate_id=candidate.candidate_id,
                        settlement_id=settlement_id,
                        reference_kind=kind,
                        reference_value=value,
                        relation_id=relation.relation_id,
                        pinned_reference_characters=relation.pinned_reference_characters,
                    )
                    if (
                        best is None
                        or contender.pinned_reference_characters
                        > best.pinned_reference_characters
                    ):
                        best = contender
            if best is not None:
                matches.append(best)
        matched_ids = tuple(sorted({m.candidate_id for m in matches}))
        findings.append(
            ReferenceFinding(
                fragment=fragment,
                matches=tuple(matches),
                matched_candidate_ids=matched_ids,
                candidates_evaluated=len(snapshot.candidates),
                is_discriminating=len(matched_ids) == 1,
            )
        )

    reference_matched = tuple(
        sorted({cid for finding in findings for cid in finding.matched_candidate_ids})
    )
    discriminating = tuple(f.fragment for f in findings if f.is_discriminating)
    identified = tuple(
        sorted(
            {finding.matched_candidate_ids[0] for finding in findings if finding.is_discriminating}
        )
    )

    assessments = tuple(
        _assess_finances(candidate, facts_by_id, policy.evidence)
        for candidate in snapshot.candidates
    )
    financially_exact = tuple(
        sorted(a.candidate_id for a in assessments if a.is_financially_exact)
    )

    # Survival needs a fragment that *separated* the candidates, not merely
    # one that touched this candidate among others.
    survivors = tuple(sorted(set(identified) & set(financially_exact)))

    return ValidatorResult(
        validator_version=VALIDATOR_VERSION,
        case_id=snapshot.case_id,
        snapshot_hash=snapshot.content_hash,
        snapshot_integrity_ok=snapshot.verify_integrity(),
        complete_candidate_ids=snapshot.candidate_ids(),
        narration_length=len(narration),
        fragments_tested_by_agent=tested,
        admissible_fragments=tuple(admissible),
        inadmissible_fragments=tuple(inadmissible),
        findings=tuple(findings),
        reference_matched_candidate_ids=reference_matched,
        discriminating_fragments=discriminating,
        reference_identified_candidate_ids=identified,
        financial_assessments=assessments,
        financially_exact_candidate_ids=financially_exact,
        surviving_candidate_ids=survivors,
        min_pinned_reference_characters_applied=floor,
    )


def parse_comparison_output(payload: dict[str, Any]) -> CompareReferenceFragmentOutput:
    """Re-validate a raw comparison payload against its declared schema.

    Used by tests and diagnostics to assert that what the validator reads is
    exactly what the tool contract promised, with no field invented in
    between.
    """
    return CompareReferenceFragmentOutput.model_validate(payload)


__all__ = [
    "FRAGMENT_INADMISSIBLE_EMPTY",
    "FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION",
    "CandidateFinancialAssessment",
    "CandidateReferenceMatch",
    "InadmissibleFragment",
    "RawToolEvidence",
    "ReferenceFinding",
    "ValidatorResult",
    "parse_comparison_output",
    "raw_tool_evidence",
    "validate_case",
]
