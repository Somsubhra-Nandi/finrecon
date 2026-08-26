"""Provider-neutral model access for the Stage-3 investigation agent.

The agent loop imports :class:`ProviderChain` and the neutral message types
and nothing else. It has no knowledge of HTTP verbs, endpoints, SDKs,
authentication headers or any provider's response shape -- all of that is
absorbed by the adapters here.

Operational order (DESIGN.md 4.1's bounded agent has to run *somewhere*;
which host it runs on is deployment policy, not architecture):

======================  ============================================
OpenRouter              primary
Groq                    first infrastructure fallback
Gemini                  second infrastructure fallback
GoRouter                third infrastructure fallback
======================  ============================================

Fallback is permitted for availability failures only. See
:mod:`finrecon.agent.providers.chain` for why, and
:mod:`finrecon.agent.providers.base` for the exception taxonomy that
enforces it.
"""

from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelResponse,
    ModelSemanticError,
    ProviderConfigurationError,
    ProviderError,
    ProviderInfrastructureError,
    TokenUsage,
    ToolCallRequest,
    ToolSpec,
)
from finrecon.agent.providers.chain import (
    AllProvidersFailedError,
    ChainResult,
    ProviderAttempt,
    ProviderChain,
)
from finrecon.agent.providers.config import (
    DEFAULT_PROVIDER_ORDER,
    build_chain,
    build_provider,
    configured_provider_ids,
    describe_configuration,
    provider_order,
)
from finrecon.agent.providers.gemini import GeminiProvider
from finrecon.agent.providers.gorouter import GoRouterProvider
from finrecon.agent.providers.groq import GroqProvider
from finrecon.agent.providers.openrouter import OpenRouterProvider

__all__ = [
    "DEFAULT_PROVIDER_ORDER",
    "AllProvidersFailedError",
    "ChainResult",
    "ConversationMessage",
    "GeminiProvider",
    "GoRouterProvider",
    "GroqProvider",
    "ModelProvider",
    "ModelResponse",
    "ModelSemanticError",
    "OpenRouterProvider",
    "ProviderAttempt",
    "ProviderChain",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderInfrastructureError",
    "TokenUsage",
    "ToolCallRequest",
    "ToolSpec",
    "build_chain",
    "build_provider",
    "configured_provider_ids",
    "describe_configuration",
    "provider_order",
]
