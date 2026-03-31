import Link from "next/link";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import { Key } from "react";

export default async function Species({
  params,
}: {
  params: { speciesId: number };
}) {
  const species: any  = await getSpeciesWithExperiments(params.speciesId);

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/traits/${params.speciesId}`,
  });

  return (
    <>
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/traits">All species</Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="capitalize">{species?.common_name}</span>
      </div>
      <div className="table-auto select-none">
        {species?.cyl_experiments.map((experiment: { id: Key | null | undefined; name: string; }) => (
          <div className="table-row" key={experiment.id}>
            <div className="table-cell text-lg align-middle p-4">
              <div className="align-middle">
                <Link
                  href={`/app/traits/${species.id}/${experiment.id}`}
                  key={experiment.id}
                >
                  <span className="text-lime-700 hover:underline">
                    {capitalizeFirstLetter(
                      experiment.name.replaceAll("-", " ")
                    )}
                  </span>
                </Link>
                <div className="text-sm mt-2 text-neutral-400">
                  {getAccessionCount(experiment)}
                  &nbsp;/&nbsp;
                  {getPlantCount(experiment)}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  </>
  );
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

function getPlantCount(experiment: any) {
  let plantCount = 0;
  experiment.cyl_waves.forEach((wave: any) => {
    plantCount += wave.cyl_plants.length;
  });
  return `${plantCount} replicates`;
}

function getAccessionCount(experiment: any) {
  // empty array of strings
  let accessionNames: string[] = [];
  experiment.cyl_waves.forEach((wave: any) => {
    wave.cyl_plants.forEach((plant: any) => {
      accessionNames.push(plant.accessions.name);
    });
  });
  const lineCount = new Set(accessionNames).size;
  return `${lineCount} accessions`;
}

async function getSpeciesWithExperiments(speciesId: number) {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select(
      "*, cyl_experiments!inner(*, cyl_waves(*, cyl_plants(*, accessions(*))))"
    )
    .eq("id", speciesId)
    .neq("cyl_experiments.deleted", true)
    .single();

  return data;
}


