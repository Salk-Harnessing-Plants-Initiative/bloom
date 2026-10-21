import Link from "next/link";
import { requesterText } from "@/lib/cyl-pipeline/run-text";
import { runHref } from "@/lib/rnaseq-reports";
import { runFastqUrl, runSraRuns, type RnaseqRun } from "@/lib/rnaseq-runs";

/** Where a pipeline dataset's reads came from: SRA (with the form's source link), or an S3
 * folder whose owner is the person who started the run. */
export type ReadsSource =
  | { kind: "sra"; runId: number; sraRuns: string[]; sourceUrl: string | null }
  | { kind: "s3"; runId: number; requester: string };

const SRA_RUN_URL = "https://www.ncbi.nlm.nih.gov/sra/";

/** The run's reads source, or null for a run that records neither SRA runs nor an S3 folder. */
export function readsSource(run: RnaseqRun, currentUserId: string | null): ReadsSource | null {
  const sraRuns = runSraRuns(run);
  if (sraRuns.length > 0) return { kind: "sra", runId: run.id, sraRuns, sourceUrl: webLink(run) };
  if (runFastqUrl(run)) {
    return { kind: "s3", runId: run.id, requester: requesterText(run.requested_by, currentUserId) };
  }
  return null;
}

/** The form's source link (metadata.source_url), only when it is a web address. */
function webLink(run: RnaseqRun): string | null {
  const meta = run.metadata;
  const url = meta && typeof meta === "object" && !Array.isArray(meta) ? meta["source_url"] : null;
  return typeof url === "string" && /^https?:\/\//i.test(url) ? url : null;
}

const linkClass = "text-lime-700 underline-offset-4 hover:underline break-all";

export function DatasetReads({ source }: { source: ReadsSource }) {
  const run = (
    <Link href={runHref(source.runId)} className={linkClass}>
      run {source.runId}
    </Link>
  );
  if (source.kind === "sra") {
    const sraLinks = new Set(source.sraRuns.map((id) => `${SRA_RUN_URL}${id}`));
    return (
      <p className="mt-3 text-xs text-stone-600">
        <span className="uppercase tracking-widest text-[10px] text-stone-500">Reads</span>{" "}
        from SRA{" "}
        {source.sraRuns.map((id, i) => (
          <span key={id}>
            {i > 0 ? ", " : null}
            <a href={`${SRA_RUN_URL}${id}`} target="_blank" rel="noreferrer" className={linkClass}>
              {id}
            </a>
          </span>
        ))}
        {source.sourceUrl && !sraLinks.has(source.sourceUrl) ? (
          <>
            {" · source "}
            <a href={source.sourceUrl} target="_blank" rel="noreferrer" className={linkClass}>
              {source.sourceUrl}
            </a>
          </>
        ) : null}{" "}
        · {run}
      </p>
    );
  }
  return (
    <p className="mt-3 text-xs text-stone-600">
      <span className="uppercase tracking-widest text-[10px] text-stone-500">Reads</span>{" "}
      {source.requester === "you" ? (
        <>from your S3 folder · {run}</>
      ) : (
        <>
          from an S3 folder; for the raw reads, contact the person who started {run} (
          {source.requester})
        </>
      )}
    </p>
  );
}
