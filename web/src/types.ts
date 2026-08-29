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
export interface RunResponse { batch_id: string; mode: "replay" | "live"; provider_calls_made: boolean; result: RunSummary }
