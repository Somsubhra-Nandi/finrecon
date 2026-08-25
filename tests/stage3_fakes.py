"""Deterministic stand-ins for a model provider, used by every Stage-3 test.

Three kinds, for three jobs.

:class:`ScriptedProvider`
    Replays a fixed list of turns (or raises a fixed error). Used wherever a
    test needs one exact model behaviour: a malformed tool call, a
    hallucinated candidate ID, a model that never stops asking for tools.

:class:`FailingProvider`
    Raises one chosen :class:`ProviderError` on every call. Used to drive
    the fallback matrix -- 429, timeout, 5xx, unauthorized -- without a
    network.

:class:`MechanicalInvestigator`
    A crude but *deterministic* stand-in that plays the investigator role
    end to end: it reads the case briefing, enumerates plausible reference
    fragments out of the narration by two mechanical splits, and tests them
    one per step. It exists so the loop, the tools, the trajectory, the
    validator, the policy gate and the ledger can be exercised over real DEV
    cases at scale with no API key.

    **It is not a model, and nothing it produces is a model result.** It
    cannot read, so it brute-forces fragments where a model would recognise
    one; equally, it never fabricates, so it cannot demonstrate the failure
    modes the architecture is built to contain. Any number it produces
    measures the *plumbing*, and is labelled that way wherever it appears.

None of these ever touch the network. :class:`RecordingTransport` is the
seam for testing an adapter's wire translation without one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelResponse,
    ProviderError,
    TokenUsage,
    ToolCallRequest,
    ToolSpec,
)


def tool_call(name: str, arguments: dict | str, call_id: str = "call_1") -> ToolCallRequest:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCallRequest(call_id=call_id, tool_name=name, raw_arguments=raw)


def turn(
    *,
    text: str = "",
    calls: Iterable[ToolCallRequest] = (),
    provider: str = "fake",
    model: str = "fake-model-v1",
    usage: TokenUsage | None = None,
) -> ModelResponse:
    return ModelResponse(
        provider=provider,
        model=model,
        text=text,
        tool_calls=tuple(calls),
        usage=usage or TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
        latency_ms=1,
        finish_reason="stop" if not tuple(calls) else "tool_calls",
    )


class ScriptedProvider(ModelProvider):
    """Replays a fixed script of turns. Records every call it received."""

    def __init__(
        self,
        turns: Iterable[ModelResponse],
        *,
        provider_id: str = "fake",
        model: str = "fake-model-v1",
        repeat_last: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self._model = model
        self._turns = list(turns)
        self._repeat_last = repeat_last
        self.calls: list[tuple[tuple[ConversationMessage, ...], tuple[ToolSpec, ...]]] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        self.calls.append((messages, tools))
        index = len(self.calls) - 1
        if index < len(self._turns):
            response = self._turns[index]
        elif self._repeat_last and self._turns:
            response = self._turns[-1]
        else:
            raise AssertionError(
                f"{self.provider_id} was called {len(self.calls)} times but only "
                f"{len(self._turns)} turns were scripted"
            )
        return ModelResponse(
            provider=self.provider_id,
            model=self._model,
            text=response.text,
            tool_calls=response.tool_calls,
            usage=response.usage,
            latency_ms=response.latency_ms,
            finish_reason=response.finish_reason,
        )


class FailingProvider(ModelProvider):
    """Raises the same error on every call. The fallback matrix runs on this."""

    def __init__(self, error: ProviderError, *, provider_id: str, model: str = "failing") -> None:
        self.provider_id = provider_id
        self._model = model
        self._error = error
        self.call_count = 0

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        self.call_count += 1
        raise self._error


class ExplodingProvider(ModelProvider):
    """Fails the test if it is called at all. Proves replay makes zero calls."""

    provider_id = "must-not-be-called"

    def __init__(self, model: str = "must-not-be-called") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        raise AssertionError(
            "a provider was contacted during replay; replay must make zero provider calls"
        )


@dataclass
class RecordingTransport:
    """Stands in for ``post_json``: returns canned bodies, records requests.

    Captures the API key it was handed so a test can assert the adapter
    passes credentials correctly -- and, separately, that the credential
    never reaches a trajectory.
    """

    responses: list[Any] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)

    def __call__(self, **kwargs) -> dict:
        self.requests.append(kwargs)
        if not self.responses:
            raise AssertionError("RecordingTransport ran out of canned responses")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


# --- the mechanical investigator ------------------------------------------

_RUN_PATTERN = re.compile(r"[A-Za-z0-9_*#\-]+")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")

MIN_FRAGMENT_LENGTH = 4


def candidate_fragments(narration: str, limit: int = 6) -> tuple[str, ...]:
    """Plausible reference fragments, by two mechanical splits of the narration.

    Two passes, because the declared degradation vocabulary splits two ways:
    a masked or separator-altered reference survives as a run that *includes*
    ``*`` or ``-``, while a truncated or reordered one survives as a plain
    alphanumeric token. Neither split alone reaches both.

    Ordering is a deterministic score, not a guess at meaning: fragments
    carrying a mask character first, then those mixing letters and digits,
    then longest. A model would recognise the reference and skip all of
    this -- which is the point of the comparison.
    """
    seen: list[str] = []
    for pattern in (_RUN_PATTERN, _TOKEN_PATTERN):
        for match in pattern.finditer(narration):
            value = match.group(0)
            if len(value) >= MIN_FRAGMENT_LENGTH and value not in seen:
                seen.append(value)

    def score(fragment: str) -> tuple[int, int, int, str]:
        has_mask = any(ch in "*#" for ch in fragment)
        mixed = any(c.isalpha() for c in fragment) and any(c.isdigit() for c in fragment)
        return (0 if has_mask else 1, 0 if mixed else 1, -len(fragment), fragment)

    return tuple(sorted(seen, key=score)[:limit])


class MechanicalInvestigator(ModelProvider):
    """A deterministic, non-linguistic stand-in for an investigating model.

    Plan: one ``lookup_candidate_records`` to see what the candidates carry,
    then one ``compare_reference_fragment`` per plausible fragment, then
    stop. One call per fragment, not one per fragment per candidate: the
    comparison tool takes the fragment alone and fans it across the complete
    snapshot itself, so surfacing a fragment is the whole of the agent's
    contribution.
    """

    provider_id = "mechanical"

    def __init__(
        self,
        *,
        model: str = "mechanical-investigator-v1",
        fragment_limit: int = 6,
        fragment_source: Callable[[str, int], tuple[str, ...]] = candidate_fragments,
    ) -> None:
        self._model = model
        self._fragment_limit = fragment_limit
        self._fragment_source = fragment_source
        self.call_count = 0

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self, messages: tuple[ConversationMessage, ...], tools: tuple[ToolSpec, ...]
    ) -> ModelResponse:
        self.call_count += 1
        briefing = next(m.content for m in messages if m.role == "user")
        narration = _extract(briefing, "narration: ")
        candidate_ids = _extract_all(briefing, "candidate_id: ")
        fragments = self._fragment_source(narration, self._fragment_limit)

        # Step 1 is the lookup; steps 2..n walk the fragment list in order.
        done = sum(1 for m in messages if m.role == "tool")
        if done == 0:
            return turn(
                text="Looking at what the first candidate carries.",
                calls=[
                    tool_call(
                        "lookup_candidate_records",
                        {"candidate_id": candidate_ids[0]},
                        call_id=f"call_{self.call_count}",
                    )
                ],
                provider=self.provider_id,
                model=self._model,
            )

        index = done - 1
        if index < len(fragments):
            return turn(
                text=f"Testing narration fragment {fragments[index]!r}.",
                calls=[
                    tool_call(
                        "compare_reference_fragment",
                        {"fragment": fragments[index]},
                        call_id=f"call_{self.call_count}",
                    )
                ],
                provider=self.provider_id,
                model=self._model,
            )

        return turn(
            text=(
                "Tested every fragment the narration carries. "
                "I make no claim about which candidate is correct."
            ),
            provider=self.provider_id,
            model=self._model,
        )


def _extract(briefing: str, prefix: str) -> str:
    for line in briefing.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return ""


def _extract_all(briefing: str, marker: str) -> tuple[str, ...]:
    found = []
    for line in briefing.splitlines():
        stripped = line.strip().lstrip("- ")
        if stripped.startswith(marker):
            found.append(stripped[len(marker) :])
    return tuple(found)


__all__ = [
    "MIN_FRAGMENT_LENGTH",
    "ExplodingProvider",
    "FailingProvider",
    "MechanicalInvestigator",
    "RecordingTransport",
    "ScriptedProvider",
    "candidate_fragments",
    "tool_call",
    "turn",
]
