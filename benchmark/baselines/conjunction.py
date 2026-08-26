"""Candidate rules for conjunctive reference evidence, and the harness that judges them.

Five rules, measured before any of them touches ``src/finrecon/decide``. Each
answers the same question -- *given an immutable snapshot and the fragments the
agent actually tested, which candidates does the reference evidence identify?*
-- and they differ only in whose evidence set the answer is computed over.

.. code-block:: text

    R0  v1, as shipped        candidates named by a model fragment that reaches
                              exactly one candidate; several disagreeing means none
    R1  model intersection    intersect the reach sets of the model's fragments
    R2  seeded closure        if the model surfaced any admissible evidence at all,
                              intersect the CLOSURE
    R3  open closure          intersect the closure, whatever the model did
    R4  closure-verified      the model's own fragments must isolate a candidate,
                              and the closure must not contradict it

R1 is included because it is the rule a reasonable person writes first, and
because the harness has to *show* it failing rather than assert that it would.

The union fallback
------------------

Every rule returns a candidate set that the existing policy gate reads as
``matched``: exactly one means proceed, zero means ``no_reference_link``, more
than one means ``ambiguous_reference_link``. An empty intersection is a
contradiction, not an absence -- but it is also emphatically not a resolution,
so the rules report the *union* of the contradicting claims in that case. The
gate then escalates with ``ambiguous_reference_link``, which is what a
contradiction is: evidence pointing at more than one candidate and therefore at
none.

That choice is what keeps the policy gate untouched by this change. The
alternative -- a new blocker id -- would change the escalation vocabulary and
force a ``policy.v2`` that nothing else about the gate justifies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.decide.policy import applicable_min_pinned
from finrecon.evidence.closure import (
    ReferenceClosure,
    build_reference_closure,
    fragment_reach,
)

from benchmark.baselines.arms import financially_exact_candidates

BOUNDED_AGENT_FRAGMENT_LIMIT = 6
"""How many fragments the stand-in agent surfaces per case.

Matches :class:`tests.stage3_fakes.MechanicalInvestigator`'s own limit, which
is where the number comes from.
"""

_RUN_PATTERN = re.compile(r"[A-Za-z0-9_*#\-]+")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def bounded_agent_fragments(
    narration: str, limit: int = BOUNDED_AGENT_FRAGMENT_LIMIT, floor: int = 4
) -> tuple[str, ...]:
    """A deterministic stand-in for what a bounded investigation surfaces.

    Two mechanical splits of the narration, scored and truncated -- the same
    construction :func:`tests.stage3_fakes.candidate_fragments` uses to drive
    the fake provider, restated here so this package needs no provider chain,
    no Stage-3 run and no import from ``tests``. That matters for a structural
    reason rather than a tidiness one: ``tests/test_v4_baselines.py`` asserts
    that no module in this package can reach a provider, and a guarantee with
    an exemption in it is not a guarantee.

    It is emphatically not a model. It cannot read, so it enumerates where a
    model would recognise. What it provides is a *fixed* per-case fragment set,
    so five rules are compared on identical agent evidence rather than on five
    different investigations.
    """
    seen: list[str] = []
    for pattern in (_RUN_PATTERN, _TOKEN_PATTERN):
        for match in pattern.finditer(narration):
            value = match.group(0)
            if len(value) >= floor and value not in seen:
                seen.append(value)

    def score(fragment: str) -> tuple[int, int, int, str]:
        has_mask = any(character in "*#" for character in fragment)
        mixed = any(c.isalpha() for c in fragment) and any(c.isdigit() for c in fragment)
        return (0 if has_mask else 1, 0 if mixed else 1, -len(fragment), fragment)

    return tuple(sorted(seen, key=score)[:limit])


RULE_V1 = "R0_v1_single_fragment"
RULE_MODEL_INTERSECTION = "R1_model_selected_intersection"
RULE_SEEDED_CLOSURE = "R2_seeded_closure_intersection"
RULE_OPEN_CLOSURE = "R3_open_closure_intersection"
RULE_CLOSURE_VERIFIED = "R4_closure_verified_model_proof"

RULES: tuple[str, ...] = (
    RULE_V1,
    RULE_MODEL_INTERSECTION,
    RULE_SEEDED_CLOSURE,
    RULE_OPEN_CLOSURE,
    RULE_CLOSURE_VERIFIED,
)


@dataclass(frozen=True)
class RuleOutcome:
    """What one rule concluded about one case, and how."""

    rule: str
    identified: tuple[str, ...]
    """The candidate set handed to the policy gate, sorted."""
    resolved_candidate_id: str | None
    intersection: tuple[str, ...]
    admissible_model_fragments: tuple[str, ...]
    informative_atom_count: int
    independent_span_count: int
    state: str
    """``resolved`` / ``no_evidence`` / ``ambiguous`` / ``contradictory`` /
    ``model_evidence_insufficient`` / ``closure_incomplete`` /
    ``not_financially_exact``."""


@dataclass(frozen=True)
class CaseEvidence:
    """Everything a rule is allowed to see for one case, computed once."""

    snapshot: CaseSnapshot
    floor: int
    closure: ReferenceClosure
    admissible_fragments: tuple[str, ...]
    """Model fragments that occur literally in the immutable narration."""
    inadmissible_fragments: tuple[str, ...]
    model_reach: dict[str, frozenset[str]]
    """Reach set per admissible model fragment. Empty reach sets included."""

    @property
    def model_atoms(self) -> tuple[frozenset[str], ...]:
        """Distinct non-empty reach sets among the model's admissible fragments.

        Distinct, so a repeated or overlapping fragment contributes once. Non-
        empty, so a fragment matching no reference contributes silence rather
        than a universally empty intersection.
        """
        seen: list[frozenset[str]] = []
        for fragment in self.admissible_fragments:
            reach = self.model_reach[fragment]
            if reach and reach not in seen:
                seen.append(reach)
        return tuple(seen)


def prepare_case(
    snapshot: CaseSnapshot,
    model_fragments: tuple[str, ...],
    policy: Stage3Policy = DEFAULT_POLICY,
) -> CaseEvidence:
    """Admit the model's fragments, then build the closure. Reads no ground truth."""
    narration = snapshot.base_evidence.bank_record.narration
    floor = applicable_min_pinned(
        snapshot.base_evidence.bank_record.amount_paise, policy
    )
    accepted = policy.evidence.accepted_relation_ids

    admissible: list[str] = []
    inadmissible: list[str] = []
    for fragment in model_fragments:
        if not fragment:
            inadmissible.append(fragment)
        elif policy.evidence.require_fragment_present_in_narration and fragment not in narration:
            inadmissible.append(fragment)
        elif fragment not in admissible:
            admissible.append(fragment)

    reach = {
        fragment: fragment_reach(
            snapshot,
            fragment,
            accepted_relation_ids=accepted,
            min_pinned_reference_characters=floor,
        )
        for fragment in admissible
    }
    return CaseEvidence(
        snapshot=snapshot,
        floor=floor,
        closure=build_reference_closure(
            snapshot,
            accepted_relation_ids=accepted,
            min_pinned_reference_characters=floor,
        ),
        admissible_fragments=tuple(admissible),
        inadmissible_fragments=tuple(inadmissible),
        model_reach=reach,
    )


