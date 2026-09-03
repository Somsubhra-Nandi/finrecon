import type { ReactNode } from "react";
import { Bot, Braces, Landmark, ShieldCheck } from "lucide-react";
import type { CaseDetailResponse } from "../types";
import { money as formatMoney } from "../api";
import {
  asArray,
  asRecord,
  asStringArray,
  formatCaseDate,
  humanizeMachineText,
  humanizeRelation,
} from "../lib/caseFormatting";

function JsonDetails({ label = "View raw evidence JSON", value }: { label?: string; value: unknown }) {
  return (
    <details className="json-details">
      <summary>{label}</summary>
      <pre className="technical-payload">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function EvidenceList({ items }: { items: ReactNode[] }) {
  if (!items.length) return <p className="muted">No evidence was recorded for this stage.</p>;
  return (
    <ul className="evidence-summary-list">
      {items.map((item, index) => <li key={index}>{item}</li>)}
    </ul>
  );
}

function deterministicSummary(data: CaseDetailResponse): ReactNode[] {
  const evidence = asRecord(data.evidence.deterministic);
  const money = asRecord(evidence.money);
  const references = asArray(evidence.references).map(asRecord);
  const competing = asArray(evidence.competing_solution_ids);
  const dates = asArray(evidence.settlement_dates);
  const items: ReactNode[] = [];

  if (typeof money.bank_amount_paise === "number" && money.unexplained_delta_paise === 0) {
    items.push(`Exact financial total matched: ${formatMoney(money.bank_amount_paise)} with no unexplained delta.`);
  }
  for (const reference of references) {
    if (reference.matched_token) {
      items.push(
        <span>
          Exact {humanizeMachineText(String(reference.kind || "reference")).toLowerCase()} matched narration token <code>{String(reference.matched_token)}</code>.
        </span>,
      );
    }
  }
  if (data.candidates.length > 0) {
    items.push(`${data.candidates.length} plausible candidate${data.candidates.length === 1 ? " was" : "s were"} retained in the immutable snapshot.`);
  } else if (data.summary.resolution_source === "deterministic") {
    items.push("Stage 2 resolved the case directly; no unresolved candidate snapshot was required.");
  }
  if (competing.length > 1) {
    items.push(`Stage 2 found ${competing.length} financially plausible settlement groups and did not choose between them.`);
  }
  if (dates.length > 0) {
    items.push(`${dates.length} settlement date${dates.length === 1 ? " was" : "s were"} evaluated within the declared value-date window.`);
  }
  return items;
}

function aiSummary(data: CaseDetailResponse, labels: Map<string, string>): ReactNode[] {
  const items: ReactNode[] = [];
  for (const rawStep of data.evidence.ai_found) {
    const step = asRecord(rawStep);
    const output = asRecord(step.output);
    if (step.tool_name === "compare_reference_fragment") {
      const comparisons = asArray(output.candidate_comparisons).map(asRecord);
      const fragment = String(output.fragment || asRecord(step.arguments).fragment || "");
      items.push(
        <span>
          Reference fragment <code>{fragment || "not reported"}</code> was compared across all {comparisons.length} candidates.
        </span>,
      );
      for (const candidate of comparisons) {
        const candidateId = String(candidate.candidate_id || "");
        const relations = asArray(candidate.comparisons)
          .map(asRecord)
          .flatMap((comparison) => asStringArray(comparison.holding_relation_ids));
        const pinned = asArray(candidate.comparisons)
          .map(asRecord)
          .reduce((highest, comparison) => Math.max(highest, Number(comparison.max_pinned_reference_characters || 0)), 0);
        if (relations.length) {
          items.push(`${labels.get(candidateId) || "A candidate"} satisfied ${humanizeRelation(relations[0])}${pinned ? ` with ${pinned} pinned characters` : ""}.`);
        } else {
          items.push(`${labels.get(candidateId) || "A candidate"} did not satisfy a declared reference relation.`);
        }
      }
    }
  }
  if (!items.length && data.evidence.ai_found.length) {
    items.push(`${data.evidence.ai_found.length} bounded investigation step${data.evidence.ai_found.length === 1 ? " was" : "s were"} recorded for audit.`);
  }
  return items;
}

function structuredFacts(data: CaseDetailResponse): ReactNode[] {
  const rawValidator = asRecord(data.validation.raw_validator);
  const closure = asRecord(rawValidator.structural_closure);
  const items: ReactNode[] = [
    `Value date: ${formatCaseDate(String(data.bank_transaction.value_date || ""))}`,
    `Amount: ${formatMoney(Number(data.bank_transaction.amount_paise ?? data.summary.amount_paise))}`,
    `Direction: ${humanizeMachineText(String(data.bank_transaction.direction || ""))}`,
  ];
  if (closure.structured_value_date_fact) {
    const state = String(rawValidator.structural_evidence_state || "");
    items.push(
      state === "none"
        ? "The structured value date was checked but did not distinguish between candidates."
        : `Structured date evidence state: ${humanizeMachineText(state)}.`,
    );
  }
  return items;
}

function validatorSummary(data: CaseDetailResponse): ReactNode[] {
  const raw = asRecord(data.validation.raw_validator);
  const items: ReactNode[] = [];
  if (data.validation.outcome) items.push(`Outcome: ${data.validation.outcome}`);
  if (data.validation.passed.length) {
    items.push(`Passed: ${data.validation.passed.map(humanizeMachineText).join(", ")}.`);
  }
  if (data.validation.failed.length) {
    items.push(`Blocked by: ${data.validation.failed.map(humanizeMachineText).join(", ")}.`);
  }
  if (data.summary.resolution_source === "ai_assisted") {
    items.push("Deterministic validation and policy authorized auto-resolution after the investigation completed.");
  } else if (data.summary.resolution_source === "escalated") {
    items.push(`Policy retained the case for human review${raw.escalation_reason ? `: ${humanizeMachineText(String(raw.escalation_reason))}` : ""}.`);
  } else if (data.summary.resolution_source === "human") {
    items.push("Automation escalated the case; human authority supplied the final resolution.");
  } else {
    items.push("Stage 2 deterministic reconciliation authorized the resolution without Stage 3.");
  }
  return items;
}

export function EvidenceSummary({ data }: { data: CaseDetailResponse }) {
  const labels = new Map(data.candidates.map((candidate, index) => [candidate.candidate_id, `Candidate ${String.fromCharCode(65 + index)}`]));
  return (
    <div className="evidence-readable-grid">
      <article className="evidence-card">
        <div className="section-heading"><ShieldCheck size={17} /><h3>Deterministic evidence</h3></div>
        <EvidenceList items={deterministicSummary(data)} />
        <JsonDetails value={data.evidence.deterministic} />
      </article>
      <article className="evidence-card">
        <div className="section-heading"><Bot size={17} /><h3>AI-found evidence</h3></div>
        <EvidenceList items={aiSummary(data, labels)} />
        <JsonDetails value={data.evidence.ai_found} />
      </article>
      <article className="evidence-card">
        <div className="section-heading"><Landmark size={17} /><h3>Structured bank facts</h3></div>
        <EvidenceList items={structuredFacts(data)} />
        <JsonDetails value={data.bank_transaction} />
      </article>
      <article className="evidence-card">
        <div className="section-heading"><Braces size={17} /><h3>Raw narration evidence</h3></div>
        <blockquote>{data.evidence.raw_narration ?? "Not available"}</blockquote>
        <p className="muted">Preserved exactly as ingested for audit and evidence tracing.</p>
      </article>
      <article className="evidence-card evidence-card-wide">
        <div className="section-heading"><ShieldCheck size={17} /><h3>Validator result</h3></div>
        <EvidenceList items={validatorSummary(data)} />
        <JsonDetails label="View raw validator JSON" value={data.validation.raw_validator} />
      </article>
    </div>
  );
}
