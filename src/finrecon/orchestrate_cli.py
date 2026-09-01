"""CLI wrapper: raw Razorpay recon + a bank CSV export, through to decisions.

Almost no business logic lives here -- it parses two input files plus a
small profile declaration, calls :func:`finrecon.orchestrate.run_reconciliation_batch`,
and prints counts, following the print conventions of ``reconcile_cli.py``/
``investigate_cli.py``: operational facts only, no accuracy (this entrypoint
never reads ground truth, so it has none to report).

A bad bank row or a quarantined settlement never fails the run -- those are
handled by the adapters' own row/settlement-scoped quarantine and the batch
continues; only a missing input file, an unparsable input, an unsupported
``--mode``, or (in live mode) an unconfigured provider fail the whole
invocation, with a clear message and a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from finrecon.adapters.bank.csv_profile import BankCsvProfile
from finrecon.adapters.bank.profile_json import (
    BankProfileFormatError,
    profile_from_payload,
)
from finrecon.adapters.razorpay.recon_row import RazorpayReconRow
from finrecon.agent.cache import DEFAULT_FIXTURE_DIR, ReplayMissError
from finrecon.agent.providers.base import ProviderConfigurationError
from finrecon.agent.providers.config import describe_configuration
from finrecon.json_text import JSON_TEXT_ENCODING
from finrecon.ledger.store import open_ledger
from finrecon.orchestrate import run_reconciliation_batch


class OrchestrationInputError(RuntimeError):
    """One of the CLI's own input files could not be turned into a usable object."""


def _load_razorpay_rows(path: Path) -> list[RazorpayReconRow]:
    try:
        payload = json.loads(path.read_text(encoding=JSON_TEXT_ENCODING))
    except (OSError, json.JSONDecodeError) as error:
        raise OrchestrationInputError(f"could not read {path} as JSON: {error}") from error
    if not isinstance(payload, list):
        raise OrchestrationInputError(
            f"{path}: expected a JSON list of Razorpay recon row objects"
        )
    try:
        return [RazorpayReconRow.model_validate_json(json.dumps(entry)) for entry in payload]
    except Exception as error:  # pydantic ValidationError, primarily
        raise OrchestrationInputError(f"{path}: invalid Razorpay recon row: {error}") from error


def _load_bank_profile(path: Path) -> BankCsvProfile:
    """Read ``--bank-profile`` through the one shared profile reader.

    The wire shape is decoded in
    :mod:`finrecon.adapters.bank.profile_json` -- shared with the API and
    the built-in profile registry so the three cannot drift -- and only the
    CLI's own error type is applied here. An omitted
    ``inactive_side_marker`` still means ``empty_only`` and an unrecognised
    one is still a hard input error, unchanged.
    """
    try:
        payload = json.loads(path.read_text(encoding=JSON_TEXT_ENCODING))
    except (OSError, json.JSONDecodeError) as error:
        raise OrchestrationInputError(f"could not read {path} as JSON: {error}") from error
    try:
        return profile_from_payload(payload)
    except BankProfileFormatError as error:
        raise OrchestrationInputError(f"{path}: invalid bank profile: {error}") from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m finrecon.orchestrate_cli",
        description=(
            "Run raw Razorpay recon rows and a bank CSV export through the "
            "existing Stage-2/Stage-3 pipeline. Reports no accuracy: this "
            "entrypoint never reads ground truth."
        ),
    )
    parser.add_argument(
        "--razorpay-recon", required=True, help="JSON file: a list of raw recon row objects"
    )
    parser.add_argument("--bank-csv", required=True, help="one bank CSV export file")
    parser.add_argument(
        "--bank-profile", required=True, help="JSON file describing a BankCsvProfile"
    )
    parser.add_argument(
        "--mode", choices=("replay", "live"), default="replay", help="Stage-3 mode (default: replay)"
    )
    parser.add_argument("--ledger", default=":memory:", help="SQLite path, or :memory: (default)")
    parser.add_argument(
        "--fixtures",
        default=str(DEFAULT_FIXTURE_DIR),
        help=f"trajectory cache directory (default: {DEFAULT_FIXTURE_DIR})",
    )
    parser.add_argument("--batch-id", default=None, help="default: batch:<split>")
    parser.add_argument("--split", default="live")
    parser.add_argument("--razorpay-source-id", default="razorpay")
    parser.add_argument("--bank-source-id", default="bank")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    razorpay_path = Path(args.razorpay_recon)
    bank_csv_path = Path(args.bank_csv)
    bank_profile_path = Path(args.bank_profile)
    for path, label in (
        (razorpay_path, "--razorpay-recon"),
        (bank_csv_path, "--bank-csv"),
        (bank_profile_path, "--bank-profile"),
    ):
        if not path.exists():
            print(f"{label}: file not found: {path}")
            return 2

    try:
        razorpay_rows = _load_razorpay_rows(razorpay_path)
        bank_profile = _load_bank_profile(bank_profile_path)
    except OrchestrationInputError as error:
        print(str(error))
        return 2

    bank_csv_bytes = bank_csv_path.read_bytes()
    batch_id = args.batch_id or f"batch:{args.split}"

    if args.mode == "live":
        configuration = describe_configuration()
        print("provider configuration:")
        print(json.dumps(configuration, indent=2, sort_keys=True))

    with open_ledger(args.ledger) as store:
        try:
            result = run_reconciliation_batch(
                store=store,
                razorpay_rows=razorpay_rows,
                razorpay_source_id=args.razorpay_source_id,
                bank_csv_bytes=bank_csv_bytes,
                bank_profile=bank_profile,
                bank_source_id=args.bank_source_id,
                batch_id=batch_id,
                split=args.split,
                mode=args.mode,
                fixtures_dir=Path(args.fixtures),
            )
        except ProviderConfigurationError as error:
            print(f"\ncannot start a live run: {error}")
            print("  Set a credential (see .env.example), or use --mode replay.")
            return 2
        except ReplayMissError as miss:
            print(f"\nreplay miss: {miss}")
            print(
                f"  fixtures dir: {args.fixtures}\n"
                "  Run with --mode live (with a credential configured) to record "
                "trajectories first, or point --fixtures at a warmed corpus."
            )
            return 2

        print(f"batch                    {result.batch_result.batch_id}")
        print(f"ingested settlements     {result.ingested_settlement_count}")
        print(f"ingested bank records    {result.ingested_bank_record_count}")
        print(f"ingestion quarantined    {result.ingestion_quarantined_count}")
        print(f"  quarantined settlements  {len(result.quarantined_settlements)}")
        print(f"  rejected bank rows       {len(result.rejected_bank_rows)}")
        print(f"total cases              {result.total_cases}")
        print(f"deterministic resolved   {len(result.deterministic_resolved)}")
        print(f"ai-assisted resolved     {len(result.ai_assisted_resolved)}")
        print(f"escalated                {len(result.escalated)}")
        print(f"ledger digest            {store.digest(result.batch_result.batch_id)}")
        print(
            "\nNo accuracy is reported here: this entrypoint never reads ground "
            "truth. Accuracy belongs to the Stage-4 benchmark harness."
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
