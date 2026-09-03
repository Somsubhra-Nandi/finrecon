"""Purpose-built API contracts; SQLite rows never cross the HTTP boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class SavedMappingView(ApiModel):
    """One user-confirmed saved mapping version, as disclosed to the operator.

    ``verification`` has no counterpart here on purpose. That field states
    how well a *shipped* profile's schema is evidenced by documentation this
    build can point at; FinRecon has no such evidence about a mapping the
    operator wrote, and inventing a level for it would be a claim it cannot
    support. ``provenance`` states the honest thing instead: a person here
    confirmed this mapping.
    """

    mapping_id: str
    name: str
    version: int
    profile_id: str
    status: Literal["active", "superseded", "disabled"]
    provenance: Literal["human_confirmed"]
    source: Literal["user_saved"]
    schema_signature: str
    expected_headers: list[str]
    profile: dict[str, Any] = Field(default_factory=dict)
    """The mapping itself, as the operator confirmed it.

    Disclosed because it is theirs: the edit flow prefills from it, and a
    reviewer asking "what does this mapping actually say" should not have to
    read a database. It is the same wire shape a manual profile upload
    carries, minus nothing."""
    created_at: str | None = None
    llm_proposal: dict[str, Any] | None = None
    """Metadata about a model proposal a human then confirmed or corrected.
    Context for review; never authority. Absent when no model was consulted."""


class MappingMatchView(ApiModel):
    """One entry that matched an uploaded statement, of either kind.

    A single shape for built-ins and saved mappings so the Run page renders
    a tie between one of each without a special case -- and so the ambiguity
    path cannot accidentally acquire a preference for one kind.
    """

    kind: Literal["built_in", "user_saved"]
    profile_id: str
    label: str
    version: str
    verification: str | None = None
    """Set for built-ins only. See :class:`SavedMappingView`."""
    description: str = ""
    evidence: str = ""
    saved_mapping: SavedMappingView | None = None
    """Set for a saved mapping only, carrying its id and version."""


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
    """The matched entry when it is a shipped built-in. Kept exactly as it
    was, so a client written before saved mappings existed still reads the
    built-in path correctly and simply never sees a saved match."""
    candidates: list[BuiltInProfileView]
    """The tied *built-in* entries for an ambiguous statement; empty
    otherwise. Never a nearest-match suggestion."""
    match: MappingMatchView | None = None
    """The matched entry of either kind. The field newer clients read."""
    matches: list[MappingMatchView] = Field(default_factory=list)
    """Every tied entry of either kind when ``status`` is ambiguous."""


class BankProfileSelectionView(ApiModel):
    """Which profile a run actually used, and how it was chosen."""

    profile_id: str
    selection_mode: Literal["built_in", "manual_upload", "user_saved"]
    match_tier: Literal["exact", "safe_normalized"] | None
    version: str | None
    label: str | None
    verification: str | None
    schema_signature: str | None
    mapping_id: str | None = None
    mapping_version: int | None = None
    provenance: str | None = None
    source: str | None = None


class MappingIssueView(ApiModel):
    """One problem with a candidate mapping, addressed to one editor field."""

    field: str
    code: str
    message: str


class MappingDateFormatView(ApiModel):
    """What the sampled rows can and cannot settle about a date format."""

    proposed: str
    plausible: list[str]
    contradicted: bool
    ambiguous_with: list[str]
    evidence_rows: int
    requires_human_choice: bool


class MappingValidationView(ApiModel):
    """The deterministic verdict on a candidate mapping.

    ``warnings`` never block confirmation -- a five-row excerpt is not proof
    about a whole statement -- but they are shown, because the operator is
    the one entitled to decide whether an observation matters.
    """

    ok: bool
    errors: list[MappingIssueView] = Field(default_factory=list)
    warnings: list[MappingIssueView] = Field(default_factory=list)
    fields_requiring_human_choice: list[str] = Field(default_factory=list)
    date_format: MappingDateFormatView | None = None


class ProposedMoneyView(ApiModel):
    kind: Literal["debit_credit", "amount_direction"]
    debit_column: str | None = None
    credit_column: str | None = None
    inactive_side_marker: Literal["empty_only", "empty_or_zero"] | None = None
    amount_column: str | None = None
    direction_column: str | None = None
    credit_values: list[str] | None = None
    debit_values: list[str] | None = None


class ProposedMappingView(ApiModel):
    """The mapping a model suggested. Every field is editable by the operator."""

    value_date_column: str
    value_date_format: str
    value_date_format_certain: bool
    narration_column: str
    reference_id_column: str | None
    money: ProposedMoneyView


class MappingProposalView(ApiModel):
    """A suggestion plus its rationale. Never a mapping FinRecon will use.

    Note what this response does *not* contain: any identifier a later
    request could submit in place of a mapping. There is no proposal id,
    because a proposal has no server-side existence to refer to -- the only
    way forward is to post a complete confirmed mapping, which is then
    validated against the file from scratch.
    """

    mapping: ProposedMappingView
    reasoning_summary: dict[str, str]
    """Short per-field rationale for display. Explanatory only; it is not
    evidence and nothing downstream reads it."""
    uncertainties: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    reported_model: str | None = None
    proposed_at: str | None = None


class MappingSampleView(ApiModel):
    """The bounded excerpt shown as a preview, and the bounds it obeyed."""

    headers: list[str]
    rows: list[list[str]]
    bounds: dict[str, Any]


class BankMappingProposalResponse(ApiModel):
    """The unknown-schema proposal response. Authorizes nothing.

    At most one of ``proposal`` / ``failure_code`` is set. A failure is not
    an HTTP error: the mapping editor stays fully usable with no proposal in
    it, which is the whole point of not blocking the product on model
    availability.
    """

    schema_status: Literal["matched", "ambiguous", "unknown"]
    """The inspection result. A proposal is only ever generated for
    ``unknown``; the other two are returned so the client can show why no
    proposal was requested."""
    sample: MappingSampleView
    supported_date_formats: list[dict[str, str]]
    raw_headers: list[str]
    normalized_headers: list[str]
    signature: str
    delimiter: str
    encoding: str
    proposal: MappingProposalView | None = None
    validation: MappingValidationView | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    provider_calls_made: bool = False
    model_calls: int = 0


class BankMappingListResponse(ApiModel):
    mappings: list[SavedMappingView]


class BankMappingDetailResponse(ApiModel):
    """One logical mapping: its active version, and its whole history.

    ``versions`` includes superseded entries and is ordered oldest first,
    because "what did this mapping used to say" is the question an audit
    reader arrives with.
    """

    mapping_id: str
    name: str
    active: SavedMappingView | None
    versions: list[SavedMappingView]


class BankMappingSaveResponse(ApiModel):
    """A mapping that is now authoritative, because a person confirmed it."""

    saved: SavedMappingView
    validation: MappingValidationView
    created_version: int


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
    termination_reason: str | None = None
    tool_call_count: int = 0
    evidence_relations: list[str] = Field(default_factory=list)
    frozen_trajectory: bool = False


class BenchmarkCasesResponse(ApiModel):
    benchmark_id: str
    total: int
    offset: int = 0
    limit: int = 50
    counts: dict[str, int] = Field(default_factory=dict)
    cases: list[BenchmarkCaseSummary]


class BenchmarkCaseDetailResponse(BenchmarkCaseSummary):
    candidate_snapshot: dict[str, Any] | None
    visible_records: dict[str, Any]
    trajectory_metadata: dict[str, Any] | None = None
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
    provenance: dict[str, Any] | None = None


class BenchmarkReplayStage2(ApiModel):
    cases: int
    resolved: int


class BenchmarkReplayT2(ApiModel):
    cases: int
    correctly_resolved: int


class BenchmarkReplayT3(ApiModel):
    cases: int
    safely_escalated: int
    termination_reasons: dict[str, int]


class BenchmarkReplayStage3(ApiModel):
    residual_cases: int
    trajectory_cache_hits: int
    t2: BenchmarkReplayT2
    t3: BenchmarkReplayT3


class BenchmarkReplayEvaluation(ApiModel):
    resolvable_cases: int
    correct_resolutions: int
    ambiguous_cases: int
    safely_escalated: int
    wrong_auto_resolutions: int
    resolvable_match_rate: float
    auto_resolution_precision: float
    unsafe_auto_match_rate: float
    value_at_risk_paise: int
    soundness_violations: int
    tool_validation_failures: int
    validation_rejections: int
    budget_exhausted: int


class BenchmarkFullReplayResponse(ApiModel):
    benchmark_id: Literal["frozen-eval-v3"]
    mode: Literal["offline_replay"]
    provider_calls: Literal[0]
    provider_calls_made: Literal[False]
    total_cases: int
    total_correct_auto_resolutions: int
    stage2: BenchmarkReplayStage2
    stage3: BenchmarkReplayStage3
    evaluation: BenchmarkReplayEvaluation
    phases: list[dict[str, Any]]
    provenance: dict[str, Any]
    integrity: dict[str, Any]

    @model_validator(mode="after")
    def validate_full_suite_cohort(self) -> "BenchmarkFullReplayResponse":
        if self.stage2.cases != self.stage2.resolved:
            raise ValueError("Frozen Eval v3 Stage 2 must resolve its complete cohort")
        if self.stage3.residual_cases != self.stage3.t2.cases + self.stage3.t3.cases:
            raise ValueError("Stage-3 residual cohort is incompatible with its T2/T3 cohorts")
        if self.total_cases != (
            self.stage2.resolved
            + self.stage3.t2.correctly_resolved
            + self.stage3.t3.safely_escalated
        ):
            raise ValueError("full-suite case counts do not reconcile")
        if self.total_correct_auto_resolutions != (
            self.stage2.resolved + self.stage3.t2.correctly_resolved
        ):
            raise ValueError("full-suite automatic-resolution counts do not reconcile")
        if self.evaluation.correct_resolutions != self.total_correct_auto_resolutions:
            raise ValueError("Stage-4 result is incompatible with full-suite resolution counts")
        if self.evaluation.safely_escalated != self.stage3.t3.safely_escalated:
            raise ValueError("Stage-4 result is incompatible with the T3 escalation cohort")
        if self.evaluation.resolvable_cases != (
            self.evaluation.correct_resolutions + self.evaluation.wrong_auto_resolutions
        ):
            raise ValueError("Stage-4 resolvable cohort does not reconcile")
        if self.evaluation.ambiguous_cases != self.stage3.t3.cases:
            raise ValueError("Stage-4 ambiguity cohort is incompatible with T3")
        if self.total_cases != self.evaluation.resolvable_cases + self.evaluation.ambiguous_cases:
            raise ValueError("Stage-4 evaluation is incompatible with the full suite")
        return self
