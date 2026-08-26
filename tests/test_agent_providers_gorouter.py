"""GoRouter: configuration, wire translation, telemetry, and what stays fail-closed.

GoRouter is a *routing gateway* that speaks the OpenAI dialect, and the two
facts that follow from that are what most of this file is about.

**Being OpenAI-shaped is not a capability claim.** GoRouter is deliberately
gated out of strict tool schemas -- there is no evidence its endpoint accepts
the extension -- and the tests below assert the two halves of that separately:
the ``strict`` flag is absent, and the JSON schema is identical to every other
provider's. Withholding a claim about a schema is not weakening the schema.
Every local defence is then re-asserted *through* GoRouter, because losing one
layer of defence in depth is only safe if the layers under it are still
load-bearing: duplicate keys, malformed JSON, unknown tools and closed schemas
all still refuse, on this provider, with strict mode off.

**A gateway substitutes models and double-reports usage.** It may answer
``claude-opus-5`` to a request for ``claude-opus-5-thinking``, and it reports
both ``completion_tokens`` and ``output_tokens`` for one quantity -- observed
live disagreeing, 8 against 0. Both facts are recorded rather than resolved:
the requested and answered model IDs stay in separate fields, and the usage
block is normalized by a declared rule with the raw block kept beside it.
Nothing in here computes a token count.

What this file does *not* test is anything Stage 3 already owns. The
validator, the policy gate, the prompt, tool behaviour and the candidate
snapshot are untouched by provider integration; the assertions below reach
them only to prove they still refuse.
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent.providers import config as provider_config
from finrecon.agent.providers.base import (
    ConversationMessage,
    ProviderConfigurationError,
    ProviderInfrastructureError,
)
from finrecon.agent.providers.chain import ChainResult, ProviderChain
from finrecon.agent.providers.gorouter import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    GoRouterProvider,
)
from finrecon.agent.providers.openai_compatible import (
    OpenAICompatibleProvider,
    normalize_usage,
)
from finrecon.agent.providers.openrouter import OpenRouterProvider
from finrecon.agent.providers.transport import classify_http_status, post_json
from finrecon.agent.tools import ToolValidationError, tool_specs, validate_call
from tests.stage3_fakes import (
    ExplodingProvider,
    FailingProvider,
    RecordingTransport,
    ScriptedProvider,
    tool_call,
    turn,
)

SECRET = "sk-gorouter-do-not-leak-0123456789"

MESSAGES = (
    ConversationMessage(role="system", content="you investigate"),
    ConversationMessage(role="user", content="case briefing"),
)

LIVE_USAGE = {
    "prompt_tokens": 7382,
    "completion_tokens": 8,
    "total_tokens": 7390,
    "input_tokens": 7382,
    "output_tokens": 0,
    "usage_source": "anthropic",
}
"""A usage block observed from a live GoRouter response, verbatim.

Both name pairs are present and the output counts disagree. Kept as data so the
normalization rule is tested against something that actually happened rather
than against a shape we found convenient.
"""


def gorouter_body(*, text="ok", calls=(), usage=None, model="claude-opus-5"):
    """A GoRouter-shaped body: the OpenAI envelope plus the gateway's extras."""
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
    if model is not None:
        body["model"] = model
    if usage is not None:
        body["usage"] = usage
    return body


def gorouter(transport, **kwargs):
    return GoRouterProvider(api_key=SECRET, transport=transport, **kwargs)


def gorouter_source() -> str:
    from pathlib import Path

    import finrecon.agent.providers.gorouter as module

    return Path(module.__file__).read_text(encoding="utf-8")


# --- configuration --------------------------------------------------------


