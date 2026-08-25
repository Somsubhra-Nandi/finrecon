"""Adapter for providers speaking the OpenAI ``/chat/completions`` tool-call dialect.

Both OpenRouter and Groq expose that dialect, so the wire translation is
written once here and subclassed twice rather than copy-pasted. It is still
adapter code: nothing above :mod:`finrecon.agent.providers.base` sees a
``choices[0].message.tool_calls`` path, and the two subclasses differ only
in endpoint, default model and identity.

One judgement call is encoded below. A response whose *envelope* is missing
-- no ``choices``, or a ``choices[0]`` that is not an object -- is raised as
:class:`ProviderInfrastructureError` with kind ``protocol_error``, because
the provider did not deliver a model turn at all and another provider
plausibly would. A response whose envelope is intact but whose *content* is
useless -- an empty answer, a nonsense tool name, arguments that will not
parse -- is returned normally and handled by the loop as model behaviour.
That is the line DESIGN.md 4.2 draws, and it is drawn here rather than at
the call site so no adapter can quietly move it.

Strict tool schemas
-------------------

Function declarations are sent with ``"strict": true`` when the provider
speaks that dialect and the tool's schema qualifies for it. Where a provider
honours it, argument generation is constrained to the declared grammar, and
a duplicate object key -- the dominant observed malformed-call shape -- stops
being expressible rather than merely being rejected afterwards.

It is defence in depth and nothing more. Nothing downstream may assume it
worked: the provider may ignore the flag, a gateway may strip it, and a model
may be served through a path that does not support it. Arguments still arrive
as raw text, the strict duplicate-key decoder in
:mod:`finrecon.agent.tools` still runs on every call, and batch preflight is
unchanged. The local distrust boundary is the one that decides.

Gateway telemetry
-----------------

Two facts a routing gateway reports that a first-party endpoint does not, both
handled here once rather than per adapter:

**The model that actually answered.** ``body["model"]`` is lifted into
``ModelResponse.reported_model`` and kept separate from the requested ID. A
gateway may resolve an alias -- ``claude-opus-5-thinking`` in,
``claude-opus-5`` out -- and a trajectory that recorded only the request would
name a model that never ran.

**Disagreeing usage names.** The OpenAI dialect names token counts
``prompt_tokens`` / ``completion_tokens``; some gateways emit an
``input_tokens`` / ``output_tokens`` pair alongside, and the two pairs are
observed to *disagree* (GoRouter live: ``completion_tokens: 8`` beside
``output_tokens: 0``). :func:`normalize_usage` therefore selects rather than
reconciles -- canonical name first, alias only as a fallback for an absent
one -- and keeps the provider's block verbatim so the choice is auditable.
Nothing is summed, averaged or back-filled: an invented token count is worse
than a missing one, because it looks like a measurement.
"""

from __future__ import annotations

import json
import time
from typing import Any

from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelResponse,
    ProviderInfrastructureError,
    TokenUsage,
    ToolCallRequest,
    ToolSpec,
)
from finrecon.agent.providers.transport import DEFAULT_TIMEOUT_SECONDS, post_json


def supports_strict_tool_schema(schema: dict[str, Any]) -> bool:
    """Whether one JSON schema qualifies for OpenAI-dialect strict mode.

    Strict mode is only valid for a closed object schema in which every
    declared property is required. Sending ``strict: true`` beside a schema
    that does not satisfy that is a request error, so an unqualified schema
    is sent *without* the flag rather than with a claim the provider will
    reject. Every current tool input qualifies; this check exists so that
    adding one which does not degrades to ordinary tool calling instead of
    breaking every live run.
    """
    if schema.get("type") != "object":
        return False
    if schema.get("additionalProperties") is not False:
        return False
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return False
    required = schema.get("required")
    if not isinstance(required, list):
        return False
    return set(required) == set(properties)


USAGE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "input_tokens": ("prompt_tokens", "input_tokens"),
    "output_tokens": ("completion_tokens", "output_tokens"),
    "total_tokens": ("total_tokens",),
}
"""Normalized field -> the wire names that may carry it, in precedence order.

The OpenAI-canonical name wins. The alias is a fallback for a canonical name
that is *absent*, never a tie-breaker for one that is present and disagrees:
picking by a fixed rule keeps the normalized number traceable to a single
reported value, and ``TokenUsage.raw`` carries the rest.
"""


def normalize_usage(body: dict[str, Any]) -> TokenUsage:
    """Translate a chat-completions ``usage`` block into neutral token counts.

    Selects by :data:`USAGE_FIELD_ALIASES`, preserves the block verbatim, and
    lifts a ``usage_source`` attribution when the provider reports one. An
    absent or non-object block is an empty :class:`TokenUsage`, not an error:
    a provider that does not meter is not a provider that failed.
    """
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return TokenUsage()

    def selected(names: tuple[str, ...]) -> int | None:
        for name in names:
            value = usage.get(name)
            # ``isinstance(value, int)`` only: a float or a numeric string is
            # a shape this layer does not recognise, and coercing one would
            # be the adapter deciding what the provider meant.
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    source = usage.get("usage_source")
    return TokenUsage(
        input_tokens=selected(USAGE_FIELD_ALIASES["input_tokens"]),
        output_tokens=selected(USAGE_FIELD_ALIASES["output_tokens"]),
        total_tokens=selected(USAGE_FIELD_ALIASES["total_tokens"]),
        usage_source=source if isinstance(source, str) else None,
        raw=dict(usage),
    )


