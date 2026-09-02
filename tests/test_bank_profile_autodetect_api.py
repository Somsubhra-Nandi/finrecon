"""The inspect endpoint, and reconciling under a detected built-in profile.

Two properties carry the safety of this feature at the HTTP boundary:

* inspection is genuinely read-only -- no batch, no case, no ledger row, no
  provider call -- so a user can safely drop a statement on the page before
  committing to anything;
* a ``built_in_profile_id`` arriving from a browser is re-verified against
  the uploaded bytes server-side, so a client cannot pair a profile id with
  an unrelated CSV and have its columns read under the wrong mapping.

The manual upload path is exercised alongside every new one, because it
remains the escape hatch that ambiguity and unknown schemas depend on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.adapters.bank.schema import BankProfileRegistry
from finrecon.adapters.bank.schema.registry import REGISTRY_ARTIFACT_VERSION
from finrecon.api.app import DEMO_ROOT, create_app

DEMO_HEADERS = ["Ref No", "Value Date", "Narration", "Debit", "Credit"]


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=tmp_path / "finrecon-api.sqlite3")) as value:
        yield value


def synthetic_artifact(profile_id: str, headers: list[str], **profile_extra) -> dict:
    profile = {
        "profile_id": profile_id,
        "currency": "INR",
        "value_date_column": "Value Date",
        "value_date_format": "%d/%m/%Y",
        "narration_column": "Narration",
        "money_columns": {"kind": "debit_credit", "debit_column": "Debit", "credit_column": "Credit"},
    }
    profile.update(profile_extra)
    return {
        "registry_artifact_version": REGISTRY_ARTIFACT_VERSION,
        "profile_id": profile_id,
        "label": f"Synthetic {profile_id}",
        "version": "v1",
        "verification": "demo_fixture",
        "description": "Authored for tests only.",
        "evidence": "Synthetic; describes no real bank.",
        "expected_headers": headers,
        "profile": profile,
    }


def install_registry(client: TestClient, tmp_path: Path, *artifacts: dict) -> None:
    """Point the running app at a test-only registry.

    The app holds its registry on ``app.state`` precisely so a test can do
    this without reaching into the module-level cache of what actually
    ships.
    """
    directory = tmp_path / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    for index, payload in enumerate(artifacts):
        (directory / f"{index}.json").write_text(json.dumps(payload), encoding="utf-8")
    client.app.state.bank_profile_registry = BankProfileRegistry.from_directory(directory)


def inspect(client: TestClient, csv_text: str, encoding: str = "utf-8") -> dict:
    response = client.post(
        "/api/bank-statement/inspect",
        files={"bank_file": ("bank.csv", csv_text.encode(encoding), "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def demo_csv(header_line: str | None = None) -> str:
    # One data row only: it is the Stage-2-resolvable one, so none of these
    # tests depends on a cached Stage-3 trajectory.
    lines = (DEMO_ROOT / "bank.csv").read_text(encoding="utf-8").splitlines()
    return "\n".join([header_line or lines[0], lines[1]]) + "\n"


class TestInspectEndpoint:
    def test_an_exactly_matching_statement_is_recognised(self, client: TestClient):
        body = inspect(client, demo_csv())
        assert body["status"] == "matched"
        assert body["match_tier"] == "exact"
        assert body["profile"]["profile_id"] == "finrecon_demo_v1"
        assert body["profile"]["verification"] == "demo_fixture"
        assert body["raw_headers"] == DEMO_HEADERS
        assert body["field_count"] == 5
        assert body["signature"]
        assert body["candidates"] == []

    def test_a_case_and_whitespace_variant_is_recognised_as_safe_normalized(self, client: TestClient):
        body = inspect(client, demo_csv("REF NO, value date ,Narration,DEBIT,Credit"))
        assert body["status"] == "matched"
        assert body["match_tier"] == "safe_normalized"
        assert body["profile"]["profile_id"] == "finrecon_demo_v1"

    def test_a_bom_prefixed_statement_is_still_recognised(self, client: TestClient):
        body = inspect(client, demo_csv(), encoding="utf-8-sig")
        assert body["status"] == "matched"
        assert body["raw_headers"] == DEMO_HEADERS

    def test_an_unknown_schema_reports_its_headers_and_offers_no_guess(self, client: TestClient):
        body = inspect(client, "Txn Ref,Posted On,Particulars,Amount,Type\nX,01/01/2026,NEFT,10.00,CR\n")
        assert body["status"] == "unknown"
        assert body["profile"] is None
        assert body["match_tier"] is None
        assert body["candidates"] == []
        assert body["raw_headers"] == ["Txn Ref", "Posted On", "Particulars", "Amount", "Type"]

    def test_an_ambiguous_statement_names_every_tied_candidate_and_selects_none(
        self, client: TestClient, tmp_path: Path
    ):
        install_registry(
            client, tmp_path,
            synthetic_artifact("synthetic_a_v1", DEMO_HEADERS, reference_id_column="Ref No"),
            synthetic_artifact("synthetic_b_v1", DEMO_HEADERS, reference_id_column="Ref No"),
        )
        body = inspect(client, demo_csv())
        assert body["status"] == "ambiguous"
        assert body["profile"] is None
        assert {item["profile_id"] for item in body["candidates"]} == {
            "synthetic_a_v1", "synthetic_b_v1"
        }

    def test_inspection_creates_no_batch_and_no_ledger_state(self, client: TestClient):
        inspect(client, demo_csv())
        inspect(client, "Txn Ref,Posted On\nX,01/01/2026\n")
        assert client.get("/api/runs").json() == []
        assert client.get("/api/cases").json()["total"] == 0
        assert client.get("/api/audit").json()["events"] == []
        assert client.get("/api/ingestion/issues").json()["issues"] == []

    def test_inspection_makes_no_provider_or_model_call(self, client: TestClient, monkeypatch):
        def no_provider_chain():
            raise AssertionError("schema inspection attempted to configure a provider")

        monkeypatch.setattr("finrecon.orchestrate.build_chain", no_provider_chain)
        assert inspect(client, demo_csv())["status"] == "matched"

    def test_an_empty_upload_is_refused_by_the_existing_bounded_read(self, client: TestClient):
        response = client.post(
            "/api/bank-statement/inspect",
            files={"bank_file": ("bank.csv", b"", "text/csv")},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "empty_upload"


class TestReconciliationUnderADetectedProfile:
    def razorpay(self) -> str:
        return json.dumps(json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1])

    def run(self, client: TestClient, *, bank_csv: str, batch_id: str, **extra):
        files = {
            "razorpay_file": ("razorpay.json", self.razorpay(), "application/json"),
            "bank_file": ("bank.csv", bank_csv, "text/csv"),
        }
        profile_bytes = extra.pop("profile_bytes", None)
        if profile_bytes is not None:
            files["bank_profile"] = ("profile.json", profile_bytes, "application/json")
        return client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": batch_id, **extra},
            files=files,
        )

    def test_the_manual_profile_upload_path_is_unchanged(self, client: TestClient, monkeypatch):
        monkeypatch.setattr(
            "finrecon.orchestrate.build_chain",
            lambda: (_ for _ in ()).throw(AssertionError("provider call in replay")),
        )
        response = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:manual",
            profile_bytes=(DEMO_ROOT / "bank-profile.json").read_bytes(),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["metrics"]["deterministic_resolved"] == 1
        # Asserted as a whole dict on purpose: the manual path must acquire no
        # provenance it has not earned. The saved-mapping fields exist on the
        # view now, and every one of them is null here -- an uploaded profile
        # JSON names no saved mapping, carries no version, and makes no
        # confirmation claim.
        assert body["bank_profile_selection"] == {
            "profile_id": "finrecon_demo_v1", "selection_mode": "manual_upload",
            "match_tier": None, "version": None, "label": None,
            "verification": None, "schema_signature": None,
            "mapping_id": None, "mapping_version": None,
            "provenance": None, "source": None,
        }

    def test_a_verified_built_in_profile_id_reconciles_without_a_profile_upload(
        self, client: TestClient
    ):
        response = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:builtin",
            built_in_profile_id="finrecon_demo_v1",
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["result"]["metrics"]["deterministic_resolved"] == 1
        selection = body["bank_profile_selection"]
        assert selection["selection_mode"] == "built_in"
        assert selection["match_tier"] == "exact"
        assert selection["version"] == "v1"
        assert selection["verification"] == "demo_fixture"
        assert selection["schema_signature"]

    def test_both_paths_produce_the_same_reconciliation(self, client: TestClient):
        manual = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:same-manual",
            profile_bytes=(DEMO_ROOT / "bank-profile.json").read_bytes(),
        ).json()
        detected = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:same-detected",
            built_in_profile_id="finrecon_demo_v1",
        ).json()
        assert manual["result"]["metrics"] == detected["result"]["metrics"]
        assert manual["result"]["record_count"] == detected["result"]["record_count"]

    def test_a_mismatching_csv_is_rejected_before_any_reconciliation_runs(
        self, client: TestClient
    ):
        response = self.run(
            client,
            bank_csv="Txn Ref,Posted On,Particulars\nX,01/01/2026,NEFT\n",
            batch_id="batch:mismatch",
            built_in_profile_id="finrecon_demo_v1",
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "bank_profile_mismatch"
        assert client.get("/api/runs").json() == []

    def test_an_unknown_built_in_profile_id_is_rejected(self, client: TestClient):
        response = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:nosuch",
            built_in_profile_id="hdfc_savings_v1",
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_built_in_profile"
        assert client.get("/api/runs").json() == []

    def test_an_ambiguous_statement_cannot_be_forced_through_by_naming_a_candidate(
        self, client: TestClient, tmp_path: Path
    ):
        install_registry(
            client, tmp_path,
            synthetic_artifact("synthetic_a_v1", DEMO_HEADERS, reference_id_column="Ref No"),
            synthetic_artifact("synthetic_b_v1", DEMO_HEADERS, reference_id_column="Ref No"),
        )
        response = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:ambiguous",
            built_in_profile_id="synthetic_a_v1",
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "bank_profile_mismatch"
        assert client.get("/api/runs").json() == []

    def test_supplying_neither_profile_source_is_a_clear_error(self, client: TestClient):
        response = self.run(client, bank_csv=demo_csv(), batch_id="batch:none")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "missing_bank_profile"

    def test_supplying_both_profile_sources_is_refused_rather_than_ranked(
        self, client: TestClient
    ):
        response = self.run(
            client, bank_csv=demo_csv(), batch_id="batch:both",
            built_in_profile_id="finrecon_demo_v1",
            profile_bytes=(DEMO_ROOT / "bank-profile.json").read_bytes(),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "conflicting_bank_profile"


class TestDetectedZeroFilledSemanticsSurviveEndToEnd:
    """A registry profile declaring ``empty_or_zero`` must keep that reading
    when it is auto-selected -- the detector chooses *which* reviewed
    mapping applies, never what the mapping means."""

    HEADERS = ["Value Date", "Transaction Remarks", "Withdrawal", "Deposit"]

    def artifact(self, marker: str) -> dict:
        return synthetic_artifact(
            "synthetic_zero_filled_v1", self.HEADERS,
            narration_column="Transaction Remarks",
            money_columns={
                "kind": "debit_credit", "debit_column": "Withdrawal",
                "credit_column": "Deposit", "inactive_side_marker": marker,
            },
        )

    CSV = (
        "Value Date,Transaction Remarks,Withdrawal,Deposit\n"
        "15/08/2026,NEFT CR SETL_DEMO_DIRECT RAZORPAY SETTLEMENT,0.0,1000.00\n"
    )

    def run(self, client: TestClient, batch_id: str):
        rows = json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
        return client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": batch_id,
                  "built_in_profile_id": "synthetic_zero_filled_v1"},
            files={
                "razorpay_file": ("razorpay.json", json.dumps(rows), "application/json"),
                "bank_file": ("bank.csv", self.CSV, "text/csv"),
            },
        )

    def test_empty_or_zero_is_honoured_when_the_profile_is_auto_selected(
        self, client: TestClient, tmp_path: Path
    ):
        install_registry(client, tmp_path, self.artifact("empty_or_zero"))
        response = self.run(client, "batch:zero-filled")
        assert response.status_code == 200, response.text
        metrics = response.json()["result"]["metrics"]
        assert metrics["total_cases"] == 1
        assert metrics["ingestion_issues"] == 0

    def test_the_same_file_under_empty_only_still_quarantines_the_row(
        self, client: TestClient, tmp_path: Path
    ):
        """The control: the declaration, not the detector, decides."""
        install_registry(client, tmp_path, self.artifact("empty_only"))
        response = self.run(client, "batch:zero-filled-control")
        assert response.status_code == 200, response.text
        metrics = response.json()["result"]["metrics"]
        assert metrics["total_cases"] == 0
        assert metrics["ingestion_issues"] == 1
        issues = client.get("/api/ingestion/issues", params={"batch_id": "batch:zero-filled-control"}).json()
        assert issues["issues"][0]["event_type"] == "rejected_bank_row"


class TestAuditProvenance:
    def selection_events(self, client: TestClient, batch_id: str) -> list[dict]:
        events = client.get("/api/audit", params={"batch_id": batch_id}).json()["events"]
        return [event for event in events if event["event_type"] == "bank_profile_selection"]

    def test_a_detected_run_records_profile_version_tier_and_selection_mode(
        self, client: TestClient
    ):
        client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": "batch:audit-detected",
                  "built_in_profile_id": "finrecon_demo_v1"},
            files={
                "razorpay_file": ("razorpay.json", json.dumps(
                    json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
                ), "application/json"),
                "bank_file": ("bank.csv", demo_csv(), "text/csv"),
            },
        )
        (event,) = self.selection_events(client, "batch:audit-detected")
        payload = event["payload"]
        assert payload["profile_id"] == "finrecon_demo_v1"
        assert payload["selection_mode"] == "built_in"
        assert payload["match_tier"] == "exact"
        assert payload["profile_version"] == "v1"
        assert payload["verification"] == "demo_fixture"
        assert payload["schema_signature"]
        assert payload["raw_headers"] == DEMO_HEADERS

    def test_a_manual_run_records_the_manual_selection_mode(self, client: TestClient):
        client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": "batch:audit-manual"},
            files={
                "razorpay_file": ("razorpay.json", json.dumps(
                    json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
                ), "application/json"),
                "bank_file": ("bank.csv", demo_csv(), "text/csv"),
                "bank_profile": ("profile.json", (DEMO_ROOT / "bank-profile.json").read_bytes(), "application/json"),
            },
        )
        (event,) = self.selection_events(client, "batch:audit-manual")
        assert event["payload"]["selection_mode"] == "manual_upload"
        assert event["payload"]["match_tier"] is None

    def test_successful_selection_is_provenance_not_an_ingestion_issue(
        self, client: TestClient
    ):
        client.post(
            "/api/reconciliation/run",
            data={"mode": "replay", "batch_id": "batch:audit-not-an-issue",
                  "built_in_profile_id": "finrecon_demo_v1"},
            files={
                "razorpay_file": ("razorpay.json", json.dumps(
                    json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
                ), "application/json"),
                "bank_file": ("bank.csv", demo_csv(), "text/csv"),
            },
        )
        issues = client.get(
            "/api/ingestion/issues", params={"batch_id": "batch:audit-not-an-issue"}
        ).json()["issues"]
        assert all(issue["event_type"] != "bank_profile_selection" for issue in issues)
        runs = {run["batch_id"]: run for run in client.get("/api/runs").json()}
        assert runs["batch:audit-not-an-issue"]["metrics"]["ingestion_issues"] == 0

    def test_raw_bank_row_evidence_is_unchanged_by_how_the_profile_was_chosen(
        self, client: TestClient
    ):
        """Selection provenance is additive: the per-row audit facts a
        detected run records must be byte-identical to a manual run's."""
        rows = json.dumps(json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1])
        for batch_id, extra, files_extra in (
            ("batch:evidence-manual", {}, {"bank_profile": ("profile.json", (DEMO_ROOT / "bank-profile.json").read_bytes(), "application/json")}),
            ("batch:evidence-detected", {"built_in_profile_id": "finrecon_demo_v1"}, {}),
        ):
            client.post(
                "/api/reconciliation/run",
                data={"mode": "replay", "batch_id": batch_id, **extra},
                files={
                    "razorpay_file": ("razorpay.json", rows, "application/json"),
                    "bank_file": ("bank.csv", demo_csv(), "text/csv"),
                    **files_extra,
                },
            )

        def bank_row_events(batch_id: str) -> list[dict]:
            events = client.get("/api/audit", params={"batch_id": batch_id}).json()["events"]
            return sorted(
                (
                    {"event_type": event["event_type"], "payload": event["payload"]}
                    for event in events
                    if event["event_type"].endswith("_bank_row")
                ),
                key=lambda item: json.dumps(item, sort_keys=True),
            )

        assert bank_row_events("batch:evidence-manual") == bank_row_events("batch:evidence-detected")
        assert bank_row_events("batch:evidence-detected")
