import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

import { orthobrowserUrl } from "./orthobrowser-url";

export default async function OrthofinderPage({
  searchParams,
}: {
  searchParams?: Promise<{ search?: string | string[] }>;
}) {
  const { search } = (await searchParams) ?? {};
  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/orthofinder",
  });

  return (
    <div className="w-full h-screen flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 bg-white rounded-lg border border-stone-200 mb-3 text-sm">
        <span className="text-neutral-600">
          Maintained by Nolan Hartwick{" "}
          <span className="font-bold text-neutral-500">(Michael Lab)</span>
        </span>
        <a
          href="mailto:nhartwick@salk.edu"
          className="text-lime-700 hover:text-lime-800 hover:underline transition-colors"
        >
          nhartwick@salk.edu
        </a>
      </div>

      <iframe
        src={orthobrowserUrl(search)}
        className="w-full border-0 rounded-lg flex-grow"
        title="HPI OrthoBrowser"
        allowFullScreen
      />
    </div>
  );
}
