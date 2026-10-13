import Link from "next/link";
import type { Json } from "@/lib/database.types";

// Where a dataset's data came from: HPI's own, or a public dataset.
export type DatasetOrigin = "hpi" | "public";

export const ORIGIN_LABELS: Record<DatasetOrigin, string> = { hpi: "HPI", public: "Public" };

/** A dataset's origin: "public" when its run marked it so, otherwise HPI's own, including every
 * dataset loaded before origins were recorded (metadata.source.origin, set by the pipeline). */
export function datasetOrigin(dataset: { metadata: Json | null }): DatasetOrigin {
  const metadata = dataset.metadata;
  if (metadata && typeof metadata === "object" && !Array.isArray(metadata)) {
    const source = metadata.source;
    if (source && typeof source === "object" && !Array.isArray(source) && source.origin === "public") {
      return "public";
    }
  }
  return "hpi";
}

/** The `?origin=` filter, or null for all datasets. */
export function originFilter(value: string | string[] | undefined): DatasetOrigin | null {
  return value === "hpi" || value === "public" ? value : null;
}

export function DatasetOriginPill({ origin }: { origin: DatasetOrigin }) {
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
  current: DatasetOrigin | null;
  counts: Record<DatasetOrigin, number>;
}) {
  const choices: [DatasetOrigin | null, string, number][] = [
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
