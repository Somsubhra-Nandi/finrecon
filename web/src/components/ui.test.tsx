import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { JsonDetails, StatusBadge } from "./ui";
import { EvidenceSection } from "./DecisionEvidence";

describe("StatusBadge", () => {
  it("uses explicit authority language", () => {
    render(<StatusBadge source="ai_assisted" />);
    expect(screen.getByText("Evidence-assisted")).toBeInTheDocument();
  });

  it("marks raw technical payloads for contained, readable wrapping", () => {
    render(<JsonDetails label="Technical details" value={{ narration: "IMPS_P2A_".repeat(80) }} />);
    expect(document.querySelector("pre.technical-payload")).toBeInTheDocument();
  });

  it("marks the rendered Source Facts value through the shared evidence section", () => {
    render(<EvidenceSection title="Source facts"><p>{"AXQE8T|".repeat(80)}</p></EvidenceSection>);
    expect(document.querySelector("section.source-facts > p:first-of-type")).toBeInTheDocument();
  });
});
