import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import Landing from "./Landing";

describe("Landing page", () => {
  it("makes the bounded authority path and demo routes discoverable", () => {
    render(<MemoryRouter><Landing /></MemoryRouter>);

    expect(screen.getByRole("heading", { name: /Reconcile with evidence/i })).toBeInTheDocument();
    expect(screen.getByText("There is no path from LLM confidence to money.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Explore Demo/i })).toHaveAttribute("href", "/run");
    expect(screen.getByRole("link", { name: /View Benchmarks/i })).toHaveAttribute("href", "/benchmarks");
  });
});
