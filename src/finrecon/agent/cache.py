"""Trajectory cache and deterministic replay.

DESIGN.md 6.2 makes this non-optional: *"Fixtures cache the full agent
trajectory keyed by case ID plus prompt-chain hash, not just single
completions."* A reviewer clones the repo, runs ``make eval``, and gets the
README's numbers back with no API key and no network. Stage 4 builds that
harness; Stage 3 builds the mechanism it will stand on.

The cache key
-------------

A key is a SHA-256 over every input that could change what a model saw or
was allowed to do:

===========================  =========================================
``snapshot_hash``            the immutable case content hash -- covers
                             the narration, the complete candidate set,
                             every base fact, the whole case
``case_id``                  identity, so two cases with coincidentally
                             identical content stay separate entries
``provider`` / ``model``     a different model is a different run
``prompt_version``           editing the prompt invalidates the entry
``tool_schema_version``      changing what a tool returns invalidates it
``agent_loop_version``       changing termination or call bounds does too
``max_steps``                a smaller budget is a different experiment
``max_tool_calls_per_step``  changing the per-turn batch bound changes execution
``validator`` / ``policy``   deterministic early-stop authority and configuration
``cache_schema_version``     the record format itself
===========================  =========================================

Note what is *not* in the key: wall-clock time, run order, machine, or
anything derived from a previous outcome. The same case under the same
configuration is the same key, forever.

Replay semantics
----------------

:meth:`TrajectoryCache.load` returns the stored trajectory with
``replayed=True`` and makes **zero** provider calls -- there is no provider
object in this module to call. :func:`investigate_case` takes the cache
first and only builds a chain on a miss, so a fully warmed corpus needs no
credentials at all. The tests prove that by handing the replay path a
provider that raises on contact.

Determinism, precisely (DESIGN.md 4.6): replay is byte-identical because it
returns stored bytes. A *live* run is not guaranteed to reproduce a stored
one, and this module does not pretend otherwise -- a live re-run writes only
if the key is absent, so an existing fixture is never silently overwritten
by a drifted response.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from finrecon.agent.version import (
    AGENT_LOOP_VERSION,
    CACHE_SCHEMA_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
    VALIDATOR_VERSION,
)
from finrecon.agent.loop import (
    DEFAULT_MAX_STEPS,
    MAX_TOOL_CALLS_PER_STEP,
    LoopConfig,
    run_investigation,
)
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.trajectory import Trajectory
from finrecon.candidates.snapshot import CaseSnapshot
from finrecon.decide.config import DEFAULT_POLICY, Stage3Policy

DEFAULT_FIXTURE_DIR = Path("fixtures") / "trajectories"


@dataclass(frozen=True)
class CacheKeyInputs:
    """Every input the key covers, kept as data so it can be asserted on."""

    case_id: str
    snapshot_hash: str
    provider: str
    model: str
    prompt_version: str
    tool_schema_version: str
    agent_loop_version: str
    cache_schema_version: str
    validator_version: str
    policy_version: str
    policy_declaration: dict[str, object]
    max_steps: int
    max_tool_calls_per_step: int

    def canonical(self) -> str:
        return json.dumps(
            {
                "agent_loop_version": self.agent_loop_version,
                "cache_schema_version": self.cache_schema_version,
                "case_id": self.case_id,
                "max_steps": self.max_steps,
                "max_tool_calls_per_step": self.max_tool_calls_per_step,
                "model": self.model,
                "policy_declaration": self.policy_declaration,
                "policy_version": self.policy_version,
                "prompt_version": self.prompt_version,
                "provider": self.provider,
                "snapshot_hash": self.snapshot_hash,
                "tool_schema_version": self.tool_schema_version,
                "validator_version": self.validator_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def key(self) -> str:
        return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()


def cache_key_inputs(
    snapshot: CaseSnapshot,
    *,
    provider: str,
    model: str,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_calls_per_step: int = MAX_TOOL_CALLS_PER_STEP,
    policy: Stage3Policy = DEFAULT_POLICY,
) -> CacheKeyInputs:
    return CacheKeyInputs(
        case_id=snapshot.case_id,
        snapshot_hash=snapshot.content_hash,
        provider=provider,
        model=model,
        prompt_version=PROMPT_VERSION,
        tool_schema_version=TOOL_SCHEMA_VERSION,
        agent_loop_version=AGENT_LOOP_VERSION,
        cache_schema_version=CACHE_SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
        policy_version=POLICY_VERSION,
        policy_declaration=policy.describe(),
        max_steps=max_steps,
        max_tool_calls_per_step=max_tool_calls_per_step,
    )


def cache_key(
    snapshot: CaseSnapshot,
    *,
    provider: str,
    model: str,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_tool_calls_per_step: int = MAX_TOOL_CALLS_PER_STEP,
    policy: Stage3Policy = DEFAULT_POLICY,
) -> str:
    return cache_key_inputs(
        snapshot,
        provider=provider,
        model=model,
        max_steps=max_steps,
        max_tool_calls_per_step=max_tool_calls_per_step,
        policy=policy,
    ).key()


class TrajectoryCache:
    """A directory of trajectory fixtures, one JSON file per cache key.

    Deliberately files rather than rows in the ledger. Fixtures are meant to
    be committed, diffed and reviewed -- the point is a reviewer can open one
    and read what the model actually did -- and a SQLite blob is none of
    those things.
    """

    def __init__(self, directory: Path | str = DEFAULT_FIXTURE_DIR) -> None:
        self.directory = Path(directory)

    def path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def has(self, key: str) -> bool:
        return self.path_for(key).exists()

    def load(self, key: str) -> Trajectory | None:
        """Return the stored trajectory, marked replayed. Makes no provider call."""
        path = self.path_for(key)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["replayed"] = True
        payload["cache_key"] = key
        # JSON-input mode, not ``model_validate``: the trajectory models are
        # ``strict=True`` (see :mod:`finrecon.normalize.provenance`), and only
        # the JSON path accepts a wire array where the model declares a
        # tuple. Strictness elsewhere -- no float into an int field -- is
        # preserved, which is the reason the loader takes the same route.
        return Trajectory.model_validate_json(json.dumps(payload))

    def store(self, key: str, trajectory: Trajectory, *, overwrite: bool = False) -> Path:
        """Write a trajectory under ``key``. Existing entries are kept by default.

        Not overwriting is the conservative choice: a hosted model drifts,
        and a re-run that silently replaced a committed fixture would change
        the numbers a README claims are reproducible without anyone noticing.
        """
        path = self.path_for(key)
        if path.exists() and not overwrite:
            return path
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = trajectory.model_dump(mode="json")
        payload["cache_key"] = key
        payload["replayed"] = False
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return path

    def keys(self) -> tuple[str, ...]:
        if not self.directory.exists():
            return ()
        return tuple(sorted(p.stem for p in self.directory.glob("*.json")))


class ReplayMissError(RuntimeError):
    """Replay-only mode was asked for a trajectory the cache does not hold."""

    def __init__(self, case_id: str, key: str) -> None:
        super().__init__(
            f"no cached trajectory for case {case_id!r} (key {key[:16]}...); "
            "replay-only mode makes no provider calls"
        )
        self.case_id = case_id
        self.key = key


@dataclass(frozen=True)
class InvestigationOutcome:
    trajectory: Trajectory
    cache_key: str
    cache_hit: bool

    @property
    def made_provider_calls(self) -> bool:
        return not self.cache_hit


def investigate_case(
    snapshot: CaseSnapshot,
    *,
    chain: ProviderChain | None = None,
    cache: TrajectoryCache | None = None,
    config: LoopConfig | None = None,
    replay_only: bool = False,
    provider_id: str | None = None,
    model: str | None = None,
    policy: Stage3Policy = DEFAULT_POLICY,
    write_cache: bool = True,
) -> InvestigationOutcome:
    """Cache-first investigation of one case.

    ``replay_only=True`` is the ``make eval`` path: a hit is returned, a miss
    raises :class:`ReplayMissError`, and no provider is ever constructed or
    called. On the live path a miss runs the bounded loop and stores the
    result under the same key.

    The key needs a provider and model identity even in replay-only mode --
    a trajectory produced by one model is not interchangeable with one
    produced by another -- so they may be supplied directly when no chain is
    available.
    """
    config = config or LoopConfig()
    cache = cache or TrajectoryCache()

    if provider_id is None or model is None:
        if chain is None:
            raise ValueError(
                "investigate_case needs either a provider chain or an explicit "
                "provider_id and model to compute the cache key"
            )
        head = chain.providers[0]
        provider_id = provider_id or head.provider_id
        model = model or head.model

    key = cache_key(
        snapshot,
        provider=provider_id,
        model=model,
        max_steps=config.max_steps,
        max_tool_calls_per_step=config.max_tool_calls_per_step,
        policy=policy,
    )

    cached = cache.load(key)
    if cached is not None:
        return InvestigationOutcome(trajectory=cached, cache_key=key, cache_hit=True)

    if replay_only:
        raise ReplayMissError(snapshot.case_id, key)

    if chain is None:
        raise ValueError("a live investigation needs a provider chain")

    trajectory = run_investigation(
        snapshot=snapshot,
        chain=chain,
        config=config,
        policy=policy,
        cache_key=key,
    )
    if write_cache:
        cache.store(key, trajectory)
    return InvestigationOutcome(trajectory=trajectory, cache_key=key, cache_hit=False)


__all__ = [
    "DEFAULT_FIXTURE_DIR",
    "CacheKeyInputs",
    "InvestigationOutcome",
    "ReplayMissError",
    "TrajectoryCache",
    "cache_key",
    "cache_key_inputs",
    "investigate_case",
]
