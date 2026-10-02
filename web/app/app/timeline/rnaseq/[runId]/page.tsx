import { notFound } from "next/navigation";
import { parseId } from "@/lib/route-params";
import { fetchRequesters, fetchRun, runSpeciesId } from "@/lib/rnaseq-runs";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import TimelineHub from "../../TimelineHub";
import RnaseqRunView from "../RnaseqRunView";
import { speciesLabelMap } from "../species-labels";

/** The Timeline page with the RNA-seq panel open on one run. */
export default async function RnaseqRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const id = parseId(runId);
  if (id === null) notFound();

  const supabase = await createServerSupabaseClient();
  let run;
  try {
    run = await fetchRun(supabase, id);
  } catch {
    return (
      <div>
        <div className="mb-6 select-none text-xl italic">Timeline</div>
        <TimelineHub open="rnaseq">
          <p role="alert" className="text-sm text-red-700">
            Couldn&apos;t load run {id}.
          </p>
        </TimelineHub>
      </div>
    );
  }
  if (!run) notFound();

  const [requesters, speciesLabels] = await Promise.all([
    fetchRequesters(supabase, [run.id]),
    speciesLabelMap(supabase),
  ]);
  const speciesId = runSpeciesId(run);
  return (
    <div>
      <div className="mb-6 select-none text-xl italic">Timeline</div>
      <TimelineHub open="rnaseq">
        <RnaseqRunView
          initialRun={run}
          startedBy={requesters.get(run.id) ?? null}
          speciesLabel={speciesId != null ? (speciesLabels[speciesId] ?? null) : null}
        />
      </TimelineHub>
    </div>
  );
}
