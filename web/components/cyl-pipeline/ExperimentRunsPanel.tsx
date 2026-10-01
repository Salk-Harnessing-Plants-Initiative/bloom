"use client";

/**
 * The experiment page's pipeline runs (spec: "Experiment page shows that
 * experiment's runs"): the 10 most recent runs that include at least one of
 * its scans, from `cyl_pipeline_run_experiments`, kept current by Realtime.
 *
 * Run events can't be filtered by experiment, so an event for a run the panel
 * doesn't hold re-queries membership, debounced by 1 s. The trigger commits
 * the run row before its scan rows, so a "no" is cached only when the event
 * that prompted it was past `queued`: by `submitted` the scan rows exist.
 * A cached "no" costs no further query.
 *
 * A run started from this page is added from the trigger response, with no
 * query: the trigger answers only after inserting the run's scan rows, so it
 * is a member. A zero-scan run has no scan rows and is never one.
 */

import Link from "next/link";
import { useEffect, useRef } from "react";
import { RunState } from "@/components/cyl-pipeline/RunState";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { formatElapsed } from "@/lib/cyl-pipeline/elapsed";
import { fetchExperimentMembers, fetchExperimentRunIds, fetchRunsByIds } from "@/lib/cyl-pipeline/queries";
import {
  addPanelRun,
  applyPanelChange,
  mergeRun,
  RUNS_TABLE,
  type Change,
  type RunRow,
} from "@/lib/cyl-pipeline/realtime-reducer";
import { useLiveSync, type LiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { useStartedRuns, type StartedRun } from "./started-runs";

export const MEMBERSHIP_DEBOUNCE_MS = 1000;

interface PanelView {
  runs: RunRow[];
  loaded: boolean;
}

/** Realtime INSERT/UPDATE payloads carry every column but an unchanged TOASTed one. */
const isWholeRow = (row: Partial<RunRow>): row is RunRow =>
  typeof row.id === "number" && typeof row.created_at === "string" && typeof row.scan_count === "number" && typeof row.status === "string";

/** A run row as the trigger just created it: queued, with no outcomes yet. */
function startedRunRow(run: StartedRun): RunRow {
  const { target } = run;
  return {
    id: run.pipeline_run_id,
    created_at: run.started_at,
    requested_by: run.requested_by,
    target_level: target.target_level,
    target_id: target.target_level === "scan_ids" ? null : target.target_id,
    params: {},
    status: "queued",
    scan_count: run.scan_count,
    done_count: 0,
    failed_count: 0,
    reused_count: 0,
    error_message: null,
    submitted_at: null,
    completed_at: null,
  };
}

/**
 * Add runs the panel doesn't hold yet. A held run is kept as it is: its own
 * events keep it current, while this row may be older (an earlier event, or
 * a read made before a snapshot that is being replayed onto).
 */
function addUnheld(runs: RunRow[], rows: RunRow[]): RunRow[] {
  return rows.reduce((acc, r) => (acc.some((h) => h.id === r.id) ? acc : addPanelRun(acc, r)), runs);
}

export function ExperimentRunsPanel({ experimentId }: { experimentId: number }) {
  const now = useNow();
  // true: touches this experiment; false: doesn't (cached only past queued).
  const members = useRef(new Map<number, boolean>());
  // Unheld runs awaiting a membership answer, with what their events said.
  const pending = useRef(new Map<number, Partial<RunRow>>());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const flush = async (): Promise<void> => {
    timer.current = null;
    const asked = new Map(pending.current);
    pending.current.clear();
    if (asked.size === 0) return;
    const client = createClientSupabaseClient();
    let yes: Set<number>;
    try {
      yes = await fetchExperimentMembers(client, experimentId, [...asked.keys()]);
    } catch {
      return; // The next event for these runs asks again.
    }
    const toRead: number[] = [];
    for (const [id, row] of asked) {
      if (yes.has(id)) {
        members.current.set(id, true);
        if (isWholeRow(row)) live.update((v) => ({ ...v, runs: addUnheld(v.runs, [row]) }));
        else toRead.push(id);
      } else if (row.status !== "queued") {
        members.current.set(id, false);
      }
    }
    if (toRead.length) {
      try {
        const rows = await fetchRunsByIds(client, toRead);
        live.update((v) => ({ ...v, runs: addUnheld(v.runs, rows) }));
      } catch {
        // Shown on the next snapshot.
      }
    }
  };

  const live: LiveSync<PanelView> = useLiveSync<PanelView>({
    topic: `cyl-pipeline-experiment-${experimentId}`,
    bindings: [
      { table: RUNS_TABLE, event: "INSERT" },
      { table: RUNS_TABLE, event: "UPDATE" },
    ],
    initial: { runs: [], loaded: false },
    snapshot: async () => {
      const client = createClientSupabaseClient();
      const ids = await fetchExperimentRunIds(client, experimentId);
      ids.forEach((id) => members.current.set(id, true));
      const rows = await fetchRunsByIds(client, ids);
      return { runs: rows.reduce<RunRow[]>((acc, r) => addPanelRun(acc, r), []), loaded: true };
    },
    apply: (view, change) => {
      const c = change as Change<RunRow>;
      const { runs, held } = applyPanelChange(view.runs, c);
      if (held) return { ...view, runs };
      if (members.current.get(c.new.id ?? -1) === true && isWholeRow(c.new)) {
        return { ...view, runs: addPanelRun(view.runs, c.new) };
      }
      return view;
    },
    onEvent: (change, view) => {
      const row = (change as Change<RunRow>).new;
      const id = row.id;
      if (typeof id !== "number" || members.current.has(id) || view.runs.some((r) => r.id === id)) return;
      pending.current.set(id, mergeRun(pending.current.get(id) as RunRow | undefined, row));
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => void flush(), MEMBERSHIP_DEBOUNCE_MS);
    },
  });

  useStartedRuns((run) => {
    if (run.scan_count === 0) return;
    members.current.set(run.pipeline_run_id, true);
    pending.current.delete(run.pipeline_run_id);
    live.update((v) => ({ ...v, runs: addUnheld(v.runs, [startedRunRow(run)]) }));
  });

  const { runs, loaded } = live.view;

  return (
    <section className="mt-10" aria-labelledby="experiment-runs-heading">
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 id="experiment-runs-heading" className="text-lg">
          Cylinder pipeline runs
        </h2>
        <div className="flex items-center gap-4 text-sm">
          <LiveIndicator state={live.connection} onRefresh={live.refresh} />
          <Link href="/app/cyl-pipeline-runs" className="text-lime-700 hover:underline">
            All cylinder pipeline runs
          </Link>
        </div>
      </div>

      {live.error && (
        <p className="mb-2 text-sm text-stone-500" title={live.error}>
          <span>Runs unavailable</span>{" "}
          <button type="button" onClick={live.refresh} className="text-lime-700 underline hover:no-underline">
            Retry
          </button>
        </p>
      )}
      {!loaded ? (
        !live.error &&
        (live.connection === "offline" ? (
          <p className="text-sm text-stone-600">
            Live updates are unavailable, so this experiment&apos;s runs haven&apos;t loaded.{" "}
            <button type="button" onClick={live.refresh} className="text-lime-700 underline hover:no-underline">
              Refresh
            </button>
          </p>
        ) : (
          <p className="text-sm text-stone-500">Loading runs…</p>
        ))
      ) : runs.length === 0 ? (
        <p className="text-sm text-stone-500">No pipeline runs include this experiment&apos;s scans yet.</p>
      ) : (
        <ul className="divide-y divide-stone-200 text-sm">
          {runs.map((run) => (
            <li key={run.id} data-testid={`panel-run-${run.id}`} className="flex items-start justify-between gap-4 py-2">
              <div>
                <Link href={`/app/cyl-pipeline-runs/${run.id}`} className="font-medium text-lime-700 hover:underline">
                  Run {run.id}
                </Link>
                <div className="text-xs text-stone-500">{now === null ? "" : `requested ${formatElapsed(run.created_at, now)} ago`}</div>
              </div>
              <RunState run={run} failedHref={`/app/cyl-pipeline-runs/${run.id}?status=failed`} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
