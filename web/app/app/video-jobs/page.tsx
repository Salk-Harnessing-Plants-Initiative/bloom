import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { parseJobId } from "@/lib/supabase/video-jobs";
import { loadJobsSnapshot } from "./load-jobs";
import { VideoJobsLive } from "./VideoJobsLive";

type SearchParams = { job?: string | string[] };

export default async function VideoJobsPage({ searchParams }: { searchParams?: Promise<SearchParams> }) {
  const [client, user, params] = await Promise.all([
    createServerSupabaseClient(),
    getUser(),
    searchParams ?? Promise.resolve<SearchParams>({}),
  ]);
  const highlightJobId = parseJobId(typeof params.job === "string" ? params.job : null);
  const { jobs, hasOlder, places, active, highlighted, error } = await loadJobsSnapshot(client, highlightJobId);

  return (
    <div>
      <h1 className="text-xl mb-2">Video jobs</h1>
      <p className="mb-6 max-w-prose text-sm text-stone-500">
        Video requests from everyone in the lab. Videos are made one at a time, in the order they
        were requested.
      </p>
      <VideoJobsLive
        initialJobs={jobs}
        initialHasOlder={hasOlder}
        initialPlaces={places}
        initialActive={active}
        currentUserId={user?.id ?? null}
        initialError={error}
        highlightJobId={highlightJobId}
        highlightedJob={highlighted}
      />
    </div>
  );
}
