import Link from "next/link";
import {
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { getExperiment } from "./getExperiment";
import {
  groupByWave,
  waveKey,
  waveLabel,
  waveScanDateRange,
} from "./plateGrouping";
import { PlateList } from "./PlateList";
import {
  Breadcrumb,
  formatExperimentName,
  formatScanDateRange,
} from "./ui";

export default async function PlateExperiment({
  params,
}: {
  params: Promise<{ speciesId: string; experimentId: string }>;
}) {
  const { speciesId, experimentId } = await params;
  const experiment = await getExperiment(Number(experimentId));

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/plate-phenotypes/${speciesId}/${experimentId}`,
  });

  if (!experiment) {
    return (
      <div>
        <Breadcrumb
          trail={[
            { label: "All species", href: "/app/plate-phenotypes" },
            { label: "Species", href: `/app/plate-phenotypes/${speciesId}` },
            { label: "Experiment" },
          ]}
        />
        <div className="text-neutral-500 italic">Experiment not found.</div>
      </div>
    );
  }

  const waves = groupByWave(experiment.gravi_scans ?? []);
  const allPlates = waves.flatMap((w) => w.plates);

  return (
    <div>
      <Breadcrumb
        trail={[
          { label: "All species", href: "/app/plate-phenotypes" },
          {
            label: experiment.species?.common_name || "Species",
            href: `/app/plate-phenotypes/${speciesId}`,
            capitalize: true,
          },
          { label: formatExperimentName(experiment.name) },
        ]}
      />

      <ExperimentHeader
        name={experiment.name}
        systemName={experiment.system_name}
        accessionName={experiment.accessions?.name ?? null}
        scientistName={experiment.cyl_scientists?.scientist_name ?? null}
        waveCount={waves.length}
        plateCount={allPlates.length}
      />

      {allPlates.length === 0 ? (
        <div className="text-neutral-500 italic">
          No plate scans uploaded yet.
        </div>
      ) : waves.length === 1 && waves[0].waveNumber === null ? (
        // No wave structure (legacy / unassigned) — skip the wave list.
        <PlateList
          plates={waves[0].plates}
          speciesId={speciesId}
          experimentId={experimentId}
          waveParam={waveKey(waves[0].waveNumber)}
        />
      ) : (
        <>
          <p className="mb-4 text-sm text-stone-500">
            Select a wave to view its plates.
          </p>
          <div className="table-auto select-none">
            {waves.map((wave) => {
              const timepoints = wave.plates.length
                ? Math.max(...wave.plates.map((p) => p.scans.length))
                : 0;
              const { first, last } = waveScanDateRange(wave.plates);
              const range = formatScanDateRange(first, last);
              const stats = [
                range ? `Scanned ${range}` : null,
                `${wave.plates.length} plate${wave.plates.length === 1 ? "" : "s"}`,
                timepoints > 0
                  ? `${timepoints} time point${timepoints === 1 ? "" : "s"}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ");

              return (
                <div className="table-row" key={waveKey(wave.waveNumber)}>
                  <div className="table-cell align-middle p-4">
                    <div className="text-lg align-middle">
                      <Link
                        href={`/app/plate-phenotypes/${speciesId}/${experimentId}/wave/${waveKey(wave.waveNumber)}`}
                        className="text-lime-700 hover:underline"
                      >
                        {waveLabel(wave.waveNumber)}
                      </Link>
                    </div>
                    {stats && (
                      <div className="mt-2 text-sm text-neutral-400">
                        {stats}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export function ExperimentHeader({
  name,
  systemName,
  accessionName,
  scientistName,
  waveCount,
  plateCount,
}: {
  name: string;
  systemName: string | null;
  accessionName: string | null;
  scientistName: string | null;
  waveCount: number;
  plateCount: number;
}) {
  return (
    <div className="mb-6">
      <div className="text-3xl font-serif italic mb-1 select-none">
        {formatExperimentName(name)}
      </div>
      <div className="text-sm text-stone-500">
        {[
          systemName,
          accessionName,
          scientistName ? `Led by ${scientistName}` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      <div className="mt-2 text-lg font-medium text-stone-700">
        {waveCount > 1 && `${waveCount} waves · `}
        {plateCount} plate{plateCount === 1 ? "" : "s"}
      </div>
    </div>
  );
}
