"""The suite is offline by construction, and stays offline as providers are added.

This file exists because of a specific incident, and it pins the specific
thing that went wrong rather than the symptom.

``test_investigate_cli.py`` asserted that a live run with no provider
credentials exits non-zero. It cleared the credentials by *naming* them --
``OPENROUTER_API_KEY``, ``GROQ_API_KEY``, ``GEMINI_API_KEY``. When GoRouter
was added as a fourth provider, nothing updated that list, so on a machine
with ``GOROUTER_API_KEY`` exported the test found a configured provider,
ran for real against a billed endpoint, and asserted its way to a failure
only after four model steps had been paid for.

The defect was not the missing name. It was that a hand-maintained list of
providers existed in a test at all. So the tests below assert the *derivation*
-- that the suite's isolation is computed from
:data:`finrecon.agent.providers.config.PROVIDER_SLOTS`, the same registry the
production chain is built from -- which is the only version of this that a
fifth provider cannot break.
"""

from __future__ import annotations

import inspect
import os
import urllib.request

import pytest

from finrecon.agent.providers.config import (
    ENV_PROVIDER_ORDER,
    PROVIDER_SLOTS,
    build_chain,
    configured_provider_ids,
)
from finrecon.agent.providers.base import ProviderConfigurationError
from tests.conftest import PROVIDER_CONFIG_ENV, PROVIDER_CREDENTIAL_ENV


class TestTheGuardsCoverEveryRegisteredProvider:
    def test_every_provider_credential_is_in_the_scrubbed_set(self):
        """Derived from the registry, so a new provider is covered on arrival."""
        registered = {slot.api_key_env for slot in PROVIDER_SLOTS.values()}
        assert registered == set(PROVIDER_CREDENTIAL_ENV)
        assert "GOROUTER_API_KEY" in PROVIDER_CREDENTIAL_ENV, (
            "the credential whose omission caused a paid call from the suite"
        )

    def test_every_configuration_input_is_in_the_scrubbed_set(self):
        """Not just keys: order and model overrides change what a test does."""
        for slot in PROVIDER_SLOTS.values():
            assert slot.api_key_env in PROVIDER_CONFIG_ENV
            assert slot.model_env in PROVIDER_CONFIG_ENV
            assert slot.base_url_env in PROVIDER_CONFIG_ENV
        assert ENV_PROVIDER_ORDER in PROVIDER_CONFIG_ENV

    def test_the_scrubbed_set_is_computed_not_typed(self):
        """A literal list would pass the assertions above and still rot."""
        source = inspect.getsource(
            __import__("tests.conftest", fromlist=["conftest"])
        )
        declaration = source.split("PROVIDER_CREDENTIAL_ENV: tuple[str, ...] =")[1]
        declaration = declaration.split('"""')[0]
        assert "PROVIDER_SLOTS" in declaration
        assert "API_KEY" not in declaration, (
            "a credential named literally is the bug this file exists to prevent"
        )


class TestNoAmbientConfigurationReachesATest:
    @pytest.mark.parametrize("name", PROVIDER_CONFIG_ENV)
    def test_the_variable_is_absent_unless_a_test_sets_it(self, name):
        """Holds even when the developer's shell exports all of them."""
        assert os.environ.get(name) is None

    def test_no_provider_is_configured_by_default(self):
        assert configured_provider_ids() == ()

    def test_building_a_chain_refuses_rather_than_reaching_a_provider(self):
        """The invariant the original test meant to assert, stated directly."""
        with pytest.raises(ProviderConfigurationError) as exc:
            build_chain()
        assert "no provider credentials found" in str(exc.value)

    def test_a_test_can_still_opt_a_credential_in(self, monkeypatch):
        """Scrubbing must not break the tests that legitimately set a key."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        assert configured_provider_ids() == ("openrouter",)


class TestTheNetworkIsUnreachable:
    def test_an_outbound_request_fails_the_test_that_attempts_it(self):
        """The backstop, independent of which credentials happen to exist."""
        with pytest.raises(AssertionError, match="offline by construction"):
            urllib.request.urlopen("https://gorouter.app/v1/chat/completions")

    def test_the_refusal_names_the_url_it_stopped(self):
        with pytest.raises(AssertionError, match="gorouter.app"):
            urllib.request.urlopen("https://gorouter.app/v1/chat/completions")

    def test_a_test_that_stubs_the_transport_still_works(self, monkeypatch):
        """The guard is a default, not a wall: transport tests override it."""

        class _Fake:
            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Fake())
        with urllib.request.urlopen("https://example.invalid") as response:
            assert response.read() == b"{}"
