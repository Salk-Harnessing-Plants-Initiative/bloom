"use client";

/**
 * One run's drill-down (spec: "Per-run drill-down at
 * /app/cyl-pipeline-runs/[runId]"). The server renders the run row; the
 * per-scan rows load on the first SUBSCRIBED, which also closes the gap
 * before the subscription.
 *
 * - The header's counts come from the held scan rows, so it moves with each
 *   scan event; the list's run row can trail it by one sweep.
 * - Links come only from the requested scans (design D6): "Scan images" per
 *   scan, and traits links for the run's most common (wave, age) pairs.
 * - "Current in trait views" compares a row's `source_id` with the scan's
 *   latest source. A row that turns written live raises the held latest to
 *   max(held, source_id) with no query: the trigger keeps the latest as the
 *   max source id, and write-back inserts the traits in the transaction that
 *   marks the row written. The next resync corrects a concurrent writer.
 * - A row that turns failed live gets one metadata and latest-source lookup,
 *   for its likely cause and the bloom#900 note.
 */

import Link from "next/link";
import { useRef, useState } from "react";
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
}

export const TIMING_NOTE =
  "Results arrive when each batch of up to 25 scans finishes. Reload the traits page to see new results.";

function paramsText(params: unknown): string {
  if (params && typeof params === "object" && !Array.isArray(params) && Object.keys(params).length > 0) {
    return Object.entries(params as Record<string, unknown>)
      .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
      .join(", ");
  }
  return "from each scan's metadata (no overrides)";
}

export function RunDetailLive({ initialRun, initialFilter = "all" }: { initialRun: RunRow; initialFilter?: StatusFilter }) {
  const runId = initialRun.id;
  const now = useNow();
  const [meta, setMeta] = useState<Map<number, ScanMeta>>(() => new Map());
  const [latest, setLatest] = useState<Map<number, number | null> | null>(null);
  const [experiments, setExperiments] = useState<RunExperiment[]>([]);
  const metaRef = useRef(meta);
  metaRef.current = meta;
  // Rows known to be failed; a row joining this set live gets one lookup.
  const failedSeen = useRef(new Set<number>());

  const lookUpFailed = async (scanId: number) => {
    const client = createClientSupabaseClient();
    try {
      const [m, l] = await Promise.all([fetchScanMeta(client, [scanId]), fetchLatestSources(client, [scanId])]);
      setMeta((prev) => new Map([...prev, ...m]));
      setLatest((prev) => {
        const next = new Map(prev ?? []);
        if (l.has(scanId)) next.set(scanId, l.get(scanId)!);
        else next.delete(scanId);
        return next;
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
    initial: { detail: detailFromSnapshot(initialRun, []), loaded: false },
    snapshot: async (): Promise<DetailView> => {
      const client = createClientSupabaseClient();
      const [run, scans] = await Promise.all([fetchRun(client, runId), fetchRunScans(client, runId)]);
      const ids = scans.map((s) => s.scan_id);
      const unknown = ids.filter((id) => !metaRef.current.has(id));
      const [m, l, exps] = await Promise.all([
        fetchScanMeta(client, unknown),
        fetchLatestSources(client, ids),
        // Experiment links are decoration; the view being unavailable doesn't block the rows.
        fetchRunExperiments(client, [runId]).catch(() => null),
      ]);
      setMeta((prev) => new Map([...prev, ...m]));
      setLatest(l);
      if (exps) setExperiments(exps);
      scans.forEach((s) => s.status === "failed" && failedSeen.current.add(s.id));
      return { detail: detailFromSnapshot(run ?? live.current().detail.run, scans), loaded: true };
    },
    apply: (view, change) => ({ ...view, detail: applyDetailChange(view.detail, change as Change<RunRow | RunScanRow>, runId) }),
    onEvent: (change) => {
      if (change.table !== RUN_SCANS_TABLE) return;
      const row = change.new as Partial<RunScanRow>;
      if (row.run_id !== runId || typeof row.id !== "number" || typeof row.scan_id !== "number") return;
      if ((row.status === "written" || row.status === "reused") && typeof row.source_id === "number") {
        const scanId = row.scan_id;
        const sourceId = row.source_id;
        setLatest((prev) => {
          if (prev === null) return prev;
          const held = prev.get(scanId);
          if (held != null && held >= sourceId) return prev;
          return new Map(prev).set(scanId, sourceId);
        });
      }
      if (row.status === "failed" && !failedSeen.current.has(row.id)) {
        failedSeen.current.add(row.id);
        void lookUpFailed(row.scan_id);
      }
    },
  });

  const { detail, loaded } = live.view;
  const scanRows = [...detail.scans.values()].sort((a, b) => a.scan_id - b.scan_id);
  const tallies = countsFromScanRows(scanRows);
  // Until the rows load, the run row's own counts are all there is.
  const headerRun = loaded ? { ...detail.run, done_count: tallies.done, failed_count: tallies.failed } : detail.run;
  const lastUpdate = scanRows.reduce<string | null>(
    (max, r) => (max === null || compareTimestamps(r.updated_at, max) > 0 ? r.updated_at : max),
    null,
  );
  const links = traitsLinks(
    scanRows.map((r) => r.scan_id),
    meta,
  );

  const tableRows: ScanTableRow[] = scanRows.map((r) => {
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
      current: latest === null ? null : r.source_id !== null && scanLatest === r.source_id,
      likelyCause: failed ? likelyCause(m) : null,
      noOpNote: failed && isNoOpCandidate(r, scanLatest != null) ? NO_OP_NOTE : null,
      scanHref: scanImagesHref(m),
    };
  });

  return (
    <div>
      <section data-testid="run-header" className="mb-6 space-y-2 text-sm">
        <div className="flex items-start justify-between gap-4">
          <RunState run={headerRun} />
          <LiveIndicator state={live.connection} onRefresh={live.refresh} />
        </div>
        <div className="text-stone-600">
          {now !== null && <span>Elapsed {formatElapsed(detail.run.created_at, now)}</span>}
          {now !== null && lastUpdate && <span> · Last scan update {formatElapsed(lastUpdate, now)} ago</span>}
        </div>
        <div className="text-stone-600">Parameters: {paramsText(detail.run.params)}</div>
        {experiments.length > 0 && (
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

      {!loaded ? (
        !live.error && <p className="text-sm text-stone-500">Loading scan rows…</p>
      ) : tableRows.length === 0 ? (
        detail.run.scan_count > 0 && <p className="text-sm text-stone-500">No scan rows recorded</p>
      ) : (
        <RunScansTable rows={tableRows} initialFilter={initialFilter} />
      )}
    </div>
  );
}
