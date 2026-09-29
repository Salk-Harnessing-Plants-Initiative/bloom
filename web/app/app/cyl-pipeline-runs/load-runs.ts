/** The runs list's server-rendered first page (the page imports it; helpers stay out of page.tsx). */

import { fetchRunExperiments, fetchRuns, type ReadClient, type RunExperiment } from "@/lib/cyl-pipeline/queries";
import type { RunRow } from "@/lib/cyl-pipeline/realtime-reducer";

export interface RunsSnapshot {
  runs: RunRow[];
  experiments: RunExperiment[];
  error: string | null;
}

export async function loadRunsSnapshot(client: ReadClient): Promise<RunsSnapshot> {
  let runs: RunRow[];
  try {
    runs = await fetchRuns(client);
  } catch (e) {
    return { runs: [], experiments: [], error: e instanceof Error ? e.message : String(e) };
  }
  // Names are decoration: a failed lookup still lists the runs.
  const experiments = runs.length ? await fetchRunExperiments(client, runs.map((r) => r.id)).catch(() => []) : [];
  return { runs, experiments, error: null };
}
