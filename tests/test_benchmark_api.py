"""Read-only product projections for frozen benchmark artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.api.app import create_app
from finrecon.api.benchmarks import BenchmarkCatalog


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=tmp_path / "ledger.sqlite3")) as value:
        yield value


def test_benchmark_catalog_distinguishes_frozen_pilot_and_replay_availability(client: TestClient):
    response = client.get("/api/benchmarks")
    assert response.status_code == 200, response.text
    catalog = {item["benchmark_id"]: item for item in response.json()["benchmarks"]}
    assert {key: catalog["frozen-eval-v3"][key] for key in ("status", "case_count", "replay_available")} == {"status": "FROZEN", "case_count": 890, "replay_available": False}
    assert {key: catalog["bounded-search-v1"][key] for key in ("status", "case_count", "replay_available")} == {"status": "FROZEN", "case_count": 50, "replay_available": True}
    assert {key: catalog["v4-pilot"][key] for key in ("status", "case_count", "replay_available")} == {"status": "PILOT", "case_count": 64, "replay_available": False}


def test_bounded_reports_preserve_their_distinct_scored_denominators(client: TestClient):
    response = client.get("/api/benchmarks/bounded-search-v1/reports")
    assert response.status_code == 200, response.text
    reports = {item["report_id"]: item for item in response.json()["reports"]}
    assert reports["openrouter-free"]["metrics"]["investigated"] == 45
    assert reports["openrouter-free"]["metrics"]["uniquely_resolvable_cases"] == 38
    assert reports["opus"]["metrics"]["investigated"] == 40
    assert reports["opus"]["metrics"]["uniquely_resolvable_cases"] == 31
    assert "per_case" not in reports["opus"]


def test_recorded_replay_is_offline_and_the_controller_rejection_demo_is_discoverable(client: TestClient, monkeypatch):
    def provider_must_not_be_constructed(*args, **kwargs):
        raise AssertionError("benchmark browsing must never construct a provider chain")

    monkeypatch.setattr("finrecon.orchestrate.build_chain", provider_must_not_be_constructed)
    cases = client.get("/api/benchmarks/bounded-search-v1/cases", params={"controller_rejection": "true"})
    assert cases.status_code == 200, cases.text
    assert [row["case_id"] for row in cases.json()["cases"]] == ["case:bnk_bsearch_000012"]
    assert cases.json()["cases"][0]["recorded_outcomes"]["openrouter-free"] == "tool_validation_failure"

    replay = client.get("/api/benchmarks/bounded-search-v1/replays/openrouter-free/case:bnk_bsearch_000012")
    assert replay.status_code == 200, replay.text
    body = replay.json()
    assert body["replayed"] is True
    assert body["provider_calls_made"] is False
    assert body["trajectory"]["termination_reason"] == "tool_validation_failed"
    assert "tool_validation_failure" in body["policy_result"]["blockers"]
    assert all("correct_relationship" not in str(value) for value in body.values())


def test_unavailable_replay_fails_closed_instead_of_fabricating_data(client: TestClient):
    response = client.get("/api/benchmarks/frozen-eval-v3/replays/openrouter-free/case:bnk_frozen_000001")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "benchmark_replay_unavailable"


def test_malformed_trajectory_is_not_returned(tmp_path: Path):
    project = tmp_path / "project"
    source = Path(__file__).resolve().parents[1]
    trajectory_dir = project / "fixtures" / "trajectories" / "bounded-search-v1-openrouter-free-final"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / "bad.json").write_text('{"case_id":"case:bad"}', encoding="utf-8")
    # The catalog index is intentionally tolerant so one corrupt file does not
    # block unrelated cases; direct replay must still fail closed.
    catalog = BenchmarkCatalog(source)
    catalog.trajectories_root = project / "fixtures" / "trajectories"
    catalog._trajectory_index = {"openrouter-free": {"case:bad": trajectory_dir / "bad.json"}, "opus": {}}
    with pytest.raises(Exception) as error:
        catalog.replay("bounded-search-v1", "openrouter-free", "case:bad")
    assert getattr(error.value, "status_code", None) == 503


def test_incompatible_validator_or_policy_trajectory_fails_closed(client: TestClient, tmp_path: Path, monkeypatch):
    catalog = client.app.state.benchmark_catalog
    original = catalog._trajectory_path("openrouter-free", "case:bnk_bsearch_000012")
    assert original is not None
    altered = json.loads(original.read_text(encoding="utf-8"))
    altered["validator_version"] = "validator.v999"
    incompatible = tmp_path / "incompatible.json"
    incompatible.write_text(json.dumps(altered), encoding="utf-8")
    monkeypatch.setattr(catalog, "_trajectory_path", lambda *_args: incompatible)
    response = client.get("/api/benchmarks/bounded-search-v1/replays/openrouter-free/case:bnk_bsearch_000012")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "benchmark_replay_version_incompatible"
