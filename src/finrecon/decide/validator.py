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
fishing-by-omission channel. Closing that is what ``validator.v2`` is about,
and the closing is more thorough than v1's.

Why v1's rule was not enough
----------------------------

``validator.v1`` resolved a case when *some one* fragment the agent tested
reached exactly one candidate. Fragments reaching two or more were recorded
and set aside as non-probative, which is correct as far as it goes -- a lazy
fragment like ``"SETL"`` stands in a prefix relation to every canonical
settlement ID at once and separates nothing, and letting it veto a fragment
that genuinely separates the set would punish a model for looking around
(``notes/STAGE3-FINDINGS.md`` section 4).

But setting those fragments aside also discards exactly the evidence a
*conjunction* is made of, and it leaves two holes:

* Two clues that each reach two candidates can, together, reach one. v1 could
  not express that, so it escalated cases whose answer was in the narration.
* A fragment reaching two candidates can *contradict* a discriminating one --
  "the reference is consistent only with B or C" refutes "the reference is A".
  v1 set the contradiction aside and resolved A.

The second is a safety hole, not a coverage one, and it is reproduced as a
fixture in ``benchmark/baselines/adversarial.py``
(``stale_strong_reference_plus_hinge``).

Why the obvious fix was worse
-----------------------------

Intersecting the reach sets of the fragments *the agent tested* is unsafe, and
not subtly:

.. code-block:: text

    f1 -> {A, B}       test f1 and f2  ->  {A}
    f2 -> {A, C}       test f1 and f3  ->  {B}
    f3 -> {B, C}       test f2 and f3  ->  {C}
                       test all three  ->  {}

One narration, three confident and mutually exclusive answers, chosen by which
pair the model happened to look at. That is the model selecting the winner by
selecting where not to look -- the omission channel returning one level up,
wearing a conjunction's clothes. Measured, not assumed: the rule resolves all
three ways on the three ``cherry_picking`` fixtures.

What v2 actually does
---------------------

The evidence a conjunction is proved over is not the agent's selection. It is
the **closure** (:mod:`finrecon.evidence.closure`): every fragment of the
immutable narration standing in a declared relation to any candidate's
reference, whether the agent asked about it or not. A candidate is identified
when it is the only one consistent with **every** informative claim in that
closure.

The agent's evidence is a *seed*, not the proof. The closure is consulted only
once the investigation has surfaced at least one admissible fragment that
relates to some candidate reference -- because "no evidence gathered escalates"
is an invariant this repository asserts at both the validator and the policy
layer, and a case that auto-resolved while its audit trail showed the
investigation contributing nothing would be a worse artifact than an
escalation. Crucially the seed carries no selection power: *which* fragment the
agent surfaced does not change what the closure concludes, only *whether* the
closure is consulted at all. So the omission attack has nothing to work with,
and an uninvestigated case still cannot move money.

Under a closed evidence set, looking away cannot help, because nothing can be
left out. Three properties follow without needing to be checked for:

*Order invariance* and *duplicate invariance*, because the rule is a set
intersection rather than a vote -- there is no count for a repetition to
inflate. *Overlap invariance*, because fragments are grouped into atoms by
their reach set, so ``"ABC123"``, ``"BC12"`` and ``"C123"`` are one claim read
three ways rather than three claims.

*Contradiction monotonicity* follows too: adding a claim can only shrink an
intersection, so valid contradicting evidence can never leave a match
standing. It empties the intersection and the case escalates.

What this costs, stated plainly
-------------------------------

The agent no longer *selects* the reference evidence. It gates whether the
reference path runs at all, and nothing more. Which fragments it tested is
recorded, and how many of the closure's informative claims it found is reported
as :attr:`ValidatorResult.agent_surfaced_atom_ids` -- but that is a *measured*
quantity now, not an input to any predicate.

A deliberate trade, and worth naming rather than burying: there is no reason to
let an untrusted component choose the evidence when the complete evidence costs
a few milliseconds to compute. What the agent keeps is the part a closure
cannot do -- deciding which *records* to open, and, when structural evidence is
eventually admitted, which amount or date in a narration is worth testing at
all. Its decision authority over money remains nil, which is where DESIGN.md
4.1 wanted it.

The predicate, in full
----------------------

A candidate survives when both hold:

*Reference link.* It is the sole candidate consistent with every informative
claim in the narration's closure. A claim is informative when it does not
reach every candidate at once; a fragment reaching none is silence rather than
contradiction and is excluded. Fragments the agent *reported* must still occur
literally in the immutable narration to be admitted at all -- checked here,
never taken from a tool's own boolean -- but the closure is built from the
narration and so cannot contain a fabrication in the first place.

*Financial exactness.* The candidate's group total equals the bank credit to
the paise, every settlement's break-up accounts for its own amount to the
paise, every referencing line names a record in a terminal successful state,
and the candidate came from exact-total blocking. Unchanged from v1.

Failing closed
--------------

