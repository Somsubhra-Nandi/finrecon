"""FastAPI boundary over orchestration, ledger queries, and human authority."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from finrecon.adapters.bank.csv_parser import BankCsvDecodeError
from finrecon.adapters.bank.csv_profile import BankCsvProfile
from finrecon.adapters.bank.profile_json import (
    BankProfileFormatError,
    profile_from_payload,
)
from finrecon.adapters.bank.schema import (
    BankProfileSelection,
    BuiltInProfile,
    BuiltInProfileVerificationError,
    CombinedMappingRegistry,
    SavedMappingEntry,
    built_in_registry,
    inspect_bank_csv,
    resolve_verified_built_in,
    resolve_verified_saved_mapping,
)
from finrecon.adapters.razorpay.recon_row import RazorpayReconRow
from finrecon.agent.cache import ReplayMissError, TrajectoryCache
from finrecon.agent.providers.base import ProviderConfigurationError
from finrecon.json_text import decode_json_bytes
from finrecon.ledger import BatchIdentityError, LedgerStore, open_ledger
from finrecon.ledger.bank_mappings import BankMappingStore
from finrecon.orchestrate import run_reconciliation_batch

from . import bank_mappings as mapping_api
from .schemas import (
    AuditResponse,
    BankMappingDetailResponse,
    BankMappingListResponse,
    BankMappingProposalResponse,
    BankMappingSaveResponse,
    BankProfileSelectionView,
    BankStatementInspectionResponse,
    BenchmarkCaseDetailResponse,
    BenchmarkCasesResponse,
    BenchmarkDetailResponse,
    BenchmarkFullReplayResponse,
    BenchmarkListResponse,
    BenchmarkReplayDetailResponse,
    BenchmarkReplaysResponse,
    BenchmarkReportsResponse,
    BuiltInProfileView,
    CaseDetailResponse,
    CaseListResponse,
    IngestionIssuesResponse,
    MappingMatchView,
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
    """Manual profile upload -> declaration, via the one shared reader.

    The decoding itself lives in
    :mod:`finrecon.adapters.bank.profile_json` so the API, the CLI and the
    built-in registry cannot drift on what a profile payload means; this
    wrapper only turns its error into this boundary's error, exactly as
    before.
    """
    try:
        return profile_from_payload(payload)
    except BankProfileFormatError as exc:
        raise _api_error("invalid_bank_profile", f"Bank profile is invalid: {exc}", 422) from exc


def _razorpay_rows(raw: bytes) -> list[RazorpayReconRow]:
    try:
        decoded = decode_json_bytes(raw)
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


def _profile_view(entry) -> BuiltInProfileView:
    return BuiltInProfileView(
        profile_id=entry.profile_id, label=entry.label, version=entry.version,
        verification=entry.verification.value, description=entry.description,
        evidence=entry.evidence,
    )


def _match_view(entry) -> MappingMatchView:
    """One matched entry, in the shape that covers both kinds.

    Built-ins and saved mappings differ in what they can honestly claim --
    a built-in states an evidence level for its schema, a saved mapping
    states that a person here confirmed it -- so the fields differ, but the
    envelope does not, and neither does the ambiguity handling above it.
    """
    if isinstance(entry, SavedMappingEntry):
        return MappingMatchView(
            kind="user_saved",
            profile_id=entry.profile_id,
            label=entry.name,
            version=entry.version,
            description="Saved by this deployment's operator.",
            evidence="",
            saved_mapping=mapping_api.saved_mapping_view(entry),
        )
    return MappingMatchView(
        kind="built_in",
        profile_id=entry.profile_id,
        label=entry.label,
        version=entry.version,
        verification=entry.verification.value,
        description=entry.description,
        evidence=entry.evidence,
    )


def _inspection_response(inspection) -> BankStatementInspectionResponse:
    """Project an inspection, keeping the pre-existing built-in fields exact.

    ``profile``/``candidates`` continue to carry *built-ins only*, so a
    client written before saved mappings existed reads them with exactly
    their old meaning rather than silently receiving an entry whose
    ``verification`` FinRecon cannot vouch for. ``match``/``matches`` are
    the fields that see both kinds.
    """
    observed = inspection.observed
    built_in_match = (
        inspection.profile if isinstance(inspection.profile, BuiltInProfile) else None
    )
    return BankStatementInspectionResponse(
        status=inspection.status.value,
        raw_headers=list(observed.raw_headers),
        normalized_headers=list(observed.normalized_headers),
        signature=observed.digest,
        field_count=observed.field_count,
        match_tier=inspection.match_tier.value if inspection.match_tier else None,
        profile=_profile_view(built_in_match) if built_in_match else None,
        candidates=[
            _profile_view(entry)
            for entry in inspection.candidates
            if isinstance(entry, BuiltInProfile)
        ],
        match=_match_view(inspection.profile) if inspection.profile else None,
        matches=[_match_view(entry) for entry in inspection.candidates],
    )


def _selection_view(selection) -> BankProfileSelectionView:
    return BankProfileSelectionView(
        profile_id=selection.profile_id,
        selection_mode=selection.selection_mode.value,
        match_tier=selection.match_tier.value if selection.match_tier else None,
        version=selection.version, label=selection.label,
        verification=selection.verification,
        schema_signature=selection.schema_signature,
        mapping_id=selection.mapping_id,
        mapping_version=selection.mapping_version,
        provenance=selection.provenance,
        source=selection.source,
    )


def _resolve_bank_profile(
    *, registry: CombinedMappingRegistry, bank_bytes: bytes,
    profile_bytes: bytes | None, built_in_profile_id: str | None,
    saved_mapping_id: str | None = None,
    mapping_store: BankMappingStore | None = None,
) -> tuple[BankCsvProfile, BankProfileSelection]:
    """Decide which profile this run uses, and record how it was decided.

    Exactly one of the three paths must be taken. The manual path is the
    pre-existing one and is unchanged. The built-in and saved-mapping paths
    both treat the client's identifier as a *claim* and re-verify it
    server-side against the uploaded bytes -- see
    :func:`~finrecon.adapters.bank.schema.detect.resolve_verified_built_in`
    for why trusting it would let a client have one bank's columns read
    under another bank's mapping. The saved-mapping path needs that check
    at least as much: nobody outside this deployment has reviewed the
    mapping, and its id is one the browser is holding.
    """
    supplied = [
        name
        for name, value in (
            ("bank_profile", profile_bytes),
            ("built_in_profile_id", built_in_profile_id),
            ("saved_mapping_id", saved_mapping_id),
        )
        if value is not None
    ]
    if len(supplied) > 1:
        raise _api_error(
            "conflicting_bank_profile",
            f"Supply exactly one bank profile source; got {supplied}.",
            422,
        )
    if saved_mapping_id is not None:
        assert mapping_store is not None  # provided together by the endpoint
        try:
            entry, inspection = resolve_verified_saved_mapping(
                saved_mapping_id,
                bank_bytes,
                registry,
                mapping_store.active_version(saved_mapping_id),
            )
        except BuiltInProfileVerificationError as exc:
            raise _api_error(
                exc.code, str(exc), 404 if exc.code == "unknown_bank_mapping" else 422
            ) from exc
        return entry.profile, BankProfileSelection.saved_mapping(entry, inspection)
    if built_in_profile_id is not None:
        try:
            entry, inspection = resolve_verified_built_in(
                built_in_profile_id, bank_bytes, registry
            )
        except BuiltInProfileVerificationError as exc:
            raise _api_error(
                exc.code, str(exc), 404 if exc.code == "unknown_built_in_profile" else 422
            ) from exc
        return entry.profile, BankProfileSelection.detected(entry, inspection)
    if profile_bytes is None:
        raise _api_error(
            "missing_bank_profile",
            "Supply a bank_profile upload, a built_in_profile_id for a "
            "recognised bank format, or a saved_mapping_id for a mapping you "
            "have confirmed.",
            422,
        )
    try:
        profile_payload = json.loads(decode_json_bytes(profile_bytes))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _api_error("invalid_bank_profile", f"Bank profile must be UTF-8 JSON: {exc}", 422) from exc
    profile = _profile_from_payload(profile_payload)
    return profile, BankProfileSelection.manual(profile.profile_id)


def _run(
    *, store: LedgerStore, registry: CombinedMappingRegistry, razorpay_bytes: bytes,
    bank_bytes: bytes, profile_bytes: bytes | None, batch_id: str, mode: str,
    built_in_profile_id: str | None = None, saved_mapping_id: str | None = None,
    mapping_store: BankMappingStore | None = None, demo: bool = False,
) -> RunResponse:
    profile, selection = _resolve_bank_profile(
        registry=registry, bank_bytes=bank_bytes, profile_bytes=profile_bytes,
        built_in_profile_id=built_in_profile_id,
        saved_mapping_id=saved_mapping_id, mapping_store=mapping_store,
    )
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
            profile_selection=selection,
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
        bank_profile_selection=_selection_view(selection),
    )


def create_app(*, ledger_path: str | Path | None = None) -> FastAPI:
    resolved_ledger = Path(ledger_path or os.environ.get("FINRECON_LEDGER_PATH", DEFAULT_LEDGER))
    app = FastAPI(title="FinRecon Operations API", version="1.0.0")
    app.state.ledger_path = resolved_ledger
    app.state.benchmark_catalog = BenchmarkCatalog(PROJECT_ROOT)
    # Loaded once at startup so a malformed shipped artifact fails the
    # build loudly rather than at somebody's first upload. Held on
    # app.state so a test can install its own registry without reaching
    # into the module-level cache.
    app.state.bank_profile_registry = built_in_registry()
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

    @app.post("/api/benchmarks/{benchmark_id}/replay", response_model=BenchmarkFullReplayResponse)
    def replay_full_benchmark(benchmark_id: str) -> dict:
        return app.state.benchmark_catalog.full_replay(benchmark_id)

    @app.get("/api/benchmarks/{benchmark_id}/cases", response_model=BenchmarkCasesResponse)
    def get_benchmark_cases(
        benchmark_id: str,
        outcome: str | None = Query(default=None, pattern="^(resolved|escalated|unknown|recorded|tool_validation_failure|budget_exhausted|malformed)$"),
        stage: str | None = Query(default=None, pattern="^(stage2|stage3|unknown)$"),
        tier: str | None = Query(default=None, pattern="^(T0|T1|T2|T3)$"),
        termination: str | None = Query(default=None, pattern="^(provider_failure|investigation_complete|deterministic_policy_resolved)$"),
        replay_only: bool = False,
        controller_rejection: bool = False,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        search: str | None = Query(default=None, max_length=200),
    ) -> dict:
        return app.state.benchmark_catalog.cases(benchmark_id, outcome=outcome, stage=stage, tier=tier, termination=termination, replay_only=replay_only,
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

    def mapping_store_for(store: LedgerStore) -> BankMappingStore:
        """Saved-mapping access over the request's existing ledger connection.

        Not a separate dependency: sharing the one request-scoped connection
        keeps a mapping write and everything else in the request under the
        same schema creation and the same transaction boundaries, and avoids
        a second connection to the same SQLite file.
        """
        return BankMappingStore(store.connection)

    def detection_corpus(store: LedgerStore) -> CombinedMappingRegistry:
        return mapping_api.combined_registry(
            app.state.bank_profile_registry, mapping_store_for(store)
        )

    @app.post("/api/bank-mappings/propose", response_model=BankMappingProposalResponse)
    async def post_bank_mapping_propose(
        bank_file: UploadFile = File(...),
        store: LedgerStore = Depends(store_dependency),
    ) -> BankMappingProposalResponse:
        """Propose a column mapping for a statement FinRecon does not recognise.

        Persists nothing and returns no identifier that could stand in for a
        mapping. A recognised or ambiguous schema returns without contacting
        a provider at all -- the server re-inspects the file rather than
        taking the client's word that a proposal is needed, so no browser can
        provoke a model call for a file that already has a mapping.
        """
        raw = await _bounded_read(bank_file)
        return mapping_api.propose_response(
            raw_bytes=raw, registry=detection_corpus(store)
        )

    @app.get("/api/bank-mappings", response_model=BankMappingListResponse)
    def get_bank_mappings(
        store: LedgerStore = Depends(store_dependency),
    ) -> BankMappingListResponse:
        return mapping_api.list_mappings(mapping_store_for(store))

    @app.get("/api/bank-mappings/{mapping_id}", response_model=BankMappingDetailResponse)
    def get_bank_mapping(
        mapping_id: str, store: LedgerStore = Depends(store_dependency)
    ) -> BankMappingDetailResponse:
        return mapping_api.mapping_detail(mapping_store_for(store), mapping_id)

    @app.post("/api/bank-mappings", response_model=BankMappingSaveResponse)
    async def post_bank_mapping(
        bank_file: UploadFile = File(...),
        mapping: str = Form(...),
        store: LedgerStore = Depends(store_dependency),
    ) -> BankMappingSaveResponse:
        """Persist a human-confirmed mapping as version 1 of a named mapping.

        This endpoint *is* the confirmation boundary. The bank file is
        required so the server reads the header row itself instead of
        believing the browser's account of it, and the mapping is validated
        against that read before anything is written.
        """
        raw = await _bounded_read(bank_file)
        return mapping_api.create_mapping(
            store=mapping_store_for(store),
            request=mapping_api.parse_save_request(mapping),
            raw_bytes=raw,
        )

    @app.post(
        "/api/bank-mappings/{mapping_id}/versions",
        response_model=BankMappingSaveResponse,
    )
    async def post_bank_mapping_version(
        mapping_id: str,
        bank_file: UploadFile = File(...),
        mapping: str = Form(...),
        store: LedgerStore = Depends(store_dependency),
    ) -> BankMappingSaveResponse:
        """Confirm an edit as the next version; the previous one is retired.

        Retired, not replaced. A batch reconciled under the old version keeps
        naming exactly the mapping it used, which is the whole reason
        versions exist rather than an editable row.
        """
        raw = await _bounded_read(bank_file)
        return mapping_api.add_mapping_version(
            store=mapping_store_for(store),
            mapping_id=mapping_id,
            request=mapping_api.parse_save_request(mapping),
            raw_bytes=raw,
        )

    @app.post("/api/bank-statement/inspect", response_model=BankStatementInspectionResponse)
    async def post_bank_statement_inspect(
        bank_file: UploadFile = File(...),
        store: LedgerStore = Depends(store_dependency),
    ) -> BankStatementInspectionResponse:
        """Recognise an uploaded statement's schema. Read-only, no side effects.

        Creates no batch, no case and no canonical record, writes nothing to
        the ledger, and reaches no model or provider -- it reads the file's
        header row and compares it with the shipped registry *and* the
        mappings this deployment's operator has confirmed.

        It now takes a ``store`` dependency, which it previously refused, for
        exactly one reason: saved mappings live in the ledger and have to be
        read to be matched. The read-only promise is unchanged and is worth
        restating because the structural guarantee weakened -- this handler
        performs no write, and every function it calls is a query or a
        signature comparison.

        Uses the same bounded read as every other upload, so the existing
        15 MB cap and empty-file rejection apply unchanged.
        """
        raw = await _bounded_read(bank_file)
        return _inspection_response(inspect_bank_csv(raw, detection_corpus(store)))

    @app.post("/api/reconciliation/run", response_model=RunResponse)
    async def post_run(
        razorpay_file: UploadFile = File(...),
        bank_file: UploadFile = File(...),
        bank_profile: UploadFile | None = File(None),
        built_in_profile_id: str | None = Form(None),
        saved_mapping_id: str | None = Form(None),
        mode: str = Form("replay"),
        batch_id: str | None = Form(None),
        store: LedgerStore = Depends(store_dependency),
    ) -> RunResponse:
        """Run a batch under an uploaded profile, a built-in, or a saved mapping.

        ``bank_profile`` remains optional purely so the other paths can
        exist; a request that supplies it behaves exactly as it always did.
        Neither ``built_in_profile_id`` nor ``saved_mapping_id`` is trusted
        on its own -- the server re-inspects the uploaded bytes and requires
        that detection would independently have selected that exact profile
        or mapping version before any ingestion runs. A statement whose
        columns have changed since a mapping was confirmed is therefore
        refused here, before a single ``BankRecord`` exists, rather than
        being read under a mapping that no longer describes it.
        """
        if mode not in {"replay", "live"}:
            raise _api_error("invalid_mode", "Mode must be replay or live.", 422)
        resolved_batch = (batch_id or f"batch:upload:{uuid.uuid4().hex[:12]}").strip()
        if not resolved_batch:
            raise _api_error("invalid_batch_id", "Batch ID must not be blank.", 422)
        requested_built_in = (built_in_profile_id or "").strip() or None
        requested_mapping = (saved_mapping_id or "").strip() or None
        return _run(
            store=store,
            registry=detection_corpus(store),
            mapping_store=mapping_store_for(store),
            razorpay_bytes=await _bounded_read(razorpay_file),
            bank_bytes=await _bounded_read(bank_file),
            profile_bytes=(await _bounded_read(bank_profile)) if bank_profile is not None else None,
            built_in_profile_id=requested_built_in,
            saved_mapping_id=requested_mapping,
            batch_id=resolved_batch,
            mode=mode,
        )

    @app.post("/api/reconciliation/demo", response_model=RunResponse)
    def post_demo(store: LedgerStore = Depends(store_dependency)) -> RunResponse:
        return _run(
            store=store,
            registry=detection_corpus(store),
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

        @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
        def unknown_api(path: str) -> None:
            raise HTTPException(status_code=404, detail={"code": "api_not_found", "message": f"API path /api/{path} does not exist."})

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = web_dist / path
            if target.is_file():
                # Real files reached here keep their default validators. Their
                # names are stable rather than content-hashed -- favicon.svg and
                # og.png -- so they must stay revalidatable; only /assets is
                # hashed, and that is served by its own mount above.
                return FileResponse(target)
            # index.html must never be cached. It is the only file whose URL
            # stays constant across deploys while its contents change, and it
            # names the hashed chunks every route lazily imports. A browser
            # holding a stale copy asks for chunk filenames the new deploy no
            # longer has, the dynamic import 404s, and the page renders blank.
            return FileResponse(web_dist / "index.html", headers={"Cache-Control": "no-store, must-revalidate"})

    return app


app = create_app()
