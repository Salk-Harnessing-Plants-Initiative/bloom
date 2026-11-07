/** The Video Jobs page's server-rendered first page (the page imports it; helpers stay out of page.tsx). */

import type { ReadClient } from "@/lib/cyl-pipeline/queries";
import type { ActiveJob, ListedJob } from "@/lib/video-jobs/list";
import { fetchActiveJobs, fetchJobById, fetchJobPlaces, fetchJobs } from "@/lib/video-jobs/queries";
import type { Places } from "./VideoJobsLive";

export interface JobsSnapshot {
  jobs: ListedJob[];
  hasOlder: boolean;
  places: Places;
  active: ActiveJob[];
  /** The job the visitor came for, found whether or not it is on the first page. */
  highlighted: ListedJob | null;
  error: string | null;
}

const NO_PLACES: Places = { plates: [], scans: [] };

export async function loadJobsSnapshot(client: ReadClient, highlightJobId: string | null): Promise<JobsSnapshot> {
  let jobs: ListedJob[];
  let hasOlder: boolean;
  let active: ActiveJob[];
  try {
    [{ rows: jobs, hasOlder }, active] = await Promise.all([fetchJobs(client), fetchActiveJobs(client)]);
  } catch (e) {
    return {
      jobs: [],
      hasOlder: false,
      places: NO_PLACES,
      active: [],
      highlighted: null,
      error: e instanceof Error ? e.message : String(e),
    };
  }
  const highlighted = highlightJobId
    ? (jobs.find((j) => j.id === highlightJobId) ?? (await fetchJobById(client, highlightJobId).catch(() => null)))
    : null;
  const all = highlighted && !jobs.includes(highlighted) ? [...jobs, highlighted] : jobs;
  // Places are for the links: a failed lookup still lists the jobs, and the browser asks again.
  const places = all.length ? await fetchJobPlaces(client, all).catch(() => NO_PLACES) : NO_PLACES;
  return { jobs, hasOlder, places, active, highlighted, error: null };
}
