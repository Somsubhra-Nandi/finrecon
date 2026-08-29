import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useApi<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { setData(await api<T>(path)); }
    catch (value) { setError(value instanceof Error ? value : new Error("Request failed")); }
    finally { setLoading(false); }
  }, [path]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, loading, reload: load };
}
