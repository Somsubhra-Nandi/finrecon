"""Providers: the neutral interface, the three adapters, and the fallback rule.

The rule under test, stated once:

    Fall back to another provider **only** when the current one failed to
    return a decodable answer.

Everything below either exercises that rule or the wire translation that
makes three very different APIs look identical to the loop. The
``no fallback`` cases matter more than the ``fallback`` ones: a system that
retries a poor answer somewhere else is sampling until a model agrees with
it, which is the pattern DESIGN.md §4.2 exists to forbid.
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent.providers import config as provider_config
from finrecon.agent.providers.base import (
    ConversationMessage,
    ModelProvider,
    ModelSemanticError,
    ProviderConfigurationError,
    ProviderInfrastructureError,
    ToolSpec,
)
from finrecon.agent.providers.chain import AllProvidersFailedError, ProviderChain
from finrecon.agent.providers.gemini import GeminiProvider
from finrecon.agent.providers.groq import GroqProvider
from finrecon.agent.providers.openrouter import OpenRouterProvider
from finrecon.agent.providers.transport import classify_http_status, redact
from finrecon.agent.providers.openai_compatible import supports_strict_tool_schema
from finrecon.agent.tools import tool_specs
from tests.stage3_fakes import (
    FailingProvider,
    RecordingTransport,
    ScriptedProvider,
    tool_call,
    turn,
)

SECRET = "sk-do-not-leak-0123456789"

MESSAGES = (
    ConversationMessage(role="system", content="you investigate"),
    ConversationMessage(role="user", content="case briefing"),
)


def openai_body(*, text="ok", calls=(), usage=True):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": args},
            }
            for i, (name, args) in enumerate(calls)
        ]
    body = {"choices": [{"message": message, "finish_reason": "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    return body


def rate_limited(provider="openrouter"):
    return ProviderInfrastructureError(
        provider, ProviderInfrastructureError.RATE_LIMITED, "HTTP 429"
    )


def timed_out(provider="openrouter"):
    return ProviderInfrastructureError(
        provider, ProviderInfrastructureError.TIMEOUT, "no response within 60s"
    )


def unavailable(provider="openrouter"):
    return ProviderInfrastructureError(
        provider, ProviderInfrastructureError.SERVER_ERROR, "HTTP 503"
    )


class TestErrorTaxonomy:
    @pytest.mark.parametrize(
        "status,expected_kind",
        [
            (429, ProviderInfrastructureError.RATE_LIMITED),
            (402, ProviderInfrastructureError.QUOTA_EXHAUSTED),
            (500, ProviderInfrastructureError.SERVER_ERROR),
            (503, ProviderInfrastructureError.SERVER_ERROR),
        ],
    )
    def test_availability_failures_permit_fallback(self, status, expected_kind):
        error = classify_http_status("openrouter", status, "{}")
        assert isinstance(error, ProviderInfrastructureError)
        assert error.kind == expected_kind
        assert error.permits_provider_fallback is True

    @pytest.mark.parametrize("status", [401, 403, 400, 404, 422])
    def test_configuration_failures_never_permit_fallback(self, status):
        error = classify_http_status("openrouter", status, "{}")
        assert isinstance(error, ProviderConfigurationError)
        assert error.permits_provider_fallback is False

    def test_a_semantic_failure_never_permits_fallback(self):
        error = ModelSemanticError(
            "openrouter", ModelSemanticError.INVALID_TOOL_ARGUMENTS, "bad args"
        )
        assert error.permits_provider_fallback is False

    def test_only_transient_transport_kinds_are_retried_on_the_same_provider(self):
        assert timed_out().permits_transport_retry is True
        assert unavailable().permits_transport_retry is True
        assert rate_limited().permits_transport_retry is False, (
            "a rate limit will not clear in milliseconds; retrying wastes the budget"
        )


class TestOpenAICompatibleAdapters:
    def test_openrouter_and_groq_do_not_share_a_default_model(self):
        """The same model ID does not exist across providers (DESIGN.md fallback)."""
        from finrecon.agent.providers import gemini, groq, openrouter

        defaults = {openrouter.DEFAULT_MODEL, groq.DEFAULT_MODEL, gemini.DEFAULT_MODEL}
        assert len(defaults) == 3

    def test_the_request_carries_the_tools_and_the_model(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(
            api_key=SECRET, model="test/model", transport=transport
        )
        provider.complete(MESSAGES, tool_specs())
        payload = transport.requests[0]["payload"]
        assert payload["model"] == "test/model"
        assert payload["temperature"] == 0.0
        assert {t["function"]["name"] for t in payload["tools"]} == set(
            {s.name for s in tool_specs()}
        )
        assert {
            tool["function"]["name"]: tool["function"]["description"]
            for tool in payload["tools"]
        } == {spec.name: spec.description for spec in tool_specs()}
        assert payload["messages"][0]["role"] == "system"


class TestStrictToolSchemas:
    """``strict: true`` on the OpenAI dialect, and what must not depend on it.

    Strict mode constrains argument generation to the declared grammar where
    a provider honours it, which is where a duplicate object key stops being
    expressible. It is defence in depth: every test below that asserts it is
    *sent* is paired with one asserting the local decoder still refuses a
    duplicate key regardless.
    """

    def test_openai_compatible_tools_declare_strict_mode(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, tool_specs())
        tools = transport.requests[0]["payload"]["tools"]
        assert tools, "the tools were sent at all"
        assert all(tool["function"]["strict"] is True for tool in tools)

    def test_groq_declares_strict_mode_too(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = GroqProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, tool_specs())
        tools = transport.requests[0]["payload"]["tools"]
        assert all(tool["function"]["strict"] is True for tool in tools)

    def test_every_tool_schema_qualifies_for_strict_mode(self):
        """Closed object, and every declared property required."""
        for spec in tool_specs():
            schema = spec.parameters_json_schema
            assert supports_strict_tool_schema(schema), spec.name
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])

    def test_a_schema_that_does_not_qualify_is_sent_without_the_flag(self):
        """Degrade to ordinary tool calling rather than send an invalid claim."""
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        loose = ToolSpec(
            name="loose",
            description="an optional field, so strict mode does not apply",
            parameters_json_schema={
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a"],
                "additionalProperties": False,
            },
        )
        assert supports_strict_tool_schema(loose.parameters_json_schema) is False
        provider.complete(MESSAGES, (loose,))
        function = transport.requests[0]["payload"]["tools"][0]["function"]
        assert "strict" not in function
        assert function["parameters"] == loose.parameters_json_schema

    def test_an_adapter_can_opt_out_of_strict_mode(self):
        """Capability-gated, so a gateway that rejects the field stays usable."""
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(
            api_key=SECRET, transport=transport, strict_tool_schema=False
        )
        provider.complete(MESSAGES, tool_specs())
        tools = transport.requests[0]["payload"]["tools"]
        assert all("strict" not in tool["function"] for tool in tools)

    def test_strict_mode_does_not_relax_the_local_duplicate_key_decoder(self):
        """The provider may ignore the flag. Local validation decides anyway."""
        from finrecon.agent.tools import ToolValidationError, validate_call

        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                "compare_reference_fragment",
                '{"fragment":"PF*******VQ","fragment":"RTGS"}',
            )
        assert exc.value.reason == ToolValidationError.DUPLICATE_ARGUMENT_KEY

    def test_arguments_are_never_rewritten_by_the_adapter(self):
        """Malformed argument text reaches the loop exactly as the model sent it."""
        malformed = '{"fragment":"A","fragment":"B"}'
        transport = RecordingTransport(
            responses=[openai_body(calls=[("compare_reference_fragment", malformed)])]
        )
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].raw_arguments == malformed

    def test_several_tool_calls_stay_separate_and_ordered(self):
        transport = RecordingTransport(
            responses=[
                openai_body(
                    calls=[
                        ("compare_reference_fragment", '{"fragment": "AAAA"}'),
                        ("compare_reference_fragment", '{"fragment": "BBBB"}'),
                        ("lookup_candidate_records", '{"candidate_id": "c1"}'),
                    ]
                )
            ]
        )
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert [c.tool_name for c in response.tool_calls] == [
            "compare_reference_fragment",
            "compare_reference_fragment",
            "lookup_candidate_records",
        ]
        assert [c.raw_arguments for c in response.tool_calls] == [
            '{"fragment": "AAAA"}',
            '{"fragment": "BBBB"}',
            '{"candidate_id": "c1"}',
        ]

    def test_the_gemini_payload_carries_no_strict_field(self):
        """A different dialect. Nothing in the OpenAI adapter reaches it."""
        transport = RecordingTransport(
            responses=[
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}
                    ]
                }
            ]
        )
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, tool_specs())
        payload = transport.requests[0]["payload"]
        assert "strict" not in json.dumps(payload)
        declarations = payload["tools"][0]["functionDeclarations"]
        assert {d["name"] for d in declarations} == {s.name for s in tool_specs()}
        assert all(set(d) == {"name", "description", "parameters"} for d in declarations)

    def test_the_credential_is_passed_to_transport_and_nowhere_else(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = GroqProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, ())
        assert transport.requests[0]["api_key"] == SECRET
        assert SECRET not in json.dumps(transport.requests[0]["payload"])
        assert SECRET not in str(response)
        assert SECRET not in repr(provider.describe())

    def test_tool_calls_are_returned_with_arguments_unparsed(self):
        transport = RecordingTransport(
            responses=[
                openai_body(calls=[("compute_expected_net", '{"candidate_id": "c1"}')])
            ]
        )
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.tool_name == "compute_expected_net"
        assert call.raw_arguments == '{"candidate_id": "c1"}'

    def test_multiple_tool_results_keep_call_ids_and_order(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        provider.complete(
            MESSAGES
            + (
                ConversationMessage(
                    role="assistant",
                    tool_calls=(
                        tool_call("compute_expected_net", {"candidate_id": "a"}, call_id="a"),
                        tool_call("compute_expected_net", {"candidate_id": "b"}, call_id="b"),
                    ),
                ),
                ConversationMessage(
                    role="tool",
                    content='{"candidate_id":"a"}',
                    tool_call_id="a",
                    tool_name="compute_expected_net",
                ),
                ConversationMessage(
                    role="tool",
                    content='{"candidate_id":"b"}',
                    tool_call_id="b",
                    tool_name="compute_expected_net",
                ),
            ),
            (),
        )
        payload_messages = transport.requests[0]["payload"]["messages"]
        assert [m.get("tool_call_id") for m in payload_messages[-2:]] == ["a", "b"]

    def test_malformed_tool_arguments_survive_to_the_loop_unrepaired(self):
        """The adapter must not fix them; the loop has to see the model's behaviour."""
        transport = RecordingTransport(
            responses=[openai_body(calls=[("compute_expected_net", "{not json")])]
        )
        provider = GroqProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].raw_arguments == "{not json"

    def test_usage_is_translated_when_the_provider_reports_it(self):
        transport = RecordingTransport(responses=[openai_body()])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, ())
        assert response.usage.input_tokens == 11
        assert response.usage.total_tokens == 14

    def test_a_missing_usage_block_is_not_an_error(self):
        transport = RecordingTransport(responses=[openai_body(usage=False)])
        provider = GroqProvider(api_key=SECRET, transport=transport)
        assert provider.complete(MESSAGES, ()).usage.is_empty()

    @pytest.mark.parametrize(
        "body", [{}, {"choices": []}, {"choices": [{}]}, {"choices": ["nope"]}]
    )
    def test_a_missing_envelope_is_an_infrastructure_failure(self, body):
        transport = RecordingTransport(responses=[body])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        with pytest.raises(ProviderInfrastructureError) as exc:
            provider.complete(MESSAGES, ())
        assert exc.value.kind == ProviderInfrastructureError.PROTOCOL_ERROR

    def test_an_empty_answer_is_not_an_infrastructure_failure(self):
        """A decodable envelope with nothing useful in it is model behaviour."""
        transport = RecordingTransport(responses=[openai_body(text="")])
        provider = OpenRouterProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, ())
        assert response.text == ""
        assert response.tool_calls == ()

    def test_openrouter_attribution_headers_are_optional(self):
        transport = RecordingTransport(responses=[openai_body(), openai_body()])
        OpenRouterProvider(api_key=SECRET, transport=transport).complete(MESSAGES, ())
        assert transport.requests[0]["extra_headers"] == {}
        OpenRouterProvider(
            api_key=SECRET, transport=transport, referer="https://x", title="FinRecon"
        ).complete(MESSAGES, ())
        assert transport.requests[1]["extra_headers"]["X-Title"] == "FinRecon"


