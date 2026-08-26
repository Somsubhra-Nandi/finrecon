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

PROMPT_VERSION: Final = "investigator.v4"
"""Identity of the system prompt and case-briefing template.

``v3`` was a measured experiment: extra operational wording telling the model
that one invocation is one logical operation, that one fragment against two
candidates is two calls, and so on. It was run against the same fifty DEV T2
cases as ``v2`` and did not reduce malformed calls (17 -> 18) while raising
mean tokens by a quarter, so the wording is gone again.

This is ``v4`` rather than a return to ``v2`` because the prompt is not back
where it was: ``compare_reference_fragment`` no longer takes a candidate, so
the clause that told the model to test a fragment "against each candidate"
would now be instructing it to do work the tool does itself. A version whose
text differs must have an identity that differs, or a cache hit stops meaning
the model saw the same case.
"""

TOOL_SCHEMA_VERSION: Final = "tools.v3"
"""Identity of the read-only tool registry: names, arguments, output shapes.

``v3`` is the first change to the tools' actual *contract* rather than their
prose: ``compare_reference_fragment`` takes a fragment and no ``candidate_id``
and returns per-candidate comparisons over the complete snapshot. A cached
``v1`` or ``v2`` trajectory holds outputs in the old shape, so it must not be
served here.
"""

AGENT_LOOP_VERSION: Final = "loop.v2"
"""Identity of the loop's control flow: termination states, call bounds.

Deliberately unchanged. The termination states, the per-turn call bound and
the atomic reject-all rule are all exactly what they were; what changed is
which calls get written down, which is the record format below.
"""

CACHE_SCHEMA_VERSION: Final = "trajectory-cache.v3"
"""Identity of the on-disk trajectory record itself.

``v3`` adds the required ``status`` field on every tool invocation record and
the skipped records that make a rejected batch fully auditable. A ``v2``
fixture cannot be validated against the new model, so the key must differ.
"""

VALIDATOR_VERSION: Final = "validator.v2"
"""Identity of the deterministic evidence predicates.

``v2`` replaces the reference-identification rule. ``v1`` resolved a case when
some one fragment *the agent tested* reached exactly one candidate; ``v2``
resolves it when one candidate is the only one consistent with **every**
informative claim in the narration's complete deterministic closure
(:mod:`finrecon.evidence.closure`).

A semantic change in both directions, which is why it is a version rather than
a refactor:

* it **resolves** cases v1 could not, where two clues that are each ambiguous
  are jointly conclusive;
* it **refuses** cases v1 resolved, where a clue the agent did not test
  contradicts one it did.

The second is the safety half. Under v1, intersecting only what the agent
surfaced would let one narration prove three different candidates depending on
which pair of clues the agent happened to test -- the fishing-by-omission
channel of DESIGN.md 4.1, reappearing inside the conjunction. Proving over the
closure closes it, because a closed evidence set has nothing to leave out.

Because this constant is part of the trajectory cache key, the bump invalidates
every cached trajectory. That is the intended behaviour and not a cost to be
worked around: a v1 fixture was recorded against a different decision contract,
and serving it as a v2 result would attribute this rule's numbers to a run that
never met it.
"""

POLICY_VERSION: Final = "policy.v1"
"""Identity of the deterministic policy gate and its declared thresholds.

Deliberately unchanged by ``validator.v2``. The gate's rule is what it was --
exactly one surviving candidate, exact paise, no hard blocker, value policy
permits -- and its blocker vocabulary is unchanged too. What changed is how the
validator computes the candidate set the gate reads, which is the validator's
version to carry.

Keeping it at ``v1`` was a design constraint on v2, not an accident: the
contradictory case is reported to the gate as the *union* of the contradicting
claims, so it fires the existing ``ambiguous_reference_link`` rather than
needing a new blocker id. Evidence pointing at more than one candidate, and
therefore at none, is what that blocker already means.
"""

__all__ = [
    "AGENT_LOOP_VERSION",
    "CACHE_SCHEMA_VERSION",
    "POLICY_VERSION",
    "PROMPT_VERSION",
    "TOOL_SCHEMA_VERSION",
    "VALIDATOR_VERSION",
]
