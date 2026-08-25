"""Shared Stage-2 test fixtures, and the two guards that keep tests offline.

The guards exist because of a real incident. ``test_investigate_cli.py``
asserted that a live run with no provider credentials exits non-zero, and
cleared the credentials by naming them: ``OPENROUTER_API_KEY``,
``GROQ_API_KEY``, ``GEMINI_API_KEY``. Adding GoRouter as a fourth provider
left that list one short, so a developer with ``GOROUTER_API_KEY`` in their
shell ran the "no credentials" test against a real, billed endpoint -- four
model steps, and a stray trajectory written to ``fixtures/``.

A hand-maintained list of provider credentials in a test is the bug. Both
fixtures below derive from :data:`PROVIDER_SLOTS`, the same registry the
production chain builds from, so a fifth provider is covered the moment it
is registered rather than the next time somebody remembers.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from finrecon.agent.providers.config import (
    ENV_PROVIDER_ORDER,
    ENV_TIMEOUT,
    ENV_TRANSPORT_RETRIES,
    PROVIDER_SLOTS,
)
from finrecon.ledger.store import LedgerStore
from finrecon.loader import default_benchmark_dir
from finrecon.pipeline import case_id_for, process_batch

PROVIDER_CREDENTIAL_ENV: tuple[str, ...] = tuple(
    slot.api_key_env for slot in PROVIDER_SLOTS.values()
)
"""Every provider credential variable, read from the production registry.

Derived, never typed out. This tuple is the thing whose staleness caused a
paid call from a test suite that believed it was offline.
"""

PROVIDER_CONFIG_ENV: tuple[str, ...] = (
    PROVIDER_CREDENTIAL_ENV
    + tuple(slot.model_env for slot in PROVIDER_SLOTS.values())
    + tuple(slot.base_url_env for slot in PROVIDER_SLOTS.values())
    + (ENV_PROVIDER_ORDER, ENV_TIMEOUT, ENV_TRANSPORT_RETRIES)
)
"""Every variable :mod:`finrecon.agent.providers.config` reads.

