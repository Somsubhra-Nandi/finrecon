"""OpenRouter -- the primary provider.

OpenRouter speaks the OpenAI chat-completions dialect, so this adapter is
the shared translation plus an endpoint, an identity and two optional
attribution headers OpenRouter uses for its dashboards.

Primary status is an *operational* choice recorded in
:mod:`finrecon.agent.providers.config`, not a quality claim. Nothing in the
loop, the validator or the policy gate knows or cares which provider
answered; the provider and model are recorded on every trajectory step so
that a later run can be compared against this one, which is the whole
reason the field exists.
"""

from __future__ import annotations

from finrecon.agent.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
"""Default only. Overridden by ``OPENROUTER_MODEL``.

Provider catalogues change faster than this repository does, so the model
is configuration, never a constant buried in the loop. Verify the ID against
the provider's current catalogue before a live run; the run logs it.
"""


class OpenRouterProvider(OpenAICompatibleProvider):
    provider_id = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        referer: str | None = None,
        title: str | None = None,
        **kwargs,
    ) -> None:
        headers = dict(kwargs.pop("extra_headers", None) or {})
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            extra_headers=headers,
            **kwargs,
        )


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "OpenRouterProvider"]
