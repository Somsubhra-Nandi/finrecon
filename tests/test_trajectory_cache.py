"""Trajectory caching and replay: the mechanism ``make eval`` will stand on.

DESIGN.md §6.2 promises a reviewer can reproduce the reported numbers from a
clean clone with no API key. Two things have to be true for that promise to
survive contact with a multi-turn agent, and both are tested here:

1. **Replay makes zero provider calls.** Proven by handing the replay path a
   provider that raises on contact, not by counting calls after the fact.
2. **The key covers everything that could change what the model saw.** A
   different snapshot, model, prompt, tool contract or step budget must miss
   the cache. Serving a stale trajectory would look reproducible while being
   wrong, which is worse than no cache at all.
"""

from __future__ import annotations

import json

import pytest

from finrecon.agent import cache as cache_module
from finrecon.agent.cache import (
    ReplayMissError,
    TrajectoryCache,
    cache_key,
    cache_key_inputs,
    investigate_case,
)
from finrecon.agent.loop import LoopConfig, run_investigation
from finrecon.agent.providers.chain import ProviderChain
from finrecon.agent.trajectory import Trajectory
from tests.stage3_factories import settlement_facts, snapshot_of, two_candidate_snapshot
from tests.stage3_fakes import ExplodingProvider, MechanicalInvestigator

SECRET = "sk-must-never-be-written-0123456789"


@pytest.fixture
def snapshot():
    return two_candidate_snapshot()


@pytest.fixture
def cache(tmp_path):
    return TrajectoryCache(tmp_path / "trajectories")


@pytest.fixture
def chain():
    return ProviderChain((MechanicalInvestigator(),))


class TestCacheKey:
    def test_the_key_covers_every_materially_relevant_input(self):
        fields = set(cache_key_inputs.__annotations__) | set(
            cache_module.CacheKeyInputs.__dataclass_fields__
        )
        for required in (
            "snapshot_hash",
            "case_id",
            "provider",
            "model",
            "prompt_version",
            "tool_schema_version",
            "agent_loop_version",
            "cache_schema_version",
            "max_steps",
        ):
            assert required in fields

    def test_the_same_configuration_gives_the_same_key(self, snapshot):
        first = cache_key(snapshot, provider="openrouter", model="m")
        second = cache_key(snapshot, provider="openrouter", model="m")
        assert first == second

    def test_a_different_model_gives_a_different_key(self, snapshot):
        assert cache_key(snapshot, provider="openrouter", model="a") != cache_key(
            snapshot, provider="openrouter", model="b"
        )

    def test_a_different_provider_gives_a_different_key(self, snapshot):
        assert cache_key(snapshot, provider="openrouter", model="m") != cache_key(
            snapshot, provider="groq", model="m"
        )

    def test_a_different_step_budget_gives_a_different_key(self, snapshot):
        assert cache_key(snapshot, provider="p", model="m", max_steps=4) != cache_key(
            snapshot, provider="p", model="m", max_steps=8
        )

    def test_a_different_case_gives_a_different_key(self, snapshot):
        other = snapshot_of(
            narration="NEFT CR REF ZZZZZZZZZ",
            settlements=(settlement_facts("setl_zzz", "ZZZZZZZZZ"),),
            bank_record_id="bnk_test_9999",
        )
        assert cache_key(snapshot, provider="p", model="m") != cache_key(
            other, provider="p", model="m"
        )

    def test_a_changed_case_content_gives_a_different_key(self, snapshot):
        """The snapshot hash is in the key, so editing the narration misses."""
        altered = two_candidate_snapshot(narration="RTGS CR REF QQ*******ZZ OTHER")
        assert snapshot.content_hash != altered.content_hash
        assert cache_key(snapshot, provider="p", model="m") != cache_key(
            altered, provider="p", model="m"
        )

    @pytest.mark.parametrize(
        "version_attr", ["prompt_version", "tool_schema_version", "agent_loop_version"]
    )
    def test_bumping_a_version_invalidates_the_entry(self, snapshot, version_attr):
        base = cache_key_inputs(snapshot, provider="p", model="m")
        bumped = cache_module.CacheKeyInputs(
            **{**base.__dict__, version_attr: "bumped.v2"}
        )
        assert base.key() != bumped.key()

    def test_the_canonical_form_is_order_independent(self, snapshot):
        inputs = cache_key_inputs(snapshot, provider="p", model="m")
        assert json.loads(inputs.canonical()) == json.loads(inputs.canonical())
        assert list(json.loads(inputs.canonical())) == sorted(
            json.loads(inputs.canonical())
        )


