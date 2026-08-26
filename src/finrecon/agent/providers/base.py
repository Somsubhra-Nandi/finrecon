"""The provider-neutral model interface, and the error taxonomy that gates fallback.

Two things live here, and the second matters more than the first.

**The interface.** :class:`ModelProvider` is one method wide. The agent loop
hands it a list of neutral messages plus the tool specifications and gets
back a :class:`ModelResponse`. No HTTP, no SDK object, no provider-shaped
JSON crosses that boundary -- adapters own their wire format completely, so
the loop cannot accidentally grow a dependency on one vendor's response
shape.

**The error taxonomy.** This is the load-bearing part. Falling back to a
second provider is only ever legitimate when the first provider failed to
*answer*. If it answered and the answer was poor -- a bad tool choice, a
hallucinated candidate ID, arguments that fail schema validation, an
investigation that ran out of steps -- that is the model's behaviour, and
retrying it somewhere else is not resilience. It is quietly resampling
until a model says something the system likes, which is the exact failure
mode DESIGN.md 4.2 rejects when it refuses to gate on model confidence.

So the taxonomy is drawn at "did the provider return a decodable answer?":

``ProviderInfrastructureError``
    Rate limit, quota exhaustion, timeout, connection failure, 5xx, or a
    response body that is not a decodable envelope at all. **Fallback is
    permitted.** These say nothing about the model's judgement.

``ProviderConfigurationError``
    401/403, or a provider selected without credentials. **No fallback.**
    A bad key is a deployment bug, and silently switching providers hides
    it. Providers with no key configured are skipped before the run starts,
    which is a different thing from failing over mid-run.

``ModelSemanticError``
    Raised *after* a successful decode, by the loop rather than by an
    adapter. **No fallback, ever.** Fails safe to escalation.

The classification is a property of the exception type, not of a string
match at the call site, so a new adapter cannot accidentally opt its
failures into cross-provider retry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolSpec:
    """A tool, described in the neutral form every adapter translates from."""

    name: str
    description: str
    parameters_json_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallRequest:
    """One tool invocation a model asked for. Arguments stay as raw text.

    The arguments are deliberately **not** parsed here. Parsing is
    validation, validation can fail, and a failure has to be recorded in the
    trajectory as the model's behaviour rather than swallowed inside a
    transport adapter.
    """

    call_id: str
    tool_name: str
    raw_arguments: str


@dataclass(frozen=True)
class ConversationMessage:
    """One neutral message. Adapters map this onto their own wire shape."""

    role: MessageRole
    content: str = ""
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class TokenUsage:
    """Normalized token counts, plus the provider's own usage block verbatim.

    The three counts are the normalized view every consumer reads. They are
    *selected* from what a provider reported, never computed: a gateway that
    reports two disagreeing names for the same quantity gets one of them
    picked by a declared rule (see
    :func:`finrecon.agent.providers.openai_compatible.normalize_usage`), and
    the disagreement stays visible in ``raw`` rather than being averaged,
    summed or otherwise invented away.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    usage_source: str | None = None
    """The provider's own attribution for the counts, when it reports one.

    Some gateways bill against an upstream and say so -- GoRouter returns
    ``usage_source: anthropic`` beside the counts. Recorded because a token
    count whose origin is a third party is a different fact from one the
    endpoint measured itself.
    """
    raw: dict[str, Any] | None = None
    """The provider's usage block exactly as it arrived, or ``None``.

    Kept so a normalization decision can always be audited against the
    numbers it was made from. Nothing downstream adjudicates on it.
    """

    def is_empty(self) -> bool:
        """True when no *count* was reported. Metadata alone does not count.

        Deliberately unchanged by the two fields above: a usage block that
        carried an attribution and no numbers is still an absence of token
        telemetry, and callers asking "did this provider report usage?" mean
        the counts.
        """
        return (
            self.input_tokens is None
            and self.output_tokens is None
            and self.total_tokens is None
        )


