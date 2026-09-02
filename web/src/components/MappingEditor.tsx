import { AlertTriangle, Check, Info, Save, Sparkles } from "lucide-react";
import { useMemo } from "react";
import type {
  MappingDraft,
  MappingProposalView,
  MappingValidationView,
  MoneyKind,
} from "../types";
import { Button } from "./ui";

/**
 * The human review surface for a column mapping.
 *
 * Three properties this component exists to guarantee, and which its tests
 * assert directly:
 *
 * 1. **Every proposed value is editable.** There is no read-only rendering of
 *    a suggestion anywhere below. A proposal pre-fills the controls and then
 *    has no further standing; what gets submitted is whatever the controls
 *    say at the moment of saving.
 * 2. **Columns are chosen, not typed.** Every column control is a `<select>`
 *    populated from the uploaded file's actual header row, so a mapping
 *    cannot name a column that does not exist -- and the operator is not
 *    asked to retype strings they can see on screen.
 * 3. **An unsettleable field must be answered.** Where the server reports
 *    that the sample cannot distinguish two readings (day-first vs
 *    month-first, overwhelmingly), the field is flagged and Save stays
 *    disabled until the operator explicitly acknowledges it. The server
 *    enforces the same rule independently; this is the courteous half.
 */

export const EMPTY_DRAFT: MappingDraft = {
  name: "",
  value_date_column: "",
  value_date_format: "",
  narration_column: "",
  reference_id_column: "",
  money_kind: "debit_credit",
  debit_column: "",
  credit_column: "",
  inactive_side_marker: "empty_only",
  amount_column: "",
  direction_column: "",
  credit_values: "",
  debit_values: "",
};

/** Pre-fill the editor from a proposal, leaving every field editable. */
export function draftFromProposal(proposal: MappingProposalView, name = ""): MappingDraft {
  const { mapping } = proposal;
  return {
    ...EMPTY_DRAFT,
    name,
    value_date_column: mapping.value_date_column,
    value_date_format: mapping.value_date_format,
    narration_column: mapping.narration_column,
    reference_id_column: mapping.reference_id_column ?? "",
    money_kind: mapping.money.kind,
    debit_column: mapping.money.debit_column ?? "",
    credit_column: mapping.money.credit_column ?? "",
    inactive_side_marker: mapping.money.inactive_side_marker ?? "empty_only",
    amount_column: mapping.money.amount_column ?? "",
    direction_column: mapping.money.direction_column ?? "",
    credit_values: (mapping.money.credit_values ?? []).join(", "),
    debit_values: (mapping.money.debit_values ?? []).join(", "),
  };
}

/**
 * Pre-fill the editor from a mapping that already exists.
 *
 * Used by the Change/Edit flow, which deliberately starts from what the
 * mapping *says* rather than from a fresh proposal: the operator asked to
 * change a mapping they already reviewed, not to have it re-guessed.
 */
export function draftFromSavedMapping(name: string, profile: Record<string, unknown>): MappingDraft {
  const money = (profile.money_columns ?? {}) as Record<string, unknown>;
  const text = (value: unknown) => typeof value === "string" ? value : "";
  const list = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").join(", ") : "";
  return {
    ...EMPTY_DRAFT,
    name,
    value_date_column: text(profile.value_date_column),
    value_date_format: text(profile.value_date_format),
    narration_column: text(profile.narration_column),
    reference_id_column: text(profile.reference_id_column),
    money_kind: money.kind === "amount_direction" ? "amount_direction" : "debit_credit",
    debit_column: text(money.debit_column),
    credit_column: text(money.credit_column),
    inactive_side_marker: money.inactive_side_marker === "empty_or_zero" ? "empty_or_zero" : "empty_only",
    amount_column: text(money.amount_column),
    direction_column: text(money.direction_column),
    credit_values: list(money.credit_values),
    debit_values: list(money.debit_values),
  };
}

