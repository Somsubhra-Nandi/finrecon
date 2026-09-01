"""The mapping API: the confirmation boundary, reuse, and run-time re-checks.

Two claims carry the safety of this feature at the HTTP boundary, and both
are asserted here against a real app over a real on-disk ledger:

* **A proposal cannot reconcile and cannot persist itself.** There is no
  endpoint that accepts a proposal identifier, because a proposal has no
  server-side identity to name. The only path forward is posting a complete
  mapping, and that is what a human confirming produces.
* **A saved mapping is re-verified against the bytes.** A ``saved_mapping_id``
  arriving from a browser is a claim. The server re-inspects the upload and
  requires that detection would independently have selected that exact
  mapping version, so a statement whose columns changed is refused before a
  single ``BankRecord`` exists.

The pre-existing paths -- manual profile JSON, built-in detection, the demo
batch -- are exercised alongside, because they remain the escape hatches that
ambiguity and provider outages depend on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.adapters.bank.mapping.proposal import PROPOSAL_TOOL_NAME
from finrecon.adapters.bank.schema import BankProfileRegistry, built_in_registry
from finrecon.api.app import DEMO_ROOT, create_app
from finrecon.agent.providers.base import (
    ModelProvider,
    ModelResponse,
    ProviderConfigurationError,
    ToolCallRequest,
)
from finrecon.agent.providers.chain import ProviderChain

HEADERS = ["Txn Reference", "Posted On", "Particulars", "Withdrawal Amt", "Deposit Amt"]
HEADER_LINE = ",".join(HEADERS)

# The demo statement's *content* under an unrecognised *header row*. That
# combination is exactly what this feature is for -- a clean transaction
# table FinRecon has no profile for -- and reusing the demo row keeps these
# tests reconciling deterministically in Stage 2, so none of them depends on
# a cached Stage-3 trajectory. Day 15 cannot be a month, so the date is
# unambiguous unless a test asks for ambiguity.
DEMO_ROW = "DEMO-001,15/08/2026,NEFT CR SETL_DEMO_DIRECT RAZORPAY SETTLEMENT,,1000.00"
BANK_CSV = f"{HEADER_LINE}\n{DEMO_ROW}\n".encode("utf-8")

# The same row with its inactive side zero-filled rather than blank: the
# `empty_or_zero` convention, which is only readable under a mapping that
# declares it.
ZERO_FILLED_ROW = DEMO_ROW.replace("SETTLEMENT,,1000.00", "SETTLEMENT,0.00,1000.00")
ZERO_FILLED_CSV = f"{HEADER_LINE}\n{ZERO_FILLED_ROW}\n".encode("utf-8")

# Day and month both 12 or lower, so day-first and month-first are equally
# good readings and only a person can settle it.
AMBIGUOUS_CSV = f"{HEADER_LINE}\n{DEMO_ROW.replace('15/08/2026', '05/08/2026')}\n".encode("utf-8")

RAZORPAY = (DEMO_ROOT / "razorpay.json").read_bytes()

# The demo statement trimmed to its one Stage-2-resolvable row, so the
# pre-existing built-in and manual paths can be exercised here without a
# cached Stage-3 trajectory (the full fixture deliberately ships an
# unresolvable row and an unreadable one).
DEMO_LINES = (DEMO_ROOT / "bank.csv").read_text(encoding="utf-8").splitlines()
DEMO_CSV = ("\n".join(DEMO_LINES[:2]) + "\n").encode("utf-8")


@pytest.fixture()
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "finrecon-api.sqlite3"


@pytest.fixture()
def client(ledger_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=ledger_path)) as value:
        yield value


def mapping_body(**overrides) -> dict:
    body = {
        "name": "Client XYZ Bank Export",
        "value_date_column": "Posted On",
        "value_date_format": "%d/%m/%Y",
        "narration_column": "Particulars",
        "reference_id_column": "Txn Reference",
        "money_columns": {
            "kind": "debit_credit",
            "debit_column": "Withdrawal Amt",
            "credit_column": "Deposit Amt",
            "inactive_side_marker": "empty_only",
        },
        "confirmed_fields": [],
    }
    body.update(overrides)
    return body


def post_mapping(client: TestClient, body: dict, csv: bytes = BANK_CSV, path="/api/bank-mappings"):
    return client.post(
        path,
        files={"bank_file": ("bank.csv", csv, "text/csv")},
        data={"mapping": json.dumps(body)},
    )


def inspect(client: TestClient, csv: bytes = BANK_CSV) -> dict:
    response = client.post(
        "/api/bank-statement/inspect",
        files={"bank_file": ("bank.csv", csv, "text/csv")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def save_mapping(client: TestClient, **overrides) -> dict:
    response = post_mapping(client, mapping_body(**overrides))
    assert response.status_code == 200, response.text
    return response.json()


# --- proposal plumbing ----------------------------------------------------


class OneShot(ModelProvider):
    provider_id = "test-provider"

    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.calls = 0

    @property
    def model(self) -> str:
        return "test-mapper-v1"

    def complete(self, messages, tools):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ModelResponse(
            provider=self.provider_id, model=self.model, text="",
            tool_calls=(ToolCallRequest("c", PROPOSAL_TOOL_NAME, json.dumps(self._payload)),),
        )


PROPOSAL = {
    "mapping": {
        "value_date_column": "Posted On",
        "value_date_format": "%d/%m/%Y",
        "value_date_format_certain": True,
        "narration_column": "Particulars",
        "reference_id_column": "Txn Reference",
        "money": {
            "kind": "debit_credit", "debit_column": "Withdrawal Amt",
            "credit_column": "Deposit Amt", "inactive_side_marker": "empty_only",
            "amount_column": None, "direction_column": None,
            "credit_values": None, "debit_values": None,
        },
    },
    "reasoning_summary": {
        "value_date": "Posted On holds dd/mm/yyyy dates.",
        "money": "Two amount columns; the unused side is left blank.",
        "narration": "Particulars is the description.",
        "reference": "Txn Reference carries UTR values.",
    },
    "uncertainties": [],
}


def install_provider(monkeypatch, provider: ModelProvider) -> None:
    monkeypatch.setattr(
        "finrecon.api.bank_mappings.build_chain",
        lambda: ProviderChain((provider,)),
    )


def install_no_provider(monkeypatch) -> None:
    def refuse():
        raise ProviderConfigurationError(
            "chain", ProviderConfigurationError.MISSING_CREDENTIALS, "no key"
        )

    monkeypatch.setattr("finrecon.api.bank_mappings.build_chain", refuse)


class TestProposalEndpoint:
    def test_an_unknown_schema_gets_a_proposal(self, client, monkeypatch):
        provider = OneShot(PROPOSAL)
        install_provider(monkeypatch, provider)
        response = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_status"] == "unknown"
        assert body["proposal"]["mapping"]["narration_column"] == "Particulars"
        assert body["validation"]["ok"] is True
        assert body["provider_calls_made"] is True
        assert provider.calls == 1
        # The bounded sample is disclosed, so a reviewer can see the rows the
        # suggestion was made from -- and see how few of them there were.
        assert body["sample"]["headers"] == HEADERS
        assert len(body["sample"]["rows"]) == 1
        assert body["sample"]["bounds"]["max_sample_rows"] == 5

    def test_a_recognised_schema_never_calls_the_proposal_service(
        self, client, monkeypatch
    ):
        """The promise that reuse is free.

        Asserted server-side rather than in the UI, so a client that asks for
        a proposal on a recognised file still cannot provoke a model call.
        """
        save_mapping(client)
        provider = OneShot(PROPOSAL)
        install_provider(monkeypatch, provider)
        response = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["schema_status"] == "matched"
        assert body["proposal"] is None
        assert body["provider_calls_made"] is False
        assert provider.calls == 0
        # And it still returns everything the editor needs to work by hand.
        assert body["raw_headers"] == HEADERS
        assert body["supported_date_formats"]

    def test_an_ambiguous_schema_never_calls_the_proposal_service(
        self, client, monkeypatch
    ):
        save_mapping(client, name="Mapping A")
        save_mapping(client, name="Mapping B")
        provider = OneShot(PROPOSAL)
        install_provider(monkeypatch, provider)
        body = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        ).json()
        assert body["schema_status"] == "ambiguous"
        assert body["proposal"] is None
        assert provider.calls == 0

    def test_an_unconfigured_provider_returns_a_usable_editor_not_an_error(
        self, client, monkeypatch
    ):
        install_no_provider(monkeypatch)
        response = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        )
        # Not a 5xx. The product does not stop working when a model does.
        assert response.status_code == 200
        body = response.json()
        assert body["proposal"] is None
        assert body["failure_code"] == "provider_not_configured"
        assert body["failure_message"]
        assert body["raw_headers"] == HEADERS
        assert body["supported_date_formats"]

    def test_the_proposal_response_carries_no_identifier_to_submit_later(
        self, client, monkeypatch
    ):
        """A proposal has no server-side existence, so it has no id.

        This is what makes the confirmation boundary unbypassable rather than
        merely enforced: there is nothing for a bypassing request to name.
        """
        install_provider(monkeypatch, OneShot(PROPOSAL))
        body = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        ).json()
        serialized = json.dumps(body)
        assert "proposal_id" not in serialized
        assert "mapping_id" not in serialized

    def test_a_proposal_writes_nothing(self, client, monkeypatch):
        install_provider(monkeypatch, OneShot(PROPOSAL))
        client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        )
        assert client.get("/api/bank-mappings").json()["mappings"] == []
        assert inspect(client)["status"] == "unknown"


class TestConfirmation:
    def test_a_confirmed_mapping_persists_and_is_listed(self, client):
        body = save_mapping(client)
        saved = body["saved"]
        assert saved["name"] == "Client XYZ Bank Export"
        assert saved["version"] == 1
        assert saved["provenance"] == "human_confirmed"
        assert saved["source"] == "user_saved"
        assert body["created_version"] == 1

        listed = client.get("/api/bank-mappings").json()["mappings"]
        assert [m["mapping_id"] for m in listed] == [saved["mapping_id"]]

    def test_a_mapping_with_no_proposal_at_all_can_be_saved(self, client):
        """The manual path through the new flow, with no model involved.

        A mapping is authoritative because a person confirmed it, not because
        a model was consulted first, so the absence of ``llm_proposal`` must
        not weaken anything.
        """
        saved = save_mapping(client, name="Finance Team CSV")["saved"]
        assert saved["llm_proposal"] is None
        assert saved["provenance"] == "human_confirmed"
        assert inspect(client)["status"] == "matched"

    def test_an_edited_proposal_is_what_gets_saved(self, client, monkeypatch):
        """The model suggested a reference column; the human said there is none.

        The persisted mapping must reflect the human's correction, and must
        not quietly restore what the model preferred.
        """
        install_provider(monkeypatch, OneShot(PROPOSAL))
        proposal = client.post(
            "/api/bank-mappings/propose",
            files={"bank_file": ("bank.csv", BANK_CSV, "text/csv")},
        ).json()
        assert proposal["proposal"]["mapping"]["reference_id_column"] == "Txn Reference"

        saved = save_mapping(
            client,
            reference_id_column=None,
            narration_column="Particulars",
            llm_proposal={"provider": "test-provider", "model": "test-mapper-v1"},
        )["saved"]
        assert saved["profile"]["reference_id_column"] is None
        # The model's involvement is recorded as context, beside a mapping
        # whose authority is still the human's.
        assert saved["llm_proposal"]["model"] == "test-mapper-v1"
        assert saved["provenance"] == "human_confirmed"

    def test_a_mapping_naming_a_column_the_file_lacks_is_refused(self, client):
        response = post_mapping(client, mapping_body(narration_column="Description"))
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "invalid_bank_mapping"
        codes = {issue["code"] for issue in detail["validation"]["errors"]}
        assert "unknown_column" in codes
        assert client.get("/api/bank-mappings").json()["mappings"] == []

    def test_the_server_reads_the_header_row_itself(self, client):
        """A client cannot get a mapping saved against columns it invented.

        The request supplies the file, and the server's own read is what the
        mapping is validated against -- so a browser that lied about the
        header row is refused by the file rather than believed.
        """
        other_csv = b"Date,Description,Amount,Type\n01/01/2024,X,1.00,CR\n"
        response = post_mapping(client, mapping_body(), csv=other_csv)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_bank_mapping"

    def test_an_unanswered_ambiguity_is_refused(self, client):
        """The date-order case, enforced by the server and not only the UI."""
        response = post_mapping(client, mapping_body(), csv=AMBIGUOUS_CSV)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "human_confirmation_required"
        assert "value_date_format" in detail["message"]
        assert client.get("/api/bank-mappings").json()["mappings"] == []

    def test_an_explicitly_confirmed_ambiguity_is_accepted(self, client):
        response = post_mapping(
            client,
            mapping_body(confirmed_fields=["value_date_format"]),
            csv=AMBIGUOUS_CSV,
        )
        assert response.status_code == 200, response.text
        assert response.json()["saved"]["version"] == 1

    def test_a_signature_mismatch_between_review_and_save_is_refused(self, client):
        """A mapping reviewed against one upload cannot be saved against another."""
        response = post_mapping(
            client, mapping_body(expected_signature="deadbeef" * 8)
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "bank_mapping_schema_mismatch"

    def test_a_mapping_without_a_name_is_refused(self, client):
        response = post_mapping(client, mapping_body(name="   "))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "mapping_name_required"

    def test_a_duplicate_name_is_refused(self, client):
        save_mapping(client)
        response = post_mapping(client, mapping_body())
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "mapping_name_taken"

    def test_a_client_supplied_profile_id_is_rejected_outright(self, client):
        """``extra="forbid"`` on the request, so the field cannot be smuggled."""
        response = post_mapping(client, mapping_body(profile_id="finrecon_demo_v1"))
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_bank_mapping"


class TestVersioningApi:
    def test_editing_creates_a_new_active_version_and_keeps_the_old(self, client):
        saved = save_mapping(client)["saved"]
        response = post_mapping(
            client,
            mapping_body(reference_id_column=None),
            path=f"/api/bank-mappings/{saved['mapping_id']}/versions",
        )
        assert response.status_code == 200, response.text
        assert response.json()["created_version"] == 2

        detail = client.get(f"/api/bank-mappings/{saved['mapping_id']}").json()
        assert [(v["version"], v["status"]) for v in detail["versions"]] == [
            (1, "superseded"), (2, "active"),
        ]
        assert detail["versions"][0]["profile"]["reference_id_column"] == "Txn Reference"
        assert detail["active"]["version"] == 2

    def test_a_new_version_is_the_one_reused_on_the_next_upload(self, client):
        saved = save_mapping(client)["saved"]
        post_mapping(
            client, mapping_body(reference_id_column=None),
            path=f"/api/bank-mappings/{saved['mapping_id']}/versions",
        )
        match = inspect(client)["match"]
        assert match["kind"] == "user_saved"
        assert match["saved_mapping"]["version"] == 2

    def test_versioning_an_unknown_mapping_is_a_404(self, client):
        response = post_mapping(
            client, mapping_body(), path="/api/bank-mappings/bankmap_nope/versions"
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_bank_mapping"


class TestReuseAndInspection:
    def test_a_saved_mapping_is_recognised_by_name_and_version(self, client):
        saved = save_mapping(client)["saved"]
        body = inspect(client)
        assert body["status"] == "matched"
        assert body["match"]["kind"] == "user_saved"
        assert body["match"]["label"] == "Client XYZ Bank Export"
        assert body["match"]["version"] == "v1"
        assert body["match"]["saved_mapping"]["mapping_id"] == saved["mapping_id"]

    def test_the_built_in_only_fields_stay_built_in_only(self, client):
        """Backwards compatibility, asserted rather than assumed.

        ``profile`` and ``candidates`` predate saved mappings and are read by
        clients that know nothing about them. A saved match must not appear
        there wearing a ``verification`` level FinRecon cannot vouch for.
        """
        save_mapping(client)
        body = inspect(client)
        assert body["status"] == "matched"
        assert body["profile"] is None
        assert body["candidates"] == []
        assert body["match"]["kind"] == "user_saved"

    def test_a_changed_header_is_not_silently_reused(self, client):
        save_mapping(client)
        changed = BANK_CSV.replace(b"Particulars", b"Description")
        body = inspect(client, changed)
        assert body["status"] == "unknown"
        assert body["match"] is None
        assert body["matches"] == []

    def test_the_demo_built_in_still_matches_alongside_saved_mappings(self, client):
        save_mapping(client)
        demo = (DEMO_ROOT / "bank.csv").read_bytes()
        body = inspect(client, demo)
        assert body["status"] == "matched"
        assert body["match"]["kind"] == "built_in"
        assert body["profile"]["profile_id"] == "finrecon_demo_v1"


class TestReconciliation:
    def run(self, client, *, bank: bytes, batch_id: str, **data):
        return client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                "bank_file": ("bank.csv", bank, "text/csv"),
            },
            data={"mode": "replay", "batch_id": batch_id, **data},
        )

    def test_a_saved_mapping_reconciles_a_matching_statement(self, client):
        saved = save_mapping(client)["saved"]
        response = self.run(
            client, bank=BANK_CSV, batch_id="batch:saved",
            saved_mapping_id=saved["mapping_id"],
        )
        assert response.status_code == 200, response.text
        selection = response.json()["bank_profile_selection"]
        assert selection["selection_mode"] == "user_saved"
        assert selection["mapping_id"] == saved["mapping_id"]
        assert selection["mapping_version"] == 1
        assert selection["label"] == "Client XYZ Bank Export"
        assert selection["provenance"] == "human_confirmed"
        assert selection["source"] == "user_saved"
        assert selection["match_tier"] == "exact"
        assert selection["schema_signature"]
        # No evidence grade is claimed about the operator's own mapping.
        assert selection["verification"] is None

    def test_a_mismatching_statement_is_refused_before_ingestion(self, client):
        """The re-verification, asserted where it matters: before records exist."""
        saved = save_mapping(client)["saved"]
        changed = BANK_CSV.replace(b"Particulars", b"Description")
        response = self.run(
            client, bank=changed, batch_id="batch:mismatch",
            saved_mapping_id=saved["mapping_id"],
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "bank_mapping_schema_mismatch"
        # Nothing was created: the batch does not exist.
        assert client.get("/api/runs").json() == []

    def test_an_unknown_saved_mapping_id_is_a_404(self, client):
        response = self.run(
            client, bank=BANK_CSV, batch_id="batch:nope",
            saved_mapping_id="bankmap_does_not_exist",
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "unknown_bank_mapping"

    def test_a_superseded_version_cannot_be_used_by_naming_its_mapping(self, client):
        """Only the active version reconciles; the mapping id resolves to it."""
        saved = save_mapping(client)["saved"]
        post_mapping(
            client, mapping_body(reference_id_column=None),
            path=f"/api/bank-mappings/{saved['mapping_id']}/versions",
        )
        response = self.run(
            client, bank=BANK_CSV, batch_id="batch:v2",
            saved_mapping_id=saved["mapping_id"],
        )
        assert response.status_code == 200, response.text
        selection = response.json()["bank_profile_selection"]
        assert selection["mapping_version"] == 2
        assert selection["profile_id"].endswith(":v2")

    def test_supplying_two_profile_sources_is_refused(self, client):
        saved = save_mapping(client)["saved"]
        response = client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", b"[]", "application/json"),
                "bank_file": ("bank.csv", BANK_CSV, "text/csv"),
                "bank_profile": ("p.json", b"{}", "application/json"),
            },
            data={"mode": "replay", "saved_mapping_id": saved["mapping_id"]},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "conflicting_bank_profile"

    def test_a_zero_filled_debit_credit_statement_reconciles_through_a_saved_mapping(
        self, client
    ):
        """The ``empty_or_zero`` semantic, end to end through persistence.

        The fixture's rows zero-fill their inactive side, so a mapping that
        lost this declaration in the database would reject every row instead
        of reading two movements.
        """
        response = post_mapping(
            client,
            mapping_body(
                name="Zero-filled export",
                money_columns={
                    "kind": "debit_credit",
                    "debit_column": "Withdrawal Amt",
                    "credit_column": "Deposit Amt",
                    "inactive_side_marker": "empty_or_zero",
                },
            ),
            csv=ZERO_FILLED_CSV,
        )
        assert response.status_code == 200, response.text
        saved = response.json()["saved"]
        assert saved["profile"]["money_columns"]["inactive_side_marker"] == "empty_or_zero"

        run = self.run(
            client, bank=ZERO_FILLED_CSV, batch_id="batch:zerofill",
            saved_mapping_id=saved["mapping_id"],
        )
        assert run.status_code == 200, run.text
        issues = client.get("/api/ingestion/issues?batch_id=batch:zerofill").json()
        rejected = [i for i in issues["issues"] if i["event_type"] == "rejected_bank_row"]
        # A lost marker would reject the row as "neither side populated"
        # rather than reading one credit of 1000.00.
        assert rejected == []
        assert run.json()["result"]["metrics"]["total_cases"] == 1

    def test_the_manual_profile_path_still_works(self, client):
        response = client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                "bank_file": ("bank.csv", DEMO_CSV, "text/csv"),
                "bank_profile": ("p.json", (DEMO_ROOT / "bank-profile.json").read_bytes(), "application/json"),
            },
            data={"mode": "replay", "batch_id": "batch:manual-still-works"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["bank_profile_selection"]["selection_mode"] == "manual_upload"

    def test_the_built_in_profile_path_still_works(self, client):
        response = self.run(
            client, bank=DEMO_CSV,
            batch_id="batch:builtin-still-works",
            built_in_profile_id="finrecon_demo_v1",
        )
        assert response.status_code == 200, response.text
        selection = response.json()["bank_profile_selection"]
        assert selection["selection_mode"] == "built_in"
        assert selection["verification"] == "demo_fixture"

    def test_the_demo_batch_still_works(self, client):
        response = client.post("/api/reconciliation/demo")
        assert response.status_code == 200, response.text
        assert response.json()["batch_id"] == "batch:demo-operations"


class TestAudit:
    def test_the_batch_records_the_mapping_id_name_version_source_and_signature(
        self, client
    ):
        """What a reviewer needs months later, from keys alone."""
        saved = save_mapping(client)["saved"]
        client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                "bank_file": ("bank.csv", BANK_CSV, "text/csv"),
            },
            data={"mode": "replay", "batch_id": "batch:audited",
                  "saved_mapping_id": saved["mapping_id"]},
        )
        events = client.get("/api/audit?batch_id=batch:audited").json()["events"]
        selections = [e for e in events if e["event_type"] == "bank_profile_selection"]
        assert len(selections) == 1
        payload = selections[0]["payload"]
        assert payload["mapping_id"] == saved["mapping_id"]
        assert payload["label"] == "Client XYZ Bank Export"
        assert payload["mapping_version"] == 1
        assert payload["profile_version"] == "v1"
        assert payload["source"] == "user_saved"
        assert payload["selection_mode"] == "user_saved"
        assert payload["match_tier"] == "exact"
        assert payload["schema_signature"]
        assert payload["raw_headers"] == HEADERS

    def test_the_recorded_provenance_is_human_confirmed(self, client, monkeypatch):
        """Even when a model proposed the mapping the human then confirmed."""
        saved = save_mapping(
            client, llm_proposal={"provider": "test-provider", "model": "test-mapper-v1"}
        )["saved"]
        client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                "bank_file": ("bank.csv", BANK_CSV, "text/csv"),
            },
            data={"mode": "replay", "batch_id": "batch:provenance",
                  "saved_mapping_id": saved["mapping_id"]},
        )
        events = client.get("/api/audit?batch_id=batch:provenance").json()["events"]
        payload = next(
            e["payload"] for e in events if e["event_type"] == "bank_profile_selection"
        )
        assert payload["provenance"] == "human_confirmed"
        # The model is recorded as context, never as the deciding authority.
        assert payload["llm_proposal"]["model"] == "test-mapper-v1"

    def test_a_successful_selection_is_provenance_and_not_an_ingestion_issue(
        self, client
    ):
        saved = save_mapping(client)["saved"]
        client.post(
            "/api/reconciliation/run",
            files={
                "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                "bank_file": ("bank.csv", BANK_CSV, "text/csv"),
            },
            data={"mode": "replay", "batch_id": "batch:notanissue",
                  "saved_mapping_id": saved["mapping_id"]},
        )
        issues = client.get("/api/ingestion/issues?batch_id=batch:notanissue").json()
        assert all(
            issue["event_type"] != "bank_profile_selection" for issue in issues["issues"]
        )

    def test_the_raw_bank_source_evidence_is_unchanged_by_the_mapping_source(
        self, tmp_path: Path
    ):
        """A row's recorded raw fields must not depend on how the profile was chosen.

        The demo statement is reconciled twice under an identical column
        mapping -- once as the shipped built-in, once as a saved mapping
        somebody typed in -- and the per-row audit facts are compared.
        Anything but equality would mean the *selection mechanism* had leaked
        into what the statement is recorded as saying, which would make the
        raw provenance trail depend on how a profile was chosen rather than
        on what the file contains.

        Two separate apps, each with exactly one entry for this header row.
        A single app holding both the built-in and an identical saved mapping
        would make the file match two entries -- correctly ambiguous, and
        therefore unusable for a comparison; that ambiguity is asserted in
        ``test_bank_mapping_matching.py``. The saved-mapping app is given an
        empty built-in registry through ``app.state``, the same seam the
        existing autodetect tests use.
        """
        demo_profile = json.loads((DEMO_ROOT / "bank-profile.json").read_bytes())

        def row_facts(name: str, batch_id: str, setup=None, drop_built_ins=False, **data):
            with TestClient(create_app(ledger_path=tmp_path / f"{name}.sqlite3")) as client:
                if drop_built_ins:
                    client.app.state.bank_profile_registry = BankProfileRegistry(())
                if setup is not None:
                    data.update(setup(client))
                run = client.post(
                    "/api/reconciliation/run",
                    files={
                        "razorpay_file": ("r.json", RAZORPAY, "application/json"),
                        "bank_file": ("bank.csv", DEMO_CSV, "text/csv"),
                    },
                    data={"mode": "replay", "batch_id": batch_id, **data},
                )
                assert run.status_code == 200, run.text
                events = client.get(f"/api/audit?batch_id={batch_id}").json()["events"]
                return sorted(
                    (
                        event["payload"]["row_index"],
                        tuple(sorted(event["payload"]["source_fields_used"])),
                        tuple(sorted(event["payload"]["dropped_fields"])),
                    )
                    for event in events
                    if event["event_type"] == "accepted_bank_row"
                )

        def save_identical_mapping(client) -> dict:
            response = post_mapping(
                client,
                {
                    "name": "Demo layout, saved by hand",
                    "value_date_column": demo_profile["value_date_column"],
                    "value_date_format": demo_profile["value_date_format"],
                    "narration_column": demo_profile["narration_column"],
                    "reference_id_column": demo_profile.get("reference_id_column"),
                    "money_columns": demo_profile["money_columns"],
                    "confirmed_fields": [],
                },
                csv=DEMO_CSV,
            )
            assert response.status_code == 200, response.text
            return {"saved_mapping_id": response.json()["saved"]["mapping_id"]}

        under_built_in = row_facts(
            "builtin", "batch:evidence", built_in_profile_id="finrecon_demo_v1"
        )
        under_saved = row_facts(
            "saved", "batch:evidence",
            setup=save_identical_mapping, drop_built_ins=True,
        )

        assert under_built_in  # not vacuously empty
        assert under_built_in == under_saved


def test_the_built_in_registry_ships_unchanged():
    """No saved-mapping work added, removed or altered a shipped profile."""
    registry = built_in_registry()
    assert [entry.profile_id for entry in registry] == ["finrecon_demo_v1"]
    assert registry.require("finrecon_demo_v1").verification.value == "demo_fixture"