The credential is not the only ambient input that changes what a test does.
The shell that ran the paid GoRouter call also exported
``FINRECON_PROVIDER_ORDER=gorouter`` and four ``*_MODEL`` overrides -- so
"the provider chain" meant something different in that terminal than in CI,
and tests asserting against the default order were passing or failing on a
developer's exports. All of it is cleared, from the registry, for the same
reason.
"""


@pytest.fixture(autouse=True)
def no_ambient_provider_configuration(monkeypatch):
    """No test inherits provider configuration from the developer's shell.

    Autouse and unconditional: a test that wants a credential, a model
    override or a particular order sets it itself with
    ``monkeypatch.setenv``, which still works and is now the *only* way one
    reaches the code under test. The suite therefore behaves identically on
    a laptop with keys and overrides exported and on a CI runner with
    none -- which is what made the original failure invisible until
    GoRouter's key happened to be present.

    This changes no production default. ``DEFAULT_PROVIDER_ORDER`` and the
    fallback rule are untouched; clearing an override simply means tests see
    the documented defaults instead of one machine's environment.
    """
    for name in PROVIDER_CONFIG_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_outbound_http(monkeypatch):
    """The backstop: reaching the network at all fails the test that did it.

    Scrubbing the environment removes the known cause. This removes the
    *capability*, so a future path to a live call -- a hard-coded key, a
    default that stops being empty, a fixture that forgets -- surfaces as a
    loud assertion naming the URL instead of a quiet charge on someone's
    account.

    Tests that exercise the transport replace ``urlopen`` themselves; their
    ``monkeypatch.setattr`` runs after this one and wins for the duration of
    the test.
    """

    def refuse(request, *args, **kwargs):
        url = getattr(request, "full_url", request)
        raise AssertionError(
            f"a test attempted a real HTTP request to {url!r}; the suite is "
            "offline by construction -- stub the transport instead"
        )

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture(scope="session")
def benchmark_dir() -> Path:
    return default_benchmark_dir()


@pytest.fixture(scope="session")
def dev_result(benchmark_dir):
    """One deterministic pass over DEV, shared across tests (it is read-only)."""
    store = LedgerStore(":memory:")
    result = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
    yield result, store
    store.close()


@pytest.fixture(scope="session")
def dev_ground_truth(benchmark_dir):
    """DEV ground truth, keyed by Stage-2 case ID.

    **Test-only.** DESIGN.md §9 keeps ground truth hidden from the system;
    it is loaded here so a development diagnostic can say whether the
    deterministic rules are *right*, not merely self-consistent. Nothing
    under ``src/finrecon`` may reach it, which
    ``test_benchmark_isolation.py`` asserts structurally.

    For FROZEN-EVAL truth, see ``frozen_eval_tier_labels`` — deliberately a
    separate, narrower fixture.
    """
    path = benchmark_dir / "ground_truth" / "dev.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {case_id_for(e["record_ids"]["bank_records"][0]): e for e in entries}


@pytest.fixture(scope="session")
def frozen_eval_tier_labels(benchmark_dir):
    """FROZEN-EVAL **tier labels only**, keyed by Stage-2 case ID.

    Deliberately narrow. This fixture exposes each case's ``tier`` and
    ``archetype`` and nothing else — not ``correct_relationship``, not
    ``required_outcome``, not ``true_reference``. It exists for one purpose:
    asserting that a tier is resolved by the *mechanism* its definition
    names (T0 by direct key, T1 by derivation), which is a benchmark-integrity
    property, not an accuracy measurement.

    **Why this is not a hole in the freeze protocol.** DESIGN.md §5.1 step 7
    says build against DEV and report against FROZEN, and the risk it
    guards against is tuning: repeatedly reading held-out *outcomes* and
    adjusting rules until they improve. Tier labels cannot support that —
    they say what a case is meant to test, not what answer it has. The
    benchmark v3 defect (``benchmark/manifests/CHANGELOG.md``) was invisible
    precisely because no test ever compared the two splits' mechanisms, so
    the fix has to include a test that does.

    **Do not widen this fixture during Stage 3.** If a Stage-3 change wants
    FROZEN-EVAL outcomes, that is the evaluation harness's job (Stage 4),
    run once against a frozen system — not a fixture consulted while
    iterating.
    """
    path = benchmark_dir / "ground_truth" / "frozen-eval.jsonl"
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {
        case_id_for(e["record_ids"]["bank_records"][0]): {
            "tier": e["tier"],
            "archetype": e["archetype"],
        }
        for e in entries
    }


@pytest.fixture(scope="session")
def dev_stage3_result(benchmark_dir, tmp_path_factory):
    """One full Stage-3 pass over every DEV case Stage 2 left unresolved.

    **DEV-only, and driven by a deterministic fake provider.** No model is
    involved: :class:`tests.stage3_fakes.MechanicalInvestigator` enumerates
    narration fragments mechanically because it cannot read. Anything
    measured from this fixture is a statement about the *plumbing* -- the
    loop, the tools, the validator, the policy gate and the ledger working
    end to end -- and never about model capability. FROZEN-EVAL outcomes are
    not touched anywhere in this file.
    """
    from finrecon.agent.cache import TrajectoryCache
    from finrecon.agent.providers.chain import ProviderChain
    from finrecon.stage3 import run_stage3
    from tests.stage3_fakes import MechanicalInvestigator

    store = LedgerStore(":memory:")
    batch = process_batch(store=store, benchmark_dir=benchmark_dir, split="dev")
    result = run_stage3(
        store=store,
        batch_result=batch,
        chain=ProviderChain((MechanicalInvestigator(),)),
        cache=TrajectoryCache(tmp_path_factory.mktemp("dev-trajectories")),
    )
    yield result, batch, store
    store.close()
