"""HTTP boundary for unknown-schema mapping: propose, confirm, list, version.

The confirmation boundary is enforced *here*, on the server, and this module
is the reason the browser cannot bypass it. Two design choices carry that:

**A proposal has no server-side identity.** ``/propose`` returns a
suggestion and stores nothing -- no row, no cache entry, no id. There is
therefore no ``proposal_id`` for a later request to submit in place of a
mapping, because there is nothing for such an id to point at. The only way
to make a mapping real is to post the complete mapping, and a client that
posts one is posting what a person confirmed.

**Confirmation re-reads the file.** A save request carries the bank CSV
again, and the server reads its header row itself rather than believing the
``raw_headers``, signature or column list the browser sent. A client that
lied about the file's columns would have its mapping rejected by the server's
own read, which is what makes "validate the mapping against the uploaded
schema" a fact rather than a courtesy.

**Ambiguity must be answered, not defaulted.** Where deterministic
validation reports that the sample cannot settle a field -- the day-first /
month-first case, overwhelmingly -- the save request must name that field in
``confirmed_fields``. The server refuses the save otherwise. A browser that
forgot to ask the operator cannot silently accept FinRecon's or a model's
guess on their behalf.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from finrecon.adapters.bank.mapping.formats import format_options
from finrecon.adapters.bank.mapping.sample import BankCsvSampleError, read_sample
from finrecon.adapters.bank.mapping.service import (
    ProposalOutcome,
    propose_mapping,
)
from finrecon.adapters.bank.mapping.validation import (
    MappingValidation,
    validate_mapping_payload,
)
from finrecon.adapters.bank.schema import (
    BankSchemaReadError,
    CombinedMappingRegistry,
    MatchStatus,
    SavedMappingEntry,
    inspect_bank_csv,
    read_signature,
)
from finrecon.adapters.bank.schema.detect import DISPLAY_DELIMITER, DISPLAY_ENCODING
from finrecon.agent.providers.base import ProviderConfigurationError
from finrecon.agent.providers.config import build_chain
from finrecon.ledger.bank_mappings import BankMappingError, BankMappingStore

from .schemas import (
    BankMappingDetailResponse,
    BankMappingListResponse,
    BankMappingProposalResponse,
    BankMappingSaveResponse,
    MappingProposalView,
    MappingSampleView,
    MappingValidationView,
    SavedMappingView,
)

DEFAULT_CURRENCY = "INR"


def _error(code: str, message: str, status_code: int) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# --- request contracts ----------------------------------------------------


class MoneyColumnsRequest(BaseModel):
    """The money model as the confirmation request states it."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["debit_credit", "amount_direction"]
    debit_column: str | None = None
    credit_column: str | None = None
    inactive_side_marker: Literal["empty_only", "empty_or_zero"] | None = None
    amount_column: str | None = None
    direction_column: str | None = None
    credit_values: list[str] | None = None
    debit_values: list[str] | None = None

    def payload(self) -> dict[str, Any]:
        if self.kind == "debit_credit":
            return {
                "kind": "debit_credit",
                "debit_column": self.debit_column,
                "credit_column": self.credit_column,
                "inactive_side_marker": self.inactive_side_marker or "empty_only",
            }
        return {
            "kind": "amount_direction",
            "amount_column": self.amount_column,
            "direction_column": self.direction_column,
            "credit_values": list(self.credit_values or ()),
            "debit_values": list(self.debit_values or ()),
        }


