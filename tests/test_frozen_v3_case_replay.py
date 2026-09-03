"""Frozen Eval v3 case-level replay remains complete, offline, and read-only."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.api.app import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=tmp_path / "ledger.sqlite3")) as value:
        yield value


def _corpus_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.glob("*.json")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_frozen_case_cohorts_have_exact_replay_availability(client: TestClient):
    catalog = client.app.state.benchmark_catalog
    projection = catalog._v3_case_evaluations
    counts = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"limit": 1}).json()["counts"]

    assert counts == {
        "all": 890,
        "stage2": 650,
        "investigations": 240,
        "t2": 200,
        "t3": 40,
        "provider_failure": 7,
    }
    stage2_ids = [case_id for case_id, item in projection.items() if item["resolution_stage"] == "STAGE_2"]
    t2_ids = [case_id for case_id, item in projection.items() if item["tier"] == "T2"]
    t3_ids = [case_id for case_id, item in projection.items() if item["tier"] == "T3"]
    assert len(stage2_ids) == 650
    assert len(t2_ids) == 200
    assert len(t3_ids) == 40
    assert all(catalog._replay_ids("frozen-eval-v3", case_id) == [] for case_id in stage2_ids)
    assert all(catalog._replay_ids("frozen-eval-v3", case_id) == ["opus-provider-recovered"] for case_id in t2_ids + t3_ids)
    assert all(catalog._v3_trajectory_metadata(case_id) is not None for case_id in t2_ids + t3_ids)

    termination_counts: dict[str, int] = {}
    for case_id in t3_ids:
        reason = projection[case_id]["termination_reason"]
        termination_counts[reason] = termination_counts.get(reason, 0) + 1
    assert termination_counts == {"investigation_complete": 33, "provider_infrastructure_failure": 7}


def test_representative_frozen_replays_are_offline_safe_and_truth_free(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    catalog = client.app.state.benchmark_catalog
    projection = catalog._v3_case_evaluations
    t2 = next(case_id for case_id, item in projection.items() if item["tier"] == "T2" and case_id not in catalog._v3_original_failure_cases)
    recovered_t2 = next(iter(catalog._v3_original_failure_cases))
    t3_complete = next(case_id for case_id, item in projection.items() if item["tier"] == "T3" and item["termination_reason"] == "investigation_complete")
    t3_provider = next(case_id for case_id, item in projection.items() if item["tier"] == "T3" and item["termination_reason"] == "provider_infrastructure_failure")
    stage2 = next(case_id for case_id, item in projection.items() if item["resolution_stage"] == "STAGE_2")

    monkeypatch.delenv("GOROUTER_API_KEY", raising=False)
    monkeypatch.setattr("finrecon.orchestrate.build_chain", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider chain must not be constructed")))
    hidden = catalog.benchmark_root / "ground_truth" / "frozen-eval.jsonl"
    original_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *args, **kwargs: (_ for _ in ()).throw(AssertionError("hidden truth must not enter replay")) if path == hidden else original_read_text(path, *args, **kwargs))

    for case_id, expected_outcome in ((t2, "RESOLVE"), (recovered_t2, "RESOLVE"), (t3_complete, "ESCALATE"), (t3_provider, "ESCALATE")):
        response = client.get(f"/api/benchmarks/frozen-eval-v3/replays/opus-provider-recovered/{case_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["provider_calls_made"] is False
        assert body["replayed"] is True
        assert body["policy_result"]["outcome"] == expected_outcome
        assert "assistant_text" not in str(body["trajectory"])
        serialized = response.text.casefold()
        for forbidden in ("ground_truth", "correct_candidate", "expected_candidate", "truth_reference", "oracle"):
            assert forbidden not in serialized

    recovered = client.get(f"/api/benchmarks/frozen-eval-v3/replays/opus-provider-recovered/{recovered_t2}").json()
    assert recovered["provenance"] == {
        "provider_recovered_case": True,
        "canonical_trajectory": "resolved through deterministic policy",
        "original_operational_attempt": "provider infrastructure failure",
        "original_failed_trajectory_preserved": True,
    }
    no_replay = client.get(f"/api/benchmarks/frozen-eval-v3/replays/opus-provider-recovered/{stage2}")
    assert no_replay.status_code == 404


def test_frozen_case_replay_does_not_modify_trajectory_artifacts(client: TestClient):
    catalog = client.app.state.benchmark_catalog
    canonical = catalog.trajectories_root / "frozen-eval-v3-opus5-thinking-final"
    original = catalog.trajectories_root / "frozen-eval-v3-opus5-thinking-t2-provider-failures-original"
    before = (_corpus_digest(canonical), _corpus_digest(original))
    case_id = next(iter(catalog._v3_trajectory_paths))

    response = client.get(f"/api/benchmarks/frozen-eval-v3/replays/opus-provider-recovered/{case_id}")
    assert response.status_code == 200, response.text
    assert (_corpus_digest(canonical), _corpus_digest(original)) == before


def test_bounded_search_replay_contract_is_unchanged(client: TestClient):
    bounded = client.get("/api/benchmarks/bounded-search-v1/replays/opus/case:bnk_bsearch_000018")
    assert bounded.status_code == 200, bounded.text
    assert bounded.json()["benchmark_id"] == "bounded-search-v1"
    assert bounded.json()["provider_calls_made"] is False
