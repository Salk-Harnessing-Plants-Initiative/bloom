import Link from "next/link";
import Illustration from "@/components/illustration";
import {
  createServerSupabaseClient,
  getUser,
} from "@salk-hpi/bloom-nextjs-auth";
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

  const speciesList: SpeciesWithRNADatasets[] = await getSpeciesList();

  const getSpeciesInfo = (species: SpeciesWithRNADatasets) => {
    const numExps = species.scrna_datasets.length;
    const suffix = numExps == 1 ? "" : "s";
    const text = numExps + " dataset" + suffix + "";
    return <span>{text}</span>;
  };

  return (
    <div>
      <div className="text-xl mb-4 select-none">All species</div>
      <div className="table-auto select-none">
        {speciesList?.map((species) => (
          <div className="table-row" key={species.id}>
            <div className="table-cell py-4">
              <Illustration path={species.illustration_path} />
            </div>
            <div className="table-cell text-lg align-middle p-4">
              <div className="align-middle">
                <Link href={`/app/expression/${species.id}`}>
                  <span className="capitalize text-lime-700 hover:underline">
                    {species.common_name}
                  </span>
                </Link>
                <div className="text-sm italic text-neutral-600">
                  <span className="capitalize">{species.genus}</span>
                  &nbsp;
                  <span className="lowercase">{species.species}</span>
                </div>
              </div>
              <div className="text-sm mt-2 text-neutral-400">
                {getSpeciesInfo(species)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

async function getSpeciesList(): Promise<SpeciesWithRNADatasets[]> {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, scrna_datasets(*)");

  // sort by length of scrna_datasets
  (data as SpeciesWithRNADatasets[] | undefined)?.sort((a, b) => {
    return b.scrna_datasets.length - a.scrna_datasets.length;
  });

  return (data ?? []) as SpeciesWithRNADatasets[];
}
