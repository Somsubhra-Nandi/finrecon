"""Versioned identities for everything that materially changes a trajectory.

DESIGN.md 6.2 caches "the **full agent trajectory** keyed by case ID plus
prompt-chain hash". A cache key that omits any of these would silently
serve a trajectory produced under a different prompt, a different tool
contract or a different step budget -- which is worse than no cache, because
it looks reproducible while being stale.

Bump the relevant constant whenever the thing it names changes in a way a
model could observe. The cache key derives from all of them
(:mod:`finrecon.agent.cache`), so a bump invalidates exactly the entries it
should and no others.
"""

from __future__ import annotations

from typing import Final

PROMPT_VERSION: Final = "investigator.v1"
"""Identity of the system prompt and case-briefing template."""

TOOL_SCHEMA_VERSION: Final = "tools.v1"
"""Identity of the read-only tool registry: names, arguments, output shapes."""

AGENT_LOOP_VERSION: Final = "loop.v1"
"""Identity of the loop's control flow: termination states, call bounds."""

CACHE_SCHEMA_VERSION: Final = "trajectory-cache.v1"
"""Identity of the on-disk trajectory record itself."""

VALIDATOR_VERSION: Final = "validator.v1"
"""Identity of the deterministic evidence predicates."""

POLICY_VERSION: Final = "policy.v1"
"""Identity of the deterministic policy gate and its declared thresholds."""

__all__ = [
    "AGENT_LOOP_VERSION",
    "CACHE_SCHEMA_VERSION",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "TOOL_SCHEMA_VERSION",
    "VALIDATOR_VERSION",
]
