"use client";

/**
 * One run's drill-down (spec: "Per-run drill-down at
 * /app/cyl-pipeline-runs/[runId]"). The server renders the run row; the
 * per-scan rows load on the first SUBSCRIBED, which also closes the gap
 * before the subscription. If the channel fails first, the view says so and
 * offers Refresh.
 *
 * - Everything a snapshot reads (rows, scan metadata, latest sources,
 *   experiments) lives in the synced view, so a superseded fetch can't leave
 *   stale pieces behind and buffered events replay onto all of it.
 * - The header's counts come from the held scan rows, so it moves with each
 *   scan event; the list's run row can trail it by one sweep.
 * - Links come only from the requested scans (design D6): "Scan images" per
 *   scan, and traits links for the run's most common (wave, age) pairs, for
 *   the experiments cyl_pipeline_run_experiments shows. cyl_scans_extended
 *   runs with its owner's rights, so its experiment names aren't shown.
 * - "Current in trait views" compares a row's `source_id` with the scan's
 *   latest source as last read. A row whose source changes live shows
 *   "unknown" until the next snapshot: it can't be inferred, because an
 *   empty envelope marks a row written without raising the latest source.
 * - A row that turns failed live gets one metadata and latest-source lookup,
 *   for its likely cause and the bloom#900 note.
 */

