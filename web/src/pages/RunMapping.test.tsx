import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Run from "./Run";
import type { BankMappingProposalResponse, BankStatementInspection, SavedMappingView } from "../types";

/**
 * The unknown-schema mapping flow, from the operator's side.
 *
 * What these tests are for. The server enforces the confirmation boundary
 * independently -- see `tests/test_bank_mapping_api.py` -- so nothing here is
 * load-bearing for safety. What is load-bearing for *usability* is that the
 * operator is genuinely able to do the reviewing they are being held
 * responsible for: every proposed value must be editable, every column must
 * be pickable from the file's own headers rather than retyped, a field the
 * data cannot settle must be impossible to skip past, and a model outage must
 * leave a working editor rather than a dead end.
 */

const HEADERS = ["Txn Reference", "Posted On", "Particulars", "Withdrawal Amt", "Deposit Amt"];

const UNKNOWN: BankStatementInspection = {
  status: "unknown", raw_headers: HEADERS,
  normalized_headers: HEADERS.map((h) => h.toLowerCase()),
  signature: "sig-unknown", field_count: 5, match_tier: null,
  profile: null, candidates: [], match: null, matches: [],
};

const SAVED: SavedMappingView = {
  mapping_id: "bankmap_abc123", name: "HDFC Current Account", version: 1,
  profile_id: "bankmap_abc123:v1", status: "active",
  provenance: "human_confirmed", source: "user_saved",
  schema_signature: "sig-unknown", expected_headers: HEADERS,
  profile: {
    value_date_column: "Posted On", value_date_format: "%d/%m/%Y",
    narration_column: "Particulars", reference_id_column: "Txn Reference",
    money_columns: {
      kind: "debit_credit", debit_column: "Withdrawal Amt",
      credit_column: "Deposit Amt", inactive_side_marker: "empty_or_zero",
    },
  },
  created_at: "2026-09-01T00:00:00+00:00", llm_proposal: null,
};

const RECOGNIZED: BankStatementInspection = {
  ...UNKNOWN,
  status: "matched", match_tier: "exact",
  match: {
    kind: "user_saved", profile_id: SAVED.profile_id, label: SAVED.name,
    version: "v1", verification: null,
    description: "Saved by this deployment's operator.", evidence: "",
    saved_mapping: SAVED,
  },
};

const AMBIGUOUS_SAVED: BankStatementInspection = {
  ...UNKNOWN,
  status: "ambiguous", match_tier: "exact",
  matches: [
    { kind: "user_saved", profile_id: "a:v1", label: "Mapping A", version: "v1", verification: null, description: "", evidence: "", saved_mapping: { ...SAVED, mapping_id: "a", name: "Mapping A" } },
    { kind: "user_saved", profile_id: "b:v1", label: "Mapping B", version: "v1", verification: null, description: "", evidence: "", saved_mapping: { ...SAVED, mapping_id: "b", name: "Mapping B" } },
  ],
};

const DATE_FORMATS = [
  { value: "%d/%m/%Y", label: "DD/MM/YYYY  (day first)" },
  { value: "%m/%d/%Y", label: "MM/DD/YYYY  (month first)" },
  { value: "%Y-%m-%d", label: "YYYY-MM-DD  (ISO)" },
];

