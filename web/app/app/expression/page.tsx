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
      <div className="text-3xl font-serif italic mb-8 select-none">
        All species
      </div>

      <div className="divide-y divide-stone-200 border-y border-stone-200">
        {speciesList.map((species) => {
          const n = species.scrna_datasets.length;
          const suffix = n === 1 ? "" : "s";
          return (
            <Link
              key={species.id}
              href={`/app/expression/${species.id}`}
              className="group flex items-center gap-6 py-5 hover:bg-stone-50 transition-colors px-3 -mx-3"
            >
              <div className="shrink-0 w-20 h-20 rounded-full bg-stone-100 overflow-hidden flex items-center justify-center">
                <Illustration path={species.illustration_path} commonName={species.common_name} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-xl capitalize text-lime-700 group-hover:underline">
                  {species.common_name}
                </div>
                <div className="text-sm italic text-neutral-600">
                  <span className="capitalize">{species.genus}</span>{" "}
                  <span className="lowercase">{species.species}</span>
                </div>
              </div>
              <div className="shrink-0 text-sm text-stone-500 tabular-nums">
                {n} dataset{suffix}
              </div>
              <div className="shrink-0 text-stone-300 group-hover:text-lime-700 text-lg">
                →
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

async function getSpeciesList(): Promise<SpeciesWithRNADatasets[]> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, scrna_datasets(*)");

  (data as SpeciesWithRNADatasets[] | undefined)?.sort((a, b) => {
    return b.scrna_datasets.length - a.scrna_datasets.length;
  });

  return (data ?? []) as SpeciesWithRNADatasets[];
}
