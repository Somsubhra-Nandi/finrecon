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


class OpenAICompatibleProvider(ModelProvider):
    """Shared translation for the OpenAI chat-completions tool-call dialect."""

    provider_id = "openai-compatible"
    default_endpoint = "/chat/completions"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = 0.0,
        extra_headers: dict[str, str] | None = None,
        transport=post_json,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._extra_headers = dict(extra_headers or {})
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
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_json_schema,
            },
        }

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
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return TokenUsage()

        def as_int(key: str) -> int | None:
            value = usage.get(key)
            return int(value) if isinstance(value, int) else None

        return TokenUsage(
            input_tokens=as_int("prompt_tokens"),
            output_tokens=as_int("completion_tokens"),
            total_tokens=as_int("total_tokens"),
        )

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
        )


__all__ = ["OpenAICompatibleProvider"]