const PROPOSAL: BankMappingProposalResponse = {
  schema_status: "unknown",
  sample: {
    headers: HEADERS,
    rows: [["UTR9911", "07/08/2024", "NEFT SETTLEMENT RZP", "0.00", "125000.00"]],
    bounds: { max_sample_rows: 5 },
  },
  supported_date_formats: DATE_FORMATS,
  raw_headers: HEADERS, normalized_headers: HEADERS.map((h) => h.toLowerCase()),
  signature: "sig-unknown", delimiter: ",", encoding: "utf-8-sig",
  proposal: {
    mapping: {
      value_date_column: "Posted On", value_date_format: "%d/%m/%Y",
      value_date_format_certain: false, narration_column: "Particulars",
      reference_id_column: "Txn Reference",
      money: {
        kind: "debit_credit", debit_column: "Withdrawal Amt",
        credit_column: "Deposit Amt", inactive_side_marker: "empty_or_zero",
        amount_column: null, direction_column: null,
        credit_values: null, debit_values: null,
      },
    },
    reasoning_summary: {
      value_date: "Posted On holds dd/mm/yyyy dates.",
      money: "Two amount columns; the unused side is zero-filled.",
      narration: "Particulars is the free-text description.",
      reference: "Txn Reference carries UTR-shaped values.",
    },
    uncertainties: ["Day/month order cannot be settled from these rows."],
    provider: "openrouter", model: "some-model",
    reported_model: null, proposed_at: "2026-09-01T00:00:00+00:00",
  },
  validation: {
    ok: true, errors: [],
    warnings: [{
      field: "value_date_format", code: "date_format_ambiguous",
      message: "The sampled dates parse equally well under '%d/%m/%Y' and ['%m/%d/%Y'].",
    }],
    fields_requiring_human_choice: ["value_date_format"],
    date_format: {
      proposed: "%d/%m/%Y", plausible: ["%d/%m/%Y", "%m/%d/%Y"],
      contradicted: false, ambiguous_with: ["%m/%d/%Y"],
      evidence_rows: 1, requires_human_choice: true,
    },
  },
  failure_code: null, failure_message: null,
  provider_calls_made: true, model_calls: 1,
};

const FAILED_PROPOSAL: BankMappingProposalResponse = {
  ...PROPOSAL,
  proposal: null, validation: null,
  failure_code: "provider_unavailable",
  failure_message: "The mapping-proposal service could not reach a model provider.",
  provider_calls_made: false, model_calls: 0,
};

type Routes = {
  inspection?: BankStatementInspection;
  proposal?: BankMappingProposalResponse;
  saveStatus?: number;
  saveBody?: unknown;
};

function stubFetch({ inspection = UNKNOWN, proposal = PROPOSAL, saveStatus = 200, saveBody }: Routes = {}) {
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    void init;
    const url = String(input);
    const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
      status, headers: { "Content-Type": "application/json" },
    });
    if (url.includes("/api/bank-statement/inspect")) return json(inspection);
    if (url.includes("/api/bank-mappings/propose")) return json(proposal);
    if (url.includes("/api/bank-mappings")) {
      return json(
        saveBody ?? { saved: SAVED, validation: PROPOSAL.validation, created_version: 1 },
        saveStatus,
      );
    }
    return json({ batch_id: "batch:x", mode: "replay", provider_calls_made: false, result: {} });
  });
  vi.stubGlobal("fetch", mock);
  return mock;
}

function renderRun() {
  return render(<MemoryRouter><Run /></MemoryRouter>);
}

