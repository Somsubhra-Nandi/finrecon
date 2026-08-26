"""Stage-3 orchestration: unresolved cases through investigation to a decision.

The shape mirrors :mod:`finrecon.pipeline` deliberately -- a fixed sequence
of pure steps with one persistence step at the end:

.. code-block:: text

    Stage-2 unresolved case
        -> immutable case snapshot            (already built, never rebuilt)
            -> bounded investigation           (cache first, provider second)
                -> raw validated tool outputs
                    -> deterministic validator (+ complete candidate set)
                        -> deterministic policy gate
                            -> RESOLVE or ESCALATE
                                -> ledger

Contention is settled the way Stage 2 settles it
------------------------------------------------

Cases are adjudicated **independently**, and only afterwards is contention
resolved -- by retracting *both* claims, never by awarding the counterparty
to whichever case ran first. Copied from
:func:`finrecon.matchers.derived_reconciliation.withdraw_contended` and for
the same reason: a first-come-first-served rule would make the batch's
outcome depend on iteration order, and "which case happened to be processed
first" is not a fact about the money.

What this module cannot do
--------------------------

It never rebuilds a snapshot, never regenerates candidates, never touches a
Stage-2 row, and never resolves anything itself -- the decision comes back
from the gate. Snapshots arrive from the Stage-2 batch result or from the
ledger, and either way their content hash is verified before the case is
investigated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from finrecon.agent.cache import (
    InvestigationOutcome,
    TrajectoryCache,
    investigate_case,
)
from finrecon.agent.loop import LoopConfig
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.trajectory import Trajectory
from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.decide.policy import (
    BLOCKER_COUNTERPARTY_ALREADY_RESOLVED,
    PolicyDecision,
    adjudicate,
    decide,
)
from finrecon.decide.validator import ValidatorResult
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import BatchResult


class SnapshotIntegrityError(RuntimeError):
    """A snapshot's content no longer matches the hash recorded at construction.

    Raised before the case is investigated rather than after. Spending a
    model call on evidence that has already been tampered with, and then
    escalating, would be a slower way of reaching the same answer while
    paying for it.
    """

    def __init__(self, case_id: str) -> None:
        super().__init__(
            f"case snapshot {case_id!r} failed its integrity check; "
            "its content no longer matches the hash recorded at construction"
        )
        self.case_id = case_id


@dataclass(frozen=True)
class CaseOutcome:
    """One case, end to end: what was investigated, found, and decided."""

    case_id: str
    snapshot: CaseSnapshot
    trajectory: Trajectory
    validator_result: ValidatorResult
    decision: PolicyDecision
    cache_key: str
    cache_hit: bool

    @property
    def resolved(self) -> bool:
        return self.decision.resolved


@dataclass(frozen=True)
class Stage3Result:
    batch_id: str
    outcomes: tuple[CaseOutcome, ...]
    policy: Stage3Policy = field(default_factory=lambda: DEFAULT_POLICY)

    def resolved(self) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if o.resolved)

    def escalated(self) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.resolved)

    def blocker_counts(self) -> Counter:
        tally: Counter = Counter()
        for outcome in self.escalated():
            for blocker in outcome.decision.blockers:
                tally[blocker] += 1
        return tally

    def cache_hits(self) -> int:
        return sum(1 for o in self.outcomes if o.cache_hit)

    def provider_calls_made(self) -> bool:
        return any(not o.cache_hit for o in self.outcomes)


def investigate_snapshots(
    snapshots: tuple[CaseSnapshot, ...],
    *,
    chain: ProviderChain | None = None,
    cache: TrajectoryCache | None = None,
    config: LoopConfig | None = None,
    policy: Stage3Policy = DEFAULT_POLICY,
    replay_only: bool = False,
    provider_id: str | None = None,
    model: str | None = None,
    already_claimed: frozenset[str] = frozenset(),
    prior_claims: dict[str, tuple[str, ...]] | None = None,
    write_cache: bool = True,
) -> tuple[CaseOutcome, ...]:
    """Adjudicate a set of snapshots, then settle contention between them.

    ``already_claimed`` is a flat set of counterparties held by someone
    else; it blocks a Stage-3 resolution outright. ``prior_claims`` is the
    per-case view of the same thing (settlement -> claiming case IDs), which
    is what makes a rerun idempotent: a case is not blocked by the claim it
    recorded on its own previous run, only by another case's.

    Contention *among* the Stage-3 resolutions in this pass is settled in
    the second pass below, symmetrically.
    """
    config = config or LoopConfig()
    prior_claims = prior_claims or {}
    ordered = tuple(sorted(snapshots, key=lambda s: s.case_id))

    first_pass: list[CaseOutcome] = []
    for snapshot in ordered:
        if not snapshot.verify_integrity():
            raise SnapshotIntegrityError(snapshot.case_id)

        investigation: InvestigationOutcome = investigate_case(
            snapshot,
            chain=chain,
            cache=cache,
            config=config,
            replay_only=replay_only,
            provider_id=provider_id,
            model=model,
            policy=policy,
            write_cache=write_cache,
        )
        validator_result, decision = adjudicate(
            snapshot=snapshot,
            trajectory=investigation.trajectory,
            claimed_settlement_ids=already_claimed
            | _external_claims(prior_claims, snapshot.case_id),
            policy=policy,
        )
        first_pass.append(
            CaseOutcome(
                case_id=snapshot.case_id,
                snapshot=snapshot,
                trajectory=investigation.trajectory,
                validator_result=validator_result,
                decision=decision,
                cache_key=investigation.cache_key,
                cache_hit=investigation.cache_hit,
            )
        )

    return _withdraw_contended(tuple(first_pass), already_claimed, prior_claims, policy)


def _external_claims(prior_claims: dict[str, tuple[str, ...]], case_id: str) -> frozenset[str]:
    """Settlements some *other* case already claims. A case's own claim is not contention."""
    return frozenset(
        settlement_id
        for settlement_id, cases in prior_claims.items()
        if any(claimant != case_id for claimant in cases)
    )


