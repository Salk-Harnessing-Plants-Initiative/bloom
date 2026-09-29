"use client";

import { useEffect, useState } from "react";

/**
 * The current time, for elapsed-time labels only; it drives no fetch. It is
 * null on the server render and first paint, so server and client markup
 * match, then ticks every `intervalMs`.
 */
export function useNow(intervalMs = 30_000): number | null {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