class TestStoreAndLoad:
    def test_a_stored_trajectory_round_trips(self, snapshot, chain, cache):
        trajectory = run_investigation(snapshot=snapshot, chain=chain, cache_key="k1")
        cache.store("k1", trajectory)
        loaded = cache.load("k1")
        assert loaded is not None
        assert loaded.case_id == trajectory.case_id
        assert loaded.step_count == trajectory.step_count
        assert loaded.tool_invocations == trajectory.tool_invocations
        assert loaded.termination_reason == trajectory.termination_reason

    def test_a_loaded_trajectory_is_marked_replayed(self, snapshot, chain, cache):
        cache.store("k1", run_investigation(snapshot=snapshot, chain=chain))
        assert cache.load("k1").replayed is True

    def test_a_stored_trajectory_is_written_unreplayed(self, snapshot, chain, cache):
        path = cache.store("k1", run_investigation(snapshot=snapshot, chain=chain))
        assert json.loads(path.read_text(encoding="utf-8"))["replayed"] is False

    def test_a_miss_returns_none(self, cache):
        assert cache.load("no-such-key") is None
        assert cache.has("no-such-key") is False

    def test_an_existing_entry_is_not_silently_overwritten(self, snapshot, chain, cache):
        """A hosted model drifts; a re-run must not rewrite a committed fixture."""
        first = run_investigation(snapshot=snapshot, chain=chain)
        cache.store("k1", first)
        replacement = first.model_copy(update={"termination_reason": "step_budget_exhausted"})
        cache.store("k1", replacement)
        assert cache.load("k1").termination_reason == first.termination_reason

    def test_overwrite_is_available_but_must_be_asked_for(self, snapshot, chain, cache):
        first = run_investigation(snapshot=snapshot, chain=chain)
        cache.store("k1", first)
        replacement = first.model_copy(update={"termination_reason": "step_budget_exhausted"})
        cache.store("k1", replacement, overwrite=True)
        assert cache.load("k1").termination_reason == "step_budget_exhausted"

    def test_the_fixture_file_is_human_readable_json(self, snapshot, chain, cache):
        path = cache.store("k1", run_investigation(snapshot=snapshot, chain=chain))
        text = path.read_text(encoding="utf-8")
        assert text.startswith("{\n"), "indented so a reviewer can read a trajectory"
        assert text.endswith("\n")


class TestReplayMakesNoProviderCalls:
    def test_a_warmed_cache_serves_a_case_without_touching_a_provider(
        self, snapshot, chain, cache
    ):
        first = investigate_case(snapshot, chain=chain, cache=cache)
        assert first.cache_hit is False

        exploding = ProviderChain((ExplodingProvider(),))
        second = investigate_case(
            snapshot,
            chain=exploding,
            cache=cache,
            provider_id="mechanical",
            model="mechanical-investigator-v1",
        )
        assert second.cache_hit is True
        assert second.made_provider_calls is False
        assert second.trajectory.replayed is True

    def test_replay_only_mode_needs_no_provider_object_at_all(self, snapshot, chain, cache):
        investigate_case(snapshot, chain=chain, cache=cache)
        outcome = investigate_case(
            snapshot,
            chain=None,
            cache=cache,
            replay_only=True,
            provider_id="mechanical",
            model="mechanical-investigator-v1",
        )
        assert outcome.cache_hit is True

    def test_replay_only_raises_on_a_miss_rather_than_calling_out(self, snapshot, cache):
        with pytest.raises(ReplayMissError):
            investigate_case(
                snapshot,
                chain=None,
                cache=cache,
                replay_only=True,
                provider_id="openrouter",
                model="m",
            )

    def test_replayed_evidence_is_identical_to_the_evidence_first_gathered(
        self, snapshot, chain, cache
    ):
        live = investigate_case(snapshot, chain=chain, cache=cache)
        replayed = investigate_case(
            snapshot,
            chain=ProviderChain((ExplodingProvider(),)),
            cache=cache,
            provider_id="mechanical",
            model="mechanical-investigator-v1",
        )
        assert [i.output for i in live.trajectory.tool_invocations] == [
            i.output for i in replayed.trajectory.tool_invocations
        ]

    def test_a_configuration_change_misses_and_would_need_a_provider(
        self, snapshot, chain, cache
    ):
        investigate_case(snapshot, chain=chain, cache=cache)
        with pytest.raises(ReplayMissError):
            investigate_case(
                snapshot,
                chain=None,
                cache=cache,
                config=LoopConfig(max_steps=3),
                replay_only=True,
                provider_id="mechanical",
                model="mechanical-investigator-v1",
            )

    def test_the_cache_key_is_recorded_on_the_outcome_and_the_trajectory(
        self, snapshot, chain, cache
    ):
        outcome = investigate_case(snapshot, chain=chain, cache=cache)
        assert outcome.cache_key
        assert outcome.trajectory.cache_key == outcome.cache_key
        assert cache.keys() == (outcome.cache_key,)


class TestNoSecretsInTrajectories:
    def test_a_trajectory_never_contains_an_api_key(self, snapshot, cache, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
        monkeypatch.setenv("GROQ_API_KEY", SECRET)
        monkeypatch.setenv("GEMINI_API_KEY", SECRET)
        trajectory = run_investigation(
            snapshot=snapshot, chain=ProviderChain((MechanicalInvestigator(),))
        )
        path = cache.store("k1", trajectory)
        assert SECRET not in path.read_text(encoding="utf-8")
        assert SECRET not in json.dumps(trajectory.model_dump(mode="json"))

    def test_the_trajectory_model_has_no_credential_field(self):
        for field in Trajectory.model_fields:
            assert "key" not in field or field == "cache_key"
            assert "token" not in field
            assert "secret" not in field
            assert "auth" not in field


def test_investigate_case_needs_an_identity_to_key_on(snapshot, cache):
    with pytest.raises(ValueError):
        investigate_case(snapshot, chain=None, cache=cache)
