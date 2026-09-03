export type ResolutionSource = "deterministic" | "ai_assisted" | "human" | "escalated";

export interface OutcomeMetrics {
  total_cases: number;
  deterministic_resolved: number;
  ai_assisted_resolved: number;
  human_resolved: number;
  needs_review: number;
  ingestion_issues: number;
  total_amount_paise: number;
  provider_calls: number;
  model_tokens: number | null;
  model_cost: null;
}

export interface RunSummary {
  batch_id: string;
  split: string;
  content_fingerprint: string;
  record_count: number;
  metrics: OutcomeMetrics;
}

export interface OverviewResponse {
  selected_batch_id: string | null;
  metrics: OutcomeMetrics;
  recent_runs: RunSummary[];
}

export interface CaseSummary {
  batch_id: string;
  case_id: string;
  bank_record_id: string;
  narration: string | null;
  amount_paise: number;
  status: "resolved" | "needs_review";
  resolution_source: ResolutionSource;
  candidate_count: number;
  evidence_state: string;
  last_updated: string | null;
}

export interface CaseListResponse { batch_id: string | null; total: number; cases: CaseSummary[] }

export interface CandidateView {
  candidate_id: string;
  settlement_ids: string[];
  total_paise: number;
  unexplained_delta_paise: number;
  blocking_rule: string;
  settlement_dates: string[];
  state: "accepted" | "rejected" | "available";
  settlements: Record<string, unknown>[];
}

export interface TimelineEvent { sequence: number; kind: string; title: string; detail: string; recorded_at: string | null }
export interface HumanResolutionView { resolution_id: string; revision: number; resolution_type: string; selected_candidate_id: string | null; reason: string; actor: string | null; recorded_at: string; active: boolean }
export interface AgentStep { step_index: number; tool_name: string; status: string; arguments: Record<string, unknown> | null; validation_error: string | null; output: Record<string, unknown> | null }

export interface CaseDetailResponse {
  summary: CaseSummary;
  snapshot_hash: string | null;
  bank_transaction: Record<string, unknown>;
  candidates: CandidateView[];
  evidence: { deterministic: Record<string, unknown>; ai_found: Record<string, unknown>[]; structured_bank_facts: Record<string, unknown>; raw_narration: string | null };
  validation: { validator_version: string | null; policy_version: string | null; outcome: string; rule_id: string; passed: string[]; failed: string[]; blockers: string[]; resolved_candidate_id: string | null; raw_validator: Record<string, unknown> | null; policy_declaration: Record<string, unknown> | null };
  trajectory: { available: boolean; replayed: boolean | null; provider: string[]; models: string[]; termination_reason: string | null; step_count: number; total_tokens: number | null; assistant_notes: string[]; tools: AgentStep[] };
  audit_timeline: TimelineEvent[];
  human_resolutions: HumanResolutionView[];
  can_resolve: boolean;
}

export interface IngestionIssue { event_id: string; batch_id: string; source_kind: "razorpay" | "bank"; source_id: string; event_type: string; subject_id: string | null; fingerprint: string; problem: string; detail: string | null; payload: Record<string, unknown> }
export interface IngestionIssuesResponse { batch_id: string | null; total: number; issues: IngestionIssue[] }
export interface RunResponse { batch_id: string; mode: "replay" | "live"; provider_calls_made: boolean; result: RunSummary; bank_profile_selection?: BankProfileSelectionView | null }

