export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: { code?: string; message?: string } | string } | null;
    const detail = body?.detail;
    const code = typeof detail === "object" ? detail?.code : undefined;
    const message = typeof detail === "object" ? detail?.message : detail;
    throw new ApiError(code ?? "backend_failure", message ?? "The FinRecon service could not complete the request.", response.status);
  }
  return response.json() as Promise<T>;
}

export const money = (paise: number) => new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", minimumFractionDigits: 2,
}).format(paise / 100);

export const compactMoney = (paise: number) => new Intl.NumberFormat("en-IN", {
  style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1,
}).format(paise / 100);

export const shortId = (value: string, size = 20) => value.length > size ? `${value.slice(0, size)}…` : value;

export function query(batchId: string | null, extra: Record<string, string | boolean | null> = {}) {
  const params = new URLSearchParams();
  if (batchId) params.set("batch_id", batchId);
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== null && value !== "" && value !== false) params.set(key, String(value));
  });
  const text = params.toString();
  return text ? `?${text}` : "";
}
