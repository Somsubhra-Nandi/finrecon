"""Offline replay of recorded trajectories through the real validator and policy.

The evaluator does not re-implement Stage 3's decision logic, and it must not:
a second copy of the gate would drift from the one that actually decides, and
then the accuracy number would describe a system nobody ships. Instead the
recorded trajectory is fed back through :func:`finrecon.stage3.run_stage3` with
the production validator and the production policy, and the decision that
comes out is the decision the controller would have taken.

**Why this cannot call a provider.** Three independent reasons, all of them
structural rather than a promise:

1. ``chain=None`` is passed. There is no provider object in the call.
2. ``replay_only=True`` is passed, so a cache miss raises
   :class:`finrecon.agent.cache.ReplayMissError` inside
   ``investigate_case`` *before* any chain would be consulted -- and this
   module converts that into a fail-closed
   :class:`~benchmark.eval.errors.EvaluationInputError`.
3. This package imports no provider module at all. ``tests/
   test_stage4_evaluator.py`` asserts that by walking the AST of every file
   here, and separately asserts ``provider_calls_made()`` is false after a
   real evaluation.

**Version drift is a hard error, not a warning.** A trajectory's cache key
covers the prompt, the tool schema, the loop, the cache format, the validator
and the policy declaration. If a recorded key no longer matches what the
current tree computes for the same case, that trajectory was produced under a
different contract; replaying it would attribute an old run's behaviour to
today's code. The key mismatch surfaces naturally as a replay miss, and the
error message says which versions the artifact was recorded under.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from finrecon.agent.cache import ReplayMissError, TrajectoryCache, cache_key
from finrecon.agent.loop import DEFAULT_MAX_STEPS, LoopConfig
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy
from finrecon.ledger.store import LedgerStore
from finrecon.pipeline import process_batch
from finrecon.stage3 import Stage3Result, run_stage3

from benchmark.eval.errors import EvaluationInputError, ReplayIntegrityError
from benchmark.eval.sources import TrajectoryRecord


@dataclass(frozen=True)
class ReplayResult:
    """The Stage-3 result produced entirely from recorded artifacts."""

    stage3: Stage3Result
    cohort: tuple[str, ...]
    provider_calls_made: bool
    cache_hits: int
    staged_directory: Path

    @property
    def outcomes_by_case(self) -> dict:
        return {o.case_id: o for o in self.stage3.outcomes}


def stage_fixtures(
    records: dict[str, TrajectoryRecord],
    directory: Path,
) -> Path:
    """Write the selected trajectories into an isolated cache directory.

    Isolated on purpose. The evaluator never writes into
    ``fixtures/trajectories/``: a committed corpus is a reviewed artifact, and
    an evaluation run is not a reason to add to it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for existing in directory.glob("*.json"):
        existing.unlink()
    for case_id, record in sorted(records.items()):
        payload = dict(record.payload)
        (directory / f"{record.cache_key}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    return directory


def replay_cohort(
    *,
    benchmark_dir: Path,
    split: str,
    records: dict[str, TrajectoryRecord],
    cohort: tuple[str, ...],
    provider_id: str,
    model: str,
    staging_dir: Path,
    max_steps: int = DEFAULT_MAX_STEPS,
    policy: Stage3Policy = DEFAULT_POLICY,
) -> ReplayResult:
    """Replay ``cohort`` offline and return the real Stage-3 decisions.

    ``provider_id`` and ``model`` are the *cache identity* of the recorded
    run, not a routing instruction. Nothing is constructed from them; they
    only enter the SHA-256 that locates the stored trajectory, because a
    trajectory produced by one model is not interchangeable with one produced
    by another.
    """
    selected = {case_id: records[case_id] for case_id in cohort if case_id in records}
    if len(selected) != len(cohort):
        absent = sorted(set(cohort) - set(selected))
        raise EvaluationInputError(
            f"cannot replay: {len(absent)} cohort case(s) have no recorded "
            f"trajectory ({', '.join(absent[:5])}"
            + (" ..." if len(absent) > 5 else "")
            + "). The evaluator is offline and will not run them live."
        )

    staged = stage_fixtures(selected, staging_dir)

    with LedgerStore(":memory:") as store:
        batch = process_batch(store=store, benchmark_dir=benchmark_dir, split=split)

        # Fail before replaying, with a message that names the drift. A raw
        # ReplayMissError would only say "no cached trajectory", which sends
        # the reader looking for a missing file rather than a version change.
        by_case = {s.case_id: s for s in batch.snapshots}
        drifted: list[str] = []
        for case_id, record in sorted(selected.items()):
            snapshot = by_case.get(case_id)
            if snapshot is None:
                raise EvaluationInputError(
                    f"case {case_id!r} is not a Stage-2 unresolved case on split "
                    f"{split!r}; it never reaches Stage 3 and has nothing to score"
                )
            expected = cache_key(
                snapshot,
                provider=provider_id,
                model=model,
                max_steps=max_steps,
                policy=policy,
            )
            if expected != record.cache_key:
                drifted.append(case_id)
        if drifted:
            sample = selected[drifted[0]]
            raise ReplayIntegrityError(
                f"{len(drifted)} recorded trajectory/ies do not match the cache key "
                f"this tree computes (first: {drifted[0]}). They were recorded under "
                f"{sample.versions} for identity {provider_id}:{model}. Either the "
                "prompt/tool/loop/validator/policy versions have moved since the run, "
                "or --provider/--model name a different identity than the recording. "
                "Scoring them here would attribute an old contract's behaviour to the "
                "current one."
            )

        try:
            stage3 = run_stage3(
                store=store,
                batch_result=batch,
                chain=None,  # no provider object exists in this call
                cache=TrajectoryCache(staged),
                config=LoopConfig(max_steps=max_steps),
                policy=policy,
                replay_only=True,  # a miss raises; it never reaches out
                provider_id=provider_id,
                model=model,
                case_ids=frozenset(cohort),
                write_cache=False,
            )
        except ReplayMissError as miss:
            raise EvaluationInputError(
                f"replay miss for case {miss.case_id!r}: no recorded trajectory. "
                "The evaluator makes zero provider calls and will not fill the gap."
            ) from miss

        replayed = tuple(sorted(o.case_id for o in stage3.outcomes))
        if replayed != tuple(sorted(cohort)):
            raise ReplayIntegrityError(
                f"replay returned {len(replayed)} case(s) for a cohort of "
                f"{len(cohort)}; the evaluated set is not the requested set"
            )
        if stage3.provider_calls_made():
            raise ReplayIntegrityError(
                "a provider call was made during evaluation; this is an offline "
                "harness and the result must not be reported"
            )
        if stage3.cache_hits() != len(cohort):
            raise ReplayIntegrityError(
                f"expected {len(cohort)} cache hits, got {stage3.cache_hits()}"
            )

        return ReplayResult(
            stage3=stage3,
            cohort=tuple(sorted(cohort)),
            provider_calls_made=stage3.provider_calls_made(),
            cache_hits=stage3.cache_hits(),
            staged_directory=staged,
        )


__all__ = ["ReplayResult", "replay_cohort", "stage_fixtures"]
