import Link from "next/link";
import {
  createServerSupabaseClient,
  getUser,
} from "@salk-hpi/bloom-nextjs-auth";
import Mixpanel from "mixpanel";
import ExpressionPage from "@/components/expression-page";

export default async function Species({
  params,
}: {
  params: Promise<{ speciesId: string }>;
}) {
  const { speciesId } = await params;
  const species : any = await getSpeciesWithDatasets(Number(speciesId));
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/expression/${speciesId}`,
  });

  return (
    <div className="">
      <div className="text-xl mb-8 select-none">
        <span className="text-stone-400">
          <span className="hover:underline">
            <Link href="/app/expression">All species</Link>
          </span>
          &nbsp;▸&nbsp;
        </span>
        <span className="capitalize">{species?.common_name}</span>
      </div>
      <div className="text-sm mb-4 text-stone-500 align-middle max-w-[650px] flex flex-row items-center">

      </div>
      {species && <ExpressionPage specieslist={species.scrna_datasets}/>}
    </div>
  );
}

function capitalizeFirstLetter(string: String) {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

async function getSpeciesWithDatasets(speciesId: number) {
  const supabase = await createServerSupabaseClient();

  const { data } = await supabase
    .from("species")
    .select("*, scrna_datasets(*, people(*))")
    .eq("id", speciesId)
    .single();

  return data;
}
