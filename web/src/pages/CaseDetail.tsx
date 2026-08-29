import * as Tabs from "@radix-ui/react-tabs";
import { ArrowLeft, Check, ChevronDown, CircleX, Gavel, Landmark, LockKeyhole, UserRound } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, money, query, shortId } from "../api";
import { EvidenceSummary } from "../components/EvidenceSummary";
import { OutcomePath } from "../components/OutcomePath";
import { Button, Card, ErrorState, JsonDetails, LoadingState, PageHeader, StatusBadge } from "../components/ui";
import { candidateLabel, formatCaseDate, friendlyCaseLabel, humanizeMachineText } from "../lib/caseFormatting";
import { useApi } from "../hooks";
import type { CandidateView, CaseDetailResponse, HumanResolutionView, ResolutionSource } from "../types";

const value = (item: unknown) => typeof item === "string" || typeof item === "number" ? String(item) : "—";

function candidateReason(data: CaseDetailResponse, candidate: CandidateView): string {
  if (data.summary.resolution_source === "human") {
    return candidate.state === "accepted" ? "Selected by human authority" : "Not selected in the active human resolution";
  }
  if (candidate.state === "accepted") return "Passed deterministic validation and policy";
  if (candidate.state === "rejected") return "Did not satisfy the complete validated evidence set";
  return "Automation could not distinguish this candidate safely";
}

function stateLabel(state: CandidateView["state"]): string {
  if (state === "accepted") return "Accepted";
  if (state === "rejected") return "Not selected";
  return "Awaiting review";
}

