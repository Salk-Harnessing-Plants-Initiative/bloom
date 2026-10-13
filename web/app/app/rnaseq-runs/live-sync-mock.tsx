/**
 * A stand-in for useLiveSync in tests: it holds the view in state, applies changes a test
 * sends with `emit`, and never touches Realtime.
 */

import { act, useState } from "react";
import { vi } from "vitest";
import type { LiveSyncOptions } from "@/lib/cyl-pipeline/use-live-sync";

let emitter: ((change: { table: string; eventType: string; new: object }) => void) | null = null;
export const refresh = vi.fn();

export function useLiveSyncMock<V>(options: LiveSyncOptions<V>) {
  const [view, setView] = useState(options.initial);
  emitter = (change) => {
    setView((current) => options.apply(current, change));
    options.onEvent?.(change, view);
  };
  return {
    view,
    connection: "live" as const,
    error: null,
    fetching: false,
    refresh,
    isFetching: () => false,
    update: (fn: (v: V) => V) => setView(fn),
    generation: () => 0,
    current: () => view,
  };
}

/** Sends a Realtime-style row change to the mounted view. */
export function emit(row: object, eventType = "UPDATE") {
  act(() => emitter?.({ table: "rnaseq_runs", eventType, new: row }));
}
