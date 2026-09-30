"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import type { Change } from "@/lib/cyl-pipeline/realtime-reducer";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import {
  CELLRANGER_STEPS,
  fetchRequesters,
  fetchRuns,
  isFinished,
  runDatasetName,
  runReference,
  runSample,
  runSpeciesId,
  upsertRun,
  type RnaseqRun,
} from "@/lib/rnaseq-runs";
import { StatusBadge, when } from "./run-labels";

type StatusFilter = "all" | "active" | "succeeded" | "failed";

const FILTERS: [StatusFilter, string][] = [
  ["all", "All"],
  ["active", "In progress"],
  ["succeeded", "Succeeded"],
  ["failed", "Failed"],
];

function matches(run: RnaseqRun, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "active") return !isFinished(run.status);
  return run.status === filter;
}

function stepLabel(step: string | null): string | null {
  return CELLRANGER_STEPS.find((s) => s.id === step)?.label ?? step;
}

/** RNA-seq runs, newest first, kept current by Realtime. */
export default function RnaseqRunsLive({
  initialRuns,
  initialRequesters,
  speciesLabels,
  currentUserId,
}: {
  initialRuns: RnaseqRun[];
  initialRequesters: Record<number, string>;
  speciesLabels: Record<number, string>;
  currentUserId: string | null;
}) {
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [mineOnly, setMineOnly] = useState(false);
  const mine = useRef(false);
  const [requesters, setRequesters] = useState(
    () => new Map(Object.entries(initialRequesters).map(([id, email]) => [Number(id), email]))
  );
  // Runs whose starter was looked up; each is asked for at most once.
  const looked = useRef(new Set(initialRuns.map((r) => r.id)));

  const accept = (run: RnaseqRun) => !mine.current || run.requested_by === currentUserId;

  async function loadRequesters(ids: number[]) {
    const fresh = ids.filter((id) => !looked.current.has(id));
    if (fresh.length === 0) return;
    fresh.forEach((id) => looked.current.add(id));
    const found = await fetchRequesters(createClientSupabaseClient(), fresh);
    setRequesters((prev) => new Map([...prev, ...found]));
  }

  const live = useLiveSync<RnaseqRun[]>({
    topic: "rnaseq-runs",
    bindings: [
      { table: "rnaseq_runs", event: "INSERT" },
      { table: "rnaseq_runs", event: "UPDATE" },
    ],
    initial: initialRuns,
    snapshot: async () => {
      const runs = await fetchRuns(createClientSupabaseClient(), {
        requestedBy: mine.current ? currentUserId : null,
      });
      await loadRequesters(runs.map((r) => r.id));
      return runs;
    },
    apply: (runs, change) => {
      const row = (change as Change<RnaseqRun>).new;
      if (typeof row.id !== "number") return runs;
      const held = runs.find((r) => r.id === row.id);
      return upsertRun(runs, { ...(held ?? {}), ...row } as RnaseqRun, accept);
    },
    onEvent: (change) => {
      const id = (change as Change<RnaseqRun>).new.id;
      if (typeof id === "number") void loadRequesters([id]);
    },
  });

  function toggleMine(checked: boolean) {
    mine.current = checked;
    setMineOnly(checked);
    live.refresh();
  }

  const runs = live.view.filter((r) => matches(r, filter) && (!mineOnly || accept(r)));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-4">
          <label className="text-sm text-stone-700">
            Show{" "}
            <select
              className="ml-1 rounded-md border border-stone-300 bg-white px-2 py-1 text-sm"
              value={filter}
              onChange={(e) => setFilter(e.target.value as StatusFilter)}
            >
              {FILTERS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-flex items-center gap-2 text-sm text-stone-700">
            <input type="checkbox" checked={mineOnly} onChange={(e) => toggleMine(e.target.checked)} />
            Only mine
          </label>
        </div>
        <LiveIndicator state={live.connection} onRefresh={live.refresh} />
      </div>

      {live.error ? (
        <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Couldn&apos;t load RNA-seq runs: {live.error}
        </div>
      ) : null}

      {runs.length === 0 ? (
        <p className="text-sm italic text-stone-500">
          {live.view.length === 0
            ? "No RNA-seq runs yet. Start one from the Expression page."
            : "No runs match this filter."}
        </p>
      ) : (
        <ul className="divide-y divide-stone-200 border-y border-stone-200">
          {runs.map((run) => {
            const species = runSpeciesId(run);
            const step = isFinished(run.status) ? null : stepLabel(run.current_step);
            return (
              <li key={run.id}>
                <Link
                  href={`/app/timeline/rnaseq/${run.id}`}
                  className="-mx-2 flex flex-wrap items-center gap-x-6 gap-y-1 rounded-sm px-2 py-3 hover:bg-stone-50"
                >
                  <span className="w-14 shrink-0 text-sm tabular-nums text-stone-500">#{run.id}</span>
                  <span className="min-w-0 flex-1">
                    <span className="font-medium text-stone-800">
                      {runSample(run) ?? "—"} · {runReference(run) ?? "—"}
                    </span>
                    <span className="block truncate text-sm text-stone-500">
                      {runDatasetName(run) ?? "No dataset name"}
                      {species != null && speciesLabels[species] ? ` · ${speciesLabels[species]}` : ""}
                    </span>
                  </span>
                  <span className="flex w-44 shrink-0 items-center gap-2">
                    <StatusBadge status={run.status} />
                    {step ? <span className="text-xs text-stone-500">{step}</span> : null}
                  </span>
                  <span className="w-48 shrink-0 truncate text-sm text-stone-500">
                    {requesters.get(run.id) ?? "—"}
                  </span>
                  <span className="w-28 shrink-0 text-right text-sm tabular-nums text-stone-500">
                    {when(run.created_at)}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
