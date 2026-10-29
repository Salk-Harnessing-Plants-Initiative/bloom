import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { loadRunsSnapshot } from "./load-runs";
import { RunsListLive } from "./RunsListLive";

export default async function PipelineRunsPage() {
  const [client, user] = await Promise.all([createServerSupabaseClient(), getUser()]);
  const { runs, experiments, error } = await loadRunsSnapshot(client);

  return (
    <div>
      <h1 className="text-xl mb-2">Cylinder pipeline runs</h1>
      <p className="mb-6 max-w-prose text-sm text-stone-500">
        Every member&apos;s cylinder pipeline runs, updated live. A run&apos;s state comes from its per-scan
        counts: results arrive when each batch of up to 25 scans finishes.
      </p>
      <RunsListLive initialRuns={runs} initialExperiments={experiments} currentUserId={user?.id ?? null} initialError={error} />
    </div>
  );
}
