import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Benchmarks from "./Benchmarks";

const payloads: Record<string, unknown> = {
  "/api/benchmarks": { benchmarks: [{ benchmark_id: "bounded-search-v1", title: "Bounded Search v1", status: "FROZEN", case_count: 50, description: "Bounded evidence search", replay_available: true, report_available: true, investigators: ["openrouter-free", "opus"] }], evolution: [] },
  "/api/benchmarks/frozen-eval-v3": { benchmark_id: "frozen-eval-v3", title: "Frozen Evaluation v3", status: "FROZEN", case_count: 890, description: "Frozen", replay_available: false, report_available: true, investigators: [], integrity: {}, constraints: {}, notices: [] },
  "/api/benchmarks/frozen-eval-v3/reports": { benchmark_id: "frozen-eval-v3", reports: [] },
  "/api/benchmarks/frozen-eval-v3/cases": { benchmark_id: "frozen-eval-v3", total: 0, cases: [] },
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
});
