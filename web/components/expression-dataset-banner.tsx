import Illustration from "@/components/illustration";
import {
  IncompleteUploadNotice,
  isIncompleteUpload,
} from "@/components/expression-upload-status";
import { DatasetReads, readsSource, type ReadsSource } from "@/components/expression-dataset-reads";
import { datasetRunId } from "@/components/expression-dataset-origin";
import { fetchRun } from "@/lib/rnaseq-runs";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";

type Props = {
  datasetId: number;
  speciesId: number;
};


async function fetchBannerData(datasetId: number) {
  const supabase = await createServerSupabaseClient();

  const { data: dataset } = await supabase
    .from("scrna_datasets")
    .select(
      "id, name, n_cells, assembly, annotation, strain, metadata, species_id, source_checksum, ingested_at, species(common_name, genus, species, illustration_path), people:scientist_id(name)",
    )
    .eq("id", datasetId)
    .single();

  if (!dataset) return null;

  const species = Array.isArray(dataset.species) ? dataset.species[0] : dataset.species;
  const scientist = Array.isArray(dataset.people) ? dataset.people[0] : dataset.people;

  const cellsRes = await supabase
    .from("scrna_cells")
    .select("cluster_id")
    .eq("dataset_id", datasetId)
    .limit(500000);

  const clusterIds = new Set<string>();
  for (const row of cellsRes.data ?? []) {
    if (row.cluster_id) clusterIds.add(row.cluster_id);
  }
  const clusters = clusterIds.size || null;
  const reads = await fetchReadsSource(supabase, datasetRunId(dataset));

  return { dataset, species, scientist, clusters, reads, incomplete: isIncompleteUpload(dataset) };
}

/** Where the reads of the run that made the dataset came from; null for a dataset no run made,
 * or when the run can't be read. */
async function fetchReadsSource(
  supabase: Awaited<ReturnType<typeof createServerSupabaseClient>>,
  runId: number | null,
): Promise<ReadsSource | null> {
  if (runId === null) return null;
  try {
    const [run, user] = await Promise.all([fetchRun(supabase, runId), getUser()]);
    return run ? readsSource(run, user?.id ?? null) : null;
  } catch {
    return null;
  }
}

function formatCount(n: number | null | undefined): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

function renderJsonValue(v: unknown): React.ReactNode {
  if (v === null || v === undefined) {
    return <span className="italic text-stone-400">—</span>;
  }
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return <span className="text-stone-700">{String(v)}</span>;
  }
  return (
    <pre className="mt-1 text-[10px] whitespace-pre-wrap break-words font-mono bg-stone-50 border border-stone-200 rounded p-2 text-stone-700 overflow-x-auto">
      {JSON.stringify(v, null, 2)}
    </pre>
  );
}

export default async function ExpressionDatasetBanner({ datasetId }: Props) {
  const data = await fetchBannerData(datasetId);
  if (!data) return null;

  const { dataset, species, scientist, clusters, reads, incomplete } = data;
  const assemblyAnnotation =
    dataset.assembly || dataset.annotation
      ? `${dataset.assembly ?? "—"} / ${dataset.annotation ?? "—"}`
      : "—";

  return (
    <section
      className="mb-8 p-6 rounded-xl border border-lime-200 bg-gradient-to-br from-lime-50 via-lime-50/40 to-white shadow-xl shadow-lime-300/35"
    >
      {incomplete ? <IncompleteUploadNotice /> : null}
      <div className="flex items-start gap-6">
        <div className="shrink-0 w-14 h-14 flex items-center justify-center">
          <Illustration
            path={species?.illustration_path ?? null}
            commonName={species?.common_name ?? null}
          />
        </div>

        <div className="flex-1 min-w-0">
          <div className="mb-1 text-xs uppercase tracking-widest text-stone-500">
            Dataset
          </div>
          <div className="flex items-baseline gap-3 flex-wrap">
            <h1
              className="text-xl font-semibold text-stone-900 break-words"
              title={dataset.name}
            >
              {dataset.name}
            </h1>
            {species ? (
              <span className="text-sm italic text-stone-500">
                <span className="capitalize">{species.genus}</span>{" "}
                <span className="lowercase">{species.species}</span>
              </span>
            ) : null}
            {scientist?.name ? (
              <span className="text-sm text-stone-600">· {scientist.name}</span>
            ) : null}
          </div>

          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <StatPill label="cells" value={formatCount(dataset.n_cells)} />
            <StatPill label="clusters" value={formatCount(clusters)} />
            <StatPill label="assembly / annotation" value={assemblyAnnotation} />
            <StatPill label="strain" value={dataset.strain ?? "—"} />
          </div>

          {reads ? <DatasetReads source={reads} /> : null}

          <MetadataDisclosure metadata={dataset.metadata} />
        </div>

      </div>
    </section>
  );
}

function StatPill({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 rounded-full border border-stone-200 bg-stone-50 px-3 py-1">
      <span className="text-xs tabular-nums text-stone-700 font-medium">
        {value}
      </span>
      <span className="text-xs text-stone-500">{label}</span>
    </span>
  );
}

function MetadataDisclosure({ metadata }: { metadata: unknown }) {
  const entries =
    metadata && typeof metadata === "object" && !Array.isArray(metadata)
      ? Object.entries(metadata as Record<string, unknown>)
      : [];

  const summaryLabel =
    entries.length === 0
      ? "no additional metadata"
      : `more metadata · ${entries.length} field${entries.length === 1 ? "" : "s"}`;

  return (
    <details className="group mt-3">
      <summary className="cursor-pointer text-xs text-lime-700 hover:underline select-none">
        <span className="group-open:hidden">{summaryLabel} ▾</span>
        <span className="hidden group-open:inline">less metadata ▴</span>
      </summary>

      {entries.length === 0 ? (
        <div className="mt-3 text-xs italic text-stone-400">
          This dataset has no extra metadata recorded.
        </div>
      ) : (
        <dl className="mt-3 max-h-72 overflow-y-auto pr-2 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-3 text-xs rounded border border-stone-200 bg-white/60 p-3">
          {entries.map(([key, value]) => (
            <div key={key} className="min-w-0">
              <dt className="uppercase tracking-widest text-stone-500 text-[10px] mb-0.5">
                {key}
              </dt>
              <dd className="text-stone-700 break-words">
                {renderJsonValue(value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </details>
  );
}
