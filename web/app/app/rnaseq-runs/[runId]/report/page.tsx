import Link from "next/link";
import { notFound } from "next/navigation";
import { parseId } from "@/lib/route-params";
import { REPORTS_BUCKET, WEB_SUMMARY, readReport, reportPath } from "@/lib/rnaseq-reports";
import { createServerSupabaseClient } from "@/lib/supabase/server";

// How long the download link works.
const DOWNLOAD_SECONDS = 600;

/** A run's Cell Ranger report. It carries its own scripts, so it is shown in a sandboxed frame
 * as inline content: the scripts draw its plots but run apart from Bloom and its session. */
export default async function RnaseqRunReportPage({ params }: { params: Promise<{ runId: string }> }) {
  const id = parseId((await params).runId);
  if (id === null) notFound();

  const supabase = await createServerSupabaseClient();
  const report = await readReport(supabase, id, WEB_SUMMARY);
  const download = report
    ? await supabase.storage
        .from(REPORTS_BUCKET)
        .createSignedUrl(reportPath(id, WEB_SUMMARY), DOWNLOAD_SECONDS, {
          download: `run-${id}-${WEB_SUMMARY}`,
        })
    : null;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
        <Link href={`/app/rnaseq-runs/${id}`} className="text-sm text-lime-700 underline-offset-4 hover:underline">
          ← Run {id}
        </Link>
        {download?.data?.signedUrl ? (
          <a href={download.data.signedUrl} className="text-sm text-lime-700 underline-offset-4 hover:underline">
            Download the report
          </a>
        ) : null}
      </div>
      <h1 className="mb-4 text-xl font-serif italic">Cell Ranger report · run {id}</h1>
      {report ? (
        <iframe
          title={`Cell Ranger report for run ${id}`}
          srcDoc={report}
          sandbox="allow-scripts"
          className="h-[85vh] w-full rounded-md border border-stone-200 bg-white"
        />
      ) : (
        <p className="text-sm text-stone-500">No Cell Ranger report was stored for this run.</p>
      )}
    </div>
  );
}
