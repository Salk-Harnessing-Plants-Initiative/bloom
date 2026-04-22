import Link from "next/link";
import Illustration from "@/components/illustration";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

import type { SpeciesWithRNADatasets } from "@/lib/custom.types";

export default async function AllSpecies() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/expression",
  });

  const speciesList = await getSpeciesList();

  return (
    <div>
      <div className="mb-1 text-sm uppercase tracking-widest text-stone-500">
        Expression
      </div>
      <div className="text-3xl font-serif italic mb-2 select-none">
        All species
      </div>
      <p className="mb-8 max-w-2xl text-sm text-stone-500">
        Single-cell expression atlases across every species in the Salk HPI
        pipeline. Pick a species to browse its datasets, UMAPs, and marker
        genes.
      </p>

      <ul className="divide-y divide-stone-200 border-y border-stone-200">
        {speciesList.map((species) => {
          const datasets = species.scrna_datasets ?? [];
          const n = datasets.length;
          const suffix = n === 1 ? "" : "s";
          const preview = datasets
            .slice(0, 4)
            .map((d) => d.name)
            .filter(Boolean)
            .join(" · ");
          return (
            <li key={species.id}>
              <Link
                href={`/app/expression/${species.id}`}
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
                      No datasets yet
                    </div>
                  )}
                </div>
                <div className="shrink-0 text-sm text-stone-500 tabular-nums">
                  {n} dataset{suffix}
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

async function getSpeciesList(): Promise<SpeciesWithRNADatasets[]> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, scrna_datasets(id, name)")
    .is("deleted_at", null);

  (data as SpeciesWithRNADatasets[] | undefined)?.sort((a, b) => {
    const diff = (b.scrna_datasets?.length ?? 0) - (a.scrna_datasets?.length ?? 0);
    if (diff !== 0) return diff;
    return (a.common_name ?? "").localeCompare(b.common_name ?? "");
  });

  return (data ?? []) as SpeciesWithRNADatasets[];
}
