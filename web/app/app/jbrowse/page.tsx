import { getUser } from "@/lib/supabase/server";
// import Mixpanel from "mixpanel";
import JBrowseClient from "@/components/jbrowse-client";

export default async function Genotypes() {
  const user = await getUser();

  // const mixpanel = process.env.MIXPANEL_TOKEN
  //   ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
  //   : null;

  // mixpanel?.track("Page view", {
  //   distinct_id: user?.email,
  //   url: "/app/jbrowse",
  // });

  return (
    <div>
      <JBrowseClient />
    </div>
  );
}