function upload(labelPattern: RegExp, file: File) {
  const field = screen.getByText(labelPattern).closest("button")!;
  const input = field.querySelector("input[type=file]") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

const bankFile = () => new File([HEADERS.join(",") + "\n"], "bank.csv", { type: "text/csv" });
const razorpayFile = () => new File(["[]"], "razorpay.json", { type: "application/json" });

const runButton = () => screen.getByRole("button", { name: /Run reconciliation/i });
const saveButton = () => screen.getByRole("button", { name: /Save (mapping|new version) & continue/i });
const nameInput = () => screen.getByPlaceholderText(/HDFC Current Account, Client XYZ/i);

/**
 * A control, found by the label that wraps it.
 *
 * `getByLabelText` rather than `getByText`, for two reasons. It searches only
 * labelled form controls, so a label name that also appears in prose
 * elsewhere on the page ("Credit column" is a substring of the money-model
 * button's "Separate debit and credit columns") is unambiguous here. And it
 * fails if a control ever loses its label, which is a regression worth
 * catching.
 */
function control(labelPattern: RegExp): HTMLSelectElement {
  return screen.getByLabelText(labelPattern) as HTMLSelectElement;
}

async function openEditor() {
  renderRun();
  upload(/Razorpay recon file/i, razorpayFile());
  upload(/Bank statement/i, bankFile());
  fireEvent.click(await screen.findByRole("button", { name: /Map these columns/i }));
  // Wait on the name field itself rather than on label text: the editor's
  // labels are deliberately wordy, and matching prose is a brittle signal.
  await screen.findByPlaceholderText(/HDFC Current Account, Client XYZ/i);
}

/** Answer the ambiguous-date acknowledgement, which every save needs here. */
function acknowledgeDateFormat() {
  fireEvent.click(screen.getByRole("checkbox"));
}

beforeEach(() => { stubFetch(); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("unknown schema opens the mapping review flow", () => {
  it("offers to map the columns and does not pretend to recognize the file", async () => {
    renderRun();
    upload(/Bank statement/i, bankFile());
    expect(await screen.findByText(/We don't recognize this bank format/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Map these columns/i })).toBeInTheDocument();
    // Nothing runnable yet: no mapping exists.
    expect(runButton()).toBeDisabled();
  });

  it("requests a proposal only after the operator asks to map the columns", async () => {
    const mock = stubFetch();
    renderRun();
    upload(/Bank statement/i, bankFile());
    await screen.findByText(/We don't recognize this bank format/i);
    // Uploading a file must not, on its own, send anything to a model.
    expect(mock.mock.calls.some(([url]) => String(url).includes("/propose"))).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /Map these columns/i }));
    await waitFor(() => expect(
      mock.mock.calls.some(([url]) => String(url).includes("/propose")),
    ).toBe(true));
  });

  it("populates every control from the proposal", async () => {
    await openEditor();
    expect(control(/Value date column/i).value).toBe("Posted On");
    expect(control(/Date format/i).value).toBe("%d/%m/%Y");
    expect(control(/Narration column/i).value).toBe("Particulars");
    expect(control(/Reference \/ UTR column/i).value).toBe("Txn Reference");
    expect(control(/Debit column/i).value).toBe("Withdrawal Amt");
    expect(control(/Credit column/i).value).toBe("Deposit Amt");
    expect(control(/Inactive-side behaviour/i).value).toBe("empty_or_zero");
  });

  it("populates the column dropdowns from the uploaded file's own headers", async () => {
    await openEditor();
    for (const selector of [/Value date column/i, /Narration column/i, /Debit column/i]) {
      const options = Array.from(control(selector).options).map((option) => option.value);
      expect(options).toEqual(["", ...HEADERS]);
    }
    // The optional reference offers an explicit "none", which is a different
    // answer from "not chosen yet".
    const reference = control(/Reference \/ UTR column/i);
    expect(reference.options[0].textContent).toMatch(/None — this statement has no such column/i);
  });

  it("offers only supported date formats", async () => {
    await openEditor();
    const options = Array.from(control(/Date format/i).options).map((option) => option.value);
    expect(options).toEqual(["", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"]);
  });

  it("shows the sampled rows the suggestion was made from", async () => {
    await openEditor();
    expect(screen.getByText(/Statement preview — first 1 row/i)).toBeInTheDocument();
    expect(screen.getByText("NEFT SETTLEMENT RZP")).toBeInTheDocument();
  });

  it("labels the suggestion as a suggestion and its rationale as explanatory", async () => {
    await openEditor();
    expect(screen.getByText(/AI suggested a mapping — review every field before saving/i)).toBeInTheDocument();
    expect(screen.getByText(/nothing is saved or reconciled until you confirm/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Why the model suggested this/i));
    expect(screen.getByText(/It is not evidence, and no part of FinRecon reads it/i)).toBeInTheDocument();
  });

  it("surfaces what the model said it could not determine", async () => {
    await openEditor();
    expect(screen.getByText(/Day\/month order cannot be settled from these rows/i)).toBeInTheDocument();
  });
});

describe("the operator is in control of every field", () => {
  it("lets the operator change any proposed value", async () => {
    await openEditor();
    fireEvent.change(control(/Value date column/i), { target: { value: "Particulars" } });
    fireEvent.change(control(/Narration column/i), { target: { value: "Posted On" } });
    fireEvent.change(control(/Date format/i), { target: { value: "%Y-%m-%d" } });
    fireEvent.change(control(/Reference \/ UTR column/i), { target: { value: "" } });
    fireEvent.change(control(/Inactive-side behaviour/i), { target: { value: "empty_only" } });

    expect(control(/Value date column/i).value).toBe("Particulars");
    expect(control(/Narration column/i).value).toBe("Posted On");
    expect(control(/Date format/i).value).toBe("%Y-%m-%d");
    expect(control(/Reference \/ UTR column/i).value).toBe("");
    expect(control(/Inactive-side behaviour/i).value).toBe("empty_only");
  });

  it("lets the operator switch the money model and offers the other controls", async () => {
    await openEditor();
    fireEvent.click(screen.getByText(/Amount \+ direction/i).closest("button")!);
    expect(control(/Amount column/i)).toBeInTheDocument();
    expect(control(/Direction column/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("CR, C")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("DR, D")).toBeInTheDocument();
  });

  it("submits the operator's edits, not the model's suggestion", async () => {
    const mock = stubFetch();
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Client XYZ Bank Export" } });
    // The model proposed a reference column; the operator says there is none.
    fireEvent.change(control(/Reference \/ UTR column/i), { target: { value: "" } });
    fireEvent.change(control(/Inactive-side behaviour/i), { target: { value: "empty_only" } });
    acknowledgeDateFormat();
    fireEvent.click(saveButton());

    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).endsWith("/api/bank-mappings"));
      expect(call).toBeDefined();
      const body = call![1]!.body as FormData;
      const mapping = JSON.parse(body.get("mapping") as string);
      expect(mapping.name).toBe("Client XYZ Bank Export");
      expect(mapping.reference_id_column).toBeNull();
      expect(mapping.money_columns.inactive_side_marker).toBe("empty_only");
      // And the bank file travels with it, so the server validates against
      // the file rather than against the browser's account of it.
      expect(body.get("bank_file")).toBeInstanceOf(File);
    });
  });

  it("records the proposal's provenance without treating it as authority", async () => {
    const mock = stubFetch();
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Named" } });
    acknowledgeDateFormat();
    fireEvent.click(saveButton());
    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).endsWith("/api/bank-mappings"));
      const mapping = JSON.parse((call![1]!.body as FormData).get("mapping") as string);
      expect(mapping.llm_proposal.provider).toBe("openrouter");
      expect(mapping.llm_proposal.model).toBe("some-model");
      // No provenance claim is made client-side; the server assigns it.
      expect(mapping.provenance).toBeUndefined();
    });
  });
});