class OpenAICompatibleProvider(ModelProvider):
    """Shared translation for the OpenAI chat-completions tool-call dialect."""

    provider_id = "openai-compatible"
    default_endpoint = "/chat/completions"

    strict_tool_schema_supported: bool = True
    """Whether this dialect accepts ``strict`` on a function declaration.

    A class-level capability, not a runtime probe. Both subclasses here speak
    the OpenAI dialect; a future adapter whose gateway rejects the field sets
    this to ``False`` and keeps working. Gemini does not inherit from this
    class at all, so its payload is untouched by anything in this module.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = 0.0,
        extra_headers: dict[str, str] | None = None,
        strict_tool_schema: bool | None = None,
        transport=post_json,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._extra_headers = dict(extra_headers or {})
        self._strict_tool_schema = (
            self.strict_tool_schema_supported
            if strict_tool_schema is None
            else strict_tool_schema
        )
        # Injected so tests can drive the adapter's *translation* without a
        # network, and without monkeypatching a module global.
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}{self.default_endpoint}"

    # --- request translation --------------------------------------------

    def _message_payload(self, message: ConversationMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content,
            }
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.call_id,
                    "type": "function",
                    "function": {
                        "name": call.tool_name,
                        "arguments": call.raw_arguments,
                    },
                }
                for call in message.tool_calls
            ]
        return payload

    def _tool_payload(self, tool: ToolSpec) -> dict[str, Any]:
        function: dict[str, Any] = {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_json_schema,
        }
        if self._strict_tool_schema and supports_strict_tool_schema(
            tool.parameters_json_schema
        ):
            function["strict"] = True
        return {"type": "function", "function": function}

    def build_payload(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message_payload(m) for m in messages],
            "temperature": self._temperature,
        }
        if tools:
            payload["tools"] = [self._tool_payload(t) for t in tools]
            payload["tool_choice"] = "auto"
        return payload

    # --- response translation -------------------------------------------

    def parse_response(self, body: dict[str, Any]) -> tuple[str, tuple[ToolCallRequest, ...], str | None]:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "response envelope carries no choices",
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "choices[0] is not an object",
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "choices[0].message is missing",
            )

        content = message.get("content")
        text = content if isinstance(content, str) else ""

        calls: list[ToolCallRequest] = []
        raw_calls = message.get("tool_calls")
        if isinstance(raw_calls, list):
            for index, entry in enumerate(raw_calls):
                if not isinstance(entry, dict):
                    continue
                function = entry.get("function")
                if not isinstance(function, dict):
                    continue
                name = function.get("name")
                arguments = function.get("arguments")
                # Both are kept exactly as the model produced them. A bad
                # name or unparsable arguments is a *semantic* failure the
                # loop must see and record, not something to repair here.
                calls.append(
                    ToolCallRequest(
                        call_id=str(entry.get("id") or f"call_{index}"),
                        tool_name=name if isinstance(name, str) else "",
                        raw_arguments=(
                            arguments
                            if isinstance(arguments, str)
                            else json.dumps(arguments if arguments is not None else {})
                        ),
                    )
                )

        finish = first.get("finish_reason")
        return text, tuple(calls), finish if isinstance(finish, str) else None

    def parse_usage(self, body: dict[str, Any]) -> TokenUsage:
        return normalize_usage(body)

    def parse_reported_model(self, body: dict[str, Any]) -> str | None:
        """The model the response body names, or ``None`` if it names none.

        Never defaulted to the requested ID. See
        :attr:`finrecon.agent.providers.base.ModelResponse.reported_model`.
        """
        reported = body.get("model")
        if not isinstance(reported, str):
            return None
        reported = reported.strip()
        return reported or None

    # --- the one public call ---------------------------------------------

    def complete(
        self,
        messages: tuple[ConversationMessage, ...],
        tools: tuple[ToolSpec, ...],
    ) -> ModelResponse:
        payload = self.build_payload(messages, tools)
        started = time.perf_counter()
        body = self._transport(
            provider=self.provider_id,
            url=self.endpoint,
            payload=payload,
            api_key=self._api_key,
            extra_headers=self._extra_headers,
            timeout_seconds=self._timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text, calls, finish_reason = self.parse_response(body)
        return ModelResponse(
            provider=self.provider_id,
            model=self._model,
            text=text,
            tool_calls=calls,
            usage=self.parse_usage(body),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            reported_model=self.parse_reported_model(body),
        )


__all__ = [
    "USAGE_FIELD_ALIASES",
    "OpenAICompatibleProvider",
    "normalize_usage",
    "supports_strict_tool_schema",
]
