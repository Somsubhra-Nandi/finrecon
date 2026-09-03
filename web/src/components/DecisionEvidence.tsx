import { ArrowRight, CheckCircle2, CircleX } from "lucide-react";
import type { ReactNode } from "react";

export function EvidenceSection({ title, children }: { title: string; children: ReactNode }) {
  return <section className={title === "Source facts" ? "decision-evidence-section source-facts" : "decision-evidence-section"}><h3>{title}</h3>{children}</section>;
}

export function DecisionPath({ stages, note }: { stages: string[]; note?: string }) {
  return <section className="decision-path" aria-label="Decision path">
    <div>{stages.map((stage, index) => <span key={`${stage}-${index}`}>{stage}{index < stages.length - 1 && <ArrowRight size={14} />}</span>)}</div>
    {note && <small>{note}</small>}
  </section>;
}

export function EvidenceChecks({ passed = [], failed = [] }: { passed?: string[]; failed?: string[] }) {
  return <ul className="evidence-summary-list">
    {passed.map(value => <li key={`pass-${value}`}><CheckCircle2 size={14} /> {value}</li>)}
    {failed.map(value => <li key={`fail-${value}`}><CircleX size={14} /> {value}</li>)}
  </ul>;
}