def _withdraw_contended(
    outcomes: tuple[CaseOutcome, ...],
    already_claimed: frozenset[str],
    prior_claims: dict[str, tuple[str, ...]],
    policy: Stage3Policy,
) -> tuple[CaseOutcome, ...]:
    """Retract every resolution whose counterparty another case also claims.

    Both claims are retracted, not one. Awarding the settlement to the
    lower case ID would be an arbitrary tie-break dressed up as a rule, and
    DESIGN.md §4.3 lists an already-claimed counterparty as a blocker rather
    than as a contest.
    """
    claim_count: Counter = Counter()
    for outcome in outcomes:
        for settlement_id in outcome.decision.resolved_settlement_ids:
            claim_count[settlement_id] += 1
    contested_now = frozenset(sid for sid, count in claim_count.items() if count > 1)

    revised: list[CaseOutcome] = []
    for outcome in outcomes:
        contended = (
            contested_now
            | already_claimed
            | _external_claims(prior_claims, outcome.case_id)
        )
        touches = any(
            sid in contended for sid in outcome.decision.resolved_settlement_ids
        )
        if not touches:
            revised.append(outcome)
            continue
        regated = decide(
            snapshot=outcome.snapshot,
            trajectory=outcome.trajectory,
            validator_result=outcome.validator_result,
            claimed_settlement_ids=contended,
            policy=policy,
        )
        assert BLOCKER_COUNTERPARTY_ALREADY_RESOLVED in regated.blockers
        revised.append(
            CaseOutcome(
                case_id=outcome.case_id,
                snapshot=outcome.snapshot,
                trajectory=outcome.trajectory,
                validator_result=outcome.validator_result,
                decision=regated,
                cache_key=outcome.cache_key,
                cache_hit=outcome.cache_hit,
            )
        )
    return tuple(revised)


def persist_stage3(
    store: LedgerStore, *, batch_id: str, outcomes: tuple[CaseOutcome, ...]
) -> None:
    """Write one Stage-3 pass to the ledger. Replaying an identical pass is a no-op.

    Every write is keyed by ``(batch_id, case_id)`` and inserted with ``ON
    CONFLICT DO NOTHING``, so a rerun -- or a replay from cache -- collapses
    onto the rows already there rather than accumulating a second history.
    No Stage-2 row is touched.
    """
    for outcome in outcomes:
        store.record_investigation(batch_id, outcome.trajectory)
        store.record_stage3_decision(
            batch_id,
            outcome.decision,
            outcome.validator_result,
            snapshot_hash=outcome.snapshot.content_hash,
            cache_key=outcome.cache_key,
        )


def run_stage3(
    *,
    store: LedgerStore,
    batch_result: BatchResult,
    chain: ProviderChain | None = None,
    cache: TrajectoryCache | None = None,
    config: LoopConfig | None = None,
    policy: Stage3Policy = DEFAULT_POLICY,
    replay_only: bool = False,
    provider_id: str | None = None,
    model: str | None = None,
    case_ids: frozenset[str] | None = None,
    write_cache: bool = True,
) -> Stage3Result:
    """The Stage-3 entry point: investigate a Stage-2 batch's unresolved cases.

    ``case_ids`` restricts the run to a subset, which is how a diagnostic
    walks one case or a small sample without spending budget on the rest.
    It narrows *which cases are investigated*; it can never narrow the
    candidate set inside one.
    """
    snapshots = tuple(
        s for s in batch_result.snapshots if case_ids is None or s.case_id in case_ids
    )
    outcomes = investigate_snapshots(
        snapshots,
        chain=chain,
        cache=cache,
        config=config,
        policy=policy,
        replay_only=replay_only,
        provider_id=provider_id,
        model=model,
        prior_claims=store.settlement_claims(batch_result.batch_id),
        write_cache=write_cache,
    )
    persist_stage3(store, batch_id=batch_result.batch_id, outcomes=outcomes)
    return Stage3Result(batch_id=batch_result.batch_id, outcomes=outcomes, policy=policy)


__all__ = [
    "CaseOutcome",
    "SnapshotIntegrityError",
    "Stage3Result",
    "investigate_snapshots",
    "persist_stage3",
    "run_stage3",
]
