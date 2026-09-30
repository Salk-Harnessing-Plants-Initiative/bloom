import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { fetchRequesters, fetchRuns, type RnaseqRun } from "@/lib/rnaseq-runs";
import { speciesLabelMap } from "./species-labels";
import RnaseqRunsLive from "./RnaseqRunsLive";

/** RNA-seq runs, newest first, updated live. */
export default async function RnaseqPanel() {
  const [supabase, user] = await Promise.all([createServerSupabaseClient(), getUser()]);
  let runs: RnaseqRun[] = [];
  let failed = false;
  try {
    runs = await fetchRuns(supabase);
  } catch {
    failed = true;
  }
  const [requesters, speciesLabels] = await Promise.all([
    fetchRequesters(supabase, runs.map((r) => r.id)),
    speciesLabelMap(supabase),
  ]);
  return (
    <div>
      <h2 className="mb-2 text-xl font-serif italic">RNA-seq runs</h2>
      <p className="mb-6 max-w-prose text-sm text-stone-500">
        Every member&apos;s RNA-seq runs, updated live. Start one from the Expression page.
      </p>
      {failed ? (
        <p role="alert" className="text-sm text-red-700">
          Couldn&apos;t load RNA-seq runs.
        </p>
      ) : (
        <RnaseqRunsLive
          initialRuns={runs}
          initialRequesters={Object.fromEntries(requesters)}
          speciesLabels={speciesLabels}
          currentUserId={user?.id ?? null}
        />
      )}
    </div>
  );
}
