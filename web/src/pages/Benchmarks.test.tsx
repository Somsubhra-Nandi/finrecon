import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Benchmarks from "./Benchmarks";

const payloads: Record<string, unknown> = {
  "/api/benchmarks": { benchmarks: [{ benchmark_id: "bounded-search-v1", title: "Bounded Search v1", status: "FROZEN", case_count: 50, description: "Bounded evidence search", replay_available: true, report_available: true, investigators: ["openrouter-free", "opus"] }], evolution: [] },
  "/api/benchmarks/frozen-eval-v3": { benchmark_id: "frozen-eval-v3", title: "Frozen Evaluation v3", status: "FROZEN", case_count: 890, description: "Frozen", replay_available: false, report_available: true, investigators: [], integrity: {}, constraints: {}, notices: [] },
  "/api/benchmarks/frozen-eval-v3/reports": { benchmark_id: "frozen-eval-v3", reports: [] },
  "/api/benchmarks/frozen-eval-v3/cases": { benchmark_id: "frozen-eval-v3", total: 0, cases: [] },
  "/api/benchmarks/bounded-search-v1": { benchmark_id: "bounded-search-v1", title: "Bounded Search v1", status: "FROZEN", case_count: 50, description: "Bounded", replay_available: true, report_available: true, investigators: ["openrouter-free"], integrity: {}, constraints: {}, notices: [] },
  "/api/benchmarks/bounded-search-v1/reports": { benchmark_id: "bounded-search-v1", reports: [] },
  "/api/benchmarks/bounded-search-v1/cases": { benchmark_id: "bounded-search-v1", total: 50, cases: [] },
  "/api/benchmarks/bounded-search-v1/cases/case%3Abnk_bsearch_000012": { case_id: "case:bnk_bsearch_000012", bank_record_id: "bank:12", narration: "raw", amount_paise: 100, candidate_count: 2, recorded_outcomes: { "openrouter-free": "tool_validation_failure" }, replay_investigators: ["openrouter-free"], controller_rejection_demo: true, candidate_snapshot: null, visible_records: {}, evaluation_metadata_notice: "visible only" },
  "/api/benchmarks/bounded-search-v1/replays/openrouter-free/case%3Abnk_bsearch_000012": { benchmark_id: "bounded-search-v1", investigator: "openrouter-free", replayed: true, provider_calls_made: false, trajectory: { termination_reason: "tool_validation_failed", steps: [], tool_invocations: [] }, deterministic_validation: {}, policy_result: { outcome: "ESCALATE", blockers: ["tool_validation_failure"] } },
};

describe("Benchmarks page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps the frozen evaluation summary prominent", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(payloads[path] ?? payloads["/api/benchmarks/frozen-eval-v3/cases"]), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MemoryRouter><Benchmarks /></MemoryRouter>);
    expect(await screen.findByText("Safety and resolution, measured across the full frozen cohort.")).toBeInTheDocument();
    expect(screen.getByText("890")).toBeInTheDocument();
    expect(screen.getByText("0", { exact: true })).toBeInTheDocument();
  });

  it("repairs an incomplete replay deep link and shows the controller rejection", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(payloads[path] ?? payloads["/api/benchmarks/frozen-eval-v3/cases"]), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MemoryRouter initialEntries={["/benchmarks?tab=replay&case=case:bnk_bsearch_000012&investigator=openrouter-free"]}><Benchmarks /></MemoryRouter>);
    expect(await screen.findByText("FinRecon rejected a model request.")).toBeInTheDocument();
  });

  it("offers curated bounded-search replay entries", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(payloads[path] ?? payloads["/api/benchmarks/bounded-search-v1/cases"]), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MemoryRouter initialEntries={["/benchmarks?benchmark=bounded-search-v1&tab=replay"]}><Benchmarks /></MemoryRouter>);
    expect(await screen.findByText("Watch FinRecon reject a model")).toBeInTheDocument();
    expect(screen.getByText("Successful Opus resolution")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: /Replay case/i })[0]);
  });
});
