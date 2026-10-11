"use client";

import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";

/** The current time, refreshed every `everyMs` (default 30 s) so countdowns tick. */
export function useNow(everyMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    const onVisible = () => {
      if (document.visibilityState === "visible") setNow(Date.now());
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [everyMs]);
  return now;
}

/** Loads `path` from the API, and again whenever `reload()` is called, the window regains focus,
 *  or `intervalMs` passes. State is only set from the fetch callbacks, never synchronously. */
export function useLoader<T>(path: string, opts: { intervalMs?: number; enabled?: boolean } = {}) {
  const { intervalMs, enabled = true } = opts;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    api<T>(path)
      .then((result) => {
        if (!alive) return;
        setData(result);
        setError(null);
      })
      .catch((err) => {
        if (alive) setError(errorMessage(err));
      });
    return () => {
      alive = false;
    };
  }, [path, tick, enabled]);

  useEffect(() => {
    if (!enabled) return;
    const bump = () => setTick((t) => t + 1);
    const id = intervalMs ? window.setInterval(bump, intervalMs) : undefined;
    window.addEventListener("focus", bump);
    return () => {
      if (id) window.clearInterval(id);
      window.removeEventListener("focus", bump);
    };
  }, [intervalMs, enabled]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, setData, error, reload };
}
