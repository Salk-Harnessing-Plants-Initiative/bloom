import Link from "next/link";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import ScientistBadge from "@/components/scientist-badge";
import ExperimentDescription from "@/components/experiment-description";

type Plant = {
  created_at: string;
  germ_day: number | null;
  germ_day_color: string | null;
  id: number;
  qr_code: string | null;
  wave_id: number | null;
  accessions: {
    created_at: string;
    id: number;
    name: string;
  } | null;
};

type Wave = {
  experiment_id: number | null;
  id: number;
  name: string | null;
  number: number | null;
  cyl_plants: Plant[];
};

function getAccessions(wave: Wave) {
  // empty array of strings
  let lineNames: string[] = [];
  let lineName2Id: { [key: string]: number } = {};
  wave.cyl_plants.forEach((plant: Plant) => {
    lineNames.push(plant.accessions?.name ?? "");
    lineName2Id[plant.accessions?.name ?? ""] = plant.accessions?.id ?? 0;
  });
  const plantCountObj = countStrings(lineNames);
  const plantCountArray = Object.entries(plantCountObj);
  // sort by name
  const plantCountArraySorted = plantCountArray.sort((a, b) => {
    if (a[0] < b[0]) {
      return -1;
    }
    if (a[0] > b[0]) {
      return 1;
    }
    return 0;
  });
  const plantNameCountsArray = plantCountArraySorted.map(([name, count]) => ({
    name,
    id: lineName2Id[name],
    count,
  }));
  return plantNameCountsArray;
}

export default async function Experiment({
  params,
}: {
  params: Promise<{ experimentId: string; speciesId: string }>;
}) {
  const { experimentId, speciesId } = await params;
  const experiment : any = await getExperimentWithPlants(Number(experimentId));
  console.log("Experiment data:", experiment);
  const experimentName = capitalizeFirstLetter(
    experiment?.name.replaceAll("-", " ") ?? ""
  );
  const speciesName = experiment?.species?.common_name ?? "";

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/phenotypes/${speciesId}/${experimentId}`,
  });

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/phenotypes">All species</Link>
          </span>
          &nbsp;▸&nbsp;
          <span className="hover:underline capitalize">
            <Link href={`/app/phenotypes/${experiment?.species?.id}`}>
              {speciesName}
            </Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="">{experimentName}</span>
      </div>

      <ExperimentDescription experiment={experiment} />

      <div className="mb-4">
        {experiment?.people && <ScientistBadge person={experiment.people} />}
      </div>
      <div className="text-lg align-middle">
        {/* <span className="">Accessions</span> */}
        <div
          className={
            "table-auto pl-4 text-sm " +
            (experiment?.cyl_waves.length === 1 ? "mt-8" : "mt-2")
          }
        >
          {experiment?.cyl_waves
            .sort((a: { number: any; }, b: { number: any; }) => (a.number ?? 0) - (b.number ?? 0))
            .map((wave: { number: any; id: any; experiment_id?: number | null; name?: string | null; cyl_plants?: Plant[]; }) => {
              // Ensure all required Wave properties are present and not undefined
              const safeWave: Wave = {
                experiment_id: wave.experiment_id ?? null,
                id: wave.id,
                name: wave.name ?? null,
                number: wave.number ?? null,
                cyl_plants: wave.cyl_plants ?? [],
              };
              return (
                <div className="table-row-group" key={"wave-" + wave.number}>
                  {experiment?.cyl_waves.length > 1 && (
                    <div className="table-row" key={"wave-title-" + wave.number}>
                      <div className="table-cell pt-4 pb-2 text-neutral-400">
                        Wave {wave.number}
                      </div>
                      <div className="table-cell"></div>
                    </div>
                  )}
                  {getAccessions(safeWave).map(({ name, count, id }) => (
                    <div className="table-row" key={"accession-" + id}>
                      <div className="table-cell">
                        <Link
                          href={`/app/phenotypes/${experiment?.species?.id}/${experiment?.id}/${wave.id}/${id}`}
                        >
                          <span className="text-lime-700 hover:underline">
                            {name}
                          </span>
                        </Link>
                      </div>
                      <div className="table-cell pl-8">
                        <span className="text-neutral-400">
                          &nbsp;{count} replicates
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

async function getExperimentWithPlants(experimentId: number) {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("cyl_experiments")
    .select(
      "*, cyl_waves(*, cyl_plants(*, accessions!inner(*))), species(*), people(*)"
    )
    .eq("id", experimentId)
    .single();

  return data;
}

function getPlantCount(experiment: any) {
  let plantCount = 0;
  experiment.cyl_waves.forEach((wave: any) => {
    plantCount += wave.cyl_plants.length;
  });
  return `${plantCount} plants`;
}

function getLineCount(experiment: any) {
  // empty array of strings
  let lineNames: string[] = [];
  experiment.cyl_waves.forEach((wave: any) => {
    wave.cyl_plants.forEach((plant: any) => {
      lineNames.push(plant.accession_name);
    });
  });
  const lineCount = new Set(lineNames).size;
  return `${lineCount} lines`;
}

function countStrings(strings: string[]) {
  const count: { [key: string]: number } = {};
  strings.forEach((s) => {
    count[s] = (count[s] || 0) + 1;
  });
  return count;
}