export interface BenchmarkSummary { benchmark_id: string; title: string; status: "FROZEN" | "PILOT"; case_count: number; description: string; replay_available: boolean; report_available: boolean; investigators: string[] }
export interface BenchmarkDetail extends BenchmarkSummary { integrity: Record<string, unknown>; constraints: Record<string, unknown>; notices: string[] }
export interface BenchmarkListResponse { benchmarks: BenchmarkSummary[]; evolution: { version: string; status: string; summary: string }[] }
export interface BenchmarkReport { report_id: string; label: string; metrics: Record<string, number | string | null> | null; telemetry: Record<string, unknown>; cohort: Record<string, unknown>; recorded_versions: Record<string, unknown> }
export interface BenchmarkReportsResponse { benchmark_id: string; reports: BenchmarkReport[] }
export interface BenchmarkCaseEvaluation { tier: "T0" | "T1" | "T2" | "T3"; final_disposition: "RESOLVED" | "ESCALATED" | "UNKNOWN"; resolution_stage: "STAGE_2" | "STAGE_3" | "UNKNOWN"; resolution_method: string | null; blockers: string[]; replay_available: boolean; replay_note: string; termination_reason?: string | null; tool_call_count?: number; evidence_relations?: string[]; frozen_trajectory?: boolean }
export interface BenchmarkCaseSummary { case_id: string; bank_record_id: string; narration: string; amount_paise: number; candidate_count: number | null; recorded_outcomes: Record<string, string>; replay_investigators: string[]; controller_rejection_demo: boolean; evaluation: BenchmarkCaseEvaluation | null }
export interface BenchmarkCasesResponse { benchmark_id: string; total: number; offset: number; limit: number; counts: Record<string, number>; cases: BenchmarkCaseSummary[] }
export interface BenchmarkCaseDetail extends BenchmarkCaseSummary { candidate_snapshot: Record<string, unknown> | null; visible_records: Record<string, unknown>; trajectory_metadata?: Record<string, unknown> | null; evaluation_metadata_notice: string }
export interface BenchmarkReplaySummary { investigator: string; label: string; scored_cohort_cases: number; persisted_trajectory_cases: number; requested_model: string | null; reported_models: string[]; provider: string | null; notes: string[] }
export interface BenchmarkReplaysResponse { benchmark_id: string; replays: BenchmarkReplaySummary[] }
export interface BenchmarkReplayDetail { benchmark_id: string; investigator: string; replayed: true; provider_calls_made: false; trajectory: Record<string, unknown>; deterministic_validation: Record<string, unknown>; policy_result: Record<string, unknown>; provenance?: Record<string, unknown> | null }
export interface BenchmarkFullReplay {
  benchmark_id: "frozen-eval-v3";
  mode: "offline_replay";
  provider_calls: 0;
  provider_calls_made: false;
  total_cases: number;
  total_correct_auto_resolutions: number;
  stage2: { cases: number; resolved: number };
  stage3: { residual_cases: number; trajectory_cache_hits: number; t2: { cases: number; correctly_resolved: number }; t3: { cases: number; safely_escalated: number; termination_reasons: Record<string, number> } };
  evaluation: { resolvable_cases: number; correct_resolutions: number; ambiguous_cases: number; safely_escalated: number; wrong_auto_resolutions: number; resolvable_match_rate: number; auto_resolution_precision: number; unsafe_auto_match_rate: number; value_at_risk_paise: number; soundness_violations: number; tool_validation_failures: number; validation_rejections: number; budget_exhausted: number };
  phases: { id: string; label: string; count?: number; total?: number; unit?: string; status?: string }[];
  provenance: { provider_recovered: Record<string, unknown>; operational: Record<string, unknown>; retry_contract: string[]; original_failed_trajectories_preserved: number };
  integrity: Record<string, string | boolean>;
}

// Bank-schema recognition. Detection identifies an already-reviewed
// profile; it never proposes a column mapping for an unknown schema.
export interface BuiltInProfileView {
  profile_id: string;
  label: string;
  version: string;
  verification: "vendor_verified" | "partially_verified" | "demo_fixture";
  description: string;
  evidence: string;
}

// A mapping this deployment's operator confirmed and saved. Authoritative
// for the same reason a built-in is -- a person reviewed the column mapping
// and it is versioned -- and carries `provenance` rather than a
// `verification` level, because FinRecon has no evidence to grade about a
// mapping it did not ship.
export interface SavedMappingView {
  mapping_id: string;
  name: string;
  version: number;
  profile_id: string;
  status: "active" | "superseded" | "disabled";
  provenance: "human_confirmed";
  source: "user_saved";
  schema_signature: string;
  expected_headers: string[];
  // The mapping itself, as confirmed. The edit flow prefills from this so
  // "Change" starts from what the mapping says, not from a fresh guess.
  profile: Record<string, unknown>;
  created_at: string | null;
  llm_proposal: Record<string, unknown> | null;
}