class TestConfiguration:
    def test_gorouter_is_a_registered_provider_slot(self):
        slot = provider_config.PROVIDER_SLOTS["gorouter"]
        assert slot.api_key_env == "GOROUTER_API_KEY"
        assert slot.model_env == "GOROUTER_MODEL"
        assert slot.base_url_env == "GOROUTER_BASE_URL"
        assert slot.default_base_url == "https://gorouter.app/v1"
        assert slot.factory is GoRouterProvider

    def test_the_endpoint_is_the_documented_base_url(self):
        assert DEFAULT_BASE_URL == "https://gorouter.app/v1"
        built = provider_config.build_provider("gorouter", env={"GOROUTER_API_KEY": "k"})
        assert built.endpoint == "https://gorouter.app/v1/chat/completions"

    def test_gorouter_is_in_the_default_order_behind_the_original_three(self):
        """First-class, but never promoted over a provider already deployed."""
        order = provider_config.DEFAULT_PROVIDER_ORDER
        assert order[:3] == ("openrouter", "groq", "gemini")
        assert order[3] == "gorouter"

    def test_a_gorouter_only_credential_produces_a_runnable_chain(self):
        """The reason it is in the default order at all: no surprise refusal."""
        env = {"GOROUTER_API_KEY": "k"}
        assert provider_config.configured_provider_ids(env) == ("gorouter",)
        chain = provider_config.build_chain(env)
        assert [p.provider_id for p in chain.providers] == ["gorouter"]

    def test_the_model_is_configurable_like_every_other_provider(self):
        env = {"GOROUTER_API_KEY": "k", "GOROUTER_MODEL": "claude-opus-5-thinking"}
        assert (
            provider_config.build_provider("gorouter", env=env).model
            == "claude-opus-5-thinking"
        )

    def test_the_base_url_is_configurable_like_every_other_provider(self):
        env = {"GOROUTER_API_KEY": "k", "GOROUTER_BASE_URL": "https://proxy.internal/v1"}
        built = provider_config.build_provider("gorouter", env=env)
        assert built.endpoint == "https://proxy.internal/v1/chat/completions"

    def test_the_default_model_is_not_shared_with_another_provider(self):
        """A shared default produces a fallback that 404s exactly when needed."""
        from finrecon.agent.providers import gemini, groq, openrouter

        others = {openrouter.DEFAULT_MODEL, groq.DEFAULT_MODEL, gemini.DEFAULT_MODEL}
        assert DEFAULT_MODEL not in others
        assert len(others | {DEFAULT_MODEL}) == 4

    def test_the_default_model_is_declared_not_silent(self):
        """The project's convention is a per-adapter default, printed before a run."""
        described = provider_config.describe_configuration({"GOROUTER_API_KEY": "k"})
        row = next(r for r in described["providers"] if r["provider"] == "gorouter")
        assert row["model"] == DEFAULT_MODEL
        assert row["credential_env"] == "GOROUTER_API_KEY"
        assert row["credential_present"] is True

    def test_an_explicit_model_overrides_the_default_in_the_description(self):
        described = provider_config.describe_configuration(
            {"GOROUTER_API_KEY": "k", "GOROUTER_MODEL": "some/other-model"}
        )
        row = next(r for r in described["providers"] if r["provider"] == "gorouter")
        assert row["model"] == "some/other-model"

    def test_the_description_never_contains_the_credential(self):
        described = provider_config.describe_configuration({"GOROUTER_API_KEY": SECRET})
        assert SECRET not in json.dumps(described)


