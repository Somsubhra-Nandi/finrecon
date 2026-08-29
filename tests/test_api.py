"""Product API tests; all reconciliation authority remains in the library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.api.app import DEMO_ROOT, create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=tmp_path / "finrecon-api.sqlite3")) as value:
        yield value


def load_demo(client: TestClient) -> dict:
    response = client.post("/api/reconciliation/demo")
    assert response.status_code == 200, response.text
    return response.json()


def test_demo_run_overview_case_list_and_detail_are_real_replay(client: TestClient, monkeypatch):
    def no_provider_chain():
        raise AssertionError("demo replay attempted to configure a live provider")

    monkeypatch.setattr("finrecon.orchestrate.build_chain", no_provider_chain)
    run = load_demo(client)
    assert run["mode"] == "replay"
    assert run["provider_calls_made"] is False
    assert run["result"]["metrics"] | {
        "deterministic_resolved": 1,
        "ai_assisted_resolved": 1,
        "needs_review": 1,
        "ingestion_issues": 2,
    } == run["result"]["metrics"]

    overview = client.get("/api/overview").json()
    assert overview["selected_batch_id"] == "batch:demo-operations"
    cases = client.get("/api/cases").json()
    assert cases["total"] == 3
    assert {case["resolution_source"] for case in cases["cases"]} == {
        "deterministic", "ai_assisted", "escalated"
    }

    for case in cases["cases"]:
        detail = client.get(f"/api/cases/{case['case_id']}").json()
        assert detail["summary"]["case_id"] == case["case_id"]
        assert detail["bank_transaction"]["narration"]
        if case["resolution_source"] != "deterministic":
            assert detail["snapshot_hash"]
            assert detail["candidates"]


def test_human_resolution_is_snapshot_bound_validated_and_revisioned(client: TestClient):
    load_demo(client)
    review_case = client.get("/api/cases", params={"escalated_only": "true"}).json()["cases"][0]
    detail = client.get(f"/api/cases/{review_case['case_id']}").json()
    endpoint = f"/api/cases/{review_case['case_id']}/resolution"
    base = {
        "batch_id": review_case["batch_id"],
        "snapshot_hash": detail["snapshot_hash"],
        "reason": "Checked the immutable settlement export",
        "actor": "API reviewer",
    }

    stale = client.post(endpoint, json={**base, "snapshot_hash": "stale", "selected_candidate_id": None})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "stale_snapshot"

    invalid = client.post(endpoint, json={**base, "selected_candidate_id": "not-in-snapshot"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_human_resolution"

    selected = detail["candidates"][0]["candidate_id"]
    saved = client.post(endpoint, json={**base, "selected_candidate_id": selected})
    assert saved.status_code == 200
    assert saved.json()["case"]["summary"]["resolution_source"] == "human"
    assert saved.json()["resolution"]["revision"] == 1

    reopened = client.get(f"/api/cases/{review_case['case_id']}").json()
    assert reopened["human_resolutions"][0]["selected_candidate_id"] == selected
    rerun = client.post("/api/reconciliation/demo").json()
    assert rerun["provider_calls_made"] is False
    assert rerun["result"]["metrics"]["human_resolved"] == 1
    assert rerun["result"]["metrics"]["needs_review"] == 0


def test_ingestion_issues_are_separate_and_have_no_resolution_surface(client: TestClient):
    load_demo(client)
    response = client.get("/api/ingestion/issues")
    assert response.status_code == 200
    issues = response.json()["issues"]
    assert {item["event_type"] for item in issues} == {
        "quarantined_settlement", "rejected_bank_row"
    }
    assert all(item["fingerprint"] for item in issues)


def test_upload_run_endpoint_handles_a_deterministic_batch_without_network(client: TestClient, monkeypatch):
    rows = json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
    bank_lines = (DEMO_ROOT / "bank.csv").read_text(encoding="utf-8").splitlines()
    bank_csv = "\n".join(bank_lines[:2]) + "\n"

    def no_provider_chain():
        raise AssertionError("deterministic replay attempted a provider call")

    monkeypatch.setattr("finrecon.orchestrate.build_chain", no_provider_chain)
    response = client.post(
        "/api/reconciliation/run",
        data={"mode": "replay", "batch_id": "batch:api-upload"},
        files={
            "razorpay_file": ("razorpay.json", json.dumps(rows), "application/json"),
            "bank_file": ("bank.csv", bank_csv, "text/csv"),
            "bank_profile": ("profile.json", (DEMO_ROOT / "bank-profile.json").read_bytes(), "application/json"),
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider_calls_made"] is False
    assert body["result"]["metrics"]["deterministic_resolved"] == 1
    assert body["result"]["metrics"]["ai_assisted_resolved"] == 0


def test_live_mode_without_server_credentials_returns_safe_configuration_guidance(client: TestClient):
    rows = json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))
    response = client.post(
        "/api/reconciliation/run",
        data={"mode": "live", "batch_id": "batch:no-live-provider"},
        files={
            "razorpay_file": ("razorpay.json", json.dumps(rows), "application/json"),
            "bank_file": ("bank.csv", (DEMO_ROOT / "bank.csv").read_bytes(), "text/csv"),
            "bank_profile": ("profile.json", (DEMO_ROOT / "bank-profile.json").read_bytes(), "application/json"),
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "live_provider_not_configured"
    assert "never accepted from the browser" in response.json()["detail"]["message"]
