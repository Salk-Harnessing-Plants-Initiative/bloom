import Link from "next/link";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database, Json } from "@/lib/database.types";
import type { DataOrigin } from "@/lib/scrna-jobs";

// The short label of each origin the job form offers; a new origin needs one here.
export const ORIGIN_LABELS: Record<DataOrigin, string> = { hpi: "HPI", public: "Public" };

function isOrigin(value: unknown): value is DataOrigin {
  return typeof value === "string" && Object.hasOwn(ORIGIN_LABELS, value);
}

type WithMetadata = { metadata: Json | null };

function field(value: Json | null | undefined, key: string): Json | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value[key] : undefined;
}

function asOrigin(value: Json | undefined): DataOrigin | null {
  return isOrigin(value) ? value : null;
}

/** The origin recorded on a dataset (metadata.source.origin), set once its run is linked. */
function recordedOrigin(dataset: WithMetadata): DataOrigin | null {
  return asOrigin(field(field(dataset.metadata, "source"), "origin"));
}

/** The RNA-seq run a pipeline dataset was loaded for (metadata.rnaseq_run_id), recorded from
 * the start of its load. */
export function datasetRunId(dataset: WithMetadata): number | null {
  const id = field(dataset.metadata, "rnaseq_run_id");
  return typeof id === "number" && Number.isSafeInteger(id) ? id : null;
}

/** A dataset's origin: as recorded on it; otherwise as its run's form gave it, while the dataset
 * is still loading or wasn't linked to its run; otherwise HPI's own, including every dataset
 * loaded before origins were recorded. */
export function datasetOrigin(
  dataset: WithMetadata,
  runOrigins: ReadonlyMap<number, DataOrigin> = new Map(),
): DataOrigin {
  const runId = datasetRunId(dataset);
  return recordedOrigin(dataset) ?? (runId != null ? runOrigins.get(runId) : undefined) ?? "hpi";
}

/** The form origin of each run whose dataset has none recorded yet; empty if they can't be read,
 * so those datasets fall back to HPI. */
export async function fetchRunOrigins(
  supabase: SupabaseClient<Database>,
  datasets: WithMetadata[],
): Promise<Map<number, DataOrigin>> {
  const ids = [
    ...new Set(
      datasets
        .filter((d) => recordedOrigin(d) === null)
        .map(datasetRunId)
        .filter((id): id is number => id !== null),
    ),
  ];
  if (ids.length === 0) return new Map();
  const { data, error } = await supabase.from("rnaseq_runs").select("id, metadata").in("id", ids);
  if (error || !data) return new Map();
  const origins = new Map<number, DataOrigin>();
  for (const run of data) {
    const origin = asOrigin(field(run.metadata, "origin"));
    if (origin) origins.set(run.id, origin);
  }
  return origins;
}

/** The `?origin=` filter, or null for all datasets. */
export function originFilter(value: string | string[] | undefined): DataOrigin | null {
  return isOrigin(value) ? value : null;
}

export function DatasetOriginPill({ origin }: { origin: DataOrigin }) {
  const style =
    origin === "public"
      ? "border-sky-300 bg-sky-50 text-sky-800"
      : "border-lime-300 bg-lime-50 text-lime-800";
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${style}`}>
      {ORIGIN_LABELS[origin]}
    </span>
  );
}

/** All · HPI · Public, each with its count; the chosen one is marked as the current page. */
export function DatasetOriginFilter({
  basePath,
  current,
  counts,
}: {
  basePath: string;
  current: DataOrigin | null;
  counts: Record<DataOrigin, number>;
}) {
  const choices: [DataOrigin | null, string, number][] = [
    [null, "All", counts.hpi + counts.public],
    ["hpi", ORIGIN_LABELS.hpi, counts.hpi],
    ["public", ORIGIN_LABELS.public, counts.public],
  ];
  return (
    <nav aria-label="Filter datasets by origin" className="flex flex-wrap gap-2">
      {choices.map(([origin, label, count]) => {
        const active = origin === current;
        return (
          <Link
            key={label}
            href={origin ? `${basePath}?origin=${origin}` : basePath}
            aria-current={active ? "page" : undefined}
            className={[
              "rounded-full border px-3 py-1 text-xs transition-colors",
              active
                ? "border-lime-700 bg-lime-50 font-medium text-lime-800"
                : "border-stone-200 bg-stone-100 text-stone-600 hover:border-lime-700 hover:text-lime-700",
            ].join(" ")}
          >
            {label} <span className="tabular-nums text-stone-500">{count}</span>
          </Link>
        );
      })}
    </nav>
  );
}
