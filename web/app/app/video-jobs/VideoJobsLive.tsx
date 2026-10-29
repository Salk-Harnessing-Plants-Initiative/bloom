"use client";

/**
 * Every member's plate and cylinder scan video jobs in one list, kept current by
 * Realtime from both job tables. The server renders the first page; the first
 * SUBSCRIBED refetches it to close the gap before the subscription.
 *
 * Each row links to the page its video plays on. The ids that address needs
 * are looked up once per job, the way the runs list looks up experiment names.
 */

import { useEffect, useRef, useState } from "react";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  PLATE_JOBS_TABLE,
  SCAN_JOBS_TABLE,
  appendOlder,
  applyJobChange,
  isActive,
  jobHref,
  jobKey,
  listFromSnapshot,
  queuedAhead,
  type JobKind,
  type JobList,
  type ListedJob,
  type PlatePlace,
  type ScanPlace,
} from "@/lib/video-jobs/list";
import { fetchJobPlaces, fetchJobs, type JobFilters } from "@/lib/video-jobs/queries";
import { VideoJobRow } from "./VideoJobRow";

export interface Places {
  plates: PlatePlace[];
  scans: ScanPlace[];
}

export interface VideoJobsLiveProps {
  initialJobs: ListedJob[];
  initialHasOlder: boolean;
  initialPlaces: Places;
  currentUserId: string | null;
  initialError: string | null;
  /** The job the visitor came to follow, from `?job=`. */
  highlightJobId: string | null;
}

type Kind = JobKind | "all";

function placesInto(places: Places, plates: Map<number, PlatePlace>, scans: Map<number, ScanPlace>) {
  for (const p of places.plates) plates.set(p.experiment_id, p);
  for (const s of places.scans) scans.set(s.scan_id, s);
}

