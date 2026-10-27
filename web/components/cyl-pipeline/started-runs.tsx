"use client";

/**
 * Runs started from the current page, handed from a run action to the
 * experiment page's runs panel (spec: "Runs triggered from this page SHALL be
 * added from the trigger response"). The panel shows the run at once, from
 * the response, without a query. Outside a provider, announcing is a no-op.
 */

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { TriggerTarget } from "@/lib/cyl-pipeline/trigger-target";

export interface StartedRun {
  pipeline_run_id: number;
  scan_count: number;
  target: TriggerTarget;
  requested_by: string | null;
  /** When the trigger answered, as an ISO timestamp. */
  started_at: string;
}

type Listener = (run: StartedRun) => void;

interface StartedRunsBus {
  announce: Listener;
  listen: (listener: Listener) => () => void;
}

const StartedRunsContext = createContext<StartedRunsBus | null>(null);

export function StartedRunsProvider({ children }: { children: ReactNode }) {
  const [bus] = useState<StartedRunsBus>(() => {
    const listeners = new Set<Listener>();
    return {
      announce: (run) => listeners.forEach((l) => l(run)),
      listen: (listener) => {
        listeners.add(listener);
        return () => void listeners.delete(listener);
      },
    };
  });
  return <StartedRunsContext.Provider value={bus}>{children}</StartedRunsContext.Provider>;
}

const noop: Listener = () => {};

export function useAnnounceStartedRun(): Listener {
  return useContext(StartedRunsContext)?.announce ?? noop;
}

/** Call `listener` for each run announced while mounted. */
export function useStartedRuns(listener: Listener): void {
  const bus = useContext(StartedRunsContext);
  const latest = useRef(listener);
  latest.current = listener;
  useEffect(() => bus?.listen((run) => latest.current(run)), [bus]);
}