class BankMappingSaveRequest(BaseModel):
    """A human-confirmed mapping, submitted for persistence.

    Note the absence of ``profile_id``: the identifier that namespaces every
    ``bank_record_id`` a mapping produces is assigned by the store from
    ``(mapping_id, version)`` and is never accepted from a client, so a
    caller cannot make one mapping's records indistinguishable from
    another's.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    """Required when creating a mapping; optional when adding a version,
    where omitting it keeps the existing name."""
    value_date_column: str
    value_date_format: str
    narration_column: str
    reference_id_column: str | None = None
    money_columns: MoneyColumnsRequest
    currency: str = DEFAULT_CURRENCY
    currency_column: str | None = None
    thousands_separator: str | None = None
    delimiter: str = ","
    encoding: str = "utf-8"
    confirmed_fields: list[str] = Field(default_factory=list)
    """Fields the operator explicitly settled. Must cover every field
    deterministic validation reports as unsettleable from the sample."""
    llm_proposal: dict[str, Any] | None = None
    """Optional provenance: the proposal metadata this mapping was reviewed
    from. Recorded beside the mapping, never as its authority."""
    expected_signature: str | None = None
    """Optional client assertion about which file this mapping is for. When
    present it must equal the signature the server reads, so a mapping
    reviewed against one upload cannot be saved against a different one."""

    def profile_payload(self) -> dict[str, Any]:
        return {
            "profile_id": "pending",  # replaced by the store; see its docstring
            "currency": self.currency,
            "value_date_column": self.value_date_column,
            "value_date_format": self.value_date_format,
            "narration_column": self.narration_column,
            "reference_id_column": self.reference_id_column,
            "currency_column": self.currency_column,
            "thousands_separator": self.thousands_separator,
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "money_columns": self.money_columns.payload(),
        }


def parse_save_request(raw: str) -> BankMappingSaveRequest:
    """Decode the multipart ``mapping`` field into the request contract."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _error(
            "invalid_bank_mapping", f"The mapping field must be JSON: {exc}", 422
        ) from exc
    try:
        return BankMappingSaveRequest.model_validate(payload)
    except ValidationError as exc:
        raise _error(
            "invalid_bank_mapping",
            f"The mapping is missing or malformed: {exc.error_count()} field error(s).",
            422,
        ) from exc


# --- views ----------------------------------------------------------------


def saved_mapping_view(entry: SavedMappingEntry) -> SavedMappingView:
    return SavedMappingView(
        mapping_id=entry.mapping_id,
        name=entry.name,
        version=entry.mapping_version,
        profile_id=entry.profile_id,
        status=entry.status,  # type: ignore[arg-type]
        provenance=entry.provenance,  # type: ignore[arg-type]
        source=entry.source,  # type: ignore[arg-type]
        schema_signature=entry.schema_signature,
        expected_headers=list(entry.expected_headers),
        profile=entry.profile_payload,
        created_at=entry.created_at,
        llm_proposal=entry.llm_proposal,
    )


def validation_view(validation: MappingValidation) -> MappingValidationView:
    return MappingValidationView.model_validate(validation.payload())


def _proposal_view(outcome: ProposalOutcome) -> MappingProposalView | None:
    if outcome.proposal is None:
        return None
    mapping = outcome.proposal.mapping
    return MappingProposalView(
        mapping={
            "value_date_column": mapping.value_date_column,
            "value_date_format": mapping.value_date_format,
            "value_date_format_certain": mapping.value_date_format_certain,
            "narration_column": mapping.narration_column,
            "reference_id_column": mapping.reference_id_column,
            "money": mapping.money.model_dump(),
        },
        reasoning_summary=outcome.proposal.reasoning_summary.model_dump(),
        uncertainties=list(outcome.proposal.uncertainties),
        provider=outcome.provider,
        model=outcome.model,
        reported_model=outcome.reported_model,
        proposed_at=outcome.proposed_at,
    )


# --- endpoint bodies ------------------------------------------------------


def combined_registry(
    built_ins, mapping_store: BankMappingStore
) -> CombinedMappingRegistry:
    """The detection corpus for one request: built-ins plus active mappings.

    Built fresh per request rather than cached, because saved mappings change
    while the server runs. Built-ins remain the process-lifetime immutable
    registry they always were.
    """
    return CombinedMappingRegistry(built_ins, mapping_store.active_entries())


