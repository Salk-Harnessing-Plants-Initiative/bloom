"use client";

/**
 * The shared runs list, kept current by Realtime (spec: "Shared runs list at
 * /app/cyl-pipeline-runs"). The server renders the first page; the first
 * SUBSCRIBED refetches it to close the gap before the subscription.
 *
 * Experiment names come with each snapshot. A run that arrives live gets no
 * names and costs no query until its first non-`queued` event: the trigger
 * commits the run row before its scan rows, and the view only maps runs to
 * experiments through those rows.
 */

import { useRef, useState } from "react";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { fetchRunExperiments, fetchRuns, type RunExperiment } from "@/lib/cyl-pipeline/queries";
import {
  appendOlder,
  applyListChange,
  listFromSnapshot,
  RUNS_TABLE,
  type Change,
  type RunList,
  type RunRow as Run,
} from "@/lib/cyl-pipeline/realtime-reducer";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { RunRow } from "./RunRow";

export interface RunsListLiveProps {
  initialRuns: Run[];
  initialExperiments: RunExperiment[];
  currentUserId: string | null;
  initialError: string | null;
}

function byRun(experiments: RunExperiment[], into = new Map<number, RunExperiment[]>()) {
  const touched = new Set<number>();
  for (const e of experiments) {
    if (!touched.has(e.run_id)) into.set(e.run_id, []);
    touched.add(e.run_id);
    into.get(e.run_id)!.push(e);
  }
  return into;
}

export function RunsListLive({ initialRuns, initialExperiments, currentUserId, initialError }: RunsListLiveProps) {
  const now = useNow();
  const [mineOnly, setMineOnly] = useState(false);
  const mine = useRef(false);
  const [names, setNames] = useState(() => byRun(initialExperiments));
  // Runs whose names were read, or are being read; each is looked up at most once.
  const looked = useRef(new Set(initialRuns.map((r) => r.id)));
  const [serverError, setServerError] = useState(initialError);
  const [older, setOlder] = useState<{ loading: boolean; error: string | null }>({ loading: false, error: null });

  const accept = (row: Partial<Run>) => !mine.current || (currentUserId !== null && row.requested_by === currentUserId);

  const loadNames = async (ids: number[]) => {
    const fresh = ids.filter((id) => !looked.current.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => looked.current.add(id));
    try {
      const rows = await fetchRunExperiments(createClientSupabaseClient(), fresh);
      setNames((prev) => byRun(rows, new Map(prev)));
    } catch {
      // Names are decoration; a run without them still shows its state. No
      // retry: a live run gets at most one lookup (the next snapshot re-reads).
    }
  };

  const live = useLiveSync<RunList>({
    topic: "cyl-pipeline-runs",
    bindings: [
      { table: RUNS_TABLE, event: "INSERT" },
      { table: RUNS_TABLE, event: "UPDATE" },
    ],
    initial: listFromSnapshot(initialRuns),
    snapshot: async () => {
      const rows = await fetchRuns(createClientSupabaseClient(), { requestedBy: mine.current ? currentUserId : null });
      // A snapshot re-reads names for every run it returns.
      rows.forEach((r) => looked.current.delete(r.id));
      await loadNames(rows.map((r) => r.id));
      return listFromSnapshot(rows);
    },
    apply: (list, change) => applyListChange(list, change as Change<Run>, accept),
    onEvent: (change, list) => {
      const id = (change as Change<Run>).new.id;
      if (typeof id !== "number" || looked.current.has(id)) return;
      const held = list.rows.find((r) => r.id === id);
      if (held && held.status !== "queued") void loadNames([id]);
    },
    onSnapshot: () => setServerError(null),
  });

  const loadOlder = async () => {
    const cursor = live.current().cursor;
    // The store, not the last render: a click can land after a resync started
    // but before the disabled button re-renders.
    if (!cursor || live.isFetching()) return;
    // A page read against one snapshot must not be appended to another: its
    // cursor would jump past rows neither holds, or mix Only-mine filters.
    const started = live.generation();
    setOlder({ loading: true, error: null });
    try {
      const rows = await fetchRuns(createClientSupabaseClient(), { cursor, requestedBy: mine.current ? currentUserId : null });
      await loadNames(rows.map((r) => r.id));
      if (live.generation() === started) live.update((list) => appendOlder(list, rows));
      setOlder({ loading: false, error: null });
    } catch (e) {
      setOlder({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const toggleMine = (checked: boolean) => {
    mine.current = checked;
    setMineOnly(checked);
    live.refresh();
  };

  const error = live.error ?? serverError;
  const { hasOlder } = live.view;
  // Ticking Only mine hides other members' runs at once, not only when the filtered snapshot lands.
  const rows = mineOnly ? live.view.rows.filter(accept) : live.view.rows;

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-4">
        <label className="inline-flex items-center gap-2 text-sm text-stone-700">
          <input type="checkbox" checked={mineOnly} onChange={(e) => toggleMine(e.target.checked)} />
          Only mine
        </label>
        <LiveIndicator state={live.connection} onRefresh={live.refresh} />
      </div>

      {error && (
        <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Could not load pipeline runs: {error}{" "}
          <button type="button" onClick={live.refresh} className="underline hover:no-underline">
            Retry
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        !error && (
          <p className="text-sm text-stone-500">
            {mineOnly ? "You haven't started any pipeline runs" : "No pipeline runs yet"}
          </p>
        )
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Run
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Target
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Experiments
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  State
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <RunRow key={run.id} run={run} experiments={names.get(run.id)} currentUserId={currentUserId} now={now} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasOlder && rows.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => void loadOlder()}
            disabled={older.loading || live.fetching}
            className="rounded-md border border-stone-300 px-3 py-1 text-sm hover:bg-stone-50 disabled:opacity-50"
          >
            {older.loading ? "Loading…" : "Load older runs"}
          </button>
          {older.error && <span className="ml-3 text-sm text-red-700">Could not load older runs: {older.error}</span>}
        </div>
      )}
    </div>
  );
}
