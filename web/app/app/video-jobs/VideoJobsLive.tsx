"use client";

/**
 * Every member's plate and cylinder scan video jobs in one list, kept current by
 * Realtime from both job tables. The server renders the first page; the first
 * SUBSCRIBED refetches it to close the gap before the subscription.
 *
 * Queue position and the "stopped worker" warning come from every queued or
 * rendering job, read and kept apart from the list, so a filter or the page
 * size cannot hide a job that is ahead in the line.
 *
 * Each row links to the page its video plays on. The ids that address needs are
 * looked up once per experiment or scan, and again after a lookup that failed.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LiveIndicator } from "@/components/recent-phenotypes-by-cyl-scanner/LiveIndicator";
import type { Change } from "@/lib/cyl-pipeline/realtime-reducer";
import { useLiveSync } from "@/lib/cyl-pipeline/use-live-sync";
import { useNow } from "@/lib/cyl-pipeline/use-now";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import {
  PLATE_JOBS_TABLE,
  SCAN_JOBS_TABLE,
  appendOlder,
  applyActiveChange,
  applyJobChange,
  changeKind,
  isActive,
  jobHref,
  jobKey,
  listFromSnapshot,
  queuedAhead,
  type ActiveJob,
  type JobKind,
  type JobList,
  type ListedJob,
  type PlatePlace,
  type ScanPlace,
} from "@/lib/video-jobs/list";
import { fetchActiveJobs, fetchJobPlaces, fetchJobs, type JobFilters } from "@/lib/video-jobs/queries";
import { VideoJobRow } from "./VideoJobRow";

export interface Places {
  plates: PlatePlace[];
  scans: ScanPlace[];
}

export interface VideoJobsLiveProps {
  initialJobs: ListedJob[];
  initialHasOlder: boolean;
  initialPlaces: Places;
  /** Every queued or rendering job, unfiltered. */
  initialActive: ActiveJob[];
  currentUserId: string | null;
  initialError: string | null;
  /** The job the visitor came to follow, from `?job=`, and that job if it was found. */
  highlightJobId: string | null;
  highlightedJob: ListedJob | null;
}

type Kind = JobKind | "all";
type Filters = { mineOnly: boolean; kind: Kind; activeOnly: boolean };

const placeKey = (job: ListedJob) => (job.kind === "plate" ? `plate:${job.experiment_id}` : `scan:${job.scan_id}`);

function placesInto(places: Places, plates: Map<number, PlatePlace>, scans: Map<number, ScanPlace>) {
  for (const p of places.plates) plates.set(p.experiment_id, p);
  for (const s of places.scans) scans.set(s.scan_id, s);
}

function emptyText(f: Filters): string {
  if (f.mineOnly && !f.activeOnly && f.kind === "all") return "You haven't asked for any videos";
  const what = f.kind === "plate" ? "plate time-lapses" : f.kind === "scan" ? "cylinder scan videos" : "videos";
  if (f.activeOnly) return `No ${what} queued or rendering right now`;
  return f.mineOnly ? `You haven't asked for any ${what}` : f.kind === "all" ? "No video jobs yet" : `No ${what} yet`;
}

