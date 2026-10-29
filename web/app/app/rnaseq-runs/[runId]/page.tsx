import { notFound } from "next/navigation";
import { parseId } from "@/lib/route-params";
import { requesterText } from "@/lib/cyl-pipeline/run-text";
import { WEB_SUMMARY, reportHref, storedReports } from "@/lib/rnaseq-reports";
import { fetchDatasetName, fetchGenomeLabel, fetchRun, runSpeciesId } from "@/lib/rnaseq-runs";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import RnaseqRunView from "../RnaseqRunView";
import { speciesLabelMap } from "../species-labels";

/** One RNA-seq run: its steps, details and how it ended. */
export default async function RnaseqRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const id = parseId(runId);
  if (id === null) notFound();

  const [supabase, user] = await Promise.all([createServerSupabaseClient(), getUser()]);
  let run;
  try {
    run = await fetchRun(supabase, id);
  } catch {
    return (
      <p role="alert" className="text-sm text-red-700">
        Couldn&apos;t load run {id}.
      </p>
    );
  }
  if (!run) notFound();

  const [speciesLabels, genomeLabel, reports, datasetName] = await Promise.all([
    speciesLabelMap(supabase),
    fetchGenomeLabel(supabase, run),
    storedReports(supabase, run.id),
    fetchDatasetName(supabase, run),
  ]);
  const speciesId = runSpeciesId(run);
  return (
    <RnaseqRunView
      initialRun={run}
      startedBy={requesterText(run.requested_by, user?.id ?? null)}
      speciesLabel={speciesId != null ? (speciesLabels[speciesId] ?? null) : null}
      genomeLabel={genomeLabel}
      reportHref={reports.has(WEB_SUMMARY) ? reportHref(run.id) : null}
      loadedDatasetName={datasetName}
    />
  );
}
