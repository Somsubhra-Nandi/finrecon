"""Gemini -- the second infrastructure fallback.

Gemini does not speak the OpenAI dialect, which is precisely why the
provider abstraction earns its keep: every difference below is absorbed
here and none of it reaches the agent loop.

Four translations are needed:

===================  ==================================================
Neutral              Gemini
===================  ==================================================
``system`` message   ``systemInstruction``, hoisted out of the turn list
``assistant``        role ``model``
``tool`` result      a ``user`` turn carrying a ``functionResponse`` part
tool specification   ``tools[0].functionDeclarations``
===================  ==================================================

The Generate Content dialect used here may omit tool-call identifiers, so
synthetic call IDs keep the neutral trajectory shape. For a multi-call turn,
function responses are grouped into one user turn and kept in request order,
matching Gemini's parallel-function-call history format. Execution remains
serial and deterministic in the loop.

The API key travels in the ``x-goog-api-key`` header rather than the
documented ``?key=`` query parameter. Both work; a header keeps the secret
out of the URL, and the URL is the part of a request that ends up in logs.
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
"""Default only. Overridden by ``GEMINI_MODEL``. Verify before a live run."""

_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {"additionalProperties", "$schema", "definitions", "$defs", "title", "default"}
)
"""JSON Schema keywords Gemini's ``functionDeclarations`` subset rejects."""


def _gemini_schema(schema: Any) -> Any:
    """Recursively drop schema keywords Gemini does not accept.

    Only *removes* constraints it cannot express; it never adds or widens
    one. A dropped ``additionalProperties: false`` means Gemini may emit an
    extra field, which the Pydantic input model then rejects as a
    validation failure -- caught, recorded, and escalated, exactly like any
    other malformed call.
    """
    if isinstance(schema, dict):
        return {
            key: _gemini_schema(value)
            for key, value in schema.items()
            if key not in _UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    return schema


class GeminiProvider(ModelProvider):
    provider_id = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        temperature: float = 0.0,
        transport=post_json,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    @property
    def endpoint(self) -> str:
        return f"{self._base_url}/models/{self._model}:generateContent"

    # --- request translation --------------------------------------------

    def build_payload(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> dict[str, Any]:
        system_texts: list[str] = []
        contents: list[dict[str, Any]] = []

        index = 0
        while index < len(messages):
            message = messages[index]
            if message.role == "system":
                if message.content:
                    system_texts.append(message.content)
                index += 1
                continue
            if message.role == "user":
                contents.append({"role": "user", "parts": [{"text": message.content}]})
                index += 1
                continue
            if message.role == "assistant":
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                for call in message.tool_calls:
                    parts.append(
                        {
                            "functionCall": {
                                "name": call.tool_name,
                                "args": _loads_or_empty(call.raw_arguments),
                            }
                        }
                    )
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
                index += 1
                continue
            if message.role == "tool":
                parts = []
                while index < len(messages) and messages[index].role == "tool":
                    tool_message = messages[index]
                    parts.append(
                        {
                            "functionResponse": {
                                "name": tool_message.tool_name or "tool",
                                "response": _loads_or_wrap(tool_message.content),
                            }
                        }
                    )
                    index += 1
                contents.append(
                    {
                        "role": "user",
                        "parts": parts,
                    }
                )
                continue
            index += 1

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": self._temperature},
        }
        if system_texts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_texts)}]}
        if tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": _gemini_schema(tool.parameters_json_schema),
                        }
                        for tool in tools
                    ]
                }
            ]
        return payload

    # --- response translation -------------------------------------------

    def parse_response(
        self, body: dict[str, Any]
    ) -> tuple[str, tuple[ToolCallRequest, ...], str | None]:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "response envelope carries no candidates",
            )
        first = candidates[0]
        if not isinstance(first, dict):
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "candidates[0] is not an object",
            )
        content = first.get("content")
        if not isinstance(content, dict):
            raise ProviderInfrastructureError(
                self.provider_id,
                ProviderInfrastructureError.PROTOCOL_ERROR,
                "candidates[0].content is missing",
            )

        texts: list[str] = []
        calls: list[ToolCallRequest] = []
        for index, part in enumerate(content.get("parts") or []):
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                texts.append(part["text"])
            call = part.get("functionCall")
            if isinstance(call, dict):
                name = call.get("name")
                args = call.get("args")
                calls.append(
                    ToolCallRequest(
                        call_id=f"gemini_call_{index}",
                        tool_name=name if isinstance(name, str) else "",
                        raw_arguments=json.dumps(args if args is not None else {}),
                    )
                )

        finish = first.get("finishReason")
        return "".join(texts), tuple(calls), finish if isinstance(finish, str) else None

    def parse_usage(self, body: dict[str, Any]) -> TokenUsage:
        usage = body.get("usageMetadata")
        if not isinstance(usage, dict):
            return TokenUsage()

        def as_int(key: str) -> int | None:
            value = usage.get(key)
            return int(value) if isinstance(value, int) else None

        return TokenUsage(
            input_tokens=as_int("promptTokenCount"),
            output_tokens=as_int("candidatesTokenCount"),
            total_tokens=as_int("totalTokenCount"),
        )

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
            auth_header="x-goog-api-key",
            auth_prefix="",
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


def _loads_or_empty(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _loads_or_wrap(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"result": raw}
    return value if isinstance(value, dict) else {"result": value}


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "GeminiProvider"]