Nothing is identified when the investigation surfaced no admissible evidence,
when the closure is incomplete (a narration longer than the declared
enumeration bound), when no claim separates the candidates, when several
candidates survive every claim, or when none does. The five are distinguished
by :attr:`ValidatorResult.reference_evidence_state` so an escalation says which
it was, and the policy gate needs only to know that the count is not one.
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
from finrecon.evidence.closure import (
    ReferenceClosure,
    build_reference_closure,
    fragment_reach,
)
from finrecon.normalize.provenance import FrozenModel

SUCCESSFUL_PAYMENT_STATUS = "captured"
SUCCESSFUL_REFUND_STATUS = "processed"

FRAGMENT_INADMISSIBLE_EMPTY = "empty_fragment"
FRAGMENT_INADMISSIBLE_NOT_IN_NARRATION = "fragment_not_present_in_narration"

REFERENCE_STATE_NO_AGENT_EVIDENCE = "no_admissible_agent_evidence"
"""The investigation surfaced nothing that relates to any candidate reference.

The closure is not consulted, and that is a deliberate precondition rather
than an oversight. An investigation that gathered no admissible evidence gets
no resolution -- otherwise a case could auto-resolve while its audit trail
showed the investigation contributing nothing, and "no evidence gathered
escalates" is an invariant this repository has asserted at both the validator
and the policy layer since ``validator.v1``.
"""

REFERENCE_STATE_NO_EVIDENCE = "no_informative_evidence"
"""Nothing in the narration separates the candidates. Not a contradiction."""

REFERENCE_STATE_IDENTIFIED = "identified"
"""Exactly one candidate is consistent with every informative claim."""

REFERENCE_STATE_AMBIGUOUS = "ambiguous"
"""Several candidates survive every claim. The evidence is simply not enough."""

REFERENCE_STATE_CONTRADICTORY = "contradictory"
"""No candidate survives every claim. The narration argues with itself."""

REFERENCE_STATE_CLOSURE_INCOMPLETE = "closure_incomplete"
"""The narration exceeded the declared exhaustive-enumeration bound.

A partial closure cannot support a claim about what the narration does *not*
contain, so nothing is identified. See
:data:`finrecon.evidence.closure.MAX_NARRATION_LENGTH`.
"""