export function VideoJobsLive({
  initialJobs,
  initialHasOlder,
  initialPlaces,
  currentUserId,
  initialError,
  highlightJobId,
}: VideoJobsLiveProps) {
  const now = useNow();
  const [mineOnly, setMineOnly] = useState(false);
  const [kind, setKind] = useState<Kind>("all");
  const [activeOnly, setActiveOnly] = useState(false);
  // Read by the snapshot and the event filter, which outlive a render.
  const filters = useRef({ mineOnly: false, kind: "all" as Kind, activeOnly: false });
  const [places, setPlaces] = useState(() => {
    const plates = new Map<number, PlatePlace>();
    const scans = new Map<number, ScanPlace>();
    placesInto(initialPlaces, plates, scans);
    return { plates, scans };
  });
  // Jobs whose place was read, or is being read; each is looked up at most once.
  const looked = useRef(new Set(initialJobs.map(jobKey)));
  const [serverError, setServerError] = useState(initialError);
  const [older, setOlder] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });

  const query = (): JobFilters => ({
    requestedBy: filters.current.mineOnly ? currentUserId : null,
    kind: filters.current.kind === "all" ? null : filters.current.kind,
    activeOnly: filters.current.activeOnly,
  });

  const accept = (job: ListedJob) => {
    const f = filters.current;
    if (f.mineOnly && (currentUserId === null || job.requested_by !== currentUserId)) return false;
    if (f.kind !== "all" && job.kind !== f.kind) return false;
    return !f.activeOnly || isActive(job);
  };

  const loadPlaces = async (jobs: ListedJob[]) => {
    const fresh = jobs.filter((j) => !looked.current.has(jobKey(j)));
    if (fresh.length === 0) return;
    fresh.forEach((j) => looked.current.add(jobKey(j)));
    try {
      const found = await fetchJobPlaces(createClientSupabaseClient(), fresh);
      setPlaces((prev) => {
        const plates = new Map(prev.plates);
        const scans = new Map(prev.scans);
        placesInto(found, plates, scans);
        return { plates, scans };
      });
    } catch {
      // A job without its place still shows its state; only the link waits.
    }
  };

  const live = useLiveSync<JobList>({
    topic: "video-jobs",
    bindings: [
      { table: PLATE_JOBS_TABLE, event: "INSERT" },
      { table: PLATE_JOBS_TABLE, event: "UPDATE" },
      { table: SCAN_JOBS_TABLE, event: "INSERT" },
      { table: SCAN_JOBS_TABLE, event: "UPDATE" },
    ],
    initial: listFromSnapshot(initialJobs, initialHasOlder),
    snapshot: async () => {
      const { rows, hasOlder } = await fetchJobs(createClientSupabaseClient(), query());
      await loadPlaces(rows);
      return listFromSnapshot(rows, hasOlder);
    },
    apply: (list, change) => applyJobChange(list, change, accept),
    onEvent: (_change, list) => void loadPlaces(list.rows),
    onSnapshot: () => setServerError(null),
  });

  const loadOlder = async () => {
    const cursor = live.current().cursor;
    if (!cursor || live.isFetching()) return;
    const started = live.generation();
    setOlder({ loading: true, error: null });
    try {
      const { rows, hasOlder } = await fetchJobs(createClientSupabaseClient(), {
        ...query(),
        before: cursor,
      });
      await loadPlaces(rows);
      if (live.generation() === started) live.update((list) => appendOlder(list, rows, hasOlder));
      setOlder({ loading: false, error: null });
    } catch (e) {
      setOlder({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const setFilter = (change: Partial<typeof filters.current>) => {
    filters.current = { ...filters.current, ...change };
    setMineOnly(filters.current.mineOnly);
    setKind(filters.current.kind);
    setActiveOnly(filters.current.activeOnly);
    live.refresh();
  };

  // Bring the job the visitor came for into view, once it is on the page.
  const scrolled = useRef(false);
  const highlightedHere = live.view.rows.some((j) => j.id === highlightJobId);
  useEffect(() => {
    if (!highlightJobId || scrolled.current || !highlightedHere) return;
    scrolled.current = true;
    document.getElementById(`job-${highlightJobId}`)?.scrollIntoView?.({ block: "center" });
  }, [highlightJobId, highlightedHere]);

  const error = live.error ?? serverError;
  // A filter change hides what it rules out at once, not only when its snapshot lands.
  const rows = live.view.rows.filter(accept);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-4 text-sm text-stone-700">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={mineOnly}
              onChange={(e) => setFilter({ mineOnly: e.target.checked })}
            />
            Only mine
          </label>
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setFilter({ activeOnly: e.target.checked })}
            />
            Only queued or rendering
          </label>
          <label className="inline-flex items-center gap-2">
            Show
            <select
              value={kind}
              onChange={(e) => setFilter({ kind: e.target.value as Kind })}
              className="rounded-md border border-stone-300 px-2 py-1"
            >
              <option value="all">All videos</option>
              <option value="plate">Plate time-lapses</option>
              <option value="scan">Cylinder scans</option>
            </select>
          </label>
        </div>
        <LiveIndicator state={live.connection} onRefresh={live.refresh} />
      </div>

      {error && (
        <div role="alert" className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Could not load video jobs: {error}{" "}
          <button type="button" onClick={live.refresh} className="underline hover:no-underline">
            Retry
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        !error && (
          <p className="text-sm text-stone-500">
            {mineOnly ? "You haven't asked for any videos" : "No video jobs yet"}
          </p>
        )
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-xs uppercase tracking-wider text-neutral-500">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Video
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Requested
                </th>
                <th scope="col" className="px-3 py-2 text-left font-medium">
                  Progress
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((job) => (
                <VideoJobRow
                  key={jobKey(job)}
                  job={job}
                  ahead={queuedAhead(live.view.rows, job)}
                  href={jobHref(job, places.plates, places.scans)}
                  plate={job.kind === "plate" ? places.plates.get(job.experiment_id) : undefined}
                  scan={job.kind === "scan" ? places.scans.get(job.scan_id) : undefined}
                  currentUserId={currentUserId}
                  now={now}
                  highlighted={job.id === highlightJobId}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {live.view.hasOlder && rows.length > 0 && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => void loadOlder()}
            disabled={older.loading || live.fetching}
            className="rounded-md border border-stone-300 px-3 py-1 text-sm hover:bg-stone-50 disabled:opacity-50"
          >
            {older.loading ? "Loading…" : "Load older jobs"}
          </button>
          {older.error && <span className="ml-3 text-sm text-red-700">Could not load older jobs: {older.error}</span>}
        </div>
      )}
    </div>
  );
}
