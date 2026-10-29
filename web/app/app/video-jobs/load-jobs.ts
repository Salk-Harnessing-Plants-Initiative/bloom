/** The Video Jobs page's server-rendered first page (the page imports it; helpers stay out of page.tsx). */

import type { ReadClient } from "@/lib/cyl-pipeline/queries";
import type { ListedJob } from "@/lib/video-jobs/list";
import { fetchJobPlaces, fetchJobs } from "@/lib/video-jobs/queries";
import type { Places } from "./VideoJobsLive";

export interface JobsSnapshot {
  jobs: ListedJob[];
  hasOlder: boolean;
  places: Places;
  error: string | null;
}

export async function loadJobsSnapshot(client: ReadClient): Promise<JobsSnapshot> {
  let jobs: ListedJob[];
  let hasOlder: boolean;
  try {
    ({ rows: jobs, hasOlder } = await fetchJobs(client));
  } catch (e) {
    return {
      jobs: [],
      hasOlder: false,
      places: { plates: [], scans: [] },
      error: e instanceof Error ? e.message : String(e),
    };
  }
  // Places are for the links: a failed lookup still lists the jobs.
  const places = jobs.length
    ? await fetchJobPlaces(client, jobs).catch(() => ({ plates: [], scans: [] }))
    : { plates: [], scans: [] };
  return { jobs, hasOlder, places, error: null };
}
