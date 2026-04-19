import Link from "next/link";
import Illustration from "@/components/illustration";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";

import type { SpeciesWithRNADatasetsAndPeople } from "@/lib/custom.types";

export default async function Species({
  params,
}: {
  params: Promise<{ speciesId: number }>;
}) {
  const { speciesId } = await params;
  const species = await getSpeciesWithDatasets(speciesId);

  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/expression/${speciesId}`,
  });

  if (!species) return <div>Species not found.</div>;

  const datasets = species.scrna_datasets ?? [];

  return (
    <div className="max-w-5xl mx-auto">
      {/* Breadcrumb */}
      <div className="text-sm mb-6 select-none">
        <Link
          href="/app/expression"
          className="text-stone-400 hover:underline"
        >
          All species
        </Link>
        <span className="text-stone-300">&nbsp;▸&nbsp;</span>
        <span className="capitalize text-stone-900">
          {species.common_name}
        </span>
      </div>

      {/* Species header */}
      <div className="flex items-start gap-6 mb-10">
        <div className="shrink-0 w-28 h-28 rounded-full bg-stone-100 overflow-hidden flex items-center justify-center">
          <Illustration path={species.illustration_path} />
        </div>
        <div className="flex-1 pt-2">
          <div className="text-3xl font-serif italic capitalize">
            {species.common_name}
          </div>
          <div className="text-base italic text-neutral-600 mt-1">
            <span className="capitalize">{species.genus}</span>{" "}
            <span className="lowercase">{species.species}</span>
          </div>
          <div className="text-sm text-stone-500 mt-3">
            {datasets.length} dataset{datasets.length === 1 ? "" : "s"}
          </div>
        </div>
      </div>

      {/* Dataset list — same row pattern as /app/expression */}
      <div className="text-xs uppercase tracking-widest text-stone-500 mb-3">
        Datasets
      </div>

      {datasets.length === 0 ? (
        <div className="text-sm text-stone-500 italic">
          No datasets yet for this species.
        </div>
      ) : (
        <div className="divide-y divide-stone-200 border-y border-stone-200">
          {datasets.map((ds) => {
            const secondary = [
              ds.assembly,
              ds.annotation,
              ds.strain ? `strain ${ds.strain}` : null,
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <Link
                key={ds.id}
                href={`/app/expression/${species.id}/${ds.id}`}
                className="group flex items-center gap-6 py-5 hover:bg-stone-50 transition-colors px-3 -mx-3"
              >
                <div className="shrink-0 w-20 h-20 rounded-full bg-stone-100 overflow-hidden flex items-center justify-center">
                  <Illustration path={species.illustration_path} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xl text-lime-700 group-hover:underline break-words">
                    {ds.name}
                  </div>
                  {secondary && (
                    <div className="text-sm italic text-neutral-600">
                      {secondary}
                    </div>
                  )}
                  {ds.people?.name && (
                    <div className="text-sm mt-2 text-neutral-400">
                      {ds.people.name}
                    </div>
                  )}
                </div>
                <span className="shrink-0 text-xs text-stone-600 bg-stone-100 border border-stone-200 px-3 py-1 rounded-full whitespace-nowrap group-hover:border-lime-700 group-hover:text-lime-700 transition-colors">
                  Open
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

async function getSpeciesWithDatasets(
  speciesId: number
): Promise<SpeciesWithRNADatasetsAndPeople | null> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, scrna_datasets(*, people(*))")
    .eq("id", speciesId)
    .single();

  return (data as SpeciesWithRNADatasetsAndPeople) ?? null;
}
