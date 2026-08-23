"""Stage 3 -- the bounded AI investigation layer.

The agent's authority, stated once and enforced structurally throughout this
package (DESIGN.md 4.1):

    The agent may enrich the case. It may not shrink it.

Concretely, nothing in this package can delete, hide, replace, rank or score
a candidate; mark a winner; resolve a case; write to the ledger; or mutate
the immutable Stage-2 snapshot. That is not a prompt instruction the model
is asked to respect. It is a property of the code: the tools receive one
frozen :class:`~finrecon.candidates.snapshot.CaseSnapshot` and no writable
object at all, every tool output is a closed Pydantic model with no verdict
field, and the deterministic validator reads the complete candidate set from
the snapshot over a path the agent never touches.

What the agent genuinely decides is *what to look at next* -- and that is a
real capability, because the sequence a truncated reference needs differs
from the one an unexplained variance needs, and hardcoding every path is the
brittleness the model is here to absorb.

Layout:

``version``      identities that feed the cache key
``prompt``       versioned system prompt + deterministic case briefing
``schemas``      Pydantic tool I/O contracts
``tools``        the four read-only tools and their access control
``providers``    provider-neutral model access, infra-only fallback
``loop``         the bounded state machine
``trajectory``   the complete replayable record
``cache``        trajectory fixtures and zero-call replay
"""

from finrecon.agent.cache import (
    InvestigationOutcome,
    ReplayMissError,
    TrajectoryCache,
    cache_key,
    cache_key_inputs,
    investigate_case,
)
from finrecon.agent.loop import DEFAULT_MAX_STEPS, LoopConfig, run_investigation
from finrecon.agent.trajectory import Trajectory
from finrecon.agent.version import (
    AGENT_LOOP_VERSION,
    CACHE_SCHEMA_VERSION,
    PROMPT_VERSION,
    TOOL_SCHEMA_VERSION,
)

__all__ = [
    "AGENT_LOOP_VERSION",
    "CACHE_SCHEMA_VERSION",
    "DEFAULT_MAX_STEPS",
    "InvestigationOutcome",
    "LoopConfig",
    "PROMPT_VERSION",
    "ReplayMissError",
    "TOOL_SCHEMA_VERSION",
    "Trajectory",
    "TrajectoryCache",
    "cache_key",
    "cache_key_inputs",
    "investigate_case",
    "run_investigation",
]