describe("the operator must name and confirm the mapping", () => {
  it("cannot save without a name", async () => {
    await openEditor();
    acknowledgeDateFormat();
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText(/Still needed: a mapping name/i)).toBeInTheDocument();

    fireEvent.change(nameInput(), { target: { value: "Finance Team CSV" } });
    await waitFor(() => expect(saveButton()).not.toBeDisabled());
  });

  it("cannot save while a field the data cannot settle is unanswered", async () => {
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Finance Team CSV" } });
    expect(screen.getByText(/This file cannot tell FinRecon which reading is right/i)).toBeInTheDocument();
    expect(saveButton()).toBeDisabled();
    expect(screen.getByText(/Confirm the flagged field above to continue/i)).toBeInTheDocument();

    acknowledgeDateFormat();
    await waitFor(() => expect(saveButton()).not.toBeDisabled());
  });

  it("does not let a proposal alone enable reconciliation", async () => {
    await openEditor();
    // A complete, valid proposal is on screen and the run is still blocked.
    expect(runButton()).toBeDisabled();
  });

  it("enables reconciliation only after the mapping is saved", async () => {
    const mock = stubFetch();
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Client XYZ Bank Export" } });
    acknowledgeDateFormat();
    fireEvent.click(saveButton());

    expect(await screen.findByText(/Mapping saved — reconciliation can run/i)).toBeInTheDocument();
    expect(screen.getByText("HDFC Current Account")).toBeInTheDocument();
    await waitFor(() => expect(runButton()).not.toBeDisabled());

    fireEvent.click(runButton());
    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).includes("/api/reconciliation/run"));
      const body = call![1]!.body as FormData;
      // By saved mapping id, which the server re-verifies against the bytes.
      expect(body.get("saved_mapping_id")).toBe("bankmap_abc123");
      expect(body.get("bank_profile")).toBeNull();
      expect(body.get("built_in_profile_id")).toBeNull();
    });
  });

  it("keeps the editor open and shows the server's field errors on rejection", async () => {
    stubFetch({
      saveStatus: 422,
      saveBody: {
        detail: {
          code: "invalid_bank_mapping",
          message: "This mapping does not fit the uploaded statement.",
          validation: {
            ok: false,
            errors: [{ field: "narration_column", code: "unknown_column", message: "'Nope' is not a column in this statement." }],
            warnings: [], fields_requiring_human_choice: [], date_format: null,
          },
        },
      },
    });
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Rejected" } });
    acknowledgeDateFormat();
    fireEvent.click(saveButton());

    expect(await screen.findByText(/does not fit the uploaded statement/i)).toBeInTheDocument();
    // Twice on purpose: once in the summary of what is wrong, and once
    // attached to the control the operator has to change.
    expect(screen.getAllByText(/'Nope' is not a column in this statement/i)).toHaveLength(2);
    const narration = control(/Narration column/i);
    expect(narration.closest("label")).toHaveTextContent(/'Nope' is not a column/i);
    // Still editable, and still not runnable.
    expect(narration).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });
});