export function VideoJobsLive({
  initialJobs,
  initialHasOlder,
  initialPlaces,
  initialActive,
  currentUserId,
  initialError,
  highlightJobId,
  highlightedJob,
}: VideoJobsLiveProps) {
  const now = useNow();
  const [filters, setFilters] = useState<Filters>({ mineOnly: false, kind: "all", activeOnly: false });
  // Read by the snapshot and the event filter, which outlive a render.
  const held = useRef(filters);
  const [active, setActive] = useState(initialActive);
  const [pinned, setPinned] = useState(highlightedJob);
  const [places, setPlaces] = useState(() => {
    const plates = new Map<number, PlatePlace>();
    const scans = new Map<number, ScanPlace>();
    placesInto(initialPlaces, plates, scans);
    return { plates, scans };
  });
  // Places already read, by experiment or scan; and those being read now.
  const looked = useRef(
    new Set([
      ...initialPlaces.plates.map((p) => `plate:${p.experiment_id}`),
      ...initialPlaces.scans.map((s) => `scan:${s.scan_id}`),
    ])
  );
  const reading = useRef(new Set<string>());
  const [serverError, setServerError] = useState(initialError);
  const [older, setOlder] = useState<{ loading: boolean; error: string | null }>({
    loading: false,
    error: null,
  });

  const query = (): JobFilters => ({
    requestedBy: held.current.mineOnly ? currentUserId : null,
    kind: held.current.kind === "all" ? null : held.current.kind,
    activeOnly: held.current.activeOnly,
  });

  const accept = (job: ListedJob) => {
    const f = held.current;
    if (f.mineOnly && (currentUserId === null || job.requested_by !== currentUserId)) return false;
    if (f.kind !== "all" && job.kind !== f.kind) return false;
    return !f.activeOnly || isActive(job);
  };

  const loadPlaces = async (jobs: ListedJob[]) => {
    const fresh = new Map<string, ListedJob>();
    for (const j of jobs) {
      const key = placeKey(j);
      if (!looked.current.has(key) && !reading.current.has(key)) fresh.set(key, j);
    }
    if (fresh.size === 0) return;
    const keys = [...fresh.keys()];
    keys.forEach((k) => reading.current.add(k));
    try {
      const found = await fetchJobPlaces(createClientSupabaseClient(), [...fresh.values()]);
      // Read, even where nothing came back: an experiment the caller cannot see stays unlinked.
      keys.forEach((k) => looked.current.add(k));
      setPlaces((prev) => {
        const plates = new Map(prev.plates);
        const scans = new Map(prev.scans);
        placesInto(found, plates, scans);
        return { plates, scans };
      });
    } catch {
      // Only the links wait; the next snapshot or event asks again.
    } finally {
      keys.forEach((k) => reading.current.delete(k));
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
      const client = createClientSupabaseClient();
      const [{ rows, hasOlder }, queue] = await Promise.all([fetchJobs(client, query()), fetchActiveJobs(client)]);
      setActive(queue);
      return listFromSnapshot(rows, hasOlder);
    },
    apply: (list, change) => applyJobChange(list, change, accept),
    onEvent: (change, list) => {
      // Every event, filtered out of the list or not, moves the line.
      setActive((queue) => applyActiveChange(queue, change));
      setPinned((job) => (job ? (applyJobChange(listFromSnapshot([job], false), change).rows[0] ?? job) : job));
      const kind = changeKind(change);
      const id = (change as Change<{ id: string }>).new.id;
      const job = list.rows.find((r) => r.kind === kind && r.id === id);
      if (job) void loadPlaces([job]);
    },
    onSnapshot: (list) => {
      setServerError(null);
      void loadPlaces(list.rows);
    },
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
      if (live.generation() === started) {
        live.update((list) => appendOlder(list, rows, hasOlder));
        void loadPlaces(rows);
      }
      setOlder({ loading: false, error: null });
    } catch (e) {
      setOlder({ loading: false, error: e instanceof Error ? e.message : String(e) });
    }
  };

  const setFilter = (change: Partial<Filters>) => {
    held.current = { ...held.current, ...change };
    setFilters(held.current);
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
  const row = (job: ListedJob, highlighted: boolean) => (
    <VideoJobRow
      key={jobKey(job)}
      job={job}
      ahead={queuedAhead(active, job)}
      href={jobHref(job, places.plates, places.scans)}
      plate={job.kind === "plate" ? places.plates.get(job.experiment_id) : undefined}
      scan={job.kind === "scan" ? places.scans.get(job.scan_id) : undefined}
      currentUserId={currentUserId}
      now={now}
      highlighted={highlighted}
      active={active}
    />
  );
  const pinnedHref = pinned ? jobHref(pinned, places.plates, places.scans) : null;

  return (
    <div>
      {highlightJobId && (
        <div
          role="status"
          aria-label="Your video job"
          className="mb-4 rounded-md border border-lime-200 bg-lime-50 p-3 text-sm text-stone-700"
        >
          {pinned ? (
            <>
              <p>
                {pinned.requested_by !== null && pinned.requested_by !== currentUserId
                  ? "Your request joined one already in the queue, highlighted below."
                  : "This is the video you asked for, highlighted below."}{" "}
                It carries on if you leave this page.
                {pinnedHref && (
                  <>
                    {" "}
                    <Link href={pinnedHref} className="text-lime-700 underline hover:no-underline">
                      Back to its page
                    </Link>
                    , where it plays once it is ready.
                  </>
                )}
              </p>
              {!highlightedHere && (
                <table className="mt-2 w-full text-sm">
                  <tbody>{row(pinned, true)}</tbody>
                </table>
              )}
            </>
          ) : (
            <p>That video job could not be found.</p>
          )}
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex flex-wrap items-center gap-4 text-sm text-stone-700">
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={filters.mineOnly}
              onChange={(e) => setFilter({ mineOnly: e.target.checked })}
            />
            Only mine
          </label>
          <label className="inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={filters.activeOnly}
              onChange={(e) => setFilter({ activeOnly: e.target.checked })}
            />
            Only queued or rendering
          </label>
          <label className="inline-flex items-center gap-2">
            Show
            <select
              value={filters.kind}
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
        !error && <p className="text-sm text-stone-500">{emptyText(filters)}</p>
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
            <tbody>{rows.map((job) => row(job, job.id === highlightJobId))}</tbody>
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
