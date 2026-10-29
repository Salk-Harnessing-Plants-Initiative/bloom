import Link from "next/link";
import { runHref } from "@/lib/rnaseq-reports";
import { runFastqUrl, runSraRuns, type RnaseqRun } from "@/lib/rnaseq-runs";
import { sraRunUrl } from "@/lib/sra-runs";

/** Where a pipeline dataset's reads came from: SRA (with the form's source link), or an S3
 * folder, which isn't named. */
export type ReadsSource =
  | { kind: "sra"; runId: number; sraRuns: string[]; sourceUrl: string | null }
  | { kind: "s3"; runId: number };

/** The run's reads source, or null for a run that records neither SRA runs nor an S3 folder. */
export function readsSource(run: RnaseqRun): ReadsSource | null {
  const sraRuns = runSraRuns(run);
  if (sraRuns.length > 0) return { kind: "sra", runId: run.id, sraRuns, sourceUrl: webLink(run) };
  if (runFastqUrl(run)) return { kind: "s3", runId: run.id };
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
  const label = <span className="uppercase tracking-widest text-[10px] text-stone-500">Reads</span>;
  if (source.kind === "s3") {
    return (
      <p className="mt-3 text-xs text-stone-600">
        {label} uploaded via S3 · {run}
      </p>
    );
  }
  const sraLinks = new Set(source.sraRuns.map(sraRunUrl));
  return (
    <p className="mt-3 text-xs text-stone-600">
      {label} from SRA{" "}
      {source.sraRuns.map((id, i) => (
        <span key={`${i}-${id}`}>
          {i > 0 ? ", " : null}
          <a href={sraRunUrl(id)} target="_blank" rel="noreferrer" className={linkClass}>
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
