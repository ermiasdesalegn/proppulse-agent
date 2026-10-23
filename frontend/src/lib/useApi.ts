"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
  setData: React.Dispatch<React.SetStateAction<T | null>>;
}

/** Load a backend path, optionally on a timer. Pass null to wait (for example until something is selected). */
export function useApi<T>(path: string | null, refreshMs = 0): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const latest = useRef(0);

  const reload = useCallback(async () => {
    if (path === null) return;
    const ticket = ++latest.current; // ignore a slow, older response arriving after a newer one
    try {
      const result = await api<T>(path);
      if (ticket === latest.current) { setData(result); setError(null); }
    } catch (e) {
      if (ticket === latest.current) setError(errorMessage(e));
    } finally {
      if (ticket === latest.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(null);
    setLoading(path !== null);
    void reload();
    if (!refreshMs || path === null) return;
    const timer = setInterval(() => void reload(), refreshMs);
    return () => clearInterval(timer);
  }, [path, refreshMs, reload]);

  return { data, error, loading, reload, setData };
}