def _finish(
    rule: str,
    evidence: CaseEvidence,
    identified: frozenset[str],
    intersection: frozenset[str],
    state: str,
    policy: Stage3Policy,
) -> RuleOutcome:
    resolved: str | None = None
    if len(identified) == 1:
        candidate_id = next(iter(identified))
        if candidate_id in financially_exact_candidates(evidence.snapshot, policy):
            resolved = candidate_id
            state = "resolved"
        else:
            state = "not_financially_exact"
    return RuleOutcome(
        rule=rule,
        identified=tuple(sorted(identified)),
        resolved_candidate_id=resolved,
        intersection=tuple(sorted(intersection)),
        admissible_model_fragments=evidence.admissible_fragments,
        informative_atom_count=len(evidence.closure.informative_atom_ids),
        independent_span_count=evidence.closure.independent_span_count(),
        state=state,
    )


def _from_atoms(
    rule: str,
    evidence: CaseEvidence,
    atoms: tuple[frozenset[str], ...],
    policy: Stage3Policy,
) -> RuleOutcome:
    """Intersect a set of claims, with the declared union fallback on contradiction."""
    if not atoms:
        return _finish(rule, evidence, frozenset(), frozenset(), "no_evidence", policy)
    intersection = atoms[0]
    union: set[str] = set()
    for atom in atoms:
        intersection = intersection & atom
        union.update(atom)
    if not intersection:
        return _finish(
            rule, evidence, frozenset(union), frozenset(), "contradictory", policy
        )
    state = "ambiguous" if len(intersection) > 1 else "resolved"
    return _finish(rule, evidence, intersection, intersection, state, policy)


# --- the five rules --------------------------------------------------------


def rule_v1(evidence: CaseEvidence, policy: Stage3Policy = DEFAULT_POLICY) -> RuleOutcome:
    """validator.v1, restated: candidates a lone model fragment reaches.

    Reimplemented here rather than imported so the harness can run all five
    rules over one prepared evidence bundle. Its agreement with the shipped
    ``validate_case`` is asserted separately in
    ``tests/test_validator_conjunction.py``.
    """
    identified: set[str] = set()
    for fragment in evidence.admissible_fragments:
        reach = evidence.model_reach[fragment]
        if len(reach) == 1:
            identified.add(next(iter(reach)))
    if not identified:
        return _finish(RULE_V1, evidence, frozenset(), frozenset(), "no_evidence", policy)
    state = "ambiguous" if len(identified) > 1 else "resolved"
    return _finish(RULE_V1, evidence, frozenset(identified), frozenset(identified), state, policy)


