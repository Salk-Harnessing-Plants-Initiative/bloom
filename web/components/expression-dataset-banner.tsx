import Link from "next/link";
import Illustration from "@/components/illustration";
import { createServerSupabaseClient } from "@/lib/supabase/server";

type Props = {
  datasetId: number;
  speciesId: number;
};

type SiblingDataset = { id: number; name: string };

async function fetchBannerData(datasetId: number) {
  const supabase = await createServerSupabaseClient();

  const { data: dataset } = await supabase
    .from("scrna_datasets")
    .select(
      "id, name, n_cells, assembly, annotation, strain, metadata, species_id, species(common_name, genus, species, illustration_path), people:scientist_id(name)",
    )
    .eq("id", datasetId)
    .single();

  if (!dataset) return null;

  const species = Array.isArray(dataset.species) ? dataset.species[0] : dataset.species;
  const scientist = Array.isArray(dataset.people) ? dataset.people[0] : dataset.people;

  const [siblingsRes, cellsRes] = await Promise.all([
    supabase
      .from("scrna_datasets")
      .select("id, name")
      .eq("species_id", dataset.species_id)
      .is("deleted_at", null)
      .neq("id", datasetId)
      .order("name"),
    supabase
      .from("scrna_cells")
      .select("cluster_id")
      .eq("dataset_id", datasetId)
      .limit(500000),
  ]);

  const siblings: SiblingDataset[] = (siblingsRes.data ?? []).map((r) => ({
    id: r.id,
    name: r.name,
  }));

  const clusterIds = new Set<string>();
  for (const row of cellsRes.data ?? []) {
    if (row.cluster_id) clusterIds.add(row.cluster_id);
  }
  const clusters = clusterIds.size || null;

  return { dataset, species, scientist, siblings, clusters };
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

export default async function ExpressionDatasetBanner({ datasetId, speciesId }: Props) {
  const data = await fetchBannerData(datasetId);
  if (!data) return null;

  const { dataset, species, scientist, siblings, clusters } = data;
  const assemblyAnnotation =
    dataset.assembly || dataset.annotation
      ? `${dataset.assembly ?? "—"} / ${dataset.annotation ?? "—"}`
      : "—";

  return (
    <section
      className="mb-8 p-6 rounded-xl border border-lime-200 bg-gradient-to-br from-lime-50 via-lime-50/40 to-white shadow-xl shadow-lime-300/35"
    >
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
              className="text-xl font-semibold text-stone-900 truncate"
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

          <MetadataDisclosure metadata={dataset.metadata} />
        </div>

        {siblings.length > 0 ? (
          <div className="shrink-0 flex flex-col gap-2 items-end">
            <div className="text-xs uppercase tracking-widest text-stone-500">
              Dataset
            </div>
            <div className="flex gap-2 flex-wrap justify-end max-w-md">
              <DatasetPill name={dataset.name} active />
              {siblings.map((s) => (
                <DatasetPill
                  key={s.id}
                  name={s.name}
                  href={`/app/expression/${speciesId}/${s.id}`}
                />
              ))}
            </div>
          </div>
        ) : null}
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

function DatasetPill({
  name,
  href,
  active = false,
}: {
  name: string;
  href?: string;
  active?: boolean;
}) {
  const classes = active
    ? "bg-lime-700 text-stone-50 border border-lime-700 cursor-default"
    : "border border-stone-300 text-stone-600 hover:bg-stone-50 hover:border-stone-400";
  const base =
    "rounded-full px-3 py-1 text-xs font-medium tabular-nums transition-colors";

  if (active || !href) {
    return <span className={`${base} ${classes}`}>{name}</span>;
  }
  return (
    <Link href={href} className={`${base} ${classes}`}>
      {name}
    </Link>
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
