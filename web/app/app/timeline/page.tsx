import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import TimelineHub from "./TimelineHub";
import { panelFromParam, type PanelId } from "./panels";
import CylinderPanel from "./panels/CylinderPanel";
import PlatePanel from "./panels/PlatePanel";
import RnaseqPanel from "./rnaseq/RnaseqPanel";

const PANEL_CONTENT: Record<PanelId, () => Promise<React.ReactElement>> = {
  cylinder: CylinderPanel,
  plate: PlatePanel,
  rnaseq: RnaseqPanel,
};

export default async function Timeline({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [user, params] = await Promise.all([getUser(), searchParams]);
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
