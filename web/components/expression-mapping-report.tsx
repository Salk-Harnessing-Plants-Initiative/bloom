"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClientSupabaseClient } from "@/lib/supabase/client";
import { WEB_SUMMARY, frameDocument, readReport, reportHref, type ReportRead } from "@/lib/rnaseq-reports";

/** The Cell Ranger report of the run that made the dataset, read when the tab first opens. It
 * carries its own scripts, so it is shown in the same sandboxed frame as the report's own page. */
export function ExpressionMappingReport({ runId }: { runId: number }) {
  const [report, setReport] = useState<ReportRead | null>(null);

  useEffect(() => {
    let current = true;
    readReport(createClientSupabaseClient(), runId, WEB_SUMMARY).then(
      (read) => {
        if (current) setReport(read);
      },
      (err: unknown) => {
        if (current) setReport({ error: err instanceof Error ? err.message : String(err) });
      },
    );
    return () => {
      current = false;
    };
  }, [runId]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-stone-600">
          Cell Ranger&apos;s report for run {runId}: how the reads mapped to the genome, how cells
          were called, and sequencing quality.
        </p>
        <Link href={reportHref(runId)} className="text-sm text-lime-700 underline-offset-4 hover:underline">
          Open on its own page
        </Link>
      </div>
      {report === null ? (
        <p className="text-sm text-stone-500">Loading the report…</p>
      ) : "text" in report ? (
        <iframe
          title={`Cell Ranger report for run ${runId}`}
          srcDoc={frameDocument(report.text)}
          sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"
          className="h-[80vh] w-full rounded-md border border-stone-200 bg-white"
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
