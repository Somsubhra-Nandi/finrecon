"""GoRouter -- an OpenAI-dialect routing gateway, third infrastructure fallback.

GoRouter exposes ``/chat/completions`` in the OpenAI dialect, so this adapter
is the shared translation in
:mod:`finrecon.agent.providers.openai_compatible` plus an endpoint, an
identity and two deliberate judgement calls. No agent loop, no tool parsing
and no argument handling is repeated here; there is nothing in this module a
second copy of that logic could hide in.

Strict tool schemas: off, on purpose
------------------------------------

The base class sends ``"strict": true`` on a qualifying function declaration.
This adapter sets :attr:`strict_tool_schema_supported` to ``False``.

Being OpenAI-*shaped* is not evidence of accepting the OpenAI strict schema
extension. ``strict`` is a constrained-decoding contract the serving path has
to implement, and a gateway that fans requests out to several upstreams may
honour it, ignore it, or reject the request outright as a 400 -- which the
transport layer correctly classifies as ``bad_request`` and which correctly
does *not* fall back, so an unverified assumption here would stop a run
rather than degrade it. We have no capability evidence for this endpoint, so
the flag stays off until there is some.

What does **not** change is the schema itself. The same JSON Schema is sent,
byte for byte, with only the ``strict`` claim withheld; declared properties,
``required`` and ``additionalProperties: false`` are identical to every other
provider's. And nothing downstream ever depended on strict mode: arguments
arrive as raw text, the duplicate-key decoder in
:mod:`finrecon.agent.tools` runs on every call, unknown candidate IDs are
refused against the immutable snapshot, and batch preflight stays atomic.
Strict mode was defence in depth; this is the run without that one layer, not
a weakened one. Flip it with ``strict_tool_schema=True`` when the endpoint has
been shown to accept it.

Usage telemetry: two names for one number
-----------------------------------------

GoRouter reports both name pairs, and they are observed to disagree. One live
response carried::

    prompt_tokens: 7382     completion_tokens: 8
    input_tokens:  7382     output_tokens:     0
    total_tokens:  7390     usage_source:      anthropic

:func:`finrecon.agent.providers.openai_compatible.normalize_usage` resolves
this by selection, not arithmetic: the canonical ``completion_tokens`` is
taken, the alias is a fallback only for an absent canonical name, the block is
kept verbatim in ``TokenUsage.raw``, and ``usage_source`` is lifted so a count
billed against an upstream is legible as one. Nothing reconciles the two
numbers, because there is no honest way to -- and a synthesized token count
would look exactly like a measured one.
"""

from __future__ import annotations

from finrecon.agent.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://gorouter.app/v1"
DEFAULT_MODEL = "claude-opus-5-thinking"
"""Default only. Overridden by ``GOROUTER_MODEL``. Verify before a live run.

Carrying a default is this project's convention, not an exception to it: every
adapter has one and every one is overridable, because the same model ID does
not exist across providers and a shared default produces a fallback that 404s
exactly when it is needed. It is not *silent* either -- ``describe_configuration``
prints the effective ID before a run starts, and the trajectory records both
what was requested and what answered.

Provider catalogues change faster than this repository does. This ID is the
one observed answering on this endpoint; check it against GoRouter's current
catalogue before a live run.
"""


class GoRouterProvider(OpenAICompatibleProvider):
    provider_id = "gorouter"

    strict_tool_schema_supported = False
    """No capability evidence for the strict schema extension on this endpoint.

    See the module docstring. The JSON schema is unchanged; only the claim
    about it is withheld.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        **kwargs,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url, **kwargs)


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "GoRouterProvider"]