@dataclass(frozen=True)
class ModelResponse:
    """One decoded model turn, in neutral form."""

    provider: str
    model: str
    """The model identifier this adapter *asked* for. Configuration, not an echo."""
    text: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int | None = None
    finish_reason: str | None = None
    transport_attempts: int = 1
    """How many HTTP attempts this provider needed, including the successful one."""
    reported_model: str | None = None
    """The model identifier the provider's response body claimed, if any.

    Distinct from ``model`` on purpose. A routing gateway may resolve an
    alias to something else -- ``claude-opus-5-thinking`` requested,
    ``claude-opus-5`` returned -- and a trajectory that recorded only the
    request would attribute the run to a model that never answered it.
    ``None`` when the provider does not report one; never back-filled from
    the request, because "it did not say" and "it said the same thing" are
    different facts.
    """


# --- Error taxonomy -------------------------------------------------------


class ProviderError(RuntimeError):
    """Base for anything that went wrong reaching a model provider."""

    def __init__(self, provider: str, kind: str, message: str) -> None:
        super().__init__(f"{provider}: {kind}: {message}")
        self.provider = provider
        self.kind = kind
        self.message = message

    @property
    def permits_provider_fallback(self) -> bool:
        raise NotImplementedError


class ProviderInfrastructureError(ProviderError):
    """The provider did not deliver a decodable answer. Fallback is permitted."""

    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    PROTOCOL_ERROR = "protocol_error"
    """Transport-level corruption: the body is not a decodable provider envelope.

    Distinct from a decodable envelope carrying poor model output, which is
    :class:`ModelSemanticError` and never triggers fallback.
    """

    RETRYABLE_ON_SAME_PROVIDER = frozenset({TIMEOUT, NETWORK_ERROR, SERVER_ERROR})
    """Kinds where one bounded retry against the same provider is sensible.

    A rate limit or an exhausted quota will not clear in milliseconds, so
    retrying the same provider merely burns wall clock; those go straight to
    the next provider.
    """

    @property
    def permits_provider_fallback(self) -> bool:
        return True

    @property
    def permits_transport_retry(self) -> bool:
        return self.kind in self.RETRYABLE_ON_SAME_PROVIDER


class ProviderConfigurationError(ProviderError):
    """Credentials or configuration are wrong. Never falls back."""

    MISSING_CREDENTIALS = "missing_credentials"
    UNAUTHORIZED = "unauthorized"
    BAD_REQUEST = "bad_request"

    @property
    def permits_provider_fallback(self) -> bool:
        return False


class ModelSemanticError(ProviderError):
    """The provider answered; the answer was unusable. Never falls back.

    Raised by the loop, not by an adapter: by the time this can be
    diagnosed, transport has already succeeded, so the failure belongs to
    the model. Fails safe to escalation.
    """

    INVALID_TOOL_NAME = "invalid_tool_name"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    UNKNOWN_CANDIDATE = "unknown_candidate"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"

    @property
    def permits_provider_fallback(self) -> bool:
        return False


class ModelProvider(ABC):
    """One model provider, reduced to a single neutral call."""

    #: Stable identifier recorded on every trajectory step.
    provider_id: str = "abstract"

    @property
    @abstractmethod
    def model(self) -> str:
        """The exact model identifier this adapter will call."""

    @abstractmethod
    def complete(
        self,
        messages: tuple[ConversationMessage, ...],
        tools: tuple[ToolSpec, ...],
    ) -> ModelResponse:
        """One model turn. Raises a :class:`ProviderError` subclass on failure."""

    def describe(self) -> dict[str, str]:
        return {"provider": self.provider_id, "model": self.model}


__all__ = [
    "ConversationMessage",
    "MessageRole",
    "ModelProvider",
    "ModelResponse",
    "ModelSemanticError",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderInfrastructureError",
    "TokenUsage",
    "ToolCallRequest",
    "ToolSpec",
]