class TestGeminiAdapter:
    def gemini_body(self, *, text="ok", calls=()):
        parts = []
        if text:
            parts.append({"text": text})
        for name, args in calls:
            parts.append({"functionCall": {"name": name, "args": args}})
        return {
            "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 9,
                "candidatesTokenCount": 2,
                "totalTokenCount": 11,
            },
        }

    def test_the_system_message_is_hoisted_into_system_instruction(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, ())
        payload = transport.requests[0]["payload"]
        assert payload["systemInstruction"]["parts"][0]["text"] == "you investigate"
        assert [c["role"] for c in payload["contents"]] == ["user"]

    def test_assistant_turns_become_model_turns_and_tool_results_become_user_turns(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(
            MESSAGES
            + (
                ConversationMessage(
                    role="assistant",
                    content="looking",
                    tool_calls=(tool_call("compute_expected_net", {"candidate_id": "c"}),),
                ),
                ConversationMessage(
                    role="tool",
                    content='{"candidate_id": "c"}',
                    tool_call_id="call_1",
                    tool_name="compute_expected_net",
                ),
            ),
            (),
        )
        contents = transport.requests[0]["payload"]["contents"]
        assert [c["role"] for c in contents] == ["user", "model", "user"]
        assert contents[1]["parts"][-1]["functionCall"]["name"] == "compute_expected_net"
        assert contents[2]["parts"][0]["functionResponse"]["name"] == "compute_expected_net"

    def test_parallel_tool_results_are_grouped_in_deterministic_order(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(
            MESSAGES
            + (
                ConversationMessage(
                    role="assistant",
                    tool_calls=(
                        tool_call("compute_expected_net", {"candidate_id": "b"}, call_id="b"),
                        tool_call("compute_expected_net", {"candidate_id": "a"}, call_id="a"),
                    ),
                ),
                ConversationMessage(
                    role="tool",
                    content='{"candidate_id":"b"}',
                    tool_call_id="b",
                    tool_name="compute_expected_net",
                ),
                ConversationMessage(
                    role="tool",
                    content='{"candidate_id":"a"}',
                    tool_call_id="a",
                    tool_name="compute_expected_net",
                ),
            ),
            (),
        )
        contents = transport.requests[0]["payload"]["contents"]
        assert [c["role"] for c in contents] == ["user", "model", "user"]
        responses = contents[-1]["parts"]
        assert len(responses) == 2
        assert [
            part["functionResponse"]["response"]["candidate_id"] for part in responses
        ] == ["b", "a"]

    def test_the_key_travels_in_a_header_not_the_url(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, ())
        request = transport.requests[0]
        assert request["auth_header"] == "x-goog-api-key"
        assert request["auth_prefix"] == ""
        assert SECRET not in request["url"]

    def test_unsupported_schema_keywords_are_dropped_never_added(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        provider.complete(MESSAGES, tool_specs())
        declarations = transport.requests[0]["payload"]["tools"][0]["functionDeclarations"]
        for declaration in declarations:
            assert "additionalProperties" not in declaration["parameters"]
            assert "properties" in declaration["parameters"]

    def test_function_calls_are_translated_into_neutral_tool_calls(self):
        transport = RecordingTransport(
            responses=[self.gemini_body(calls=[("compute_expected_net", {"candidate_id": "c"})])]
        )
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].tool_name == "compute_expected_net"
        assert json.loads(response.tool_calls[0].raw_arguments) == {"candidate_id": "c"}

    def test_usage_metadata_is_translated(self):
        transport = RecordingTransport(responses=[self.gemini_body()])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        assert provider.complete(MESSAGES, ()).usage.total_tokens == 11

    @pytest.mark.parametrize("body", [{}, {"candidates": []}, {"candidates": [{}]}])
    def test_a_missing_envelope_is_an_infrastructure_failure(self, body):
        transport = RecordingTransport(responses=[body])
        provider = GeminiProvider(api_key=SECRET, transport=transport)
        with pytest.raises(ProviderInfrastructureError):
            provider.complete(MESSAGES, ())


class TestInfrastructureFallback:
    def test_openrouter_rate_limited_falls_back_to_groq(self):
        primary = FailingProvider(rate_limited(), provider_id="openrouter")
        secondary = ScriptedProvider([turn(text="done")], provider_id="groq", model="g")
        result = ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert result.response.provider == "groq"
        assert result.provider_fallback_used is True
        assert result.fallback_reason == ProviderInfrastructureError.RATE_LIMITED

    def test_openrouter_timeout_falls_back_to_groq_after_a_bounded_retry(self):
        primary = FailingProvider(timed_out(), provider_id="openrouter")
        secondary = ScriptedProvider([turn(text="done")], provider_id="groq", model="g")
        result = ProviderChain((primary, secondary), transport_retries=1).complete(
            MESSAGES, ()
        )
        assert primary.call_count == 2, "one bounded retry on the same provider"
        assert result.response.provider == "groq"
        assert result.fallback_reason == ProviderInfrastructureError.TIMEOUT

    def test_a_rate_limit_is_not_retried_on_the_same_provider(self):
        primary = FailingProvider(rate_limited(), provider_id="openrouter")
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        ProviderChain((primary, secondary), transport_retries=1).complete(MESSAGES, ())
        assert primary.call_count == 1

    def test_a_transient_failure_that_clears_never_leaves_the_provider(self):
        class FlakyProvider(ModelProvider):
            provider_id = "openrouter"

            def __init__(self):
                self.call_count = 0

            @property
            def model(self):
                return "flaky"

            def complete(self, messages, tools):
                self.call_count += 1
                if self.call_count == 1:
                    raise unavailable()
                return turn(text="recovered", provider="openrouter", model="flaky")

        flaky = FlakyProvider()
        never = ScriptedProvider([], provider_id="groq", model="g")
        result = ProviderChain((flaky, never), transport_retries=1).complete(MESSAGES, ())
        assert result.response.provider == "openrouter"
        assert result.provider_fallback_used is False
        assert result.response.transport_attempts == 2
        assert never.call_count == 0

    def test_openrouter_unavailable_and_groq_rate_limited_reach_gemini(self):
        primary = FailingProvider(unavailable(), provider_id="openrouter")
        secondary = FailingProvider(rate_limited("groq"), provider_id="groq")
        tertiary = ScriptedProvider([turn(text="done")], provider_id="gemini", model="gm")
        result = ProviderChain((primary, secondary, tertiary), transport_retries=0).complete(
            MESSAGES, ()
        )
        assert result.response.provider == "gemini"
        assert [a.provider for a in result.attempts] == ["openrouter", "groq", "gemini"]
        assert result.fallback_reason == ProviderInfrastructureError.SERVER_ERROR

    def test_every_attempt_is_recorded_including_the_failures(self):
        primary = FailingProvider(rate_limited(), provider_id="openrouter")
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        result = ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert [(a.provider, a.outcome) for a in result.attempts] == [
            ("openrouter", ProviderInfrastructureError.RATE_LIMITED),
            ("groq", "success"),
        ]

    def test_all_providers_failing_raises_rather_than_inventing_an_answer(self):
        chain = ProviderChain(
            (
                FailingProvider(rate_limited(), provider_id="openrouter"),
                FailingProvider(unavailable("groq"), provider_id="groq"),
                FailingProvider(timed_out("gemini"), provider_id="gemini"),
            ),
            transport_retries=0,
        )
        with pytest.raises(AllProvidersFailedError) as exc:
            chain.complete(MESSAGES, ())
        assert len(exc.value.attempts) == 3


class TestNoFallbackWhenTheProviderAnswered:
    def test_a_successful_response_with_a_bad_tool_call_does_not_fall_back(self):
        """The provider answered. The answer is poor. That is not an outage."""
        primary = ScriptedProvider(
            [turn(calls=[tool_call("compute_expected_net", "{not json")])],
            provider_id="openrouter",
            model="o",
        )
        secondary = ScriptedProvider([], provider_id="groq", model="g")
        result = ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert result.response.provider == "openrouter"
        assert secondary.call_count == 0
        assert result.provider_fallback_used is False

    def test_a_hallucinated_candidate_id_does_not_fall_back(self):
        primary = ScriptedProvider(
            [tool_turn := turn(calls=[tool_call("compute_expected_net", '{"candidate_id": "x"}')])],
            provider_id="openrouter",
            model="o",
        )
        secondary = ScriptedProvider([], provider_id="groq", model="g")
        ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert secondary.call_count == 0
        assert tool_turn.tool_calls

    def test_an_ambiguous_but_valid_answer_does_not_fall_back(self):
        primary = ScriptedProvider(
            [turn(text="the evidence does not distinguish the candidates")],
            provider_id="openrouter",
            model="o",
        )
        secondary = ScriptedProvider([], provider_id="groq", model="g")
        result = ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert result.response.provider == "openrouter"
        assert secondary.call_count == 0

    def test_an_unauthorized_key_stops_the_chain_instead_of_failing_over(self):
        primary = FailingProvider(
            ProviderConfigurationError(
                "openrouter", ProviderConfigurationError.UNAUTHORIZED, "HTTP 401"
            ),
            provider_id="openrouter",
        )
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        with pytest.raises(ProviderConfigurationError):
            ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert secondary.call_count == 0, (
            "a bad key is a deployment bug; failing over would hide it"
        )

    def test_a_malformed_request_stops_the_chain(self):
        primary = FailingProvider(
            ProviderConfigurationError(
                "openrouter", ProviderConfigurationError.BAD_REQUEST, "HTTP 422"
            ),
            provider_id="openrouter",
        )
        secondary = ScriptedProvider([turn()], provider_id="groq", model="g")
        with pytest.raises(ProviderConfigurationError):
            ProviderChain((primary, secondary)).complete(MESSAGES, ())
        assert secondary.call_count == 0


class TestConfiguration:
    def test_the_default_order_is_openrouter_then_groq_then_gemini(self):
        """The original three keep their original positions, in front.

        Asserted as a prefix rather than as the whole tuple: adding a provider
        must never be able to change which one answers first, and stating the
        prefix separately is what makes that the tested invariant instead of an
        incidental property of a literal.
        """
        expected = ("openrouter", "groq", "gemini", "gorouter")
        assert provider_config.DEFAULT_PROVIDER_ORDER == expected
        assert provider_config.provider_order({}) == expected
        assert provider_config.DEFAULT_PROVIDER_ORDER[:3] == ("openrouter", "groq", "gemini")

    def test_the_order_is_configurable(self):
        env = {"FINRECON_PROVIDER_ORDER": "gemini, groq"}
        assert provider_config.provider_order(env) == ("gemini", "groq")

    def test_an_unknown_provider_in_the_order_is_refused(self):
        with pytest.raises(ProviderConfigurationError):
            provider_config.provider_order({"FINRECON_PROVIDER_ORDER": "openai"})

    def test_a_provider_without_a_credential_is_skipped_not_failed_over(self):
        env = {"GROQ_API_KEY": "k"}
        assert provider_config.configured_provider_ids(env) == ("groq",)

    def test_building_a_chain_with_no_credentials_fails_loudly(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            provider_config.build_chain({})
        assert "OPENROUTER_API_KEY" in str(exc.value)

    def test_the_chain_contains_only_configured_providers_in_order(self):
        env = {"OPENROUTER_API_KEY": "a", "GEMINI_API_KEY": "c"}
        chain = provider_config.build_chain(env)
        assert [p.provider_id for p in chain.providers] == ["openrouter", "gemini"]

    def test_models_are_configurable_per_provider(self):
        env = {"GROQ_API_KEY": "k", "GROQ_MODEL": "custom-model"}
        assert provider_config.build_provider("groq", env=env).model == "custom-model"

    def test_the_configuration_description_never_contains_a_credential(self):
        env = {"OPENROUTER_API_KEY": SECRET, "GROQ_API_KEY": SECRET}
        described = provider_config.describe_configuration(env)
        assert SECRET not in json.dumps(described)
        assert described["providers"][0]["credential_present"] is True
        assert described["providers"][2]["credential_present"] is False

    def test_a_nonsense_timeout_is_refused_rather_than_defaulted(self):
        with pytest.raises(ProviderConfigurationError):
            provider_config.describe_configuration(
                {"GROQ_API_KEY": "k", "FINRECON_AGENT_TIMEOUT_SECONDS": "soon"}
            )

    def test_redaction_never_returns_the_value(self):
        assert redact(SECRET) == "<redacted>"
        assert SECRET not in redact(SECRET)


def test_a_chain_needs_at_least_one_provider():
    with pytest.raises(ValueError):
        ProviderChain(())


def test_the_chain_describes_provider_and_model_together():
    chain = ProviderChain(
        (
            ScriptedProvider([], provider_id="openrouter", model="a/b"),
            ScriptedProvider([], provider_id="groq", model="c-d"),
        )
    )
    assert chain.describe() == ("openrouter:a/b", "groq:c-d")


def test_tool_specs_are_provider_neutral():
    for spec in tool_specs():
        assert isinstance(spec, ToolSpec)
        assert spec.description
