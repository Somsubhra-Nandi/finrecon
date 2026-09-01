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
export interface BenchmarkCaseEvaluation { tier: "T0" | "T1" | "T2" | "T3"; final_disposition: "RESOLVED" | "ESCALATED" | "UNKNOWN"; resolution_stage: "STAGE_2" | "STAGE_3" | "UNKNOWN"; resolution_method: string | null; blockers: string[]; replay_available: boolean; replay_note: string }
export interface BenchmarkCaseSummary { case_id: string; bank_record_id: string; narration: string; amount_paise: number; candidate_count: number | null; recorded_outcomes: Record<string, string>; replay_investigators: string[]; controller_rejection_demo: boolean; evaluation: BenchmarkCaseEvaluation | null }
export interface BenchmarkCasesResponse { benchmark_id: string; total: number; offset: number; limit: number; cases: BenchmarkCaseSummary[] }
export interface BenchmarkCaseDetail extends BenchmarkCaseSummary { candidate_snapshot: Record<string, unknown> | null; visible_records: Record<string, unknown>; evaluation_metadata_notice: string }
export interface BenchmarkReplaySummary { investigator: string; label: string; scored_cohort_cases: number; persisted_trajectory_cases: number; requested_model: string | null; reported_models: string[]; provider: string | null; notes: string[] }
export interface BenchmarkReplaysResponse { benchmark_id: string; replays: BenchmarkReplaySummary[] }
export interface BenchmarkReplayDetail { benchmark_id: string; investigator: string; replayed: true; provider_calls_made: false; trajectory: Record<string, unknown>; deterministic_validation: Record<string, unknown>; policy_result: Record<string, unknown> }

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

export interface BankStatementInspection {
  status: "matched" | "ambiguous" | "unknown";
  raw_headers: string[];
  normalized_headers: string[];
  signature: string;
  field_count: number;
  match_tier: "exact" | "safe_normalized" | null;
  profile: BuiltInProfileView | null;
  candidates: BuiltInProfileView[];
}

export interface BankProfileSelectionView {
  profile_id: string;
  selection_mode: "built_in" | "manual_upload";
  match_tier: "exact" | "safe_normalized" | null;
  version: string | null;
  label: string | null;
  verification: string | null;
  schema_signature: string | null;
}
