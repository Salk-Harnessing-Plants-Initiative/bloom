import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

export default async function Accessions() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/accessions",
  });

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Accessions</div>
      <div className="mb-6 select-none">
        Information about different plant accessions.
      </div>
    </div>
  );
}
