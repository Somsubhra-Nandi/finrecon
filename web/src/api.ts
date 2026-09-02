export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    // The error body's own fields, beyond code and message. Some rejections
    // carry structured context a form can act on -- the mapping-confirmation
    // endpoints return the deterministic validation that refused the save,
    // so the editor can point at the offending control instead of showing a
    // bare sentence.
    public detail: Record<string, unknown> = {},
  ) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: Record<string, unknown> | string } | null;
    const detail = body?.detail;
    const structured = (detail && typeof detail === "object" ? detail : {}) as Record<string, unknown>;
    const code = typeof structured.code === "string" ? structured.code : undefined;
    const message = typeof structured.message === "string" ? structured.message : (typeof detail === "string" ? detail : undefined);
    throw new ApiError(code ?? "backend_failure", message ?? "The FinRecon service could not complete the request.", response.status, structured);
  }
  try {
    return await response.json() as T;
  } catch {
    throw new ApiError(
      "invalid_api_response",
      "FinRecon received an invalid response from the server. Check that the backend is running on port 8000.",
      response.status,
    );
  }
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
