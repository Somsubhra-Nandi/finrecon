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
  "/api/benchmarks/bounded-search-v1/reports": { benchmark_id: "bounded-search-v1", reports: [{ report_id: "openrouter-free", label: "45-case valid provider-response scored cohort", metrics: { investigated: 45, uniquely_resolvable_cases: 38, correct_auto_resolutions: 30, wrong_auto_resolutions: 0, escalated: 15, overall_match_rate: 30 / 38, tool_validation_failed: 11, unsafe_auto_match_rate: 0, value_at_risk_paise: 0 }, telemetry: {}, cohort: {}, recorded_versions: {} }, { report_id: "opus", label: "Authoritative complete 50-case frozen scored cohort", metrics: { investigated: 50, uniquely_resolvable_cases: 40, correct_auto_resolutions: 40, wrong_auto_resolutions: 0, escalated: 10, overall_match_rate: 1, tool_validation_failed: 0, unsafe_auto_match_rate: 0, value_at_risk_paise: 0 }, telemetry: { models_requested: { "gorouter:claude-opus-5-thinking": 50 }, models_reported: { "gorouter:claude-opus-5": 50 } }, cohort: { requested_count: 50, complete: true }, recorded_versions: {} }, { report_id: "mechanical", label: "Mechanical baseline", metrics: {}, telemetry: {}, cohort: {}, recorded_versions: {} }] },
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

  it("projects the complete Opus cohort without a partial-cohort claim", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(payloads[path] ?? payloads["/api/benchmarks/bounded-search-v1/cases"]), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<MemoryRouter initialEntries={["/benchmarks?benchmark=bounded-search-v1&tab=compare"]}><Benchmarks /></MemoryRouter>);
    expect(await screen.findByText("50 / 50 cohort complete")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "P" && element.textContent?.includes("Requested model: gorouter:claude-opus-5-thinking") === true)).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "P" && element.textContent?.includes("Provider-reported model: gorouter:claude-opus-5") === true)).toBeInTheDocument();
    expect(screen.queryByText(/Opus 40 valid scored cases/i)).not.toBeInTheDocument();
  });
});
