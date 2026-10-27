import { notFound } from "next/navigation";
import { fetchRun } from "@/lib/cyl-pipeline/queries";
import { isPipelineTriggerEnabled } from "@/lib/cyl-pipeline/trigger-enabled";
import { parseId } from "@/lib/route-params";
import { createServerSupabaseClient } from "@/lib/supabase/server";
import { Breadcrumbs } from "./Breadcrumbs";
import { RunDetailLive } from "./RunDetailLive";
import { parseStatusFilter } from "./status-filter";

export default async function RunPage({
  params,
  searchParams,
}: {
  params: Promise<{ runId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { runId } = await params;
  const id = parseId(runId);
  if (id === null) notFound();

  const client = await createServerSupabaseClient();
  let run;
  try {
    run = await fetchRun(client, id);
  } catch (e) {
    return (
      <div>
        <Breadcrumbs runId={id} />
        <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Could not load run {id}: {e instanceof Error ? e.message : String(e)}
        </div>
      </div>
    );
  }
  // Also the answer for a run hidden from the caller: RLS makes it invisible, not forbidden.
  if (!run) notFound();

  const { status } = await searchParams;
  return (
    <div>
      <Breadcrumbs runId={id} />
      <h1 className="text-xl mb-4">Run {id}</h1>
      <RunDetailLive initialRun={run} initialFilter={parseStatusFilter(status)} triggerEnabled={isPipelineTriggerEnabled()} />
    </div>
  );
}
