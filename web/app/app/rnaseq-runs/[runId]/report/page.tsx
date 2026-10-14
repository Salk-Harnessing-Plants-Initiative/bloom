import Link from "next/link";
import { notFound } from "next/navigation";
import { parseId } from "@/lib/route-params";
import { WEB_SUMMARY, frameDocument, readReport, runHref } from "@/lib/rnaseq-reports";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** A run's Cell Ranger report. It carries its own scripts, so it is shown in a sandboxed frame
 * as inline content: the scripts draw its plots but run apart from Bloom and its session. */
export default async function RnaseqRunReportPage({ params }: { params: Promise<{ runId: string }> }) {
  const id = parseId((await params).runId);
  if (id === null) notFound();

  const report = await readReport(await createServerSupabaseClient(), id, WEB_SUMMARY);

  return (
    <div>
      <div className="mb-4">
        <Link href={runHref(id)} className="text-sm text-lime-700 underline-offset-4 hover:underline">
          ← Run {id}
        </Link>
      </div>
      <h1 className="mb-4 text-xl font-serif italic">Cell Ranger report · run {id}</h1>
      {"text" in report ? (
        <iframe
          title={`Cell Ranger report for run ${id}`}
          srcDoc={frameDocument(report.text)}
          sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
          className="h-[85vh] w-full rounded-md border border-stone-200 bg-white"
        />
      ) : "error" in report ? (
        <p role="alert" className="text-sm text-red-700">
          Couldn&apos;t load the report: {report.error}
        </p>
      ) : (
        <p className="text-sm text-stone-500">No Cell Ranger report was stored for this run.</p>
      )}
    </div>
  );
}