function CandidateCard({ candidate, label, reason, selectable, selected, onSelect }: {
  candidate: CandidateView;
  label: string;
  reason: string;
  selectable: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  const firstSettlement = candidate.settlements[0] ?? {};
  const settlementDate = candidate.settlement_dates[0] || value(firstSettlement.settlement_date_utc);
  const utr = value(firstSettlement.utr);
  return (
    <div className={`candidate-card ${candidate.state} ${selected ? "selected" : ""}`}>
      <button type="button" disabled={!selectable} onClick={onSelect} className="candidate-select-target" aria-pressed={selected}>
        <div className="candidate-head"><div>{candidate.state === "accepted" ? <Check size={16} /> : candidate.state === "rejected" ? <CircleX size={16} /> : <span className="radio-dot" />}<strong>{label}</strong></div><span>{stateLabel(candidate.state)}</span></div>
        <div className="candidate-money"><strong>{money(candidate.total_paise)}</strong><span>Residual {money(candidate.unexplained_delta_paise)}</span></div>
        <dl className="candidate-comparison-facts"><div><dt>Settlement date</dt><dd>{formatCaseDate(settlementDate)}</dd></div><div><dt>Reference / UTR</dt><dd>{utr}</dd></div></dl>
        <p className="candidate-reason">{reason}</p>
      </button>
      <details className="technical-details"><summary>Technical details</summary><dl><div><dt>Candidate ID</dt><dd><code>{candidate.candidate_id}</code></dd></div><div><dt>Settlement ID</dt><dd><code>{candidate.settlement_ids.join(", ")}</code></dd></div><div><dt>Matching rule</dt><dd>{humanizeMachineText(candidate.blocking_rule)}</dd></div></dl></details>
    </div>
  );
}

export function ResolutionSummary({ source, activeResolution, selectedLabel }: { source: ResolutionSource; activeResolution?: HumanResolutionView; selectedLabel?: string }) {
  if (source === "human") {
    return <Card className="review-card resolved-review human-resolved-review"><div className="review-icon"><UserRound size={20} /></div><span className="eyebrow">Human authority</span><h2>Human authority resolved this case</h2><p>The automated pipeline escalated this case. A reviewer supplied the final decision against the immutable snapshot.</p>{activeResolution && <div className="human-resolution-summary"><div><span>Selected</span><strong>{selectedLabel || "No candidate selected"}</strong></div><div><span>Reviewer</span><strong>{activeResolution.actor || "Unattributed"}</strong></div><div><span>Recorded</span><strong>{new Date(activeResolution.recorded_at).toLocaleString("en-IN")}</strong></div><blockquote>{activeResolution.reason}</blockquote></div>}</Card>;
  }
  if (source === "ai_assisted") {
    return <Card className="review-card resolved-review ai-resolved-review"><div className="review-icon"><Check size={20} /></div><span className="eyebrow">Automated resolution</span><h2>AI investigation completed</h2><p>Stage 3 gathered evidence. Deterministic validation and policy authorized the resolution.</p></Card>;
  }
  return <Card className="review-card resolved-review deterministic-review"><div className="review-icon"><Check size={20} /></div><span className="eyebrow">Automated resolution</span><h2>Resolved deterministically</h2><p>Stage 2 resolved this case without AI investigation or human review.</p></Card>;
}

export default function CaseDetail() {
  const { caseId = "" } = useParams();
  const decodedCase = decodeURIComponent(caseId);
  const [params] = useSearchParams();
  const batch = params.get("batch");
  const { data: initial, error, loading, reload } = useApi<CaseDetailResponse>(`/api/cases/${encodeURIComponent(decodedCase)}${query(batch)}`);
  const [updated, setUpdated] = useState<CaseDetailResponse | null>(null);
  const data = updated ?? initial;
  const [selected, setSelected] = useState<string | null>(null);
  const [keepReview, setKeepReview] = useState(false);
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("Demo reviewer");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  if (loading) return <LoadingState label="Loading complete case evidence" />;
  if (error || !data) return <ErrorState error={error ?? new Error("Case not found")} retry={reload} />;

  const bank = data.bank_transaction;
  const bankRecordId = value(bank.bank_record_id);
  const labels = new Map(data.candidates.map((candidate, index) => [candidate.candidate_id, candidateLabel(index)]));
  const activeResolution = data.human_resolutions.find((item) => item.active);
  const save = async () => {
    if (!data.snapshot_hash || (!selected && !keepReview) || !reason.trim()) return;
    setSaving(true); setSaveError(null);
    try {
      const response = await api<{ case: CaseDetailResponse }>(`/api/cases/${encodeURIComponent(decodedCase)}/resolution`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ batch_id: data.summary.batch_id, snapshot_hash: data.snapshot_hash, selected_candidate_id: keepReview ? null : selected, reason: reason.trim(), actor: actor.trim() || null }) });
      setUpdated(response.case); setReason(""); setSelected(null); setKeepReview(false);
    } catch (e) { setSaveError(e instanceof ApiError ? e.message : "The resolution could not be saved."); }
    finally { setSaving(false); }
  };

  return <div className="page detail-page">
    <Link to={`/reconciliation?batch=${encodeURIComponent(data.summary.batch_id)}`} className="back-link"><ArrowLeft size={15} /> Back to queue</Link>
    <PageHeader eyebrow={friendlyCaseLabel(data.summary.bank_record_id, data.summary.case_id)} title="Case investigation" description="The full path from source transaction through financial authority and durable review." action={<StatusBadge source={data.summary.resolution_source} />} />
    <details className="case-technical-id"><summary>View technical case ID</summary><code>{data.summary.case_id}</code></details>
    <OutcomePath source={data.summary.resolution_source} />
    <section className="detail-grid">
      <div className="detail-main">
        <Card><div className="section-heading"><div><span className="eyebrow">A · Source fact</span><h2>Bank transaction</h2></div><Landmark size={18} className="muted" /></div><div className="fact-grid"><div><span>Amount</span><strong>{money(Number(bank.amount_paise ?? data.summary.amount_paise))}</strong></div><div><span>Direction</span><strong className="capitalize">{value(bank.direction)}</strong></div><div><span>Value date</span><strong>{formatCaseDate(value(bank.value_date))}</strong></div><div><span>Bank record ID</span><strong title={bankRecordId}>{shortId(bankRecordId, 34)}</strong></div></div><div className="narration-block"><span>Raw narration</span><code>{value(bank.narration)}</code></div></Card>
        <Card><div className="section-heading"><div><span className="eyebrow">B · Immutable snapshot</span><h2>Candidate snapshot</h2><p>Every plausible candidate remains visible, including candidates the investigation did not use.</p></div><LockKeyhole size={18} className="muted" /></div>{data.candidates.length ? <div className="candidate-list">{data.candidates.map((candidate, index) => <CandidateCard key={candidate.candidate_id} candidate={candidate} label={candidateLabel(index)} reason={candidateReason(data, candidate)} selectable={data.can_resolve} selected={selected === candidate.candidate_id && !keepReview} onSelect={() => { setSelected(candidate.candidate_id); setKeepReview(false); }} />)}</div> : <p className="muted body-copy">This case resolved in Stage 2, before an unresolved candidate snapshot was necessary.</p>}{data.snapshot_hash && <div className="snapshot-hash"><LockKeyhole size={13} /> Snapshot {shortId(data.snapshot_hash, 48)}</div>}</Card>
        <Tabs.Root defaultValue="evidence" className="detail-tabs"><Tabs.List aria-label="Case evidence sections"><Tabs.Trigger value="evidence">Evidence</Tabs.Trigger><Tabs.Trigger value="validation">Validator & policy</Tabs.Trigger><Tabs.Trigger value="trajectory">Agent trajectory</Tabs.Trigger><Tabs.Trigger value="audit">Audit timeline</Tabs.Trigger></Tabs.List>
          <Tabs.Content value="evidence"><Card><div className="section-heading"><div><span className="eyebrow">C · Evidence</span><h2>Evidence record</h2><p>Readable findings first; complete source records remain available for audit.</p></div></div><EvidenceSummary data={data} /></Card></Tabs.Content>
          <Tabs.Content value="validation"><Card><div className="section-heading"><div><span className="eyebrow">D · Financial authority</span><h2>Validator & policy outcome</h2></div><span className={`outcome outcome-${data.validation.outcome.toLowerCase()}`}>{data.validation.outcome}</span></div><div className="validation-versions"><span>{data.validation.validator_version ?? "Stage 2 matcher"}</span><span>{data.validation.policy_version ?? data.validation.rule_id}</span></div><div className="validation-grid"><div><h3>Passed</h3>{data.validation.passed.length ? <ul className="check-list">{data.validation.passed.map((item) => <li key={item}><Check size={15} />{humanizeMachineText(item)}</li>)}</ul> : <p className="muted">No passing predicate was recorded.</p>}</div><div><h3>Failed or blocked</h3>{data.validation.failed.length ? <ul className="fail-list">{data.validation.failed.map((item) => <li key={item}><CircleX size={15} />{humanizeMachineText(item)}</li>)}</ul> : <p className="muted">No blockers remained.</p>}</div></div>{data.validation.raw_validator && <JsonDetails label="View raw validator JSON" value={data.validation.raw_validator} />}</Card></Tabs.Content>
          <Tabs.Content value="trajectory"><Card><div className="section-heading"><div><span className="eyebrow">E · Bounded investigation</span><h2>Agent trajectory</h2></div>{data.trajectory.replayed && <span className="replay-badge">Replay · zero provider calls</span>}</div>{!data.trajectory.available ? <p className="muted">Stage 3 was not involved in this case.</p> : <><div className="trajectory-meta"><span>{data.trajectory.step_count} model steps</span><span>{data.trajectory.tools.length} tool calls</span><span>{data.trajectory.termination_reason?.replaceAll("_", " ")}</span><span>{data.trajectory.total_tokens ?? "No"} tokens reported</span></div><div className="tool-list">{data.trajectory.tools.map((tool, index) => <details key={`${tool.step_index}-${index}`}><summary><span className="step-index">{tool.step_index}</span><strong>{humanizeMachineText(tool.tool_name)}</strong><span>{tool.status}</span><ChevronDown size={15} /></summary><div><JsonDetails label="Validated arguments" value={tool.arguments} />{tool.output && <JsonDetails label="Raw tool output" value={tool.output} />}{tool.validation_error && <p className="error-copy">{tool.validation_error}</p>}</div></details>)}</div></>}</Card></Tabs.Content>
          <Tabs.Content value="audit"><Card><div className="section-heading"><div><span className="eyebrow">F · Durable record</span><h2>Audit timeline</h2></div></div><ol className="timeline">{data.audit_timeline.map((event) => <li key={`${event.sequence}-${event.kind}`}><div className="timeline-marker" /><div><div><strong>{event.title}</strong><span>{event.recorded_at ? new Date(event.recorded_at).toLocaleString("en-IN") : `Step ${event.sequence}`}</span></div><p>{event.detail}</p></div></li>)}</ol></Card></Tabs.Content>
        </Tabs.Root>
      </div>
      <aside className="review-column">
        {data.can_resolve ? <Card className="review-card human-review-required"><div className="review-icon"><Gavel size={20} /></div><span className="eyebrow">Human review required</span><h2>Resolve this case</h2><p>Automation could not establish unique financial authority. Select from the immutable candidate snapshot or retain the case in review.</p><div className="snapshot-binding"><LockKeyhole size={15} /><span>This decision is bound to the current immutable snapshot.</span></div><div className="review-selection"><span>Selection</span><strong>{keepReview ? "Keep in review — no match confirmed" : selected ? labels.get(selected) : "Choose a candidate"}</strong>{selected && <code>{selected}</code>}</div><button type="button" className={`keep-review ${keepReview ? "selected" : ""}`} onClick={() => { setKeepReview(true); setSelected(null); }}><span className="radio-dot" /> Keep in review / no match confirmed</button><label>Resolution reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={4} placeholder="Record the source evidence and reasoning used…" /></label><label>Reviewer<input value={actor} onChange={(event) => setActor(event.target.value)} /></label>{saveError && <p className="error-copy" role="alert">{saveError}</p>}<Button disabled={saving || (!selected && !keepReview) || !reason.trim()} onClick={save}>{saving ? "Saving durable resolution…" : keepReview ? "Keep in review" : "Resolve case"}</Button><small>Server-validated · revisioned · persisted in SQLite</small></Card> : <ResolutionSummary source={data.summary.resolution_source} activeResolution={activeResolution} selectedLabel={activeResolution?.selected_candidate_id ? labels.get(activeResolution.selected_candidate_id) : undefined} />}
        {data.human_resolutions.length > 0 && <Card><div className="section-heading"><div><span className="eyebrow">Revision journal</span><h2>Human history</h2></div></div><div className="revision-list">{[...data.human_resolutions].reverse().map((item) => <div key={item.resolution_id} className={item.active ? "active" : ""}><div><strong>Revision {item.revision}</strong><span>{item.active ? "Active" : "Superseded"}</span></div><p>{item.reason}</p><small>{item.actor ?? "Unattributed"} · {new Date(item.recorded_at).toLocaleString("en-IN")}</small></div>)}</div></Card>}
      </aside>
    </section>
  </div>;
}
