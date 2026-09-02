"""JSON text boundaries must tolerate a Windows UTF-8 byte-order mark.

Reproduces and closes a real failure: profile/Razorpay JSON authored by
Windows tooling (PowerShell ``Out-File``, Notepad) carries a leading
``EF BB BF``, which is legal in the byte stream but not legal JSON
grammar, so a strict ``utf-8`` decode plus ``json.loads`` fails with
``Unexpected UTF-8 BOM (decode using utf-8-sig)``.

Scope is deliberately narrow: JSON *text* only. Bank CSV bytes still go
through the profile's declared ``encoding`` and are not covered here --
see ``tests/test_bank_csv_adapter.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finrecon.api.app import DEMO_ROOT, create_app
from finrecon.json_text import decode_json_bytes
from finrecon.orchestrate_cli import _load_bank_profile, _load_razorpay_rows

BOM = b"\xef\xbb\xbf"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(ledger_path=tmp_path / "finrecon-bom.sqlite3")) as value:
        yield value


def _demo_inputs() -> tuple[bytes, bytes, bytes]:
    rows = json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
    bank_lines = (DEMO_ROOT / "bank.csv").read_text(encoding="utf-8").splitlines()
    bank_csv = ("\n".join(bank_lines[:2]) + "\n").encode("utf-8")
    profile = (DEMO_ROOT / "bank-profile.json").read_bytes()
    return json.dumps(rows).encode("utf-8"), bank_csv, profile


def test_the_failure_being_fixed_is_real_under_a_strict_utf8_decode():
    with pytest.raises(json.JSONDecodeError, match="BOM"):
        json.loads((BOM + b'{"a": 1}').decode("utf-8"))


def test_decode_json_bytes_strips_one_bom_and_is_a_no_op_without_one():
    assert decode_json_bytes(BOM + b'{"a": 1}') == '{"a": 1}'
    assert decode_json_bytes(b'{"a": 1}') == '{"a": 1}'
    # Not encoding auto-detection: non-UTF-8 bytes still fail loudly.
    with pytest.raises(UnicodeDecodeError):
        decode_json_bytes(b"\xff\xfe{")


def _post_run(client: TestClient, *, razorpay: bytes, bank: bytes, profile: bytes, batch_id: str):
    return client.post(
        "/api/reconciliation/run",
        data={"mode": "replay", "batch_id": batch_id},
        files={
            "razorpay_file": ("razorpay.json", razorpay, "application/json"),
            "bank_file": ("bank.csv", bank, "text/csv"),
            "bank_profile": ("profile.json", profile, "application/json"),
        },
    )


def test_api_accepts_a_bom_prefixed_bank_profile_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "finrecon.orchestrate.build_chain",
        lambda: (_ for _ in ()).throw(AssertionError("replay attempted a provider call")),
    )
    razorpay, bank, profile = _demo_inputs()
    response = _post_run(
        client, razorpay=razorpay, bank=bank, profile=BOM + profile, batch_id="batch:bom-profile"
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["metrics"]["deterministic_resolved"] == 1


def test_api_accepts_a_bom_prefixed_razorpay_json(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "finrecon.orchestrate.build_chain",
        lambda: (_ for _ in ()).throw(AssertionError("replay attempted a provider call")),
    )
    razorpay, bank, profile = _demo_inputs()
    response = _post_run(
        client, razorpay=BOM + razorpay, bank=bank, profile=profile, batch_id="batch:bom-razorpay"
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["metrics"]["deterministic_resolved"] == 1


def test_api_still_accepts_plain_utf8_json_identically(client: TestClient, monkeypatch):
    monkeypatch.setattr(
        "finrecon.orchestrate.build_chain",
        lambda: (_ for _ in ()).throw(AssertionError("replay attempted a provider call")),
    )
    razorpay, bank, profile = _demo_inputs()
    response = _post_run(
        client, razorpay=razorpay, bank=bank, profile=profile, batch_id="batch:plain-utf8"
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["metrics"]["deterministic_resolved"] == 1


def test_api_still_rejects_json_that_is_actually_malformed(client: TestClient):
    razorpay, bank, profile = _demo_inputs()
    response = _post_run(
        client, razorpay=razorpay, bank=bank, profile=BOM + b"{not json", batch_id="batch:bad"
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_bank_profile"


def test_cli_loads_a_bom_prefixed_bank_profile_json(tmp_path: Path):
    plain = (DEMO_ROOT / "bank-profile.json").read_bytes()
    bom_path = tmp_path / "bom-profile.json"
    bom_path.write_bytes(BOM + plain)
    plain_path = tmp_path / "plain-profile.json"
    plain_path.write_bytes(plain)

    assert _load_bank_profile(bom_path) == _load_bank_profile(plain_path)
    assert _load_bank_profile(bom_path).profile_id == "finrecon_demo_v1"


def test_cli_loads_a_bom_prefixed_razorpay_json(tmp_path: Path):
    plain = json.dumps(
        json.loads((DEMO_ROOT / "razorpay.json").read_text(encoding="utf-8"))[:1]
    ).encode("utf-8")
    bom_path = tmp_path / "bom-razorpay.json"
    bom_path.write_bytes(BOM + plain)
    plain_path = tmp_path / "plain-razorpay.json"
    plain_path.write_bytes(plain)

    rows = _load_razorpay_rows(bom_path)
    assert len(rows) == 1
    assert rows == _load_razorpay_rows(plain_path)
