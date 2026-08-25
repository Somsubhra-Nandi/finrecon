"""Provider configuration, read from the environment and nowhere else.

Two rules, both structural.

**Secrets come from the environment.** No key is defaulted, no key is
written to a file by this module, no key appears in a trajectory, a log
line, an exception message or a ``repr``. :func:`describe_configuration`
exists so a run can print exactly what it is about to do -- provider order,
model IDs, which credentials are present -- without printing a credential.

**Models are configuration, not code.** Every provider's model ID has an
environment override. The same model ID does not exist across providers, so
there is no shared default and no attempt to translate one, which is why a
fallback records a different model rather than pretending it ran the same
one.

A provider with no credential is *not configured* and is skipped when the
chain is built. That is deliberately different from a provider that has a
credential and rejects it: the first is an absent option, the second is a
deployment bug that must surface rather than fail over.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from finrecon.agent.providers import gemini, gorouter, groq, openrouter
from finrecon.agent.providers.base import ModelProvider, ProviderConfigurationError
from finrecon.agent.providers.chain import DEFAULT_TRANSPORT_RETRIES, ProviderChain
from finrecon.agent.providers.transport import DEFAULT_TIMEOUT_SECONDS

DEFAULT_PROVIDER_ORDER: tuple[str, ...] = ("openrouter", "groq", "gemini", "gorouter")
"""Operational order: OpenRouter primary, then Groq, Gemini and GoRouter.

Order is availability policy, not a quality ranking. Override with
``FINRECON_PROVIDER_ORDER`` as a comma-separated list.

GoRouter was appended rather than inserted. The first three keep the exact
positions they had, so no existing deployment changes which provider answers
first; and a provider with no credential is skipped before the run starts, so
adding a fourth slot is inert until ``GOROUTER_API_KEY`` is set. It is in the
default order at all -- rather than reachable only through
``FINRECON_PROVIDER_ORDER`` -- because a first-class provider whose credential
alone is configured must produce a run, not a "no provider credentials found"
refusal.
"""

ENV_PROVIDER_ORDER = "FINRECON_PROVIDER_ORDER"
ENV_TIMEOUT = "FINRECON_AGENT_TIMEOUT_SECONDS"
ENV_TRANSPORT_RETRIES = "FINRECON_AGENT_TRANSPORT_RETRIES"


@dataclass(frozen=True)
class ProviderSlot:
    """Everything needed to build one provider, minus the secret itself."""

    provider_id: str
    api_key_env: str
    model_env: str
    base_url_env: str
    default_model: str
    default_base_url: str
    factory: Callable[..., ModelProvider]


PROVIDER_SLOTS: dict[str, ProviderSlot] = {
    "openrouter": ProviderSlot(
        provider_id="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        model_env="OPENROUTER_MODEL",
        base_url_env="OPENROUTER_BASE_URL",
        default_model=openrouter.DEFAULT_MODEL,
        default_base_url=openrouter.DEFAULT_BASE_URL,
        factory=openrouter.OpenRouterProvider,
    ),
    "groq": ProviderSlot(
        provider_id="groq",
        api_key_env="GROQ_API_KEY",
        model_env="GROQ_MODEL",
        base_url_env="GROQ_BASE_URL",
        default_model=groq.DEFAULT_MODEL,
        default_base_url=groq.DEFAULT_BASE_URL,
        factory=groq.GroqProvider,
    ),
    "gemini": ProviderSlot(
        provider_id="gemini",
        api_key_env="GEMINI_API_KEY",
        model_env="GEMINI_MODEL",
        base_url_env="GEMINI_BASE_URL",
        default_model=gemini.DEFAULT_MODEL,
        default_base_url=gemini.DEFAULT_BASE_URL,
        factory=gemini.GeminiProvider,
    ),
    "gorouter": ProviderSlot(
        provider_id="gorouter",
        api_key_env="GOROUTER_API_KEY",
        model_env="GOROUTER_MODEL",
        base_url_env="GOROUTER_BASE_URL",
        default_model=gorouter.DEFAULT_MODEL,
        default_base_url=gorouter.DEFAULT_BASE_URL,
        factory=gorouter.GoRouterProvider,
    ),
}


def provider_order(env: dict[str, str] | None = None) -> tuple[str, ...]:
    source = os.environ if env is None else env
    raw = source.get(ENV_PROVIDER_ORDER, "")
    if not raw.strip():
        return DEFAULT_PROVIDER_ORDER
    order = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    unknown = [p for p in order if p not in PROVIDER_SLOTS]
    if unknown:
        raise ProviderConfigurationError(
            "chain",
            ProviderConfigurationError.BAD_REQUEST,
            f"unknown provider(s) in {ENV_PROVIDER_ORDER}: {unknown}",
        )
    return order


def configured_provider_ids(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Providers that have a credential present, in the configured order."""
    source = os.environ if env is None else env
    return tuple(
        pid
        for pid in provider_order(env)
        if (source.get(PROVIDER_SLOTS[pid].api_key_env) or "").strip()
    )


