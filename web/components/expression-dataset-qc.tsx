import Link from "next/link";
import { reportHref, runHref } from "@/lib/rnaseq-reports";

/** A dataset's quality from the Cell Ranger run that made it: the key metrics and its report. */
export default function ExpressionDatasetQc({
  runId,
  metrics,
  hasReport,
}: {
  runId: number;
  metrics: [string, string][];
  hasReport: boolean;
}) {
  if (metrics.length === 0 && !hasReport) return null;
  return (
    <section aria-labelledby="dataset-qc" className="mb-6 rounded-md border border-stone-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="dataset-qc" className="text-xs uppercase tracking-widest text-stone-500">
          Quality control · Cell Ranger
        </h2>
        <span className="flex gap-4 text-sm">
          {hasReport ? (
            <Link href={reportHref(runId)} className="text-lime-700 underline-offset-4 hover:underline">
              Open the full report
            </Link>
          ) : null}
          <Link href={runHref(runId)} className="text-lime-700 underline-offset-4 hover:underline">
            Run {runId}
          </Link>
        </span>
      </div>
      {metrics.length > 0 ? (
        <p className="mb-3 text-xs text-stone-500">
          As Cell Ranger counted them, before Bloom&apos;s analysis kept only cells with enough genes; the
          dataset&apos;s own cell count is above.
        </p>
      ) : null}
      {metrics.length > 0 ? (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
          {metrics.map(([name, value]) => (
            <div key={name}>
              <dt className="text-xs text-stone-500">{name}</dt>
              <dd className="text-lg tabular-nums text-stone-800">{value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
    </section>
  );
}
