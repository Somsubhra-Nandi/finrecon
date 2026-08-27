"""Loader for the official-doc-derived Razorpay recon fixtures.

Test-only. Not on the reconciliation path and not imported by anything
under ``src/finrecon`` — see ``fixtures/razorpay/doc_samples/README.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

from finrecon.adapters.razorpay import RazorpayReconRow

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "razorpay" / "doc_samples"


def load_fixture_rows(name: str) -> list[RazorpayReconRow]:
    path = FIXTURE_DIR / name
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [RazorpayReconRow.model_validate_json(json.dumps(entry)) for entry in payload]
