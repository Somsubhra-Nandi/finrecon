import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OutcomePath } from "./OutcomePath";
import { ResolutionSummary } from "../pages/CaseDetail";

describe("case authority presentation", () => {
  it("shows only the stages that occurred for deterministic cases", () => {
    render(<OutcomePath source="deterministic" />);
    expect(screen.getByText("Deterministic reconciliation")).toBeInTheDocument();
    expect(screen.getByText("Resolution")).toBeInTheDocument();
    expect(screen.queryByText("AI investigation")).not.toBeInTheDocument();
  });

  it("shows escalation and human authority for human-resolved cases", () => {
    render(<OutcomePath source="human" />);
    expect(screen.getByText("AI investigation")).toBeInTheDocument();
    expect(screen.getByText("Deterministic validation")).toBeInTheDocument();
    expect(screen.getByText("Escalation")).toBeInTheDocument();
    expect(screen.getByText("Human review")).toBeInTheDocument();
    expect(screen.getByText("Resolution")).toBeInTheDocument();
  });

  it("states that deterministic validation authorized an AI-assisted outcome", () => {
    render(<ResolutionSummary source="ai_assisted" />);
    expect(screen.getByText("Evidence-assisted resolution")).toBeInTheDocument();
    expect(screen.getByText(/Evidence found; deterministic validation passed/)).toBeInTheDocument();
    expect(screen.queryByText(/sufficient deterministic authority/)).not.toBeInTheDocument();
  });
});
