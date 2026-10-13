import { permanentRedirect } from "next/navigation";
import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import TimelineHub from "./TimelineHub";
import { panelFromParam, type PanelId } from "./panels";
import CylinderPanel from "./panels/CylinderPanel";
import PlatePanel from "./panels/PlatePanel";

const PANEL_CONTENT: Record<PanelId, () => Promise<React.ReactElement>> = {
  cylinder: CylinderPanel,
  plate: PlatePanel,
};

// RNA-seq runs were a Timeline panel; the old address now goes to their own page.
const FORMER_RNASEQ_PANEL = "rnaseq";

export default async function Timeline({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [user, params] = await Promise.all([getUser(), searchParams]);
  if ([params.panel].flat()[0] === FORMER_RNASEQ_PANEL) permanentRedirect("/app/rnaseq-runs");
  const panel = panelFromParam(params.panel);

  const mixpanel = process.env.MIXPANEL_TOKEN ? Mixpanel.init(process.env.MIXPANEL_TOKEN) : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/timeline",
    panel,
  });

  const Content = PANEL_CONTENT[panel];
  return (
    <div>
      <div className="mb-6 select-none text-xl italic">Timeline</div>
      <TimelineHub open={panel}>
        <Content />
      </TimelineHub>
    </div>
  );
}