def rule_model_intersection(
    evidence: CaseEvidence, policy: Stage3Policy = DEFAULT_POLICY
) -> RuleOutcome:
    """Intersect only what the agent tested. The unsafe rule, measured."""
    return _from_atoms(RULE_MODEL_INTERSECTION, evidence, evidence.model_atoms, policy)


def _closure_atoms(evidence: CaseEvidence) -> tuple[frozenset[str], ...]:
    return tuple(
        frozenset(atom.reach) for atom in evidence.closure.informative_atoms()
    )


def rule_seeded_closure(
    evidence: CaseEvidence, policy: Stage3Policy = DEFAULT_POLICY
) -> RuleOutcome:
    """Intersect the closure, but only once the agent has surfaced something.

    The agent's contribution is a *precondition*, never the proof. An
    investigation that gathered no admissible evidence gets no resolution, so
    an exhausted step budget, a provider failure and a model that declines to
    look all still escalate -- which is what makes those blockers mean
    anything.
    """
    if not evidence.closure.is_complete:
        return _finish(
            RULE_SEEDED_CLOSURE, evidence, frozenset(), frozenset(), "closure_incomplete", policy
        )
    if not evidence.model_atoms:
        return _finish(
            RULE_SEEDED_CLOSURE, evidence, frozenset(), frozenset(), "no_evidence", policy
        )
    return _from_atoms(RULE_SEEDED_CLOSURE, evidence, _closure_atoms(evidence), policy)


def rule_open_closure(
    evidence: CaseEvidence, policy: Stage3Policy = DEFAULT_POLICY
) -> RuleOutcome:
    """Intersect the closure regardless of what the agent did."""
    if not evidence.closure.is_complete:
        return _finish(
            RULE_OPEN_CLOSURE, evidence, frozenset(), frozenset(), "closure_incomplete", policy
        )
    return _from_atoms(RULE_OPEN_CLOSURE, evidence, _closure_atoms(evidence), policy)


def rule_closure_verified(
    evidence: CaseEvidence, policy: Stage3Policy = DEFAULT_POLICY
) -> RuleOutcome:
    """The agent's own evidence must isolate a candidate; the closure must agree.

    Strictly more conservative than :func:`rule_open_closure`: model atoms are a
    subset of closure atoms, so the model's intersection always contains the
    closure's. If the model isolates one candidate and the closure has not ruled
    it out, the two necessarily agree.
    """
    if not evidence.closure.is_complete:
        return _finish(
            RULE_CLOSURE_VERIFIED, evidence, frozenset(), frozenset(), "closure_incomplete", policy
        )
    closure_outcome = _from_atoms(
        RULE_CLOSURE_VERIFIED, evidence, _closure_atoms(evidence), policy
    )
    if closure_outcome.resolved_candidate_id is None:
        # The closure did not isolate anything either, so the model's evidence
        # cannot matter. Report the closure's reading; the gate escalates on it.
        return closure_outcome

    model = rule_model_intersection(evidence, policy)
    if len(model.identified) == 1 and model.intersection:
        return closure_outcome

    # The closure proves a candidate and the agent's own evidence does not.
    # This rule declines, which is the whole difference between it and R3: an
    # investigation that did not do the work gets no resolution from work it
    # did not do. The empty identified set escalates as ``no_reference_link``,
    # which under-describes the situation -- there *is* a link, the agent just
    # failed to find it -- and is one reason R4 is measured rather than shipped.
    return _finish(
        RULE_CLOSURE_VERIFIED,
        evidence,
        frozenset(),
        frozenset(closure_outcome.intersection),
        "model_evidence_insufficient",
        policy,
    )


RULE_FUNCTIONS: dict[str, Callable[..., RuleOutcome]] = {
    RULE_V1: rule_v1,
    RULE_MODEL_INTERSECTION: rule_model_intersection,
    RULE_SEEDED_CLOSURE: rule_seeded_closure,
    RULE_OPEN_CLOSURE: rule_open_closure,
    RULE_CLOSURE_VERIFIED: rule_closure_verified,
}


def evaluate_rules(
    snapshot: CaseSnapshot,
    model_fragments: tuple[str, ...],
    policy: Stage3Policy = DEFAULT_POLICY,
) -> dict[str, RuleOutcome]:
    """Run all five rules over one case from one prepared evidence bundle."""
    evidence = prepare_case(snapshot, model_fragments, policy)
    return {name: function(evidence, policy) for name, function in RULE_FUNCTIONS.items()}


__all__ = [
    "BOUNDED_AGENT_FRAGMENT_LIMIT",
    "RULES",
    "RULE_CLOSURE_VERIFIED",
    "RULE_FUNCTIONS",
    "RULE_MODEL_INTERSECTION",
    "RULE_OPEN_CLOSURE",
    "RULE_SEEDED_CLOSURE",
    "RULE_V1",
    "CaseEvidence",
    "RuleOutcome",
    "bounded_agent_fragments",
    "evaluate_rules",
    "prepare_case",
    "rule_closure_verified",
    "rule_model_intersection",
    "rule_open_closure",
    "rule_seeded_closure",
    "rule_v1",
]