def propose_response(
    *, raw_bytes: bytes, registry: CombinedMappingRegistry
) -> BankMappingProposalResponse:
    """Inspect, and propose a mapping only if the schema is genuinely unknown.

    The inspection is run here, server-side, rather than trusting a client's
    claim that the file is unknown. That is what keeps the promise that a
    recognised statement costs no model call: a browser cannot provoke one by
    asking for a proposal on a file FinRecon already knows how to read.
    """
    try:
        sample = read_sample(raw_bytes)
    except BankCsvSampleError as exc:
        raise _error("unreadable_bank_statement", str(exc), 422) from exc

    inspection = inspect_bank_csv(raw_bytes, registry)
    base = {
        "schema_status": inspection.status.value,
        "sample": MappingSampleView(
            headers=list(sample.raw_headers),
            rows=[list(row) for row in sample.rows],
            bounds=sample.bounds_payload(),
        ),
        "supported_date_formats": [dict(option) for option in format_options()],
        "raw_headers": list(inspection.observed.raw_headers or sample.raw_headers),
        "normalized_headers": list(
            inspection.observed.normalized_headers or sample.normalized_headers
        ),
        "signature": inspection.observed.digest,
        "delimiter": sample.delimiter,
        "encoding": sample.encoding,
    }

    if inspection.status is not MatchStatus.UNKNOWN:
        # Recognised or ambiguous: no proposal, and pointedly no provider
        # call. A mapping already exists for this schema (or two do), and
        # asking a model to invent a third is both wasteful and a way to
        # end up disagreeing with a mapping a human already confirmed.
        return BankMappingProposalResponse(**base)

    try:
        chain = build_chain()
    except ProviderConfigurationError:
        return BankMappingProposalResponse(
            **base,
            failure_code="provider_not_configured",
            failure_message=(
                "No model provider is configured on this server, so a mapping "
                "could not be proposed automatically. Map the columns below."
            ),
        )

    outcome = propose_mapping(sample, chain=chain)
    return BankMappingProposalResponse(
        **base,
        proposal=_proposal_view(outcome),
        validation=(
            validation_view(outcome.validation) if outcome.validation else None
        ),
        failure_code=outcome.failure_code,
        failure_message=outcome.failure_message,
        provider_calls_made=bool(outcome.attempts),
        model_calls=len(outcome.attempts),
    )


def _read_headers(raw_bytes: bytes, *, delimiter: str, encoding: str):
    """Read the header row under the mapping's own declared read.

    Under the mapping's declared delimiter and encoding, not a display
    default, because these are the exact headers detection will later compare
    -- it reads each candidate under that candidate's own declared read. A
    mapping stored against a differently-decoded header row would fail to
    recognise the very file it was confirmed on.
    """
    try:
        return read_signature(raw_bytes, delimiter=delimiter, encoding=encoding)
    except BankSchemaReadError as exc:
        raise _error("unreadable_bank_statement", str(exc), 422) from exc


def _validate_for_save(
    request: BankMappingSaveRequest, raw_bytes: bytes
) -> tuple[MappingValidation, tuple[str, ...], str]:
    """Server-side validation of a confirmed mapping against the actual file.

    Runs the same deterministic validator the proposal path runs, on a
    sample the server read itself, and then adds the one check that only
    makes sense at confirmation time: every field the sample cannot settle
    must have been explicitly settled by the operator.
    """
    signature = _read_headers(
        raw_bytes, delimiter=request.delimiter, encoding=request.encoding
    )
    if request.expected_signature:
        # Compared against the *display* read, because that is the signature
        # `/inspect` and `/propose` handed the client. Comparing against the
        # mapping's own declared read instead would reject a BOM-prefixed
        # export out of hand: the display read is BOM-tolerant, a mapping
        # declaring plain `utf-8` is not, and the two therefore see different
        # first headers for a file that is perfectly mappable. The stored
        # headers still come from the declared read below, which is what
        # detection will compare later.
        display = _read_headers(
            raw_bytes, delimiter=DISPLAY_DELIMITER, encoding=DISPLAY_ENCODING
        )
        if request.expected_signature not in {signature.digest, display.digest}:
            raise _error(
                "bank_mapping_schema_mismatch",
                "This mapping was reviewed against a different statement schema "
                "than the file supplied with it. Re-inspect the file and confirm "
                "again.",
                409,
            )
    try:
        sample = read_sample(
            raw_bytes, delimiter=request.delimiter, encoding=request.encoding
        )
    except BankCsvSampleError as exc:
        raise _error("unreadable_bank_statement", str(exc), 422) from exc

    validation = validate_mapping_payload(request.profile_payload(), sample)
    if not validation.ok:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_bank_mapping",
                "message": "This mapping does not fit the uploaded statement.",
                "validation": validation.payload(),
            },
        )
    unanswered = [
        field
        for field in validation.fields_requiring_human_choice
        if field not in set(request.confirmed_fields)
    ]
    if unanswered:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "human_confirmation_required",
                "message": (
                    "This statement's sample rows cannot settle "
                    f"{unanswered}. Choose the value explicitly before saving; "
                    "FinRecon will not accept a default for it."
                ),
                "validation": validation.payload(),
            },
        )
    return validation, signature.raw_headers, signature.digest


