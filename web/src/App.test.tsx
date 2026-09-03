import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("application routes", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders Source Issues inside the shared application shell", async () => {
    vi.stubGlobal("fetch", vi.fn(async (path: string) => {
      const payload = path === "/api/runs"
        ? []
        : path === "/api/ingestion/issues"
          ? { batch_id: null, total: 0, issues: [] }
          : {};
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));

    render(<MemoryRouter initialEntries={["/issues"]}><App /></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: "Ingestion issues" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Primary navigation" })).toBeInTheDocument();
    expect(await screen.findByText("No ingestion issues")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Source issues" })).toHaveAttribute("href", "/issues");
  });
});