describe("provider failure leaves a working manual editor", () => {
  it("says the proposal failed and still offers every control", async () => {
    stubFetch({ proposal: FAILED_PROPOSAL });
    await openEditor();
    expect(screen.getByText(/Could not propose a mapping automatically/i)).toBeInTheDocument();
    expect(screen.getByText(/Map the columns yourself below — everything still works/i)).toBeInTheDocument();

    // Empty, because nothing was proposed -- and fully usable.
    expect(control(/Value date column/i).value).toBe("");
    expect(Array.from(control(/Value date column/i).options).map((o) => o.value)).toEqual(["", ...HEADERS]);
    expect(Array.from(control(/Date format/i).options).length).toBe(DATE_FORMATS.length + 1);
    // No suggestion banner, because there is no suggestion.
    expect(screen.queryByText(/AI suggested a mapping/i)).not.toBeInTheDocument();
  });

  it("lets the operator complete and save a mapping with no proposal at all", async () => {
    const mock = stubFetch({ proposal: FAILED_PROPOSAL });
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "My Settlement File" } });
    fireEvent.change(control(/Value date column/i), { target: { value: "Posted On" } });
    fireEvent.change(control(/Date format/i), { target: { value: "%d/%m/%Y" } });
    fireEvent.change(control(/Narration column/i), { target: { value: "Particulars" } });
    fireEvent.change(control(/Debit column/i), { target: { value: "Withdrawal Amt" } });
    fireEvent.change(control(/Credit column/i), { target: { value: "Deposit Amt" } });

    await waitFor(() => expect(saveButton()).not.toBeDisabled());
    fireEvent.click(saveButton());
    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).endsWith("/api/bank-mappings"));
      const mapping = JSON.parse((call![1]!.body as FormData).get("mapping") as string);
      expect(mapping.name).toBe("My Settlement File");
      expect(mapping.value_date_column).toBe("Posted On");
      // No model was involved, and the mapping says so by omission.
      expect(mapping.llm_proposal).toBeUndefined();
    });
  });

  it("does not enable reconciliation on a failed proposal", async () => {
    stubFetch({ proposal: FAILED_PROPOSAL });
    await openEditor();
    expect(runButton()).toBeDisabled();
  });
});

