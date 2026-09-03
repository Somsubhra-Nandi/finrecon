import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Overview from "./Overview";

const metrics = { total_cases: 2, deterministic_resolved: 0, ai_assisted_resolved: 1, human_resolved: 0, needs_review: 1, ingestion_issues: 0, total_amount_paise: 350000, provider_calls: 0, model_tokens: 960, model_cost: null };

describe("operations overview polish", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses canonical authority copy, accurate count grammar, and recorded telemetry labels", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path.startsWith("/api/overview")
      ? { selected_batch_id: "batch:test", metrics, recent_runs: [{ batch_id: "batch:test", split: "demo", content_fingerprint: "abc", record_count: 1, metrics }] }
      : { batch_id: "batch:test", total: 2, cases: [{ batch_id: "batch:test", case_id: "case:1", bank_record_id: "bank:1", narration: "test", amount_paise: 100000, status: "resolved", resolution_source: "ai_assisted", candidate_count: 2, evidence_state: "AI evidence validated", last_updated: null }, { batch_id: "batch:test", case_id: "case:2", bank_record_id: "bank:2", narration: "test", amount_paise: 250000, status: "needs_review", resolution_source: "escalated", candidate_count: 2, evidence_state: "1 policy blocker", last_updated: null }] }), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MemoryRouter><Overview /></MemoryRouter>);

    expect(await screen.findByText("AI investigates. Deterministic controls decide.")).toBeInTheDocument();
    expect(screen.getAllByText("Evidence-assisted").length).toBeGreaterThan(0);
    expect(screen.getByText("Evidence found; deterministic validation passed.")).toBeInTheDocument();
    expect(screen.getAllByText("1 review case").length).toBeGreaterThan(0);
    expect(screen.queryByText("1 review cases")).not.toBeInTheDocument();
    expect(screen.getByText("Recorded model tokens")).toBeInTheDocument();
    expect(screen.getByText("Provider calls during this run")).toBeInTheDocument();
    await screen.findByText("Resolved value");
    const valueCard = screen.getByText("Value represented").closest<HTMLElement>(".metric")!;
    expect(within(valueCard).getByText("Resolved value")).toBeInTheDocument();
    expect(within(valueCard).getByText("₹1K")).toBeInTheDocument();
    expect(within(valueCard).getByText("Held for review")).toBeInTheDocument();
    expect(within(valueCard).getByText("₹2.5K")).toBeInTheDocument();
  });
});