class TestMissingCredential:
    def test_building_gorouter_without_a_key_is_a_configuration_error(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            provider_config.build_provider("gorouter", env={})
        assert exc.value.kind == ProviderConfigurationError.MISSING_CREDENTIALS
        assert "GOROUTER_API_KEY" in str(exc.value)

    def test_a_blank_credential_is_treated_as_absent_and_never_falls_back(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            provider_config.build_provider("gorouter", env={"GOROUTER_API_KEY": "   "})
        assert exc.value.permits_provider_fallback is False

    def test_gorouter_without_a_key_is_skipped_not_failed_over(self):
        env = {"OPENROUTER_API_KEY": "a"}
        assert "gorouter" not in provider_config.configured_provider_ids(env)
        chain = provider_config.build_chain(env)
        assert [p.provider_id for p in chain.providers] == ["openrouter"]

    def test_no_credential_anywhere_still_names_gorouter_as_an_option(self):
        with pytest.raises(ProviderConfigurationError) as exc:
            provider_config.build_chain({})
        assert "GOROUTER_API_KEY" in str(exc.value)


# --- request generation ---------------------------------------------------


class TestOpenAICompatibleRequest:
    def test_gorouter_reuses_the_shared_transport_abstraction(self):
        """Not a second dialect implementation. Absence of the methods is the proof."""
        assert issubclass(GoRouterProvider, OpenAICompatibleProvider)
        assert GoRouterProvider.default_endpoint == "/chat/completions"
        source = gorouter_source()
        for reimplemented in (
            "def complete",
            "def build_payload",
            "def parse_response",
            "def parse_usage",
            "def _tool_payload",
            "json.loads",
        ):
            assert reimplemented not in source, reimplemented

    def test_the_request_carries_the_model_the_tools_and_the_messages(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport, model="claude-opus-5-thinking").complete(
            MESSAGES, tool_specs()
        )
        payload = transport.requests[0]["payload"]
        assert payload["model"] == "claude-opus-5-thinking"
        assert payload["temperature"] == 0.0
        assert payload["tool_choice"] == "auto"
        assert {t["function"]["name"] for t in payload["tools"]} == {
            s.name for s in tool_specs()
        }
        assert [m["role"] for m in payload["messages"]] == ["system", "user"]

    def test_the_tool_descriptions_are_the_registry_s_own(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(MESSAGES, tool_specs())
        sent = {
            t["function"]["name"]: t["function"]["description"]
            for t in transport.requests[0]["payload"]["tools"]
        }
        assert sent == {spec.name: spec.description for spec in tool_specs()}

    def test_the_request_goes_to_the_gorouter_completions_endpoint(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(MESSAGES, ())
        assert transport.requests[0]["url"] == "https://gorouter.app/v1/chat/completions"
        assert transport.requests[0]["provider"] == "gorouter"

    def test_a_request_with_no_tools_omits_the_tool_fields(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(MESSAGES, ())
        payload = transport.requests[0]["payload"]
        assert "tools" not in payload
        assert "tool_choice" not in payload

    def test_the_credential_reaches_the_transport_and_nowhere_else(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        provider = gorouter(transport)
        response = provider.complete(MESSAGES, tool_specs())
        assert transport.requests[0]["api_key"] == SECRET
        assert SECRET not in json.dumps(transport.requests[0]["payload"])
        assert SECRET not in str(response)
        assert SECRET not in repr(provider.describe())

    def test_the_credential_travels_as_a_bearer_header(self):
        """Asserted against ``post_json``'s own defaults, not a copy of them."""
        import inspect

        parameters = inspect.signature(post_json).parameters
        assert parameters["auth_header"].default == "Authorization"
        assert parameters["auth_prefix"].default == "Bearer "


class TestStrictModeIsGatedOff:
    def test_gorouter_does_not_claim_strict_tool_schemas(self):
        """OpenAI-shaped is not evidence of accepting the strict extension."""
        assert GoRouterProvider.strict_tool_schema_supported is False
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(MESSAGES, tool_specs())
        tools = transport.requests[0]["payload"]["tools"]
        assert tools, "the tools were sent at all"
        assert all("strict" not in tool["function"] for tool in tools)

    def test_the_json_schema_is_identical_to_a_strict_provider_s(self):
        """Only the *claim* is withheld. The contract sent is the same one."""
        gorouter_transport = RecordingTransport(responses=[gorouter_body()])
        openrouter_transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(gorouter_transport).complete(MESSAGES, tool_specs())
        OpenRouterProvider(api_key=SECRET, transport=openrouter_transport).complete(
            MESSAGES, tool_specs()
        )

        def schemas(transport):
            return {
                t["function"]["name"]: t["function"]["parameters"]
                for t in transport.requests[0]["payload"]["tools"]
            }

        assert schemas(gorouter_transport) == schemas(openrouter_transport)
        for schema in schemas(gorouter_transport).values():
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])

    def test_strict_mode_can_be_switched_on_when_evidence_exists(self):
        """Capability-gated, not hard-coded: the gate opens without a code change."""
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport, strict_tool_schema=True).complete(MESSAGES, tool_specs())
        tools = transport.requests[0]["payload"]["tools"]
        assert all(tool["function"]["strict"] is True for tool in tools)

    def test_the_other_providers_still_declare_strict_mode(self):
        """Gating one adapter off must not gate the dialect off."""
        from finrecon.agent.providers.groq import GroqProvider

        assert OpenRouterProvider.strict_tool_schema_supported is True
        assert GroqProvider.strict_tool_schema_supported is True
        transport = RecordingTransport(responses=[gorouter_body()])
        OpenRouterProvider(api_key=SECRET, transport=transport).complete(
            MESSAGES, tool_specs()
        )
        tools = transport.requests[0]["payload"]["tools"]
        assert all(tool["function"]["strict"] is True for tool in tools)


# --- tool calls -----------------------------------------------------------


class TestToolCallParsing:
    def test_a_valid_tool_call_is_parsed_with_arguments_left_as_text(self):
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[("compare_reference_fragment", '{"fragment": "PF*******VQ"}')]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert call.tool_name == "compare_reference_fragment"
        assert call.raw_arguments == '{"fragment": "PF*******VQ"}'
        assert call.call_id == "call_0"

    def test_a_valid_call_validates_against_the_unchanged_tool_contract(self):
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[("compare_reference_fragment", '{"fragment": "PF*******VQ"}')]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        _definition, arguments = validate_call(
            response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
        )
        assert arguments.model_dump(mode="json") == {"fragment": "PF*******VQ"}

    def test_compare_reference_fragment_takes_a_fragment_and_nothing_else(self):
        """The controller fans it across the snapshot; the agent supplies one field."""
        spec = next(s for s in tool_specs() if s.name == "compare_reference_fragment")
        assert set(spec.parameters_json_schema["properties"]) == {"fragment"}
        assert spec.parameters_json_schema["required"] == ["fragment"]
        assert spec.parameters_json_schema["additionalProperties"] is False

    def test_a_fragment_only_call_round_trips_through_the_gateway_unchanged(self):
        """The tool contract GoRouter is handed is the fragment-only one."""
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(MESSAGES, tool_specs())
        sent = {
            t["function"]["name"]: t["function"]["parameters"]
            for t in transport.requests[0]["payload"]["tools"]
        }
        assert set(sent["compare_reference_fragment"]["properties"]) == {"fragment"}

    def test_a_candidate_id_on_the_fragment_tool_is_refused_with_strict_mode_off(self):
        """The closed schema still closes without the provider-side guarantee."""
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[
                        (
                            "compare_reference_fragment",
                            '{"fragment": "PF*******VQ", "candidate_id": "cand_1"}',
                        )
                    ]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.SCHEMA_VALIDATION_FAILED

    def test_several_calls_stay_separate_and_keep_request_order(self):
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[
                        ("compare_reference_fragment", '{"fragment": "AAAA"}'),
                        ("lookup_candidate_records", '{"candidate_id": "cand_1"}'),
                        ("compare_reference_fragment", '{"fragment": "BBBB"}'),
                    ]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert [c.tool_name for c in response.tool_calls] == [
            "compare_reference_fragment",
            "lookup_candidate_records",
            "compare_reference_fragment",
        ]
        assert [c.raw_arguments for c in response.tool_calls] == [
            '{"fragment": "AAAA"}',
            '{"candidate_id": "cand_1"}',
            '{"fragment": "BBBB"}',
        ]
        assert [c.call_id for c in response.tool_calls] == ["call_0", "call_1", "call_2"]

    def test_an_answer_with_no_tool_calls_is_not_a_failure(self):
        transport = RecordingTransport(responses=[gorouter_body(text="done")])
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert response.tool_calls == ()
        assert response.text == "done"
        assert response.finish_reason == "stop"


class TestMalformedArgumentsStayMalformed:
    @pytest.mark.parametrize(
        "raw", ["{not json", '{"fragment": }', '{"fragment"', "", "null", "[1,2]"]
    )
    def test_unusable_arguments_reach_the_loop_byte_for_byte(self, raw):
        """The adapter must not repair them: the loop has to see the behaviour."""
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_reference_fragment", raw)])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].raw_arguments == raw

    def test_malformed_json_is_refused_by_validation_not_by_the_adapter(self):
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_reference_fragment", "{not json")])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.MALFORMED_ARGUMENTS_JSON

    def test_arguments_that_decode_to_a_non_object_are_refused(self):
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_reference_fragment", "[1,2]")])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.MALFORMED_ARGUMENTS_JSON

    def test_an_unknown_tool_name_survives_unrepaired_and_is_refused(self):
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_everything", '{"fragment": "A"}')])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].tool_name == "compare_everything"
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.UNKNOWN_TOOL

    def test_a_missing_tool_name_becomes_empty_rather_than_a_guess(self):
        transport = RecordingTransport(
            responses=[
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {"id": "c1", "type": "function", "function": {}}
                                ],
                            }
                        }
                    ]
                }
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert response.tool_calls[0].tool_name == ""
        with pytest.raises(ToolValidationError) as exc:
            validate_call("", "{}")
        assert exc.value.reason == ToolValidationError.UNKNOWN_TOOL


class TestDuplicateKeysStayFailClosed:
    """Strict mode is off for GoRouter, so this is the layer that decides.

    A duplicate object key is the dominant observed malformed-call shape.
    ``json.loads`` keeps the last value silently, which would turn a
    contradictory call into a plausible one, so the decoder refuses instead.
    """

    def test_raw_arguments_preserve_both_keys_before_parsing(self):
        """Preserved *before* parsing, or the duplicate could not be detected."""
        duplicated = '{"fragment":"PF*******VQ","fragment":"RTGS"}'
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_reference_fragment", duplicated)])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        raw = response.tool_calls[0].raw_arguments
        assert raw == duplicated
        assert raw.count('"fragment"') == 2, (
            "normalizing to one key here would silently defeat the decoder"
        )

    def test_a_duplicate_key_is_refused_on_gorouter_with_strict_mode_off(self):
        duplicated = '{"fragment":"PF*******VQ","fragment":"RTGS"}'
        transport = RecordingTransport(
            responses=[gorouter_body(calls=[("compare_reference_fragment", duplicated)])]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.DUPLICATE_ARGUMENT_KEY

    def test_a_duplicate_candidate_id_is_refused_too(self):
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[
                        (
                            "lookup_candidate_records",
                            '{"candidate_id":"cand_1","candidate_id":"cand_2"}',
                        )
                    ]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.DUPLICATE_ARGUMENT_KEY

    def test_a_duplicate_key_in_one_call_of_a_batch_is_still_caught(self):
        """Preflight is per call, so a valid sibling cannot launder a bad one."""
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[
                        ("compare_reference_fragment", '{"fragment": "AAAA"}'),
                        (
                            "compare_reference_fragment",
                            '{"fragment":"BBBB","fragment":"CCCC"}',
                        ),
                    ]
                )
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        first = validate_call(
            response.tool_calls[0].tool_name, response.tool_calls[0].raw_arguments
        )
        assert first[1].model_dump(mode="json") == {"fragment": "AAAA"}
        with pytest.raises(ToolValidationError) as exc:
            validate_call(
                response.tool_calls[1].tool_name, response.tool_calls[1].raw_arguments
            )
        assert exc.value.reason == ToolValidationError.DUPLICATE_ARGUMENT_KEY

    def test_an_object_argument_is_serialized_rather_than_merged_upstream(self):
        """A non-string ``arguments`` is JSON-encoded; nothing else is inferred."""
        transport = RecordingTransport(
            responses=[
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "compare_reference_fragment",
                                            "arguments": {"fragment": "AAAA"},
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ]
        )
        response = gorouter(transport).complete(MESSAGES, tool_specs())
        assert json.loads(response.tool_calls[0].raw_arguments) == {"fragment": "AAAA"}


class TestToolResultContinuation:
    def test_a_tool_result_turn_is_sent_back_in_the_openai_shape(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        gorouter(transport).complete(
            MESSAGES
            + (
                ConversationMessage(
                    role="assistant",
                    content="checking",
                    tool_calls=(
                        tool_call(
                            "compare_reference_fragment", {"fragment": "AAAA"}, call_id="a"
                        ),
                    ),
                ),
                ConversationMessage(
                    role="tool",
                    content='{"comparisons":[]}',
                    tool_call_id="a",
                    tool_name="compare_reference_fragment",
                ),
            ),
            tool_specs(),
        )
        messages = transport.requests[0]["payload"]["messages"]
        assistant = messages[2]
        assert assistant["role"] == "assistant"
        assert assistant["tool_calls"][0]["id"] == "a"
        assert (
            assistant["tool_calls"][0]["function"]["arguments"] == '{"fragment": "AAAA"}'
        )
        assert messages[3] == {
            "role": "tool",
            "tool_call_id": "a",
            "content": '{"comparisons":[]}',
        }

    def test_a_multi_step_tool_loop_keeps_call_ids_and_order_across_turns(self):
        transport = RecordingTransport(
            responses=[
                gorouter_body(
                    calls=[
                        ("compare_reference_fragment", '{"fragment": "AAAA"}'),
                        ("compare_reference_fragment", '{"fragment": "BBBB"}'),
                    ]
                ),
                gorouter_body(text="that settles it"),
            ]
        )
        provider = gorouter(transport)
        first = provider.complete(MESSAGES, tool_specs())
        conversation = list(MESSAGES)
        conversation.append(
            ConversationMessage(
                role="assistant", content=first.text, tool_calls=first.tool_calls
            )
        )
        for call in first.tool_calls:
            conversation.append(
                ConversationMessage(
                    role="tool",
                    content='{"comparisons":[]}',
                    tool_call_id=call.call_id,
                    tool_name=call.tool_name,
                )
            )
        second = provider.complete(tuple(conversation), tool_specs())
        assert second.tool_calls == ()
        assert second.text == "that settles it"
        sent = transport.requests[1]["payload"]["messages"]
        assert [m.get("tool_call_id") for m in sent[-2:]] == ["call_0", "call_1"]
        assert [c["id"] for c in sent[-3]["tool_calls"]] == ["call_0", "call_1"]

    def test_the_tools_are_re_declared_on_every_turn_of_the_loop(self):
        transport = RecordingTransport(
            responses=[gorouter_body(), gorouter_body(text="done")]
        )
        provider = gorouter(transport)
        provider.complete(MESSAGES, tool_specs())
        provider.complete(MESSAGES, tool_specs())
        for request in transport.requests:
            assert {t["function"]["name"] for t in request["payload"]["tools"]} == {
                s.name for s in tool_specs()
            }


# --- telemetry ------------------------------------------------------------


class TestReturnedModelTelemetry:
    def test_the_requested_and_returned_models_are_recorded_separately(self):
        transport = RecordingTransport(responses=[gorouter_body(model="claude-opus-5")])
        response = gorouter(transport, model="claude-opus-5-thinking").complete(
            MESSAGES, ()
        )
        assert response.provider == "gorouter"
        assert response.model == "claude-opus-5-thinking"
        assert response.reported_model == "claude-opus-5"

    def test_a_body_that_names_no_model_reports_none_rather_than_the_request(self):
        """"It did not say" and "it said the same thing" are different facts."""
        transport = RecordingTransport(responses=[gorouter_body(model=None)])
        response = gorouter(transport, model="claude-opus-5-thinking").complete(
            MESSAGES, ()
        )
        assert response.model == "claude-opus-5-thinking"
        assert response.reported_model is None

    @pytest.mark.parametrize("value", ["", "   ", 7, None, {"name": "x"}])
    def test_a_model_field_that_is_not_a_usable_string_is_not_recorded(self, value):
        body = gorouter_body(model=None)
        body["model"] = value
        transport = RecordingTransport(responses=[body])
        assert gorouter(transport).complete(MESSAGES, ()).reported_model is None

    def test_latency_is_measured_on_every_answered_turn(self):
        transport = RecordingTransport(responses=[gorouter_body()])
        response = gorouter(transport).complete(MESSAGES, ())
        assert response.latency_ms is not None
        assert response.latency_ms >= 0

    def test_the_returned_model_and_usage_reach_the_trajectory_step_record(self):
        from finrecon.agent.loop import _usage_record
        from finrecon.agent.trajectory import ModelStepRecord

        transport = RecordingTransport(
            responses=[gorouter_body(model="claude-opus-5", usage=LIVE_USAGE)]
        )
        response = gorouter(transport, model="claude-opus-5-thinking").complete(
            MESSAGES, ()
        )
        step = ModelStepRecord(
            index=1,
            provider=response.provider,
            model=response.model,
            reported_model=response.reported_model,
            fallback_used=False,
            fallback_reason=None,
            transport_attempts=1,
            attempts=(),
            latency_ms=response.latency_ms,
            usage=_usage_record(ChainResult(response=response, attempts=())),
            finish_reason=response.finish_reason,
            assistant_text=response.text,
            requested_tool_calls=(),
        )
        assert step.provider == "gorouter"
        assert step.model == "claude-opus-5-thinking"
        assert step.reported_model == "claude-opus-5"
        assert step.usage.input_tokens == 7382
        assert step.usage.output_tokens == 8
        assert step.usage.total_tokens == 7390
        assert step.usage.usage_source == "anthropic"
        assert step.usage.raw == LIVE_USAGE
        assert step.latency_ms is not None

    def test_a_trajectory_distinguishes_requested_from_answered_models(self):
        trajectory = trajectory_with(
            provider="gorouter",
            model="claude-opus-5-thinking",
            reported_model="claude-opus-5",
        )
        assert trajectory.models_used == ("gorouter:claude-opus-5-thinking",)
        assert trajectory.models_reported == ("gorouter:claude-opus-5",)
        assert trajectory.providers_used == ("gorouter",)

    def test_a_step_with_no_reported_model_is_absent_from_models_reported(self):
        trajectory = trajectory_with(provider="gorouter", model="claude-opus-5-thinking")
        assert trajectory.models_used == ("gorouter:claude-opus-5-thinking",)
        assert trajectory.models_reported == ()

    def test_a_fallback_reason_stays_recorded_alongside_the_model_telemetry(self):
        trajectory = trajectory_with(
            provider="gorouter",
            model="claude-opus-5-thinking",
            reported_model="claude-opus-5",
            fallback_used=True,
            fallback_reason=ProviderInfrastructureError.RATE_LIMITED,
        )
        assert trajectory.fallback_used is True
        assert trajectory.fallback_reasons == (ProviderInfrastructureError.RATE_LIMITED,)

    def test_the_step_record_round_trips_through_json(self):
        from finrecon.agent.trajectory import ModelStepRecord

        original = trajectory_with(
            provider="gorouter",
            model="claude-opus-5-thinking",
            reported_model="claude-opus-5",
        ).steps[0]
        restored = ModelStepRecord.model_validate_json(
            json.dumps(original.model_dump(mode="json"))
        )
        assert restored == original


class TestUsageNormalization:
    """Two names for one number, observed disagreeing. Selected, never computed."""

    def test_the_live_response_normalizes_to_the_canonical_names(self):
        transport = RecordingTransport(responses=[gorouter_body(usage=LIVE_USAGE)])
        usage = gorouter(transport).complete(MESSAGES, ()).usage
        assert usage.input_tokens == 7382
        assert usage.output_tokens == 8, (
            "completion_tokens is the canonical name and wins over output_tokens"
        )
        assert usage.total_tokens == 7390

    def test_the_disagreement_is_preserved_rather_than_reconciled(self):
        transport = RecordingTransport(responses=[gorouter_body(usage=LIVE_USAGE)])
        usage = gorouter(transport).complete(MESSAGES, ()).usage
        assert usage.raw == LIVE_USAGE
        assert usage.raw["completion_tokens"] == 8
        assert usage.raw["output_tokens"] == 0

    def test_no_count_is_summed_averaged_or_invented(self):
        """Every normalized count is a value the provider actually reported.

        The block below is chosen so no arithmetic over it lands on a reported
        number: 100 + 7 is 107, and a missing total stays missing rather than
        being filled in from the parts. Against ``LIVE_USAGE`` this assertion
        would pass by coincidence, since 7382 + 8 happens to equal the reported
        7390.
        """
        block = {"prompt_tokens": 100, "completion_tokens": 7}
        usage = normalize_usage({"usage": block})
        assert usage.input_tokens == 100
        assert usage.output_tokens == 7
        assert usage.total_tokens is None, "an absent total is absent, not derived"
        for value in (usage.input_tokens, usage.output_tokens):
            assert value in set(block.values())

    def test_the_usage_source_attribution_is_lifted(self):
        transport = RecordingTransport(responses=[gorouter_body(usage=LIVE_USAGE)])
        assert gorouter(transport).complete(MESSAGES, ()).usage.usage_source == "anthropic"

    def test_a_usage_source_that_is_not_a_string_is_dropped(self):
        usage = normalize_usage({"usage": {"prompt_tokens": 1, "usage_source": 7}})
        assert usage.usage_source is None
        assert usage.input_tokens == 1

    def test_the_alias_pair_is_used_only_when_the_canonical_name_is_absent(self):
        usage = normalize_usage(
            {"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
        )
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (
            100,
            20,
            120,
        )

    def test_a_canonical_name_present_and_zero_still_beats_the_alias(self):
        """Zero is a reported measurement, not a missing one."""
        usage = normalize_usage({"usage": {"completion_tokens": 0, "output_tokens": 99}})
        assert usage.output_tokens == 0

    def test_a_partial_usage_block_leaves_the_rest_none(self):
        usage = normalize_usage({"usage": {"prompt_tokens": 7382}})
        assert usage.input_tokens == 7382
        assert usage.output_tokens is None
        assert usage.total_tokens is None
        assert usage.is_empty() is False

    @pytest.mark.parametrize("value", ["7382", 73.82, None, True, [7382], {"n": 1}])
    def test_a_count_that_is_not_an_integer_is_not_coerced(self, value):
        """Coercing one would be the adapter deciding what the provider meant."""
        usage = normalize_usage({"usage": {"prompt_tokens": value}})
        assert usage.input_tokens is None

    def test_a_non_integer_canonical_count_falls_through_to_the_alias(self):
        usage = normalize_usage({"usage": {"completion_tokens": "8", "output_tokens": 8}})
        assert usage.output_tokens == 8

    def test_a_missing_usage_block_is_not_an_error(self):
        transport = RecordingTransport(responses=[gorouter_body(usage=None)])
        usage = gorouter(transport).complete(MESSAGES, ()).usage
        assert usage.is_empty()
        assert usage.raw is None
        assert usage.usage_source is None

    @pytest.mark.parametrize("block", ["nope", 7, [], None])
    def test_a_usage_block_of_the_wrong_shape_is_empty_not_a_failure(self, block):
        assert normalize_usage({"usage": block}).is_empty()

    def test_metadata_alone_does_not_count_as_reported_usage(self):
        usage = normalize_usage({"usage": {"usage_source": "anthropic"}})
        assert usage.is_empty() is True
        assert usage.usage_source == "anthropic"

    def test_the_raw_block_is_copied_not_aliased(self):
        block = dict(LIVE_USAGE)
        usage = normalize_usage({"usage": block})
        block["total_tokens"] = 1
        assert usage.raw["total_tokens"] == 7390

    def test_the_existing_providers_normalize_exactly_as_before(self):
        """A canonical-only block yields the same three counts it always did."""
        canonical = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
        usage = normalize_usage({"usage": canonical})
        assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (11, 3, 14)
        assert usage.usage_source is None

    def test_usage_survives_the_trajectory_round_trip_verbatim(self):
        from finrecon.agent.trajectory import UsageRecord

        record = UsageRecord(
            input_tokens=7382,
            output_tokens=8,
            total_tokens=7390,
            usage_source="anthropic",
            raw=dict(LIVE_USAGE),
        )
        restored = UsageRecord.model_validate_json(
            json.dumps(record.model_dump(mode="json"))
        )
        assert restored == record
        assert restored.raw["output_tokens"] == 0

    def test_a_cache_v3_usage_record_without_the_new_fields_still_validates(self):
        """Why ``trajectory-cache.v3`` needed no bump: v3 fixtures still load."""
        from finrecon.agent.trajectory import UsageRecord

        restored = UsageRecord.model_validate_json(
            '{"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}'
        )
        assert (restored.input_tokens, restored.output_tokens) == (11, 3)
        assert restored.usage_source is None
        assert restored.raw is None

    def test_trajectory_token_totals_sum_the_normalized_counts_only(self):
        trajectory = trajectory_with(
            provider="gorouter",
            model="claude-opus-5-thinking",
            reported_model="claude-opus-5",
            usage_block=LIVE_USAGE,
        )
        assert trajectory.input_tokens() == 7382
        assert trajectory.output_tokens() == 8
        assert trajectory.total_tokens() == 7390


# --- infrastructure classification ---------------------------------------


class TestInfrastructureClassification:
    @pytest.mark.parametrize(
        "status,kind",
        [
            (429, ProviderInfrastructureError.RATE_LIMITED),
            (402, ProviderInfrastructureError.QUOTA_EXHAUSTED),
            (500, ProviderInfrastructureError.SERVER_ERROR),
            (502, ProviderInfrastructureError.SERVER_ERROR),
            (503, ProviderInfrastructureError.SERVER_ERROR),
            (504, ProviderInfrastructureError.SERVER_ERROR),
        ],
    )
    def test_availability_failures_are_infrastructure_and_permit_fallback(
        self, status, kind
    ):
        error = classify_http_status("gorouter", status, '{"error":"upstream"}')
        assert isinstance(error, ProviderInfrastructureError)
        assert error.kind == kind
        assert error.permits_provider_fallback is True
        assert error.provider == "gorouter"

    def test_a_rate_limit_is_not_retried_against_the_same_gateway(self):
        """It will not clear in milliseconds; retrying only burns the budget."""
        assert classify_http_status("gorouter", 429, "{}").permits_transport_retry is False

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_a_server_error_is_worth_one_bounded_retry(self, status):
        assert (
            classify_http_status("gorouter", status, "{}").permits_transport_retry is True
        )

    @pytest.mark.parametrize("status", [401, 403])
    def test_an_unauthorized_key_is_configuration_and_never_falls_over(self, status):
        error = classify_http_status("gorouter", status, "{}")
        assert isinstance(error, ProviderConfigurationError)
        assert error.kind == ProviderConfigurationError.UNAUTHORIZED
        assert error.permits_provider_fallback is False

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_a_rejected_request_shape_is_our_bug_and_stops_the_chain(self, status):
        """Which is what a rejected ``strict`` flag would be -- hence the gate."""
        error = classify_http_status("gorouter", status, "unsupported field: strict")
        assert isinstance(error, ProviderConfigurationError)
        assert error.kind == ProviderConfigurationError.BAD_REQUEST
        assert error.permits_provider_fallback is False

    def test_a_timeout_is_infrastructure_and_retryable(self):
        error = ProviderInfrastructureError(
            "gorouter", ProviderInfrastructureError.TIMEOUT, "no response within 60s"
        )
        assert error.permits_provider_fallback is True
        assert error.permits_transport_retry is True

    @pytest.mark.parametrize(
        "kind",
        [
            ProviderInfrastructureError.TIMEOUT,
            ProviderInfrastructureError.RATE_LIMITED,
            ProviderInfrastructureError.SERVER_ERROR,
        ],
    )
    def test_a_transport_failure_propagates_through_the_adapter_unchanged(self, kind):
        transport = RecordingTransport(
            responses=[ProviderInfrastructureError("gorouter", kind, "detail")]
        )
        with pytest.raises(ProviderInfrastructureError) as exc:
            gorouter(transport).complete(MESSAGES, tool_specs())
        assert exc.value.kind == kind
        assert exc.value.provider == "gorouter"

    def test_a_failed_request_never_puts_the_credential_in_the_exception(self):
        """The key is built inside the transport and reaches no error path.

        A stronger guarantee than remembering to redact: the adapter has no
        code that could copy the key into a message, so the only way one could
        appear is if the *provider* echoed it back in a response body -- which
        is why the credential travels in a header and never in a URL or payload.
        """
        transport = RecordingTransport(
            responses=[
                ProviderConfigurationError(
                    "gorouter", ProviderConfigurationError.UNAUTHORIZED, "HTTP 401"
                )
            ]
        )
        with pytest.raises(ProviderConfigurationError) as exc:
            gorouter(transport).complete(MESSAGES, tool_specs())
        assert SECRET not in str(exc.value)
        assert SECRET not in repr(exc.value)
        request = transport.requests[0]
        assert SECRET not in request["url"]
        assert SECRET not in json.dumps(request["payload"])

    @pytest.mark.parametrize(
        "body", [{}, {"choices": []}, {"choices": [{}]}, {"choices": ["nope"]}]
    )
    def test_a_missing_envelope_is_a_protocol_error_not_model_behaviour(self, body):
        transport = RecordingTransport(responses=[body])
        with pytest.raises(ProviderInfrastructureError) as exc:
            gorouter(transport).complete(MESSAGES, ())
        assert exc.value.kind == ProviderInfrastructureError.PROTOCOL_ERROR

    def test_an_empty_but_decodable_answer_is_model_behaviour_not_infrastructure(self):
        transport = RecordingTransport(responses=[gorouter_body(text="")])
        response = gorouter(transport).complete(MESSAGES, ())
        assert response.text == ""
        assert response.tool_calls == ()

    def test_gorouter_rate_limited_falls_back_and_records_the_reason(self):
        failing = FailingProvider(
            ProviderInfrastructureError(
                "gorouter", ProviderInfrastructureError.RATE_LIMITED, "HTTP 429"
            ),
            provider_id="gorouter",
            model="claude-opus-5-thinking",
        )
        secondary = ScriptedProvider([turn(text="ok")], provider_id="groq", model="c-d")
        result = ProviderChain((failing, secondary), transport_retries=0).complete(
            MESSAGES, ()
        )
        assert result.response.provider == "groq"
        assert result.provider_fallback_used is True
        assert result.fallback_reason == ProviderInfrastructureError.RATE_LIMITED
        assert [a.provider for a in result.attempts] == ["gorouter", "groq"]
        assert result.attempts[0].error_class == "ProviderInfrastructureError"

    def test_a_bad_gorouter_key_stops_the_chain_instead_of_failing_over(self):
        failing = FailingProvider(
            ProviderConfigurationError(
                "gorouter", ProviderConfigurationError.UNAUTHORIZED, "HTTP 401"
            ),
            provider_id="gorouter",
        )
        with pytest.raises(ProviderConfigurationError):
            ProviderChain((failing, ExplodingProvider())).complete(MESSAGES, ())

    def test_gorouter_is_reachable_as_the_last_infrastructure_fallback(self):
        chain = ProviderChain(
            (
                FailingProvider(
                    ProviderInfrastructureError(
                        "openrouter", ProviderInfrastructureError.RATE_LIMITED, "429"
                    ),
                    provider_id="openrouter",
                ),
                ScriptedProvider(
                    [turn(text="ok", provider="gorouter", model="claude-opus-5-thinking")],
                    provider_id="gorouter",
                    model="claude-opus-5-thinking",
                ),
            ),
            transport_retries=0,
        )
        result = chain.complete(MESSAGES, ())
        assert result.response.provider == "gorouter"

    def test_the_chain_describes_gorouter_with_its_model(self):
        chain = provider_config.build_chain(
            {"GOROUTER_API_KEY": "k", "GOROUTER_MODEL": "claude-opus-5-thinking"}
        )
        assert chain.describe() == ("gorouter:claude-opus-5-thinking",)


# --- helpers --------------------------------------------------------------


def trajectory_with(
    *,
    provider: str,
    model: str,
    reported_model: str | None = None,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
    usage_block: dict | None = None,
):
    """A one-step trajectory carrying exactly the telemetry under test.

    Local rather than shared: every other trajectory factory in the suite
    builds one to exercise the validator or the policy gate, and this one
    exists only so the provider fields can be read back off a real record.
    """
    from finrecon.agent.trajectory import (
        TERMINATION_INVESTIGATION_COMPLETE,
        ModelStepRecord,
        Trajectory,
        UsageRecord,
    )

    usage = (
        UsageRecord()
        if usage_block is None
        else UsageRecord(
            input_tokens=usage_block.get("prompt_tokens"),
            output_tokens=usage_block.get("completion_tokens"),
            total_tokens=usage_block.get("total_tokens"),
            usage_source=usage_block.get("usage_source"),
            raw=dict(usage_block),
        )
    )
    return Trajectory(
        case_id="case:bnk_dev_000003",
        snapshot_hash="0" * 64,
        batch_id="batch",
        prompt_version="investigator.v4",
        tool_schema_version="tools.v3",
        agent_loop_version="loop.v2",
        cache_schema_version="trajectory-cache.v3",
        validator_version="validator.v1",
        policy_version="policy.v1",
        policy_declaration={},
        max_steps=8,
        max_tool_calls_per_step=8,
        provider_chain=(f"{provider}:{model}",),
        steps=(
            ModelStepRecord(
                index=1,
                provider=provider,
                model=model,
                reported_model=reported_model,
                fallback_used=fallback_used,
                fallback_reason=fallback_reason,
                transport_attempts=1,
                attempts=(),
                latency_ms=12,
                usage=usage,
                finish_reason="stop",
                assistant_text="",
                requested_tool_calls=(),
            ),
        ),
        tool_invocations=(),
        termination_reason=TERMINATION_INVESTIGATION_COMPLETE,
    )
