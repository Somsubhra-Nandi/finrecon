import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./ui";

describe("StatusBadge", () => {
  it("uses explicit authority language", () => {
    render(<StatusBadge source="ai_assisted" />);
    expect(screen.getByText("AI-assisted")).toBeInTheDocument();
  });
});
