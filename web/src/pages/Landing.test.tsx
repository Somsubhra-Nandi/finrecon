import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Landing from "./Landing";

describe("Landing page", () => {
  it("makes the bounded authority path and demo routes discoverable", () => {
    render(<MemoryRouter><Landing /></MemoryRouter>);

    expect(screen.getByRole("heading", { name: /Reconcile with evidence/i })).toBeInTheDocument();
    expect(screen.getByText("There is no path from LLM confidence to money.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Explore Demo/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Evidence & Evaluation/i })).toHaveAttribute("href", "/benchmarks");
  });

  it("reports a failed demo load instead of doing nothing", async () => {
    // The handler used to be try/finally with no catch: a rejected request
    // skipped the navigation and cleared the spinner, so the button looked
    // inert and the operator was told nothing.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: { code: "backend_failure", message: "The demo batch could not be built." } }),
    }));

    render(<MemoryRouter><Landing /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: /Explore Demo/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("The demo batch could not be built."));
    expect(screen.getByRole("button", { name: /Explore Demo/i })).toBeEnabled();
  });
});

afterEach(() => { vi.unstubAllGlobals(); });