import Link from "next/link";
import { useMemo, useRef } from "react";
import { RunState } from "@/components/cyl-pipeline/RunState";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { formatElapsed } from "@/lib/cyl-pipeline/elapsed";
import { isNoOpCandidate, likelyCause, NO_OP_NOTE } from "@/lib/cyl-pipeline/failure-hints";
import {
  fetchLatestSources,
  fetchRun,
  fetchRunExperiments,
  fetchRunScans,
  fetchScanMeta,
  type RunExperiment,
} from "@/lib/cyl-pipeline/queries";
import {
  applyDetailChange,
  countsFromScanRows,
  detailFromSnapshot,
  RUN_SCANS_TABLE,
  RUNS_TABLE,
  type Change,
  type RunDetail,
  type RunRow,
  type RunScanRow,
} from "@/lib/cyl-pipeline/realtime-reducer";
import { scanStatusLabel } from "@/lib/cyl-pipeline/run-display";
import { scanImagesHref, traitsLinks } from "@/lib/cyl-pipeline/run-links";
import type { ScanMeta } from "@/lib/cyl-pipeline/scan-meta";
import { compareTimestamps } from "@/lib/cyl-pipeline/timestamps";
import { useLiveSync, type LiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { RunScansTable, type ScanTableRow, type StatusFilter } from "./RunScansTable";

interface DetailView {
  detail: RunDetail;
  /** The scan rows have been read at least once. */
  loaded: boolean;
  meta: Map<number, ScanMeta>;
  /** Each scan's latest source as last read; null when not read (or unreadable). */
  latest: Map<number, number | null> | null;
  /** Scans whose row's source changed since `latest` was read. */
  changed: Set<number>;
  /** From the security-invoker view; null when it couldn't be read. */
  experiments: RunExperiment[] | null;
  /** Why scan details or latest sources are missing, if they are. */
  detailsError: string | null;
}

export const TIMING_NOTE =
  "Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.";

function paramsText(params: unknown): string {
  if (params && typeof params === "object" && !Array.isArray(params) && Object.keys(params).length > 0) {
    const pairs = Object.entries(params as Record<string, unknown>)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(", ");
    // The trigger records params but doesn't apply them yet (bloom#897).
    return `Requested overrides (not applied yet, bloom#897): ${pairs}`;
  }
  return "Parameters: from each scan's metadata (no overrides)";
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

function applyChange(view: DetailView, change: Change<RunRow | RunScanRow>, runId: number): DetailView {
  const detail = applyDetailChange(view.detail, change, runId);
  if (detail === view.detail || change.table !== RUN_SCANS_TABLE) return { ...view, detail };
  const incoming = change.new as Partial<RunScanRow>;
  const held = typeof incoming.id === "number" ? view.detail.scans.get(incoming.id) : undefined;
  if (typeof incoming.scan_id === "number" && "source_id" in incoming && incoming.source_id !== (held?.source_id ?? null)) {
    return { ...view, detail, changed: new Set(view.changed).add(incoming.scan_id) };
  }
  return { ...view, detail };
}

export function RunDetailLive({ initialRun, initialFilter = "all" }: { initialRun: RunRow; initialFilter?: StatusFilter }) {
  const runId = initialRun.id;
  const now = useNow();
  // Rows known to be failed; a row joining this set live gets one lookup.
  const failedSeen = useRef(new Set<number>());

  const lookUpFailed = async (scanId: number) => {
    const client = createClientSupabaseClient();
    try {
      const [m, l] = await Promise.all([fetchScanMeta(client, [scanId]), fetchLatestSources(client, [scanId])]);
      live.update((v) => {
        const latest = v.latest === null ? null : new Map(v.latest);
        if (latest) {
          if (l.has(scanId)) latest.set(scanId, l.get(scanId)!);
          else latest.delete(scanId);
        }
        const changed = new Set(v.changed);
        if (latest) changed.delete(scanId);
        return { ...v, meta: new Map([...v.meta, ...m]), latest, changed };
      });
    } catch {
      // The hint is optional; the row still shows its status and error.
    }
  };

  const live: LiveSync<DetailView> = useLiveSync<DetailView>({
    topic: `cyl-pipeline-run-${runId}`,
    bindings: [
      { table: RUNS_TABLE, event: "UPDATE", filter: `id=eq.${runId}` },
      { table: RUN_SCANS_TABLE, event: "INSERT", filter: `run_id=eq.${runId}` },
      { table: RUN_SCANS_TABLE, event: "UPDATE", filter: `run_id=eq.${runId}` },
    ],
    initial: {
      detail: detailFromSnapshot(initialRun, []),
      loaded: false,
      meta: new Map(),
      latest: null,
      changed: new Set(),
      experiments: null,
      detailsError: null,
    },
    snapshot: async (): Promise<DetailView> => {
      const client = createClientSupabaseClient();
      const held = live.current();
      // The rows are the page; they must load or the snapshot fails.
      const [run, scans] = await Promise.all([fetchRun(client, runId), fetchRunScans(client, runId)]);
      const ids = scans.map((s) => s.scan_id);
      const unknown = ids.filter((id) => !held.meta.has(id));
      // Details are columns and hints: without them the rows still show.
      const [m, l, exps] = await Promise.allSettled([
        fetchScanMeta(client, unknown),
        fetchLatestSources(client, ids),
        fetchRunExperiments(client, [runId]),
      ]);
      const failures = [m, l].filter((r): r is PromiseRejectedResult => r.status === "rejected");
      return {
        detail: detailFromSnapshot(run ?? held.detail.run, scans),
        loaded: true,
        meta: m.status === "fulfilled" ? new Map([...held.meta, ...m.value]) : held.meta,
        latest: l.status === "fulfilled" ? l.value : null,
        changed: new Set(),
        experiments: exps.status === "fulfilled" ? exps.value : held.experiments,
        detailsError: failures.length ? failures.map((f) => message(f.reason)).join("; ") : null,
      };
    },
    apply: (view, change) => applyChange(view, change as Change<RunRow | RunScanRow>, runId),
    onEvent: (change) => {
      if (change.table !== RUN_SCANS_TABLE) return;
      const row = change.new as Partial<RunScanRow>;
      if (row.run_id !== runId || typeof row.id !== "number" || typeof row.scan_id !== "number") return;
      if (row.status === "failed" && !failedSeen.current.has(row.id)) {
        failedSeen.current.add(row.id);
        void lookUpFailed(row.scan_id);
      }
    },
    onSnapshot: (view) => view.detail.scans.forEach((s) => s.status === "failed" && failedSeen.current.add(s.id)),
  });

  const { detail, loaded, meta, latest, changed, experiments, detailsError } = live.view;

  const scanRows = useMemo(() => [...detail.scans.values()].sort((a, b) => a.scan_id - b.scan_id), [detail.scans]);

  const tableRows: ScanTableRow[] = useMemo(
    () =>
      scanRows.map((r) => {
        const m = meta.get(r.scan_id);
        const scanLatest = latest?.get(r.scan_id);
        const failed = r.status === "failed";
        return {
          id: r.id,
          scan_id: r.scan_id,
          status: r.status,
          statusLabel: scanStatusLabel(r.status).label,
          attempts: r.attempts,
          error_message: r.error_message,
          argo_workflow_name: r.argo_workflow_name,
          source_id: r.source_id,
          updated_at: r.updated_at,
          qr_code: m?.qr_code ?? null,
          wave_number: m?.wave_number ?? null,
          plant_age_days: m?.plant_age_days ?? null,
          current: latest === null || changed.has(r.scan_id) ? null : r.source_id !== null && scanLatest === r.source_id,
          likelyCause: failed ? likelyCause(m) : null,
          noOpNote: failed && latest !== null && isNoOpCandidate(r, scanLatest != null) ? NO_OP_NOTE : null,
          scanHref: scanImagesHref(m),
        };
      }),
    [scanRows, meta, latest, changed],
  );

  const links = useMemo(() => {
    const shown = experiments === null ? null : new Map(experiments.map((e) => [e.experiment_id, e]));
    return traitsLinks(
      scanRows.map((r) => r.scan_id),
      meta,
    )
      .filter((e) => shown === null || shown.has(e.experimentId))
      .map((e) => ({ ...e, experimentName: shown?.get(e.experimentId)?.name ?? null }));
  }, [scanRows, meta, experiments]);

  const tallies = countsFromScanRows(scanRows);
  // Until the rows load, the run row's own counts are all there is.
  const headerRun = loaded ? { ...detail.run, done_count: tallies.done, failed_count: tallies.failed } : detail.run;
  const lastUpdate = scanRows.reduce<string | null>(
    (max, r) => (max === null || compareTimestamps(r.updated_at, max) > 0 ? r.updated_at : max),
    null,
  );

  return (
    <div>
      <section data-testid="run-header" className="mb-6 space-y-2 text-sm">
        <div className="flex items-start justify-between gap-4">
          <RunState run={headerRun} />
          <LiveIndicator state={live.connection} onRefresh={live.refresh} />
        </div>
        <div className="text-stone-600">
          {now !== null && <span>Started {formatElapsed(detail.run.created_at, now)} ago</span>}
          {now !== null && lastUpdate && <span> · Last scan update {formatElapsed(lastUpdate, now)} ago</span>}
        </div>
        <div className="text-stone-600">{paramsText(detail.run.params)}</div>
        {experiments && experiments.length > 0 && (
          <div className="flex flex-wrap gap-x-3">
            <span className="text-stone-500">Experiments:</span>
            {experiments.map((e) =>
              e.species_id == null ? (
                <span key={e.experiment_id}>{e.name ?? `Experiment ${e.experiment_id}`}</span>
              ) : (
                <Link key={e.experiment_id} href={`/app/phenotypes/${e.species_id}/${e.experiment_id}`} className="text-lime-700 hover:underline">
                  {e.name ?? `Experiment ${e.experiment_id}`}
                </Link>
              ),
            )}
          </div>
        )}
        {links.length > 0 && (
          <ul className="space-y-1">
            {links.flatMap((e) =>
              e.links.map((l) => (
                <li key={l.href}>
                  <Link href={l.href} className="text-lime-700 hover:underline">
                    {l.label}
                  </Link>
                  {links.length > 1 && e.experimentName && <span className="text-stone-500"> · {e.experimentName}</span>}
                </li>
              )),
            )}
          </ul>
        )}
        <p className="text-stone-500">{TIMING_NOTE}</p>
      </section>

      {live.error && (
        <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Could not load this run&apos;s scans: {live.error}{" "}
          <button type="button" onClick={live.refresh} className="underline hover:no-underline">
            Retry
          </button>
        </div>
      )}
      {loaded && detailsError && (
        <p className="mb-2 text-sm text-amber-700">
          Scan details unavailable: {detailsError}. QR codes, days and &ldquo;current in trait views&rdquo; may be missing.
        </p>
      )}

      {!loaded ? (
        !live.error &&
        (live.connection === "offline" ? (
          <p className="text-sm text-stone-600">
            Live updates are unavailable, so the scan rows haven&apos;t loaded.{" "}
            <button type="button" onClick={live.refresh} className="text-lime-700 underline hover:no-underline">
              Refresh
            </button>
          </p>
        ) : (
          <p className="text-sm text-stone-500">Loading scan rows…</p>
        ))
      ) : tableRows.length === 0 ? (
        detail.run.scan_count > 0 && (
          <p className="text-sm text-stone-500">
            <span>No scan rows recorded</span>. The trigger may have stopped after creating the run, before recording its scans.
          </p>
        )
      ) : (
        <RunScansTable rows={tableRows} initialFilter={initialFilter} />
      )}
    </div>
  );
}
