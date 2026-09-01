import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Run from "./Run";
import type { BankStatementInspection } from "../types";

/**
 * The Run page's job in this phase is disclosure, not cleverness: it may
 * say "this is a format we already reviewed", and in every other case it
 * must hand the operator back the manual bank-profile path rather than
 * proceeding on a guess.
 */

const MATCHED: BankStatementInspection = {
  status: "matched",
  raw_headers: ["Ref No", "Value Date", "Narration", "Debit", "Credit"],
  normalized_headers: ["ref no", "value date", "narration", "debit", "credit"],
  signature: "abc123",
  field_count: 5,
  match_tier: "exact",
  profile: {
    profile_id: "finrecon_demo_v1", label: "FinRecon demo statement (synthetic)",
    version: "v1", verification: "demo_fixture",
    description: "Demo layout.", evidence: "Synthetic, in-repo.",
  },
  candidates: [],
};

const UNKNOWN: BankStatementInspection = {
  status: "unknown", raw_headers: ["Txn Ref", "Posted On"],
  normalized_headers: ["txn ref", "posted on"], signature: "def456",
  field_count: 2, match_tier: null, profile: null, candidates: [],
};

const AMBIGUOUS: BankStatementInspection = {
  status: "ambiguous", raw_headers: MATCHED.raw_headers,
  normalized_headers: MATCHED.normalized_headers, signature: "abc123",
  field_count: 5, match_tier: "exact", profile: null,
  candidates: [
    { profile_id: "bank_a_v1", label: "Bank A statement", version: "v1", verification: "demo_fixture", description: "", evidence: "" },
    { profile_id: "bank_b_v2", label: "Bank B statement", version: "v2", verification: "demo_fixture", description: "", evidence: "" },
  ],
};

function fetchReturning(inspection: BankStatementInspection) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    const url = String(input);
    if (url.includes("/api/bank-statement/inspect")) {
      return new Response(JSON.stringify(inspection), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({ batch_id: "batch:x", mode: "replay", provider_calls_made: false, result: {} }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

function renderRun() {
  return render(<MemoryRouter><Run /></MemoryRouter>);
}

function upload(labelPattern: RegExp, file: File) {
  const field = screen.getByText(labelPattern).closest("button")!;
  const input = field.querySelector("input[type=file]") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

const bankFile = () => new File(["Ref No,Value Date\n"], "bank.csv", { type: "text/csv" });
const razorpayFile = () => new File(["[]"], "razorpay.json", { type: "application/json" });
const profileFile = () => new File(["{}"], "profile.json", { type: "application/json" });

const runButton = () => screen.getByRole("button", { name: /Run reconciliation/i });

beforeEach(() => { vi.stubGlobal("fetch", fetchReturning(MATCHED)); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Run page bank-format detection", () => {
  it("recognizes a known format and removes the need for a manual profile", async () => {
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByText(/Bank format recognized/i)).toBeInTheDocument();
    expect(screen.getByText("FinRecon demo statement (synthetic)")).toBeInTheDocument();
    expect(screen.getByText(/finrecon_demo_v1 · v1/)).toBeInTheDocument();
    expect(screen.getByText(/Exact schema match/i)).toBeInTheDocument();
    // No manual profile uploaded, yet the run is available.
    expect(screen.queryByText(/Advanced · Manual bank profile/i)).not.toBeInTheDocument();
    await waitFor(() => expect(runButton()).not.toBeDisabled());
  });

  it("discloses that a shipped demo profile is synthetic rather than a real bank", async () => {
    renderRun();
    upload(/Bank statement/i, bankFile());
    expect(await screen.findByText(/Demo fixture — synthetic schema, not a real bank/i)).toBeInTheDocument();
  });

  it("sends built_in_profile_id, not a profile upload, for a recognized format", async () => {
    const fetchMock = fetchReturning(MATCHED);
    vi.stubGlobal("fetch", fetchMock);
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());
    await waitFor(() => expect(runButton()).not.toBeDisabled());

    fireEvent.click(runButton());

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/api/reconciliation/run"));
      expect(call).toBeDefined();
      const body = call![1]!.body as FormData;
      expect(body.get("built_in_profile_id")).toBe("finrecon_demo_v1");
      expect(body.get("bank_profile")).toBeNull();
    });
  });

  it("exposes the manual-profile upload when the format is unknown", async () => {
    vi.stubGlobal("fetch", fetchReturning(UNKNOWN));
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByText(/Bank format not recognized/i)).toBeInTheDocument();
    expect(screen.getByText(/does not guess column meanings/i)).toBeInTheDocument();
    expect(screen.getByText(/Advanced · Manual bank profile/i)).toBeInTheDocument();
    // Nothing is runnable until the operator declares the mapping.
    expect(runButton()).toBeDisabled();

    upload(/Bank profile \/ config/i, profileFile());
    await waitFor(() => expect(runButton()).not.toBeDisabled());
  });

  it("blocks a silent run when two known formats match", async () => {
    vi.stubGlobal("fetch", fetchReturning(AMBIGUOUS));
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByText(/Multiple known bank formats match/i)).toBeInTheDocument();
    expect(screen.getByText("Bank A statement")).toBeInTheDocument();
    expect(screen.getByText("Bank B statement")).toBeInTheDocument();
    expect(screen.getByText(/will not choose between them/i)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });

  it("lets the operator switch from a detected profile to a manual one", async () => {
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());
    expect(await screen.findByText(/Bank format recognized/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Change \/ use manual profile/i }));

    expect(screen.queryByText(/Bank format recognized/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Advanced · Manual bank profile/i)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();

    upload(/Bank profile \/ config/i, profileFile());
    await waitFor(() => expect(runButton()).not.toBeDisabled());
  });

  it("falls back to the manual path when inspection itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ detail: { code: "upload_too_large", message: "Each upload must be 15 MB or smaller." } }),
      { status: 413, headers: { "Content-Type": "application/json" } },
    )));
    renderRun();
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByRole("alert")).toHaveTextContent(/15 MB or smaller/i);
    expect(screen.getByText(/Advanced · Manual bank profile/i)).toBeInTheDocument();
  });
});