describe("a recognized saved mapping is reused without a model", () => {
  it("shows the mapping's name and version and skips the proposal entirely", async () => {
    const mock = stubFetch({ inspection: RECOGNIZED });
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByText(/Mapping recognized/i)).toBeInTheDocument();
    expect(screen.getByText("HDFC Current Account")).toBeInTheDocument();
    expect(screen.getByText(/version 1 · human-confirmed/i)).toBeInTheDocument();
    expect(screen.getByText(/no AI proposal needed/i)).toBeInTheDocument();
    expect(mock.mock.calls.some(([url]) => String(url).includes("/propose"))).toBe(false);
    await waitFor(() => expect(runButton()).not.toBeDisabled());
  });

  it("runs by saved mapping id", async () => {
    const mock = stubFetch({ inspection: RECOGNIZED });
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());
    await waitFor(() => expect(runButton()).not.toBeDisabled());
    fireEvent.click(runButton());
    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).includes("/api/reconciliation/run"));
      expect((call![1]!.body as FormData).get("saved_mapping_id")).toBe("bankmap_abc123");
    });
  });

  it("blocks a silent run when two saved mappings match", async () => {
    stubFetch({ inspection: AMBIGUOUS_SAVED });
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());

    expect(await screen.findByText(/Multiple known bank formats match/i)).toBeInTheDocument();
    expect(screen.getByText("Mapping A")).toBeInTheDocument();
    expect(screen.getByText("Mapping B")).toBeInTheDocument();
    expect(screen.getByText(/will not choose between them/i)).toBeInTheDocument();
    expect(runButton()).toBeDisabled();
  });
});

describe("editing a saved mapping creates a new version", () => {
  async function openEditFlow() {
    const mock = stubFetch({
      inspection: RECOGNIZED,
      proposal: { ...PROPOSAL, schema_status: "matched", proposal: null, validation: null },
      saveBody: { saved: { ...SAVED, version: 2 }, validation: PROPOSAL.validation, created_version: 2 },
    });
    renderRun();
    upload(/Razorpay recon file/i, razorpayFile());
    upload(/Bank statement/i, bankFile());
    await screen.findByText(/Mapping recognized/i);
    fireEvent.click(screen.getByRole("button", { name: /Change/i }));
    await screen.findByText(/Edit saved mapping/i);
    return mock;
  }

  it("prefills from what the mapping says, not from a fresh suggestion", async () => {
    await openEditFlow();
    expect(control(/Value date column/i).value).toBe("Posted On");
    expect(control(/Narration column/i).value).toBe("Particulars");
    expect(control(/Inactive-side behaviour/i).value).toBe("empty_or_zero");
    expect(nameInput()).toHaveValue("HDFC Current Account");
    expect(screen.queryByText(/AI suggested a mapping/i)).not.toBeInTheDocument();
  });

  it("explains that the previous version is preserved", async () => {
    await openEditFlow();
    expect(screen.getByText(/Saving creates a new version/i)).toBeInTheDocument();
    expect(screen.getByText(/batches that used it remain traceable/i)).toBeInTheDocument();
  });

  it("posts to the versions endpoint and reports the new version", async () => {
    const mock = await openEditFlow();
    fireEvent.change(control(/Reference \/ UTR column/i), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Save new version & continue/i }));

    await waitFor(() => {
      const call = mock.mock.calls.find(([url]) => String(url).includes("/versions"));
      expect(call).toBeDefined();
      expect(String(call![0])).toBe("/api/bank-mappings/bankmap_abc123/versions");
      const mapping = JSON.parse((call![1]!.body as FormData).get("mapping") as string);
      expect(mapping.reference_id_column).toBeNull();
    });
    expect(await screen.findByText(/version 2 · human-confirmed/i)).toBeInTheDocument();
  });
});

describe("existing paths are unaffected", () => {
  it("still offers the manual bank profile upload for an unknown schema", async () => {
    renderRun();
    upload(/Bank statement/i, bankFile());
    await screen.findByText(/We don't recognize this bank format/i);
    const advanced = screen.getByText(/Advanced · Manual bank profile/i);
    expect(advanced).toBeInTheDocument();
    expect(within(advanced.closest("details")!).getByText(/Bank profile \/ config/i)).toBeInTheDocument();
  });

  it("hides the manual upload once a mapping has been saved", async () => {
    stubFetch();
    await openEditor();
    fireEvent.change(nameInput(), { target: { value: "Named" } });
    acknowledgeDateFormat();
    fireEvent.click(saveButton());
    await screen.findByText(/Mapping saved — reconciliation can run/i);
    expect(screen.queryByText(/Advanced · Manual bank profile/i)).not.toBeInTheDocument();
  });
});
