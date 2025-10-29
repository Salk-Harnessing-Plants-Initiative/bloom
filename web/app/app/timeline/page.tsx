import {
  createServerSupabaseClient,
  getUser,
} from "@salk-hpi/bloom-nextjs-auth";

import Calendar from "@/components/calendar";

import Mixpanel from "mixpanel";

export default async function Timeline() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/timeline",
  });

  // const scanTimeline = await getScanTimeline();
  const waveTimeline = (await getWaveTimeline()) as WaveRow[] | null;

  const data =
    (waveTimeline as WaveRow[] | null)?.map((x) => {
      return {
        date: new Date(x.date_scanned || ""),
        count: x.count || 0,
      };
    }) || [];

  return (
    <div>
      <div className="italic text-xl mb-8 select-none">Timeline</div>
      <div className="mb-6 mt-6 select-none">Batches of cylinders scanned.</div>
      <div className="mb-4 h-48 overflow-scroll border-2 w-[600px] p-4 rounded-md">
        <table>
          <thead>
            <tr>
              <th className="text-sm text-left pr-4">Date</th>
              <th className="text-sm text-left pr-4">Species</th>
              <th className="text-sm text-left pr-4">Experiment</th>
              <th className="text-sm text-left pr-4">Wave</th>
              <th className="text-sm text-left pr-4">Scans</th>
            </tr>
          </thead>
          <tbody>
            {waveTimeline?.map((row) => (
              <tr key={generateKey(row)}>
                <td className="pr-4">{row.date_scanned}</td>
                <td className="pr-4">{row.species_name}</td>
                <td className="pr-4">{row.experiment_name}</td>
                <td className="pr-4">{row.wave_number}</td>
                <td className="pr-4">{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div>
        <div className="mb-6 mt-6 select-none">
          Total cylinders scanned on each day.
        </div>
        <div className="bg-white rounded-md w-[1000px]">
          {<Calendar data={data} />}
        </div>
      </div>
    </div>
  );
}

import type { Database } from "@/lib/database.types";

type WaveRow = Database["public"]["Views"]["cyl_wave_timeline"]["Row"];

function generateKey(row: WaveRow) {
  return row.date_scanned + "-" + row.experiment_name + "-" + row.wave_number;
}

async function getScanTimeline() {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase.from("cyl_scan_timeline").select("*");

  return data;
}

async function getWaveTimeline() {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase.from("cyl_wave_timeline").select("*");

  return data;
}
