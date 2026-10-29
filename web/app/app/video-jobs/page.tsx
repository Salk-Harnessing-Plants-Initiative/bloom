import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { loadJobsSnapshot } from "./load-jobs";
import { VideoJobsLive } from "./VideoJobsLive";

const JOB_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export default async function VideoJobsPage({
  searchParams,
}: {
  searchParams?: Promise<{ job?: string | string[] }>;
}) {
  const [client, user, params] = await Promise.all([
    createServerSupabaseClient(),
    getUser(),
    searchParams ?? Promise.resolve({} as { job?: string | string[] }),
  ]);
  const { jobs, hasOlder, places, error } = await loadJobsSnapshot(client);
  const asked = typeof params.job === "string" ? params.job : null;

  return (
    <div>
      <h1 className="text-xl mb-2">Video jobs</h1>
      <p className="mb-6 max-w-prose text-sm text-stone-500">
        Every member&apos;s plate time-lapse and cylinder scan video requests, updated live. Each
        kind has its own worker, which makes one video at a time in the order they were asked for.
      </p>
      <VideoJobsLive
        initialJobs={jobs}
        initialHasOlder={hasOlder}
        initialPlaces={places}
        currentUserId={user?.id ?? null}
        initialError={error}
        highlightJobId={asked !== null && JOB_ID.test(asked) ? asked : null}
      />
    </div>
  );
}
