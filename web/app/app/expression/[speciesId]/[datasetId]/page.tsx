import Link from "next/link";
import { notFound } from "next/navigation";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import ScientistBadge from "@/components/scientist-badge";
import { ExpressionCockpit } from "@/components/expression-cockpit";
import ExpressionDatasetBanner from "@/components/expression-dataset-banner";
import ExpressionDatasetQc from "@/components/expression-dataset-qc";
import { datasetRunId } from "@/components/expression-dataset-origin";
import { METRICS_SUMMARY, WEB_SUMMARY, keyMetrics, parseMetrics, readReport, storedReports } from "@/lib/rnaseq-reports";

export default async function Dataset({
  params,
  searchParams,
}: {
  params: Promise<{ datasetId: string; speciesId: string }>;
  searchParams?: Promise<{ tab?: string | string[] }>;
}) {
  const { datasetId, speciesId } = await params;
  const { tab } = (await searchParams) ?? {};
  const datasetIdNum = Number(datasetId);
  const speciesIdNum = Number(speciesId);
  if (!Number.isFinite(datasetIdNum) || !Number.isFinite(speciesIdNum)) notFound();
  const { data: dataset, error } = await getDataset(datasetIdNum);

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/expression/${speciesId}/${datasetId}`,
  });

  // A failed lookup is not a missing dataset: say which it was.
  if (error) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="text-sm mb-6 select-none">
          <Link href="/app/expression" className="text-stone-400 hover:underline">
            All species
          </Link>
        </div>
        <div role="alert" className="text-sm text-rose-700">
          Could not load this dataset: {error.message}
        </div>
      </div>
    );
  }

  // NULL_DATASET rows are placeholders without real expression data, and a
  // reference atlas has no cells of its own — treat both as not-found rather
  // than rendering an empty cockpit.
  if (!dataset || dataset.name === "NULL_DATASET" || dataset.kind === "reference") {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="text-sm mb-6 select-none">
          <Link href="/app/expression" className="text-stone-400 hover:underline">
            All species
          </Link>
        </div>
        <div className="text-sm text-stone-500 italic">Dataset not found.</div>
      </div>
    );
  }

  const speciesName = dataset.species?.common_name ?? "";
  const qc = await getRunQc(datasetRunId(dataset));

  return (
    <div>
      <div className="text-sm mb-6 pb-4 border-b border-stone-200 select-none">
        <Link
          href="/app/expression"
          className="text-stone-400 hover:underline"
        >
          All species
        </Link>
        <span className="text-stone-300">&nbsp;▸&nbsp;</span>
        <Link
          href={`/app/expression/${speciesId}`}
          className="text-stone-400 hover:underline capitalize"
        >
          {speciesName}
        </Link>
        <span className="text-stone-300">&nbsp;▸&nbsp;</span>
        <span className="text-stone-900">{dataset.name}</span>
      </div>

      <ExpressionDatasetBanner datasetId={datasetIdNum} speciesId={speciesIdNum} />

      {dataset.people && (
        <div className="mb-6">
          <ScientistBadge person={dataset.people} />
        </div>
      )}

      {qc ? <ExpressionDatasetQc {...qc} /> : null}

      <ExpressionCockpit
        datasetId={datasetIdNum}
        datasetName={dataset.name}
        initialTab={typeof tab === "string" ? tab : undefined}
      />
    </div>
  );
}

/** The dataset, or null when there is none; a database failure comes back as the error. */
async function getDataset(datasetId: number) {
  const supabase = await createServerSupabaseClient();
  return supabase
    .from("scrna_datasets")
    .select("*, people(*), species(*)")
    .eq("id", datasetId)
    .maybeSingle();
}

/** The Cell Ranger metrics and report of the run that made the dataset, or null for a dataset
 * no run made (or one whose run stored none). */
async function getRunQc(runId: number | null) {
  if (runId === null) return null;
  const supabase = await createServerSupabaseClient();
  const stored = await storedReports(supabase, runId);
  if (stored.size === 0) return null;
  const csv = stored.has(METRICS_SUMMARY) ? await readReport(supabase, runId, METRICS_SUMMARY) : null;
  return {
    runId,
    metrics: csv && "text" in csv ? keyMetrics(parseMetrics(csv.text)) : [],
    hasReport: stored.has(WEB_SUMMARY),
  };
}
