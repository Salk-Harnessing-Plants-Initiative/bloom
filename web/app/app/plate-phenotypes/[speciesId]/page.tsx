import Link from "next/link";
import type { SupabaseClient } from "@supabase/supabase-js";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

interface GraviExperimentRow {
  id: number;
  name: string;
  system_name: string | null;
  cyl_scientists: { scientist_name: string | null; email: string | null } | null;
  accessions: { name: string | null } | null;
  gravi_scans: { id: number }[];
}

interface SpeciesWithGraviExperimentsFull {
  id: number;
  common_name: string | null;
  gravi_experiments: GraviExperimentRow[];
}

export default async function PlateSpecies({
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
    url: `/app/plate-phenotypes/${speciesId}`,
  });

  if (
    !species ||
    !species.gravi_experiments ||
    species.gravi_experiments.length === 0
  ) {
    return (
      <div>
        <Breadcrumb commonName={species?.common_name ?? "Unknown species"} />
        <div className="text-neutral-500 italic">
          No plate experiments found for this species.
        </div>
      </div>
    );
  }

  return (
    <div>
      <Breadcrumb commonName={species.common_name ?? ""} />
      <p className="mb-6 max-w-2xl text-sm text-stone-500">
        Plate (gravitropism) experiments on this species. Per-experiment
        drilldown is coming soon.
      </p>

      <div className="table-auto select-none">
        {species.gravi_experiments.map((experiment) => {
          const scanCount = experiment.gravi_scans?.length ?? 0;
          const accessionName = experiment.accessions?.name ?? null;
          const scientistName = experiment.cyl_scientists?.scientist_name ?? null;
          const scientistEmail = experiment.cyl_scientists?.email ?? null;

          return (
            <div className="table-row" key={experiment.id}>
              <div className="table-cell align-middle p-4">
                <div className="text-lg text-stone-800">
                  {capitalizeFirstLetter(experiment.name.replaceAll("-", " "))}
                  {experiment.system_name && (
                    <span className="ml-3 text-sm font-normal text-stone-400">
                      {experiment.system_name}
                    </span>
                  )}
                  {scientistEmail && (
                    <a
                      href={`mailto:${scientistEmail}`}
                      className="ml-2 inline-block align-middle"
                      title={scientistName ?? scientistEmail}
                    >
                      <EmailIcon />
                    </a>
                  )}
                </div>
                <div className="mt-2 text-sm text-neutral-400">
                  {accessionName ? `${accessionName} · ` : ""}
                  {scanCount} scan{scanCount === 1 ? "" : "s"}
                  {scientistName ? ` · Led by ${scientistName}` : ""}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Breadcrumb({ commonName }: { commonName: string }) {
  return (
    <div className="text-xl mb-8 select-none">
      <span className="text-stone-400">
        <span className="hover:underline">
          <Link href="/app/plate-phenotypes">All species</Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span className="capitalize">{commonName}</span>
    </div>
  );
}

function EmailIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
      className="w-4 h-4 inline-block -mt-1 opacity-50 hover:opacity-100"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"
      />
    </svg>
  );
}

function capitalizeFirstLetter(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

async function getSpeciesWithExperiments(
  speciesId: number,
): Promise<SpeciesWithGraviExperimentsFull | null> {
  const supabase = await createServerSupabaseClient();

  const { data } = await (supabase as unknown as SupabaseClient<unknown>)
    .from("species")
    .select(
      "id, common_name, gravi_experiments!inner(id, name, system_name, cyl_scientists(scientist_name, email), accessions(name), gravi_scans(id))",
    )
    .eq("id", speciesId)
    .single();

  return (data as SpeciesWithGraviExperimentsFull | null) ?? null;
}