REFERENCE_STATES: tuple[str, ...] = (
    REFERENCE_STATE_IDENTIFIED,
    REFERENCE_STATE_AMBIGUOUS,
    REFERENCE_STATE_CONTRADICTORY,
    REFERENCE_STATE_NO_EVIDENCE,
    REFERENCE_STATE_NO_AGENT_EVIDENCE,
    REFERENCE_STATE_CLOSURE_INCOMPLETE,
)


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
    """Union over *all* agent fragments, discriminating or not. Audit only."""
    discriminating_fragments: tuple[str, ...]
    """Agent fragments that reached exactly one candidate. Audit only since v2.

    Under ``validator.v1`` this was the decision input. It is kept, and kept
    accurate, because it is what makes the v1-to-v2 difference legible in a
    stored result: a v2 resolution with an empty ``discriminating_fragments``
    is precisely a case v1 could not have reached.
    """
    reference_identified_candidate_ids: tuple[str, ...]
    """The decision input. Derived from the closure, never from agent selection.

    One candidate when the closure isolates one; the surviving set when several
    survive; the contradicting set when none do; empty when nothing separates
    the candidates. The policy gate needs only "is this exactly one", so the
    non-singleton cases are reported in whichever form makes the escalation
    blocker accurate.
    """
    reference_evidence_state: str
    """One of :data:`REFERENCE_STATES`. Why the reference path ended as it did."""
    reference_closure: ReferenceClosure
    """The complete deterministic reference evidence, with full provenance."""
    reference_intersection_candidate_ids: tuple[str, ...]
    """Candidates consistent with every informative claim. Empty on contradiction."""
    reference_union_candidate_ids: tuple[str, ...]
    """Candidates consistent with at least one informative claim."""
    informative_atom_ids: tuple[str, ...]
    """Every claim that separates the candidates at all."""
    agent_surfaced_atom_ids: tuple[str, ...]
    """Informative claims the agent's own fragments happened to reach.

    A *measured* quantity, not an input. Since v2 the decision does not depend
    on which claims the agent found, so how many it found is free to report --
    and reporting it is how "did the investigation earn its tokens?" stays
    answerable without the answer being able to move money.
    """
    financial_assessments: tuple[CandidateFinancialAssessment, ...]
    financially_exact_candidate_ids: tuple[str, ...]
    surviving_candidate_ids: tuple[str, ...]
    min_pinned_reference_characters_applied: int

    @property
    def has_unique_survivor(self) -> bool:
        return len(self.surviving_candidate_ids) == 1

    @property
    def resolved_conjunctively(self) -> bool:
        """True when *no single claim* would have sufficed. The v2-only case.

        Not "more than one claim exists" -- a case can carry several claims and
        still be resolved by one of them alone, which is what ``validator.v1``
        already did and what benchmark v3's T2 tier is built to be. The
        distinguishing question is whether any informative claim isolates a
        candidate by itself; if none does, the intersection is what identified
        it and the resolution is one v1 could not have reached.
        """
        if self.reference_evidence_state != REFERENCE_STATE_IDENTIFIED:
            return False
        return not any(
            len(atom.reach) == 1 for atom in self.reference_closure.informative_atoms()
        )

    @property
    def informative_atom_count(self) -> int:
        return len(self.informative_atom_ids)


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

    # --- the closure, and the identification derived from it -------------
    #
    # validator.v2. Everything above this line describes what the *agent*
    # looked at, and is audit. What identifies a candidate is the closure: every
    # fragment of the immutable narration that stands in a declared relation to
    # any candidate's reference, whether the agent asked about it or not.
    #
    # The reason is the omission channel. Intersecting only the fragments the
    # agent tested would let one narration prove three different candidates
    # depending on which pair of clues the agent happened to test, which is the
    # model choosing the winner by choosing where not to look -- see
    # notes/BENCHMARK-V4-FINDINGS.md and benchmark/baselines/adversarial.py,
    # where that attack is a fixture rather than a hypothesis.
    closure = build_reference_closure(
        snapshot,
        accepted_relation_ids=policy.evidence.accepted_relation_ids,
        min_pinned_reference_characters=floor,
    )

    # What the agent's own admissible fragments reach. Two jobs, and only one
    # of them touches the decision: this *seeds* the closure (an investigation
    # that gathered nothing gets no resolution), and it is reported so that how
    # much of the closure the agent found stays measurable.
    agent_reach = {
        fragment: fragment_reach(
            snapshot,
            fragment,
            accepted_relation_ids=policy.evidence.accepted_relation_ids,
            min_pinned_reference_characters=floor,
        )
        for fragment in admissible
    }
    agent_gathered_evidence = any(reach for reach in agent_reach.values())

    intersection = closure.intersection()
    union = closure.union()
    if not closure.is_complete:
        state = REFERENCE_STATE_CLOSURE_INCOMPLETE
        identified: tuple[str, ...] = ()
    elif not agent_gathered_evidence:
        # The seed. Note what this is *not*: it is not the agent choosing the
        # evidence. Whether the closure then identifies anything does not
        # depend on which fragment the agent surfaced, only on the fact that it
        # surfaced one -- so the omission attack has nothing to work with,
        # while a case that was never investigated still cannot resolve.
        state = REFERENCE_STATE_NO_AGENT_EVIDENCE
        identified = ()
    elif not closure.has_informative_evidence():
        state = REFERENCE_STATE_NO_EVIDENCE
        identified = ()
    elif len(intersection) == 1:
        state = REFERENCE_STATE_IDENTIFIED
        identified = tuple(sorted(intersection))
    elif intersection:
        state = REFERENCE_STATE_AMBIGUOUS
        identified = tuple(sorted(intersection))
    else:
        # The claims contradict each other. Reported as the union so the gate
        # escalates on "more than one candidate has reference support, so none
        # is supported by all of it" rather than on a bare absence -- there was
        # evidence, and saying there was none would misdescribe the refusal.
        state = REFERENCE_STATE_CONTRADICTORY
        identified = tuple(sorted(union))

    # Which informative claims the agent's own fragments landed on. Measured,
    # never an input to the decision above.
    agent_atom_ids: list[str] = []
    for reach in agent_reach.values():
        if not reach:
            continue
        atom = closure.atom_for_reach(reach)
        if atom is None or atom.atom_id not in closure.informative_atom_ids:
            continue
        if atom.atom_id not in agent_atom_ids:
            agent_atom_ids.append(atom.atom_id)

    assessments = tuple(
        _assess_finances(candidate, facts_by_id, policy.evidence)
        for candidate in snapshot.candidates
    )
    financially_exact = tuple(
        sorted(a.candidate_id for a in assessments if a.is_financially_exact)
    )

    # Survival needs the reference evidence to have isolated *one* candidate and
    # that candidate's money to add up. Unchanged from v1 in form; what changed
    # is where ``identified`` comes from.
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
        reference_evidence_state=state,
        reference_closure=closure,
        reference_intersection_candidate_ids=tuple(sorted(intersection)),
        reference_union_candidate_ids=tuple(sorted(union)),
        informative_atom_ids=closure.informative_atom_ids,
        agent_surfaced_atom_ids=tuple(agent_atom_ids),
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
    "REFERENCE_STATES",
    "REFERENCE_STATE_AMBIGUOUS",
    "REFERENCE_STATE_CLOSURE_INCOMPLETE",
    "REFERENCE_STATE_CONTRADICTORY",
    "REFERENCE_STATE_IDENTIFIED",
    "REFERENCE_STATE_NO_AGENT_EVIDENCE",
    "REFERENCE_STATE_NO_EVIDENCE",
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