export interface MappingMatchView {
  kind: "built_in" | "user_saved";
  profile_id: string;
  label: string;
  version: string;
  verification: string | null;
  description: string;
  evidence: string;
  saved_mapping: SavedMappingView | null;
}

export interface BankStatementInspection {
  status: "matched" | "ambiguous" | "unknown";
  raw_headers: string[];
  normalized_headers: string[];
  signature: string;
  field_count: number;
  match_tier: "exact" | "safe_normalized" | null;
  // Built-ins only, for backwards compatibility; `match`/`matches` see both
  // kinds and are what this UI reads.
  profile: BuiltInProfileView | null;
  candidates: BuiltInProfileView[];
  match?: MappingMatchView | null;
  matches?: MappingMatchView[];
}

export interface BankProfileSelectionView {
  profile_id: string;
  selection_mode: "built_in" | "manual_upload" | "user_saved";
  match_tier: "exact" | "safe_normalized" | null;
  version: string | null;
  label: string | null;
  verification: string | null;
  schema_signature: string | null;
  mapping_id?: string | null;
  mapping_version?: number | null;
  provenance?: string | null;
  source?: string | null;
}

// --- Unknown-schema mapping proposal and confirmation --------------------

export type MoneyKind = "debit_credit" | "amount_direction";
export type InactiveSideMarker = "empty_only" | "empty_or_zero";

export interface MappingIssueView { field: string; code: string; message: string }

export interface MappingDateFormatView {
  proposed: string;
  plausible: string[];
  contradicted: boolean;
  ambiguous_with: string[];
  evidence_rows: number;
  requires_human_choice: boolean;
}

export interface MappingValidationView {
  ok: boolean;
  errors: MappingIssueView[];
  warnings: MappingIssueView[];
  fields_requiring_human_choice: string[];
  date_format: MappingDateFormatView | null;
}

export interface ProposedMoneyView {
  kind: MoneyKind;
  debit_column: string | null;
  credit_column: string | null;
  inactive_side_marker: InactiveSideMarker | null;
  amount_column: string | null;
  direction_column: string | null;
  credit_values: string[] | null;
  debit_values: string[] | null;
}

export interface ProposedMappingView {
  value_date_column: string;
  value_date_format: string;
  value_date_format_certain: boolean;
  narration_column: string;
  reference_id_column: string | null;
  money: ProposedMoneyView;
}

export interface MappingProposalView {
  mapping: ProposedMappingView;
  // Display text only. It explains why a column was suggested so a reviewer
  // can judge the suggestion; nothing downstream reads it.
  reasoning_summary: Record<string, string>;
  uncertainties: string[];
  provider: string | null;
  model: string | null;
  reported_model: string | null;
  proposed_at: string | null;
}

export interface BankMappingProposalResponse {
  schema_status: "matched" | "ambiguous" | "unknown";
  sample: { headers: string[]; rows: string[][]; bounds: Record<string, unknown> };
  supported_date_formats: { value: string; label: string }[];
  raw_headers: string[];
  normalized_headers: string[];
  signature: string;
  delimiter: string;
  encoding: string;
  proposal: MappingProposalView | null;
  validation: MappingValidationView | null;
  failure_code: string | null;
  failure_message: string | null;
  provider_calls_made: boolean;
  model_calls: number;
}

export interface BankMappingSaveResponse {
  saved: SavedMappingView;
  validation: MappingValidationView;
  created_version: number;
}

export interface BankMappingListResponse { mappings: SavedMappingView[] }

export interface BankMappingDetailResponse {
  mapping_id: string;
  name: string;
  active: SavedMappingView | null;
  versions: SavedMappingView[];
}

// The editor's own working state. Deliberately a separate shape from
// `ProposedMappingView`: a proposal is what a model said, this is what the
// operator currently has on screen, and conflating them would make it easy
// to submit the former believing it was the latter.
export interface MappingDraft {
  name: string;
  value_date_column: string;
  value_date_format: string;
  narration_column: string;
  reference_id_column: string;
  money_kind: MoneyKind;
  debit_column: string;
  credit_column: string;
  inactive_side_marker: InactiveSideMarker;
  amount_column: string;
  direction_column: string;
  credit_values: string;
  debit_values: string;
}
