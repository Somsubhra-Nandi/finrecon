"""The outgoing client identity: one stable User-Agent, built in one place.

A provider can refuse a request before the model ever sees it, on the
strength of the client signature alone. Left unset, ``urllib`` announces
itself as ``Python-urllib/3.x``; an edge WAF in front of a provider may
reject that outright -- observed live as Cloudflare error 1010 behind an
``HTTP 403`` from GoRouter, and earlier from Groq, using a key the very
same endpoint accepts from an ordinary HTTP client. The run died in under
half a second, before a single model step existed.

That is a transport-fingerprint rejection, not a credential problem, so the
header is set in :func:`finrecon.agent.providers.transport.post_json` --
the one function in the project that builds outgoing headers -- rather than
in any adapter. These tests pin that placement: they exercise the *real*
transport with only the socket call replaced, because a fake transport
substituted for ``post_json`` would skip the very code under test and pass
whether or not the header exists.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from finrecon.agent.providers.base import ConversationMessage
from finrecon.agent.providers.gemini import GeminiProvider
from finrecon.agent.providers.gorouter import GoRouterProvider
from finrecon.agent.providers.groq import GroqProvider
from finrecon.agent.providers.openrouter import OpenRouterProvider
from finrecon.agent.providers.transport import USER_AGENT, post_json
from finrecon.agent.tools import tool_specs

SECRET = "sk-do-not-leak-0123456789"

MESSAGES = (
    ConversationMessage(role="system", content="you investigate"),
    ConversationMessage(role="user", content="case briefing"),
)

OPENAI_BODY = {
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
}

GEMINI_BODY = {
    "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]
}


class _Response:
    """The two methods ``post_json`` uses from a ``urlopen`` result."""

    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


class _Captured(list):
    """Requests the transport built, plus the canned body to answer with."""

    body: dict = OPENAI_BODY


@pytest.fixture
def sent(monkeypatch):
    """Capture every ``urllib.request.Request`` the real transport builds.

    Only the socket call is replaced. Header construction, the auth header
    and ``extra_headers`` merging all run exactly as they do live.
    """
    captured = _Captured()

    def fake_urlopen(request, timeout=None):
        captured.append(request)
        return _Response(captured.body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return captured


def headers_of(request: urllib.request.Request) -> dict[str, str]:
    """Header names lowercased. ``urllib`` capitalizes keys on the way in."""
    return {name.lower(): value for name, value in request.header_items()}


class TestTheStableUserAgent:
    def test_the_identity_is_a_fixed_application_string(self):
        """Pinned, not derived from the package version.

        The property being asserted is *stability*: a User-Agent that moved
        with every release would be a moving fingerprint, which is the thing
        that got the live run blocked in the first place.
        """
        assert USER_AGENT == "finrecon/0.1"

    def test_post_json_sends_it_on_every_request(self, sent):
        post_json(
            provider="gorouter",
            url="https://x/v1/chat/completions",
            payload={"model": "m"},
            api_key=SECRET,
        )
        assert headers_of(sent[0])["user-agent"] == USER_AGENT

    def test_it_displaces_the_urllib_default_signature(self, sent):
        """The actual defect: ``Python-urllib/3.x`` is what the WAF refused."""
        post_json(
            provider="gorouter",
            url="https://x/v1/chat/completions",
            payload={"model": "m"},
            api_key=SECRET,
        )
        assert "python-urllib" not in headers_of(sent[0])["user-agent"].lower()
        assert sent[0].has_header("User-agent"), (
            "urllib only injects its own default when the header is absent"
        )

    def test_authorization_and_content_type_are_untouched(self, sent):
        post_json(
            provider="gorouter",
            url="https://x/v1/chat/completions",
            payload={"model": "m"},
            api_key=SECRET,
        )
        headers = headers_of(sent[0])
        assert headers["authorization"] == f"Bearer {SECRET}"
        assert headers["content-type"] == "application/json"
        assert headers["user-agent"] == USER_AGENT

    def test_a_provider_specific_auth_header_is_untouched(self, sent):
        """Gemini authenticates by its own header, not by ``Authorization``."""
        post_json(
            provider="gemini",
            url="https://x/v1/models/m:generateContent",
            payload={},
            api_key=SECRET,
            auth_header="x-goog-api-key",
            auth_prefix="",
        )
        headers = headers_of(sent[0])
        assert headers["x-goog-api-key"] == SECRET
        assert "authorization" not in headers
        assert headers["user-agent"] == USER_AGENT

    def test_extra_headers_still_win_over_the_default(self, sent):
        """Merged last, so an adapter that must announce itself otherwise can."""
        post_json(
            provider="openrouter",
            url="https://x/v1/chat/completions",
            payload={},
            api_key=SECRET,
            extra_headers={"User-Agent": "something-else/9"},
        )
        assert headers_of(sent[0])["user-agent"] == "something-else/9"

    def test_the_identity_never_carries_the_credential(self, sent):
        post_json(
            provider="gorouter",
            url="https://x/v1/chat/completions",
            payload={"model": "m"},
            api_key=SECRET,
        )
        assert SECRET not in headers_of(sent[0])["user-agent"]


class TestEveryOpenAICompatibleAdapterSendsIt:
    """Through ``complete()`` and the real transport, not a stubbed one."""

    @pytest.mark.parametrize(
        "factory",
        [OpenRouterProvider, GroqProvider, GoRouterProvider],
        ids=["openrouter", "groq", "gorouter"],
    )
    def test_a_live_shaped_call_carries_the_identity(self, sent, factory):
        factory(api_key=SECRET).complete(MESSAGES, tool_specs())
        headers = headers_of(sent[0])
        assert headers["user-agent"] == USER_AGENT
        assert headers["authorization"] == f"Bearer {SECRET}"
        assert headers["content-type"] == "application/json"

    def test_openrouter_attribution_headers_coexist_with_it(self, sent):
        OpenRouterProvider(
            api_key=SECRET, referer="https://finrecon.example", title="FinRecon"
        ).complete(MESSAGES, tool_specs())
        headers = headers_of(sent[0])
        assert headers["x-title"] == "FinRecon"
        assert headers["http-referer"] == "https://finrecon.example"
        assert headers["user-agent"] == USER_AGENT

    def test_the_identity_is_shared_not_per_adapter(self, sent):
        """One string, from one place. A per-adapter copy would drift."""
        for factory in (OpenRouterProvider, GroqProvider, GoRouterProvider):
            factory(api_key=SECRET).complete(MESSAGES, ())
        assert {headers_of(r)["user-agent"] for r in sent} == {USER_AGENT}

    def test_gemini_gets_it_too_because_the_transport_is_shared(self, sent):
        """Not an OpenAI-dialect adapter, and still behind the same WAF risk."""
        sent.body = GEMINI_BODY
        GeminiProvider(api_key=SECRET).complete(MESSAGES, ())
        assert headers_of(sent[0])["user-agent"] == USER_AGENT


def test_the_payload_and_tool_declarations_are_unchanged_by_the_header(sent):
    """The fix is a header. Nothing about the request body may have moved."""
    provider = GoRouterProvider(api_key=SECRET)
    provider.complete(MESSAGES, tool_specs())
    payload = json.loads(sent[0].data.decode("utf-8"))
    assert payload["model"] == provider.model
    assert payload["tool_choice"] == "auto"
    assert all("strict" not in t["function"] for t in payload["tools"]), (
        "gorouter's strict-schema gate is untouched by this patch"
    )