def create_mapping(
    *,
    store: BankMappingStore,
    request: BankMappingSaveRequest,
    raw_bytes: bytes,
) -> BankMappingSaveResponse:
    if not (request.name or "").strip():
        raise _error(
            "mapping_name_required",
            "Give this mapping a name so it can be recognised next time.",
            422,
        )
    validation, raw_headers, _ = _validate_for_save(request, raw_bytes)
    try:
        entry = store.create_mapping(
            name=str(request.name),
            profile_payload=request.profile_payload(),
            raw_headers=raw_headers,
            delimiter=request.delimiter,
            encoding=request.encoding,
            llm_proposal=request.llm_proposal,
        )
    except BankMappingError as exc:
        raise _error(exc.code, str(exc), 409 if exc.code == "mapping_name_taken" else 422) from exc
    return BankMappingSaveResponse(
        saved=saved_mapping_view(entry),
        validation=validation_view(validation),
        created_version=entry.mapping_version,
    )


def add_mapping_version(
    *,
    store: BankMappingStore,
    mapping_id: str,
    request: BankMappingSaveRequest,
    raw_bytes: bytes,
) -> BankMappingSaveResponse:
    """Confirm an edit as the next version. The old version is retired, not lost."""
    validation, raw_headers, _ = _validate_for_save(request, raw_bytes)
    try:
        entry = store.add_version(
            mapping_id=mapping_id,
            profile_payload=request.profile_payload(),
            raw_headers=raw_headers,
            delimiter=request.delimiter,
            encoding=request.encoding,
            llm_proposal=request.llm_proposal,
            name=request.name,
        )
    except BankMappingError as exc:
        status = {
            "unknown_bank_mapping": 404,
            "mapping_name_taken": 409,
        }.get(exc.code, 422)
        raise _error(exc.code, str(exc), status) from exc
    return BankMappingSaveResponse(
        saved=saved_mapping_view(entry),
        validation=validation_view(validation),
        created_version=entry.mapping_version,
    )


def list_mappings(store: BankMappingStore) -> BankMappingListResponse:
    return BankMappingListResponse(
        mappings=[saved_mapping_view(entry) for entry in store.active_entries()]
    )


def mapping_detail(
    store: BankMappingStore, mapping_id: str
) -> BankMappingDetailResponse:
    versions = store.versions_of(mapping_id)
    if not versions:
        raise _error(
            "unknown_bank_mapping", f"No saved mapping {mapping_id!r} exists.", 404
        )
    active = next((entry for entry in versions if entry.active), None)
    return BankMappingDetailResponse(
        mapping_id=mapping_id,
        name=versions[-1].name,
        active=saved_mapping_view(active) if active else None,
        versions=[saved_mapping_view(entry) for entry in versions],
    )


__all__ = [
    "BankMappingSaveRequest",
    "MoneyColumnsRequest",
    "add_mapping_version",
    "combined_registry",
    "create_mapping",
    "list_mappings",
    "mapping_detail",
    "parse_save_request",
    "propose_response",
    "saved_mapping_view",
    "validation_view",
]
