"""Groq -- the first infrastructure fallback.

Also an OpenAI-dialect endpoint, so again the shared translation with a
different endpoint and identity.

Note the deliberate absence of any shared model default with OpenRouter.
The same model ID does not exist across providers, and pretending otherwise
produces a fallback that 404s exactly when it is needed. Each adapter
carries its own default and its own ``*_MODEL`` environment override, and
the trajectory records which model actually answered -- so a run that fell
back is legible as a run that used a different model, not merely a
different host.
"""

from __future__ import annotations

from finrecon.agent.providers.openai_compatible import OpenAICompatibleProvider

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
"""Default only. Overridden by ``GROQ_MODEL``. Verify before a live run."""


class GroqProvider(OpenAICompatibleProvider):
    provider_id = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        **kwargs,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url, **kwargs)


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_MODEL", "GroqProvider"]
