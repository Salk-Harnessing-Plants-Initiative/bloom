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
  const species : any = await getSpeciesWithExperiments(params.speciesId);

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/phenotypes/${params.speciesId}`,
  });

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/phenotypes">All species</Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="capitalize">{species?.common_name}</span>
      </div>
      <div className="text-sm mb-4 text-stone-500 align-middle max-w-[650px] flex flex-row items-center">
        {/* <div>
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
            className="w-6 h-6 inline-block mr-4 -mt-1"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"
            />
          </svg>
        </div> */}
        <div>
          <div className="mb-1">
            Each experiment below has a Slack channel (
            <img
              src="/slack.svg"
              className="w-8 h-8 inline pb-1 opacity-70 -my-2 -mx-2"
            />
            ) and the lead scientist's email address (
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              className="w-4 h-4 inline-block -mt-1"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"
              />
            </svg>
            ).
          </div>
        </div>
      </div>
      <div className="table-auto select-none">
        {species?.cyl_experiments.map((experiment: { id: Key | null | undefined; name: string; slack_channel_url: string | undefined; people: any; description: string; }) => (
          <div className="table-row" key={experiment.id}>
            <div className="table-cell text-lg align-middle p-4">
              <div className="align-middle">
                <Link
                  href={`/app/phenotypes/${species.id}/${experiment.id}`}
                  key={experiment.id}
                  className="mr-4"
                >
                  <span className="text-lime-700 hover:underline">
                    {capitalizeFirstLetter(
                      experiment.name.replaceAll("-", " ")
                    )}
                  </span>
                </Link>
                {experiment.slack_channel_url && (
                  <a href={experiment.slack_channel_url} target="_blank">
                    <img
                      src="/slack.svg"
                      className="w-8 h-8 inline pb-1 opacity-70 hover:opacity-100 -my-2"
                    />
                  </a>
                )}
                {(
                  experiment.people as any as {
                    email: string | null;
                    id: number;
                    name: string | null;
                  }
                )?.email && (
                  <a
                    href={`mailto:${
                      (
                        experiment.people as any as {
                          email: string | null;
                          id: number;
                          name: string | null;
                        }
                      )?.email
                    }`}
                  >
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth={1.5}
                      stroke="currentColor"
                      className="w-4 h-4 inline-block -mt-1 ml-2 opacity-50 hover:opacity-100 "
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"
                      />
                    </svg>
                  </a>
                )}
                <div className="text-sm mt-2 text-neutral-400">
                  {getAccessionCount(experiment)}
                  &nbsp;/&nbsp;
                  {getPlantCount(experiment)}
                </div>
                <div>
                  <span className="text-sm text-neutral-400 italic">
                    {experiment.description
                      ? experiment.description.slice(0, 100) +
                        (experiment.description.length > 100 ? "..." : "")
                      : "No description provided."}
                  </span>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
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
      "*, cyl_experiments!inner(*, people(*), cyl_waves(*, cyl_plants(*, accessions(*))))"
    )
    .eq("id", speciesId)
    .neq("cyl_experiments.deleted", true)
    .single();

  return data;
}
