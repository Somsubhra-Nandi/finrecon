import {
  ArrowRight,
  Bot,
  Gavel,
  Scale,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import type { ResolutionSource } from "../types";

type Stage = {
  label: string;
  icon: typeof Bot;
  tone?: "review" | "human" | "resolved";
};

function stagesFor(source: ResolutionSource): Stage[] {
  if (source === "deterministic") {
    return [
      { label: "Deterministic reconciliation", icon: ShieldCheck },
      { label: "Resolution", icon: Scale, tone: "resolved" },
    ];
  }
  if (source === "ai_assisted") {
    return [
      { label: "AI investigation", icon: Bot },
      { label: "Deterministic validation", icon: ShieldCheck },
      { label: "Resolution", icon: Scale, tone: "resolved" },
    ];
  }
  if (source === "human") {
    return [
      { label: "AI investigation", icon: Bot },
      { label: "Deterministic validation", icon: ShieldCheck },
      { label: "Escalation", icon: ShieldAlert, tone: "review" },
      { label: "Human review", icon: Gavel, tone: "human" },
      { label: "Resolution", icon: Scale, tone: "resolved" },
    ];
  }
  return [
    { label: "AI investigation", icon: Bot },
    { label: "Deterministic validation", icon: ShieldCheck },
    { label: "Escalation", icon: ShieldAlert, tone: "review" },
    { label: "Human review required", icon: Gavel, tone: "human" },
  ];
}

export function OutcomePath({ source }: { source: ResolutionSource }) {
  const stages = stagesFor(source);
  return (
    <section className={`authority-chain authority-chain-${source}`} aria-label="Resolution authority path">
      {stages.map((stage, index) => {
        const Icon = stage.icon;
        return (
          <div className="authority-chain-fragment" key={stage.label}>
            <div className={`chain-stage ${stage.tone || ""}`}>
              <Icon size={16} aria-hidden="true" />
              <span>{stage.label}</span>
            </div>
            {index < stages.length - 1 && <ArrowRight className="chain-arrow" size={15} aria-hidden="true" />}
          </div>
        );
      })}
    </section>
  );
}
