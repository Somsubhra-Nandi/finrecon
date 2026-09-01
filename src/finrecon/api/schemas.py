"""Purpose-built API contracts; SQLite rows never cross the HTTP boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ResolutionSource = Literal["deterministic", "ai_assisted", "human", "escalated"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutcomeMetrics(ApiModel):
    total_cases: int
    deterministic_resolved: int
    ai_assisted_resolved: int
    human_resolved: int
    needs_review: int
    ingestion_issues: int
    total_amount_paise: int
    provider_calls: int
    model_tokens: int | None
    model_cost: None = None


class RunSummary(ApiModel):
    batch_id: str
    split: str
    content_fingerprint: str
    record_count: int
    metrics: OutcomeMetrics


class OverviewResponse(ApiModel):
    selected_batch_id: str | None
    metrics: OutcomeMetrics
    recent_runs: list[RunSummary]


class CaseSummary(ApiModel):
    batch_id: str
    case_id: str
    bank_record_id: str
    narration: str | None
    amount_paise: int
    status: Literal["resolved", "needs_review"]
    resolution_source: ResolutionSource
    candidate_count: int
    evidence_state: str
    last_updated: str | None


class CaseListResponse(ApiModel):
    batch_id: str | None
    total: int
    cases: list[CaseSummary]


class CandidateView(ApiModel):
    candidate_id: str
    settlement_ids: list[str]
    total_paise: int
    unexplained_delta_paise: int
    blocking_rule: str
    settlement_dates: list[str]
    state: Literal["accepted", "rejected", "available"]
    settlements: list[dict[str, Any]]


class EvidenceSection(ApiModel):
    deterministic: dict[str, Any]
    ai_found: list[dict[str, Any]]
    structured_bank_facts: dict[str, Any]
    raw_narration: str | None


class ValidationView(ApiModel):
    validator_version: str | None
    policy_version: str | None
    outcome: str
    rule_id: str
    passed: list[str]
    failed: list[str]
    blockers: list[str]
    resolved_candidate_id: str | None
    raw_validator: dict[str, Any] | None
    policy_declaration: dict[str, Any] | None


class AgentStep(ApiModel):
    step_index: int
    tool_name: str
    status: str
    arguments: dict[str, Any] | None
    validation_error: str | None
    output: dict[str, Any] | None


class AgentTrajectoryView(ApiModel):
    available: bool
    replayed: bool | None = None
    provider: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    termination_reason: str | None = None
    step_count: int = 0
    total_tokens: int | None = None
    assistant_notes: list[str] = Field(default_factory=list)
    tools: list[AgentStep] = Field(default_factory=list)


class HumanResolutionView(ApiModel):
    resolution_id: str
    revision: int
    resolution_type: str
    selected_candidate_id: str | None
    reason: str
    actor: str | None
    recorded_at: str
    active: bool


class TimelineEvent(ApiModel):
    sequence: int
    kind: str
    title: str
    detail: str
    recorded_at: str | None = None


class CaseDetailResponse(ApiModel):
    summary: CaseSummary
    snapshot_hash: str | None
    bank_transaction: dict[str, Any]
    candidates: list[CandidateView]
    evidence: EvidenceSection
    validation: ValidationView
    trajectory: AgentTrajectoryView
    audit_timeline: list[TimelineEvent]
    human_resolutions: list[HumanResolutionView]
    can_resolve: bool


class ResolutionRequest(ApiModel):
    batch_id: str
    snapshot_hash: str
    selected_candidate_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)
    actor: str | None = Field(default=None, max_length=120)


class ResolutionResponse(ApiModel):
    resolution: HumanResolutionView
    case: CaseDetailResponse


class IngestionIssue(ApiModel):
    event_id: str
    batch_id: str
    source_kind: Literal["razorpay", "bank"]
    source_id: str
    event_type: str
    subject_id: str | None
    fingerprint: str
    problem: str
    detail: str | None
    payload: dict[str, Any]


class IngestionIssuesResponse(ApiModel):
    batch_id: str | None
    total: int
    issues: list[IngestionIssue]


class AuditEvent(ApiModel):
    channel: Literal["reconciliation", "ingestion", "human"]
    batch_id: str
    case_id: str | None
    event_type: str
    payload: dict[str, Any]


class AuditResponse(ApiModel):
    batch_id: str | None
    events: list[AuditEvent]


# Bank-schema recognition projections. Read-only: nothing below creates a
# batch, a case or a canonical record, and none of it reaches a model.
class BuiltInProfileView(ApiModel):
    """One registry entry, as disclosed to the operator.

    ``verification`` is surfaced verbatim rather than collapsed into a
    "supported" flag: an operator about to reconcile money is entitled to
    know whether a profile is vendor-verified, partially verified, or (as
    everything shipped today is) a synthetic demo schema.
    """

    profile_id: str
    label: str
    version: str
    verification: Literal["vendor_verified", "partially_verified", "demo_fixture"]
    description: str
    evidence: str


class BankStatementInspectionResponse(ApiModel):
    """The outcome of inspecting one uploaded statement's header row.

    ``raw_headers`` is always present, matched or not, so an unrecognised
    file can still be explained to the operator in its own words.
    """

    status: Literal["matched", "ambiguous", "unknown"]
    raw_headers: list[str]
    normalized_headers: list[str]
    signature: str
    field_count: int
    match_tier: Literal["exact", "safe_normalized"] | None
    profile: BuiltInProfileView | None
    candidates: list[BuiltInProfileView]
    """The tied entries for an ambiguous statement; empty otherwise. Never
    a nearest-match suggestion."""


class BankProfileSelectionView(ApiModel):
    """Which profile a run actually used, and how it was chosen."""

    profile_id: str
    selection_mode: Literal["built_in", "manual_upload"]
    match_tier: Literal["exact", "safe_normalized"] | None
    version: str | None
    label: str | None
    verification: str | None
    schema_signature: str | None


class RunResponse(ApiModel):
    batch_id: str
    mode: Literal["replay", "live"]
    provider_calls_made: bool
    result: RunSummary
    bank_profile_selection: BankProfileSelectionView | None = None
    """How the bank profile for this run was chosen. Optional so existing
    clients that ignore it, and existing response snapshots, are unaffected."""


# Benchmark projections are deliberately separate from ledger projections.
# They are read-only views over manifests, reports, visible inputs and persisted
# trajectories; they never load hidden truth or call the agent stack.
class BenchmarkSummary(ApiModel):
    benchmark_id: str
    title: str
    status: Literal["FROZEN", "PILOT"]
    case_count: int
    description: str
    replay_available: bool
    report_available: bool
    investigators: list[str] = Field(default_factory=list)


class BenchmarkListResponse(ApiModel):
    benchmarks: list[BenchmarkSummary]
    evolution: list[dict[str, str]]


class BenchmarkDetailResponse(BenchmarkSummary):
    integrity: dict[str, Any]
    constraints: dict[str, Any]
    notices: list[str] = Field(default_factory=list)


class BenchmarkReportsResponse(ApiModel):
    benchmark_id: str
    reports: list[dict[str, Any]]


class BenchmarkCaseSummary(ApiModel):
    case_id: str
    bank_record_id: str
    narration: str
    amount_paise: int
    candidate_count: int | None
    recorded_outcomes: dict[str, str]
    replay_investigators: list[str]
    controller_rejection_demo: bool = False
    evaluation: "BenchmarkCaseEvaluation | None" = None


class BenchmarkCaseEvaluation(ApiModel):
    """Judge-safe, final evaluation metadata for a single benchmark case."""

    tier: Literal["T0", "T1", "T2", "T3"]
    final_disposition: Literal["RESOLVED", "ESCALATED", "UNKNOWN"]
    resolution_stage: Literal["STAGE_2", "STAGE_3", "UNKNOWN"]
    resolution_method: str | None = None
    blockers: list[str] = Field(default_factory=list)
    replay_available: bool = False
    replay_note: str


class BenchmarkCasesResponse(ApiModel):
    benchmark_id: str
    total: int
    offset: int = 0
    limit: int = 50
    cases: list[BenchmarkCaseSummary]


class BenchmarkCaseDetailResponse(BenchmarkCaseSummary):
    candidate_snapshot: dict[str, Any] | None
    visible_records: dict[str, Any]
    evaluation_metadata_notice: str


class BenchmarkReplaySummary(ApiModel):
    investigator: str
    label: str
    scored_cohort_cases: int
    persisted_trajectory_cases: int
    requested_model: str | None
    reported_models: list[str]
    provider: str | None
    notes: list[str] = Field(default_factory=list)


class BenchmarkReplaysResponse(ApiModel):
    benchmark_id: str
    replays: list[BenchmarkReplaySummary]


class BenchmarkReplayDetailResponse(ApiModel):
    benchmark_id: str
    investigator: str
    replayed: bool
    provider_calls_made: Literal[False] = False
    trajectory: dict[str, Any]
    deterministic_validation: dict[str, Any]
    policy_result: dict[str, Any]
