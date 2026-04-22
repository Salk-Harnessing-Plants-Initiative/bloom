import Link from "next/link";
import Illustration from "@/components/illustration";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

import type { SpeciesWithExperiments } from "@/lib/custom.types";

export default async function AllSpecies() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/phenotypes",
  });

  const speciesList = await getSpeciesList();

  return (
    <div>
      <div className="mb-1 text-sm uppercase tracking-widest text-stone-500">
        Phenotypes
      </div>
      <div className="text-3xl font-serif italic mb-2 select-none">
        All species
      </div>
      <p className="mb-8 max-w-2xl text-sm text-stone-500">
        Cylinder phenotyping experiments organised by species. Pick a species to
        browse its experiments, waves, plants, and scans.
      </p>

      <ul className="divide-y divide-stone-200 border-y border-stone-200">
        {(speciesList ?? []).map((species) => {
          const experiments = species.cyl_experiments ?? [];
          const n = experiments.length;
          const suffix = n === 1 ? "" : "s";
          const preview = experiments
            .slice(0, 4)
            .map((e) => e.name)
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={species.id}>
              <Link
                href={`/app/phenotypes/${species.id}`}
                className="group flex items-center gap-6 py-6 hover:bg-stone-50 transition-colors px-4 -mx-4 rounded-sm"
              >
                <div className="shrink-0 w-16 h-16 flex items-center justify-center">
                  <Illustration
                    path={species.illustration_path}
                    commonName={species.common_name}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-3 flex-wrap">
                    <span className="text-xl capitalize text-lime-700 group-hover:underline underline-offset-4">
                      {species.common_name}
                    </span>
                    <span className="text-sm italic text-stone-500">
                      <span className="capitalize">{species.genus}</span>{" "}
                      <span className="lowercase">{species.species}</span>
                    </span>
                  </div>
                  {preview ? (
                    <div className="mt-1 truncate text-sm text-stone-500">
                      {preview}
                    </div>
                  ) : (
                    <div className="mt-1 text-sm italic text-stone-400">
                      No experiments yet
                    </div>
                  )}
                </div>
                <div className="shrink-0 text-sm text-stone-500 tabular-nums">
                  {n} experiment{suffix}
                </div>
                <div className="shrink-0 text-stone-300 group-hover:text-lime-700 text-lg">
                  →
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

async function getSpeciesList(): Promise<SpeciesWithExperiments[] | null> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, cyl_experiments!inner(id, name)")
    .neq("cyl_experiments.deleted", true);

  const typedData = data as SpeciesWithExperiments[] | null;

  typedData?.sort((a, b) => {
    const diff = (b.cyl_experiments?.length ?? 0) - (a.cyl_experiments?.length ?? 0);
    if (diff !== 0) return diff;
    return (a.common_name ?? "").localeCompare(b.common_name ?? "");
  });

  return typedData;
}
