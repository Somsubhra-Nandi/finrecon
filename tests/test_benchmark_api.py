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


def test_judge_catalog_exposes_only_frozen_safety_and_investigation_suites(client: TestClient):
    response = client.get("/api/benchmarks")
    assert response.status_code == 200, response.text
    catalog = {item["benchmark_id"]: item for item in response.json()["benchmarks"]}
    assert {key: catalog["frozen-eval-v3"][key] for key in ("status", "case_count", "replay_available")} == {"status": "FROZEN", "case_count": 890, "replay_available": False}
    assert {key: catalog["bounded-search-v1"][key] for key in ("status", "case_count", "replay_available")} == {"status": "FROZEN", "case_count": 50, "replay_available": True}
    assert set(catalog) == {"frozen-eval-v3", "bounded-search-v1"}


def test_bounded_reports_project_the_authoritative_full_opus_cohort(client: TestClient):
    response = client.get("/api/benchmarks/bounded-search-v1/reports")
    assert response.status_code == 200, response.text
    reports = {item["report_id"]: item for item in response.json()["reports"]}
    assert reports["openrouter-free"]["metrics"]["investigated"] == 45
    assert reports["openrouter-free"]["metrics"]["uniquely_resolvable_cases"] == 38
    assert reports["opus"]["metrics"]["investigated"] == 50
    assert reports["opus"]["metrics"]["uniquely_resolvable_cases"] == 40
    assert reports["opus"]["metrics"]["correct_auto_resolutions"] == 40
    assert reports["opus"]["metrics"]["escalated"] == 10
    assert reports["opus"]["metrics"]["wrong_auto_resolutions"] == 0
    assert reports["opus"]["metrics"]["value_at_risk_paise"] == 0
    assert reports["opus"]["cohort"]["complete"] is True
    assert reports["opus"]["telemetry"]["models_requested"] == {"gorouter:claude-opus-5-thinking": 50}
    assert reports["opus"]["telemetry"]["models_reported"] == {"gorouter:claude-opus-5": 50}
    assert "per_case" not in reports["opus"]

    replays = client.get("/api/benchmarks/bounded-search-v1/replays")
    assert replays.status_code == 200, replays.text
    opus_replay = next(item for item in replays.json()["replays"] if item["investigator"] == "opus")
    assert opus_replay["scored_cohort_cases"] == 50
    assert opus_replay["persisted_trajectory_cases"] == 50
    assert opus_replay["requested_model"] == "claude-opus-5-thinking"
    assert opus_replay["reported_models"] == ["gorouter:claude-opus-5"]


def test_frozen_case_explorer_paginates_and_searches_case_ids(client: TestClient):
    first = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"offset": 0, "limit": 50})
    assert first.status_code == 200, first.text
    body = first.json()
    assert (body["total"], body["offset"], body["limit"], len(body["cases"])) == (890, 0, 50, 50)

    next_page = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"offset": 50, "limit": 50})
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["cases"][0]["case_id"] != body["cases"][0]["case_id"]

    search = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"search": "000012"})
    assert search.status_code == 200, search.text
    assert [row["case_id"] for row in search.json()["cases"]] == ["case:bnk_frozeneval_000012"]


def test_frozen_v3_case_projection_is_complete_safe_and_matches_the_final_report(client: TestClient):
    response = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"offset": 0, "limit": 100})
    assert response.status_code == 200, response.text
    first_page = response.json()
    assert len(first_page["cases"]) == 100
    assert all(item["evaluation"] for item in first_page["cases"])

    catalog = client.app.state.benchmark_catalog
    projection = catalog._v3_case_evaluations
    assert len(projection) == 890
    assert sum(item["final_disposition"] == "RESOLVED" for item in projection.values()) == 823
    assert sum(item["final_disposition"] == "ESCALATED" for item in projection.values()) == 67
    assert sum(item["resolution_stage"] == "STAGE_2" for item in projection.values()) == 650
    assert sum(item["resolution_stage"] == "STAGE_3" and item["final_disposition"] == "RESOLVED" for item in projection.values()) == 173

    stage2 = next(case_id for case_id, item in projection.items() if item["resolution_stage"] == "STAGE_2")
    stage3 = next(case_id for case_id, item in projection.items() if item["resolution_stage"] == "STAGE_3" and item["final_disposition"] == "RESOLVED")
    escalated = next(case_id for case_id, item in projection.items() if item["final_disposition"] == "ESCALATED")
    for case_id, expected in ((stage2, ("STAGE_2", "RESOLVED")), (stage3, ("STAGE_3", "RESOLVED")), (escalated, ("STAGE_3", "ESCALATED"))):
        detail = client.get(f"/api/benchmarks/frozen-eval-v3/cases/{case_id}")
        assert detail.status_code == 200, detail.text
        evaluation = detail.json()["evaluation"]
        assert (evaluation["resolution_stage"], evaluation["final_disposition"]) == expected
        assert evaluation["replay_available"] is False
        assert "replay was not persisted" in evaluation["replay_note"]
        serialized = detail.text.casefold()
        for forbidden in ("correct_candidate", "ground_truth", "expected_candidate", "answer", "oracle", "true_settlement", "true_group", "truth_reference"):
            assert forbidden not in serialized

    filtered = client.get("/api/benchmarks/frozen-eval-v3/cases", params={"outcome": "escalated", "tier": "T3", "stage": "stage3", "limit": 100})
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 40
    assert all(case["evaluation"]["final_disposition"] == "ESCALATED" for case in filtered.json()["cases"])


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
