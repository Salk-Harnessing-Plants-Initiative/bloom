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
  params: Promise<{ speciesId: string }>;
}) {
  const { speciesId } = await params;
  const species = await getSpeciesWithExperiments(Number(speciesId));

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/traits/${speciesId}`,
  });

  if (
    !species ||
    !species.cyl_experiments ||
    species.cyl_experiments.length === 0
  ) {
    return (
      <div>
        <div className="text-xl mb-8 select-none">
          <span className="text-stone-400">
            <span className="hover:underline">
              <Link href="/app/traits">All species</Link>
            </span>
            &nbsp;▸&nbsp;
          </span>
          <span className="capitalize">
            {species?.common_name || "Unknown Species"}
          </span>
        </div>
        <div className="text-neutral-500 italic">
          No experiments found for this species.
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/traits">All species</Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="capitalize">{species.common_name}</span>
      </div>

      <div className="text-sm mb-4 text-stone-500 align-middle max-w-[650px] flex flex-row items-center">
        <div>
          <div className="mb-1">
            Each experiment below has a Slack channel (
            <img
              src="/slack.svg"
              alt="Slack"
              className="w-8 h-8 inline pb-1 opacity-70 -my-2 -mx-2"
            />
            ) and the lead scientist&apos;s email address (
            <EmailIcon className="w-4 h-4 inline-block -mt-1" />
            ).
          </div>
        </div>
      </div>

      <div className="table-auto select-none">
        {species.cyl_experiments.map(
          (experiment: {
            id: Key | null | undefined;
            name: string;
            slack_channel_url: string | null | undefined;
            people: { email: string | null; id: number; name: string | null } | null;
            description: string | null;
            cyl_waves: {
              id: number;
              cyl_plants: { id: number; accessions: { name: string | null } | null }[];
            }[];
          }) => (
            <div className="table-row" key={experiment.id}>
              <div className="table-cell text-lg align-middle p-4">
                <div className="align-middle">
                  <Link
                    href={`/app/traits/${species.id}/${experiment.id}`}
                    key={experiment.id}
                    className="mr-4"
                  >
                    <span className="text-lime-700 hover:underline">
                      {capitalizeFirstLetter(
                        experiment.name.replaceAll("-", " "),
                      )}
                    </span>
                  </Link>
                  {experiment.slack_channel_url && (
                    <a
                      href={experiment.slack_channel_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <img
                        src="/slack.svg"
                        alt="Slack channel"
                        className="w-8 h-8 inline pb-1 opacity-70 hover:opacity-100 -my-2"
                      />
                    </a>
                  )}
                  {experiment.people?.email && (
                    <a href={`mailto:${experiment.people.email}`}>
                      <EmailIcon className="w-4 h-4 inline-block -mt-1 ml-2 opacity-50 hover:opacity-100" />
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
          ),
        )}
      </div>
    </div>
  );
}

function EmailIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"
      />
    </svg>
  );
}

function capitalizeFirstLetter(string: string): string {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

interface ExperimentForCount {
  cyl_waves?:
    | {
        cyl_plants?:
          | { accessions?: { name?: string | null } | null }[]
          | null;
      }[]
    | null;
}

function getPlantCount(experiment: ExperimentForCount): string {
  if (!experiment?.cyl_waves || !Array.isArray(experiment.cyl_waves)) {
    return "0 replicates";
  }

  let plantCount = 0;
  experiment.cyl_waves.forEach((wave) => {
    if (wave?.cyl_plants && Array.isArray(wave.cyl_plants)) {
      plantCount += wave.cyl_plants.length;
    }
  });
  return `${plantCount} replicates`;
}

function getAccessionCount(experiment: ExperimentForCount): string {
  if (!experiment?.cyl_waves || !Array.isArray(experiment.cyl_waves)) {
    return "0 accessions";
  }

  const accessionNames: string[] = [];
  experiment.cyl_waves.forEach((wave) => {
    if (wave?.cyl_plants && Array.isArray(wave.cyl_plants)) {
      wave.cyl_plants.forEach((plant) => {
        if (plant?.accessions?.name) {
          accessionNames.push(plant.accessions.name);
        }
      });
    }
  });
  const lineCount = new Set(accessionNames).size;
  return `${lineCount} accessions`;
}

interface SpeciesWithExperiments {
  id: number;
  common_name: string | null;
  cyl_experiments: {
    id: number;
    name: string;
    slack_channel_url: string | null;
    people: { email: string | null; id: number; name: string | null } | null;
    description: string | null;
    cyl_waves: {
      id: number;
      cyl_plants: { id: number; accessions: { name: string | null } | null }[];
    }[];
  }[];
}

async function getSpeciesWithExperiments(
  speciesId: number,
): Promise<SpeciesWithExperiments | null> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select(
      "*, cyl_experiments!inner(*, people(*), cyl_waves(*, cyl_plants(*, accessions(*))))",
    )
    .eq("id", speciesId)
    .neq("cyl_experiments.deleted", true)
    .single();

  return data as SpeciesWithExperiments | null;
}
