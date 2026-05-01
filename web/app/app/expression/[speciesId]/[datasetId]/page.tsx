import Link from "next/link";
import { notFound } from "next/navigation";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import ScientistBadge from "@/components/scientist-badge";
import { ExpressionCockpit } from "@/components/expression-cockpit";

export default async function Dataset({
  params,
}: {
  // Next.js dynamic route params are always strings; parse to numbers
  // at the boundary below.
  params: Promise<{ datasetId: string; speciesId: string }>;
}) {
  const { datasetId, speciesId } = await params;
  const datasetIdNum = Number(datasetId);
  if (!Number.isFinite(datasetIdNum)) notFound();
  const dataset = await getDataset(datasetIdNum);

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/expression/${speciesId}/${datasetId}`,
  });

  if (!dataset) {
    return (
      <div className="max-w-5xl mx-auto">
        <div className="text-sm mb-6 select-none">
          <Link href="/app/expression" className="text-stone-400 hover:underline">
            All species
          </Link>
        </div>
        <div className="text-sm text-stone-500 italic">Dataset not found.</div>
      </div>
    );
  }

  const speciesName = dataset.species?.common_name ?? "";

  return (
    <div>
      {/* Top navigation bar — the breadcrumb sits in a border-bottom
          strip spanning the shared container width set by the parent
          route layout, so the "All species" link stays at the same
          screen position across the three expression routes. */}
      <div className="text-sm mb-6 pb-4 border-b border-stone-200 select-none">
        <Link
          href="/app/expression"
          className="text-stone-400 hover:underline"
        >
          All species
        </Link>
        <span className="text-stone-300">&nbsp;▸&nbsp;</span>
        <Link
          href={`/app/expression/${speciesId}`}
          className="text-stone-400 hover:underline capitalize"
        >
          {speciesName}
        </Link>
        <span className="text-stone-300">&nbsp;▸&nbsp;</span>
        <span className="text-stone-900">{dataset.name}</span>
      </div>

      {dataset.people && (
        <div className="mb-6">
          <ScientistBadge person={dataset.people} />
        </div>
      )}

      <ExpressionCockpit datasetId={datasetIdNum} datasetName={dataset.name} />
    </div>
  );
}

async function getDataset(datasetId: number) {
  const supabase = await createServerSupabaseClient();
  const { data } = await supabase
    .from("scrna_datasets")
    .select("*, people(*), species(*)")
    .eq("id", datasetId)
    .single();
  return data;
}