def build_provider(
    provider_id: str,
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> ModelProvider:
    """Build one provider. Raises if its credential is absent."""
    source = os.environ if env is None else env
    slot = PROVIDER_SLOTS[provider_id]
    api_key = (source.get(slot.api_key_env) or "").strip()
    if not api_key:
        raise ProviderConfigurationError(
            provider_id,
            ProviderConfigurationError.MISSING_CREDENTIALS,
            f"{slot.api_key_env} is not set",
        )
    kwargs = {
        "api_key": api_key,
        "model": (source.get(slot.model_env) or "").strip() or slot.default_model,
        "base_url": (source.get(slot.base_url_env) or "").strip() or slot.default_base_url,
        "timeout_seconds": timeout_seconds
        if timeout_seconds is not None
        else _timeout_seconds(source),
    }
    return slot.factory(**kwargs)


def build_chain(env: dict[str, str] | None = None) -> ProviderChain:
    """Build the chain from every configured provider, in order.

    Raises :class:`ProviderConfigurationError` when nothing is configured --
    a loud failure, because the alternative is a "run" that silently
    escalated every case for want of a key and looked like a result.
    """
    source = os.environ if env is None else env
    ids = configured_provider_ids(env)
    if not ids:
        expected = ", ".join(PROVIDER_SLOTS[p].api_key_env for p in provider_order(env))
        raise ProviderConfigurationError(
            "chain",
            ProviderConfigurationError.MISSING_CREDENTIALS,
            f"no provider credentials found; set one of: {expected}",
        )
    providers = tuple(build_provider(pid, env=env) for pid in ids)
    return ProviderChain(providers, transport_retries=_transport_retries(source))


def describe_configuration(env: dict[str, str] | None = None) -> dict[str, object]:
    """A printable description of the provider setup. Contains no secret."""
    source = os.environ if env is None else env
    rows = []
    for pid in provider_order(env):
        slot = PROVIDER_SLOTS[pid]
        rows.append(
            {
                "provider": pid,
                "model": (source.get(slot.model_env) or "").strip() or slot.default_model,
                "base_url": (source.get(slot.base_url_env) or "").strip()
                or slot.default_base_url,
                "credential_env": slot.api_key_env,
                "credential_present": bool((source.get(slot.api_key_env) or "").strip()),
            }
        )
    return {
        "order": list(provider_order(env)),
        "configured": list(configured_provider_ids(env)),
        "timeout_seconds": _timeout_seconds(source),
        "transport_retries": _transport_retries(source),
        "providers": rows,
    }


def _timeout_seconds(source) -> float:
    raw = (source.get(ENV_TIMEOUT) or "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ProviderConfigurationError(
            "chain", ProviderConfigurationError.BAD_REQUEST, f"{ENV_TIMEOUT}={raw!r} is not a number"
        ) from None
    if value <= 0:
        raise ProviderConfigurationError(
            "chain", ProviderConfigurationError.BAD_REQUEST, f"{ENV_TIMEOUT} must be positive"
        )
    return value


def _transport_retries(source) -> int:
    raw = (source.get(ENV_TRANSPORT_RETRIES) or "").strip()
    if not raw:
        return DEFAULT_TRANSPORT_RETRIES
    try:
        value = int(raw)
    except ValueError:
        raise ProviderConfigurationError(
            "chain",
            ProviderConfigurationError.BAD_REQUEST,
            f"{ENV_TRANSPORT_RETRIES}={raw!r} is not an integer",
        ) from None
    if value < 0:
        raise ProviderConfigurationError(
            "chain",
            ProviderConfigurationError.BAD_REQUEST,
            f"{ENV_TRANSPORT_RETRIES} must not be negative",
        )
    return value


__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "PROVIDER_SLOTS",
    "ProviderSlot",
    "build_chain",
    "build_provider",
    "configured_provider_ids",
    "describe_configuration",
    "provider_order",
]
