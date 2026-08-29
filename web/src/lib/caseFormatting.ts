export type JsonRecord = Record<string, unknown>;

export function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asStringArray(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}

export function candidateLabel(index: number): string {
  return index >= 0 && index < 26
    ? `Candidate ${String.fromCharCode(65 + index)}`
    : `Candidate ${index + 1}`;
}

export function friendlyCaseLabel(bankRecordId?: string, caseId?: string): string {
  const source = bankRecordId || caseId || "Unidentified";
  const finalSegment = source.split(":").filter(Boolean).at(-1) || source;
  return `Case ${finalSegment}`;
}

export function humanizeMachineText(value?: string | null): string {
  if (!value) return "Not reported";
  const normalized = value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\s+/g, " ")
    .trim();
  return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

export function humanizeRelation(value: string): string {
  const labels: Record<string, string> = {
    exact_reference: "an exact reference match",
    exact_utr: "an exact UTR match",
    mask_consistent: "a mask-consistent reference relation",
    suffix_match: "a matching reference suffix",
    prefix_match: "a matching reference prefix",
  };
  return labels[value] || humanizeMachineText(value).toLowerCase();
}

export function formatCaseDate(value?: string | null): string {
  if (!value) return "Not reported";
  const date = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}
