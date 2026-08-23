"""Ordered provider chain with fallback restricted to infrastructure failures.

The chain is short and the rule it enforces is shorter:

    Move to the next provider **only** when the current one failed to
    return a decodable answer.

Everything else -- a poor tool choice, a hallucinated candidate ID, an
argument that fails schema validation, an investigation that runs out of
steps, an answer that simply does not prove anything -- is the model's
behaviour, and the correct response to it is the safe one: record it and
escalate. Retrying such a case on a second provider is resampling until
some model says something the system likes, which is DESIGN.md 4.2's
rejected pattern wearing a resilience costume.

Enforcement is structural rather than conventional. The chain never
inspects a message string to decide; it asks the raised exception whether
it permits fallback (:mod:`finrecon.agent.providers.base`), and only
:class:`ProviderInfrastructureError` answers yes. A future adapter cannot
opt its semantic failures into cross-provider retry without deliberately
raising the wrong exception type.

Two bounded escalations, in order:

1. **Transport retry, same provider.** For timeouts, connection failures
   and 5xx -- outcomes that plausibly clear in a second -- one bounded
   retry against the same provider, recorded as an attempt. Rate limits and
   exhausted quotas are excluded: those do not clear in milliseconds, so
   retrying merely burns wall clock before the inevitable fallback.
2. **Provider fallback.** The next configured provider is tried, and the
   reason is recorded on the step.

If every provider fails on infrastructure, the chain raises the last error
and the loop terminates with ``provider_infrastructure_failure``, which the
policy gate treats as a hard blocker. A case whose evidence could not be
gathered is escalated, never guessed.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ProviderInfrastructureError,
    ToolSpec,
)

DEFAULT_TRANSPORT_RETRIES = 1
"""Extra attempts against the *same* provider for a transient transport error.

One, not three. The step budget already bounds the run, and a chain that
retries aggressively turns a provider outage into a long stall rather than
a fast, honest escalation.
"""


@dataclass(frozen=True)
class ProviderAttempt:
    """One attempt against one provider, successful or not. Recorded verbatim."""

    provider: str
    model: str
    attempt: int
    """1-based attempt index against this provider."""
    outcome: str
    """``"success"`` or the :class:`ProviderError` ``kind``."""
    error_class: str | None = None
    detail: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome == "success"


@dataclass(frozen=True)
class ChainResult:
    """A model turn plus the full record of what it took to get one."""

    response: ModelResponse
    attempts: tuple[ProviderAttempt, ...]

    @property
    def fallback_used(self) -> bool:
        return any(not attempt.succeeded for attempt in self.attempts)

    @property
    def provider_fallback_used(self) -> bool:
        """True only when a *different* provider than the first was used."""
        if not self.attempts:
            return False
        return self.attempts[0].provider != self.response.provider

    @property
    def fallback_reason(self) -> str | None:
        """Why the first provider was left, or ``None`` if it answered."""
        failures = [a for a in self.attempts if not a.succeeded]
        return failures[0].outcome if failures else None


class AllProvidersFailedError(RuntimeError):
    """Every configured provider failed on infrastructure grounds."""

    def __init__(self, attempts: tuple[ProviderAttempt, ...], last: ProviderError) -> None:
        summary = ", ".join(f"{a.provider}:{a.outcome}" for a in attempts)
        super().__init__(f"all providers failed ({summary})")
        self.attempts = attempts
        self.last_error = last


class ProviderChain:
    """An ordered list of providers, tried under the infrastructure-only rule."""

    def __init__(
        self,
        providers: tuple[ModelProvider, ...],
        *,
        transport_retries: int = DEFAULT_TRANSPORT_RETRIES,
    ) -> None:
        if not providers:
            raise ValueError("a provider chain needs at least one provider")
        if transport_retries < 0:
            raise ValueError("transport_retries must not be negative")
        self._providers = providers
        self._transport_retries = transport_retries

    @property
    def providers(self) -> tuple[ModelProvider, ...]:
        return self._providers

    def describe(self) -> tuple[str, ...]:
        return tuple(f"{p.provider_id}:{p.model}" for p in self._providers)

    def complete(
        self,
        messages: tuple[ConversationMessage, ...],
        tools: tuple[ToolSpec, ...],
    ) -> ChainResult:
        attempts: list[ProviderAttempt] = []
        last_error: ProviderError | None = None

        for provider in self._providers:
            for attempt_index in range(1, self._transport_retries + 2):
                try:
                    response = provider.complete(messages, tools)
                except ProviderError as error:
                    last_error = error
                    attempts.append(
                        ProviderAttempt(
                            provider=provider.provider_id,
                            model=provider.model,
                            attempt=attempt_index,
                            outcome=error.kind,
                            error_class=type(error).__name__,
                            detail=error.message,
                        )
                    )
                    if not error.permits_provider_fallback:
                        # Configuration and semantic failures stop the chain
                        # dead. Trying another provider would hide the bug.
                        raise
                    retryable = (
                        isinstance(error, ProviderInfrastructureError)
                        and error.permits_transport_retry
                        and attempt_index <= self._transport_retries
                    )
                    if retryable:
                        continue
                    break
                else:
                    attempts.append(
                        ProviderAttempt(
                            provider=provider.provider_id,
                            model=provider.model,
                            attempt=attempt_index,
                            outcome="success",
                        )
                    )
                    return ChainResult(
                        response=dataclasses.replace(
                            response, transport_attempts=attempt_index
                        ),
                        attempts=tuple(attempts),
                    )

        assert last_error is not None  # unreachable: the chain is non-empty
        raise AllProvidersFailedError(tuple(attempts), last_error)


__all__ = [
    "DEFAULT_TRANSPORT_RETRIES",
    "AllProvidersFailedError",
    "ChainResult",
    "ProviderAttempt",
    "ProviderChain",
]
