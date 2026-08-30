"""FastAPI boundary over orchestration, ledger queries, and human authority."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from finrecon.adapters.bank.csv_parser import BankCsvDecodeError
from finrecon.adapters.bank.csv_profile import (
    AmountDirectionColumns,
    BankCsvProfile,
    DebitCreditColumns,
)
from finrecon.adapters.razorpay.recon_row import RazorpayReconRow
from finrecon.agent.cache import ReplayMissError, TrajectoryCache
from finrecon.agent.providers.base import ProviderConfigurationError
from finrecon.ledger import BatchIdentityError, LedgerStore, open_ledger
from finrecon.orchestrate import run_reconciliation_batch

from .schemas import (
    AuditResponse,
    BenchmarkCaseDetailResponse,
    BenchmarkCasesResponse,
    BenchmarkDetailResponse,
    BenchmarkListResponse,
    BenchmarkReplayDetailResponse,
    BenchmarkReplaysResponse,
    BenchmarkReportsResponse,
    CaseDetailResponse,
    CaseListResponse,
    IngestionIssuesResponse,
    OverviewResponse,
    ResolutionRequest,
    ResolutionResponse,
    RunResponse,
    RunSummary,
)
from .benchmarks import BenchmarkCatalog
from .service import (
    audit_events,
    case_detail,
    ingestion_issues,
    list_cases,
    overview,
    resolve_case,
    run_summaries,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER = PROJECT_ROOT / "var" / "finrecon.sqlite3"
DEMO_ROOT = PROJECT_ROOT / "fixtures" / "demo"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024


def _api_error(code: str, message: str, status_code: int) -> Exception:
    from fastapi import HTTPException
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _profile_from_payload(payload: dict) -> BankCsvProfile:
    try:
        money_payload = payload["money_columns"]
        kind = money_payload["kind"]
        if kind == "debit_credit":
            money_columns = DebitCreditColumns(
                debit_column=money_payload["debit_column"],
                credit_column=money_payload["credit_column"],
            )
        elif kind == "amount_direction":
            money_columns = AmountDirectionColumns(
                amount_column=money_payload["amount_column"],
                direction_column=money_payload["direction_column"],
                credit_values=frozenset(money_payload["credit_values"]),
                debit_values=frozenset(money_payload["debit_values"]),
            )
        else:
            raise ValueError("money_columns.kind must be debit_credit or amount_direction")
        return BankCsvProfile(
            profile_id=payload["profile_id"], currency=payload["currency"],
            value_date_column=payload["value_date_column"],
            value_date_format=payload["value_date_format"],
            narration_column=payload["narration_column"], money_columns=money_columns,
            reference_id_column=payload.get("reference_id_column"),
            currency_column=payload.get("currency_column"),
            thousands_separator=payload.get("thousands_separator"),
            delimiter=payload.get("delimiter", ","), encoding=payload.get("encoding", "utf-8"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _api_error("invalid_bank_profile", f"Bank profile is invalid: {exc}", 422) from exc


def _razorpay_rows(raw: bytes) -> list[RazorpayReconRow]:
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _api_error("malformed_razorpay_upload", f"Razorpay file must be UTF-8 JSON: {exc}", 422) from exc
    if not isinstance(payload, list):
        raise _api_error("malformed_razorpay_upload", "Razorpay file must contain a JSON array of recon rows.", 422)
    try:
        # The source model is strict; JSON mode is the sanctioned wire path
        # that turns the enum's JSON string into RazorpayReconType.
        return [RazorpayReconRow.model_validate_json(json.dumps(item)) for item in payload]
    except ValidationError as exc:
        raise _api_error("invalid_razorpay_row", f"A Razorpay recon row failed validation: {exc.error_count()} field error(s).", 422) from exc


async def _bounded_read(upload: UploadFile) -> bytes:
    data = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise _api_error("upload_too_large", "Each upload must be 15 MB or smaller.", 413)
    if not data:
        raise _api_error("empty_upload", f"{upload.filename or 'Upload'} is empty.", 422)
    return data


def _run(
    *, store: LedgerStore, razorpay_bytes: bytes, bank_bytes: bytes,
    profile_bytes: bytes, batch_id: str, mode: str, demo: bool = False,
) -> RunResponse:
    try:
        profile_payload = json.loads(profile_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _api_error("invalid_bank_profile", f"Bank profile must be UTF-8 JSON: {exc}", 422) from exc
    profile = _profile_from_payload(profile_payload)
    rows = _razorpay_rows(razorpay_bytes)
    try:
        result = run_reconciliation_batch(
            store=store,
            razorpay_rows=rows,
            razorpay_source_id="demo:razorpay" if demo else "upload:razorpay",
            bank_csv_bytes=bank_bytes,
            bank_profile=profile,
            bank_source_id="demo:bank" if demo else "upload:bank",
            batch_id=batch_id,
            split="demo" if demo else "uploaded",
            mode=mode,
            fixtures_dir=(DEMO_ROOT / "trajectories") if demo else None,
            provider_id="mechanical" if demo else None,
            model="mechanical-investigator-v1" if demo else None,
        )
    except ReplayMissError as exc:
        raise _api_error(
            "replay_cache_miss",
            f"No cached Stage-3 trajectory exists for {exc.case_id}. Use Live only when provider credentials are configured.",
            409,
        ) from exc
    except ProviderConfigurationError as exc:
        raise _api_error("live_provider_not_configured", "Live mode is not configured on the server. Provider secrets are never accepted from the browser.", 503) from exc
    except BankCsvDecodeError as exc:
        raise _api_error("malformed_bank_upload", str(exc), 422) from exc
    except BatchIdentityError as exc:
        raise _api_error("batch_identity_conflict", str(exc), 409) from exc
    summaries = {item.batch_id: item for item in run_summaries(store)}
    summary = summaries[batch_id]
    return RunResponse(
        batch_id=batch_id, mode=mode,
        provider_calls_made=result.stage3_result.provider_calls_made(),
        result=summary,
    )


def create_app(*, ledger_path: str | Path | None = None) -> FastAPI:
    resolved_ledger = Path(ledger_path or os.environ.get("FINRECON_LEDGER_PATH", DEFAULT_LEDGER))
    app = FastAPI(title="FinRecon Operations API", version="1.0.0")
    app.state.ledger_path = resolved_ledger
    app.state.benchmark_catalog = BenchmarkCatalog(PROJECT_ROOT)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def store_dependency() -> Iterator[LedgerStore]:
        store = open_ledger(app.state.ledger_path)
        try:
            yield store
        finally:
            store.close()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/benchmarks", response_model=BenchmarkListResponse)
    def get_benchmarks() -> dict:
        return app.state.benchmark_catalog.list()

    @app.get("/api/benchmarks/{benchmark_id}", response_model=BenchmarkDetailResponse)
    def get_benchmark(benchmark_id: str) -> dict:
        return app.state.benchmark_catalog.detail(benchmark_id)

    @app.get("/api/benchmarks/{benchmark_id}/reports", response_model=BenchmarkReportsResponse)
    def get_benchmark_reports(benchmark_id: str) -> dict:
        return app.state.benchmark_catalog.reports(benchmark_id)

    @app.get("/api/benchmarks/{benchmark_id}/cases", response_model=BenchmarkCasesResponse)
    def get_benchmark_cases(
        benchmark_id: str,
        outcome: str | None = Query(default=None, pattern="^(resolved|escalated|unknown|recorded|tool_validation_failure|budget_exhausted|malformed)$"),
        stage: str | None = Query(default=None, pattern="^(stage2|stage3|unknown)$"),
        tier: str | None = Query(default=None, pattern="^(T0|T1|T2|T3)$"),
        replay_only: bool = False,
        controller_rejection: bool = False,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        search: str | None = Query(default=None, max_length=200),
    ) -> dict:
        return app.state.benchmark_catalog.cases(benchmark_id, outcome=outcome, stage=stage, tier=tier, replay_only=replay_only,
                                                  controller_rejection=controller_rejection, offset=offset,
                                                  limit=limit, search=search)

    @app.get("/api/benchmarks/{benchmark_id}/cases/{case_id:path}", response_model=BenchmarkCaseDetailResponse)
    def get_benchmark_case(benchmark_id: str, case_id: str) -> dict:
        return app.state.benchmark_catalog.case(benchmark_id, case_id)

    @app.get("/api/benchmarks/{benchmark_id}/replays", response_model=BenchmarkReplaysResponse)
    def get_benchmark_replays(benchmark_id: str) -> dict:
        return app.state.benchmark_catalog.replays(benchmark_id)

    @app.get("/api/benchmarks/{benchmark_id}/replays/{investigator}/{case_id:path}", response_model=BenchmarkReplayDetailResponse)
    def get_benchmark_replay(benchmark_id: str, investigator: str, case_id: str) -> dict:
        return app.state.benchmark_catalog.replay(benchmark_id, investigator, case_id)

    @app.get("/api/overview", response_model=OverviewResponse)
    def get_overview(batch_id: str | None = None, store: LedgerStore = Depends(store_dependency)) -> OverviewResponse:
        return overview(store, batch_id)

    @app.get("/api/runs", response_model=list[RunSummary])
    def get_runs(store: LedgerStore = Depends(store_dependency)) -> list[RunSummary]:
        return run_summaries(store)

    @app.get("/api/runs/{batch_id}", response_model=OverviewResponse)
    def get_run(batch_id: str, store: LedgerStore = Depends(store_dependency)) -> OverviewResponse:
        return overview(store, batch_id)

    @app.get("/api/cases", response_model=CaseListResponse)
    def get_cases(
        batch_id: str | None = None,
        search: str | None = Query(default=None, max_length=200),
        status_filter: str | None = Query(default=None, alias="status"),
        source: str | None = None,
        escalated_only: bool = False,
        store: LedgerStore = Depends(store_dependency),
    ) -> CaseListResponse:
        return list_cases(store, batch_id=batch_id, search=search, status_filter=status_filter,
                          source_filter=source, escalated_only=escalated_only)

    @app.get("/api/cases/{case_id:path}", response_model=CaseDetailResponse)
    def get_case(case_id: str, batch_id: str | None = None,
                 store: LedgerStore = Depends(store_dependency)) -> CaseDetailResponse:
        return case_detail(store, case_id, batch_id=batch_id)

    @app.post("/api/cases/{case_id:path}/resolution", response_model=ResolutionResponse)
    def post_resolution(case_id: str, request: ResolutionRequest,
                        store: LedgerStore = Depends(store_dependency)) -> ResolutionResponse:
        return resolve_case(store, case_id, request)

    @app.get("/api/ingestion/issues", response_model=IngestionIssuesResponse)
    def get_ingestion_issues(batch_id: str | None = None,
                             store: LedgerStore = Depends(store_dependency)) -> IngestionIssuesResponse:
        return ingestion_issues(store, batch_id)

    @app.get("/api/audit", response_model=AuditResponse)
    def get_audit(batch_id: str | None = None, case_id: str | None = None,
                  store: LedgerStore = Depends(store_dependency)) -> AuditResponse:
        return audit_events(store, batch_id, case_id)

    @app.post("/api/reconciliation/run", response_model=RunResponse)
    async def post_run(
        razorpay_file: UploadFile = File(...),
        bank_file: UploadFile = File(...),
        bank_profile: UploadFile = File(...),
        mode: str = Form("replay"),
        batch_id: str | None = Form(None),
        store: LedgerStore = Depends(store_dependency),
    ) -> RunResponse:
        if mode not in {"replay", "live"}:
            raise _api_error("invalid_mode", "Mode must be replay or live.", 422)
        resolved_batch = (batch_id or f"batch:upload:{uuid.uuid4().hex[:12]}").strip()
        if not resolved_batch:
            raise _api_error("invalid_batch_id", "Batch ID must not be blank.", 422)
        return _run(
            store=store,
            razorpay_bytes=await _bounded_read(razorpay_file),
            bank_bytes=await _bounded_read(bank_file),
            profile_bytes=await _bounded_read(bank_profile),
            batch_id=resolved_batch,
            mode=mode,
        )

    @app.post("/api/reconciliation/demo", response_model=RunResponse)
    def post_demo(store: LedgerStore = Depends(store_dependency)) -> RunResponse:
        return _run(
            store=store,
            razorpay_bytes=(DEMO_ROOT / "razorpay.json").read_bytes(),
            bank_bytes=(DEMO_ROOT / "bank.csv").read_bytes(),
            profile_bytes=(DEMO_ROOT / "bank-profile.json").read_bytes(),
            batch_id="batch:demo-operations",
            mode="replay",
            demo=True,
        )

    web_dist = PROJECT_ROOT / "web" / "dist"
    if web_dist.exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = web_dist / path
            return FileResponse(target if target.is_file() else web_dist / "index.html")

    return app


app = create_app()
