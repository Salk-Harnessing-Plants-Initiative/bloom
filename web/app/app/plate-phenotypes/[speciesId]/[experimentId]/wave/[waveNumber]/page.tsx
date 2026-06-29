import { getUser } from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { getExperiment } from "../../getExperiment";
import { groupByWave, parseWaveKey, waveLabel } from "../../plateGrouping";
import { PlateList } from "../../PlateList";
import {
  Breadcrumb,
  ExperimentHeaderless,
  formatExperimentName,
} from "../../ui";

export default async function WavePlates({
  params,
}: {
  params: Promise<{
    speciesId: string;
    experimentId: string;
    waveNumber: string;
  }>;
}) {
  const { speciesId, experimentId, waveNumber } = await params;
  const experiment = await getExperiment(Number(experimentId));

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}/${experimentId}/wave/${waveNumber}`,
  });

  const target = parseWaveKey(waveNumber);
  const waves = experiment ? groupByWave(experiment.gravi_scans ?? []) : [];
  const wave = waves.find((w) => w.waveNumber === target) ?? null;

  const trail = [
    { label: "All species", href: "/app/plate-phenotypes" },
    {
      label: experiment?.species?.common_name || "Species",
      href: `/app/plate-phenotypes/${speciesId}`,
      capitalize: true,
    },
    {
      label: experiment ? formatExperimentName(experiment.name) : "Experiment",
      href: `/app/plate-phenotypes/${speciesId}/${experimentId}`,
    },
    { label: waveLabel(target) },
  ];

  if (!experiment || !wave) {
    return (
      <div>
        <Breadcrumb trail={trail} />
        <div className="text-neutral-500 italic">
          {experiment ? "Wave not found." : "Experiment not found."}
        </div>
      </div>
    );
  }

  return (
    <div>
      <Breadcrumb trail={trail} />
      <ExperimentHeaderless
        title={`${formatExperimentName(experiment.name)} · ${waveLabel(target)}`}
        plateCount={wave.plates.length}
      />
      <PlateList
        plates={wave.plates}
        speciesId={speciesId}
        experimentId={experimentId}
        waveParam={waveNumber}
      />
    </div>
  );
}