/** The request body the confirmation endpoint expects, built from the draft. */
export function draftToRequest(draft: MappingDraft, confirmedFields: string[], extra: {
  signature?: string;
  llmProposal?: Record<string, unknown> | null;
  includeName?: boolean;
} = {}) {
  const list = (raw: string) => raw.split(",").map((part) => part.trim()).filter(Boolean);
  const money = draft.money_kind === "debit_credit"
    ? {
      kind: "debit_credit" as const,
      debit_column: draft.debit_column,
      credit_column: draft.credit_column,
      inactive_side_marker: draft.inactive_side_marker,
    }
    : {
      kind: "amount_direction" as const,
      amount_column: draft.amount_column,
      direction_column: draft.direction_column,
      credit_values: list(draft.credit_values),
      debit_values: list(draft.debit_values),
    };
  return {
    ...(extra.includeName === false ? {} : { name: draft.name.trim() }),
    value_date_column: draft.value_date_column,
    value_date_format: draft.value_date_format,
    narration_column: draft.narration_column,
    // "" is the editor's representation of "this statement has no reference
    // column"; the wire representation is null, and the two must not be
    // confused -- an empty string would be read as a column named "".
    reference_id_column: draft.reference_id_column || null,
    money_columns: money,
    confirmed_fields: confirmedFields,
    ...(extra.signature ? { expected_signature: extra.signature } : {}),
    ...(extra.llmProposal ? { llm_proposal: extra.llmProposal } : {}),
  };
}

/** Fields still missing a value, so Save can say what is outstanding. */
export function draftGaps(draft: MappingDraft, requireName: boolean): string[] {
  const gaps: string[] = [];
  if (requireName && !draft.name.trim()) gaps.push("a mapping name");
  if (!draft.value_date_column) gaps.push("the value date column");
  if (!draft.value_date_format) gaps.push("the date format");
  if (!draft.narration_column) gaps.push("the narration column");
  if (draft.money_kind === "debit_credit") {
    if (!draft.debit_column) gaps.push("the debit column");
    if (!draft.credit_column) gaps.push("the credit column");
  } else {
    if (!draft.amount_column) gaps.push("the amount column");
    if (!draft.direction_column) gaps.push("the direction column");
    if (!draft.credit_values.trim()) gaps.push("the credit marker values");
    if (!draft.debit_values.trim()) gaps.push("the debit marker values");
  }
  return gaps;
}

function issuesFor(validation: MappingValidationView | null, field: string) {
  if (!validation) return [];
  return [...validation.errors, ...validation.warnings].filter((issue) => issue.field === field);
}

function ColumnSelect({ label, hint, headers, value, onChange, allowNone, flagged, issues }: {
  label: string;
  hint?: string;
  headers: string[];
  value: string;
  onChange: (value: string) => void;
  allowNone?: boolean;
  flagged?: boolean;
  issues?: { code: string; message: string }[];
}) {
  return <label className={`mapping-field ${flagged ? "mapping-field-flagged" : ""}`}>
    <span className="mapping-field-label">{label}{hint && <small>{hint}</small>}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{allowNone ? "None — this statement has no such column" : "Choose a column…"}</option>
      {headers.map((header) => <option key={header} value={header}>{header}</option>)}
    </select>
    {issues?.map((issue) => <small key={issue.code} className="mapping-issue">{issue.message}</small>)}
  </label>;
}

export function SamplePreview({ headers, rows }: { headers: string[]; rows: string[][] }) {
  if (!headers.length) return null;
  return <details className="mapping-sample" open>
    <summary>Statement preview — first {rows.length} row{rows.length === 1 ? "" : "s"}</summary>
    <div className="mapping-sample-scroll">
      <table>
        <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell || <span className="cell-empty">empty</span>}</td>)}</tr>)}</tbody>
      </table>
    </div>
  </details>;
}

export default function MappingEditor({
  headers, dateFormats, draft, onChange, validation, proposal, requireName = true,
  acknowledged, onAcknowledge, onSave, saving, saveLabel = "Save mapping & continue", error,
}: {
  headers: string[];
  dateFormats: { value: string; label: string }[];
  draft: MappingDraft;
  onChange: (draft: MappingDraft) => void;
  validation: MappingValidationView | null;
  proposal: MappingProposalView | null;
  requireName?: boolean;
  acknowledged: string[];
  onAcknowledge: (field: string, on: boolean) => void;
  onSave: () => void;
  saving?: boolean;
  saveLabel?: string;
  error?: string | null;
}) {
  const set = <K extends keyof MappingDraft>(key: K, value: MappingDraft[K]) => onChange({ ...draft, [key]: value });
  const needsChoice = validation?.fields_requiring_human_choice ?? [];
  const gaps = useMemo(() => draftGaps(draft, requireName), [draft, requireName]);
  const unanswered = needsChoice.filter((field) => !acknowledged.includes(field));
  const blockingErrors = validation?.errors ?? [];
  const canSave = gaps.length === 0 && unanswered.length === 0 && !saving;

  return <div className="mapping-editor">
    {proposal && <div className="mapping-proposal-note" role="status">
      <Sparkles size={15} />
      <div>
        <strong>AI suggested a mapping — review every field before saving</strong>
        <small>
          Suggested by {proposal.provider ?? "a model"}{proposal.model ? ` · ${proposal.model}` : ""}.
          A suggestion is not a decision: nothing is saved or reconciled until you confirm below.
        </small>
      </div>
    </div>}

    {proposal && proposal.uncertainties.length > 0 && <div className="mapping-uncertainties" role="status">
      <Info size={15} />
      <div><strong>The model flagged what it could not determine</strong>
        <ul>{proposal.uncertainties.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </div>
    </div>}

    {requireName && <label className="mapping-field">
      <span className="mapping-field-label">Mapping name<small>Your own label — it does not have to be a bank's name</small></span>
      <input
        value={draft.name}
        onChange={(e) => set("name", e.target.value)}
        placeholder="e.g. HDFC Current Account, Client XYZ Bank Export"
      />
    </label>}

    <div className="mapping-grid">
      <ColumnSelect
        label="Value date column" headers={headers} value={draft.value_date_column}
        onChange={(value) => set("value_date_column", value)}
        issues={issuesFor(validation, "value_date_column")}
      />

      <label className={`mapping-field ${needsChoice.includes("value_date_format") ? "mapping-field-flagged" : ""}`}>
        <span className="mapping-field-label">Date format<small>Matched exactly; never re-attempted under another format</small></span>
        <select value={draft.value_date_format} onChange={(e) => set("value_date_format", e.target.value)}>
          <option value="">Choose a format…</option>
          {dateFormats.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
        {issuesFor(validation, "value_date_format").map((issue) => <small key={issue.code} className="mapping-issue">{issue.message}</small>)}
      </label>

      <ColumnSelect
        label="Narration column" hint="The bank's free-text description"
        headers={headers} value={draft.narration_column}
        onChange={(value) => set("narration_column", value)}
        issues={issuesFor(validation, "narration_column")}
      />

      <ColumnSelect
        label="Reference / UTR column" hint="Optional" allowNone
        headers={headers} value={draft.reference_id_column}
        onChange={(value) => set("reference_id_column", value)}
        issues={issuesFor(validation, "reference_id_column")}
      />
    </div>

    <fieldset className="mapping-money">
      <legend>Money model</legend>
      <div className="mapping-money-kinds">
        {([["debit_credit", "Two amount columns", "Separate debit and credit columns"],
          ["amount_direction", "Amount + direction", "One amount column plus a DR/CR marker column"]] as [MoneyKind, string, string][])
          .map(([kind, title, detail]) => <button
            key={kind} type="button"
            className={draft.money_kind === kind ? "selected" : ""}
            onClick={() => set("money_kind", kind)}
          ><strong>{title}</strong><span>{detail}</span></button>)}
      </div>

      {draft.money_kind === "debit_credit" ? <div className="mapping-grid">
        <ColumnSelect label="Debit column" headers={headers} value={draft.debit_column}
          onChange={(value) => set("debit_column", value)} issues={issuesFor(validation, "debit_column")} />
        <ColumnSelect label="Credit column" headers={headers} value={draft.credit_column}
          onChange={(value) => set("credit_column", value)} issues={issuesFor(validation, "credit_column")} />
        <label className="mapping-field">
          <span className="mapping-field-label">Inactive-side behaviour<small>What the side a row does not use looks like</small></span>
          <select value={draft.inactive_side_marker} onChange={(e) => set("inactive_side_marker", e.target.value as MappingDraft["inactive_side_marker"])}>
            <option value="empty_only">Empty only — the unused column is blank</option>
            <option value="empty_or_zero">Empty or zero — the unused column is zero-filled</option>
          </select>
          {issuesFor(validation, "inactive_side_marker").map((issue) => <small key={issue.code} className="mapping-issue">{issue.message}</small>)}
        </label>
      </div> : <div className="mapping-grid">
        <ColumnSelect label="Amount column" headers={headers} value={draft.amount_column}
          onChange={(value) => set("amount_column", value)} issues={issuesFor(validation, "amount_column")} />
        <ColumnSelect label="Direction column" headers={headers} value={draft.direction_column}
          onChange={(value) => set("direction_column", value)} issues={issuesFor(validation, "direction_column")} />
        <label className="mapping-field">
          <span className="mapping-field-label">Credit marker values<small>Comma-separated, matched exactly</small></span>
          <input value={draft.credit_values} onChange={(e) => set("credit_values", e.target.value)} placeholder="CR, C" />
          {issuesFor(validation, "credit_values").map((issue) => <small key={issue.code} className="mapping-issue">{issue.message}</small>)}
        </label>
        <label className="mapping-field">
          <span className="mapping-field-label">Debit marker values<small>Comma-separated, matched exactly</small></span>
          <input value={draft.debit_values} onChange={(e) => set("debit_values", e.target.value)} placeholder="DR, D" />
        </label>
      </div>}
      {issuesFor(validation, "money_columns").map((issue) => <small key={issue.code} className="mapping-issue">{issue.message}</small>)}
    </fieldset>

    {needsChoice.length > 0 && <div className="mapping-ack" role="alert">
      <AlertTriangle size={15} />
      <div>
        <strong>This file cannot tell FinRecon which reading is right</strong>
        <small>These fields are your decision. FinRecon will not accept a default, and neither will the server.</small>
        {needsChoice.map((field) => <label key={field} className="mapping-ack-check">
          <input
            type="checkbox"
            checked={acknowledged.includes(field)}
            onChange={(e) => onAcknowledge(field, e.target.checked)}
          />
          <span>I have confirmed <code>{field}</code> is correct for this bank</span>
        </label>)}
      </div>
    </div>}

    {blockingErrors.length > 0 && <div className="mapping-errors" role="alert">
      <AlertTriangle size={15} />
      <div><strong>This mapping does not fit the statement</strong>
        <ul>{blockingErrors.map((issue) => <li key={`${issue.field}:${issue.code}`}>{issue.message}</li>)}</ul>
      </div>
    </div>}

    {proposal && <details className="mapping-reasoning">
      <summary>Why the model suggested this</summary>
      <p className="mapping-reasoning-note">Explanatory text only. It is not evidence, and no part of FinRecon reads it.</p>
      <dl>{Object.entries(proposal.reasoning_summary).map(([field, text]) => <div key={field}><dt>{field.replace(/_/g, " ")}</dt><dd>{text}</dd></div>)}</dl>
    </details>}

    {error && <div className="mapping-errors" role="alert">{error}</div>}

    <div className="mapping-actions">
      <Button disabled={!canSave} onClick={onSave}>
        {saving ? <>Saving…</> : <><Save size={15} /> {saveLabel}</>}
      </Button>
      {gaps.length > 0 && <small className="mapping-gap-note">Still needed: {gaps.join(", ")}.</small>}
      {gaps.length === 0 && unanswered.length > 0 && <small className="mapping-gap-note">Confirm the flagged field{unanswered.length === 1 ? "" : "s"} above to continue.</small>}
      {canSave && <small className="mapping-gap-note"><Check size={13} /> Saving makes this mapping authoritative and reusable.</small>}
    </div>
  </div>;
}
