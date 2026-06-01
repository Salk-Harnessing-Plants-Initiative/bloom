import Link from "next/link";
import {
  createServerSupabaseClient,
  getUser,
} from "@/lib/supabase/server";
import Mixpanel from "mixpanel";
import ScientistBadge from "@/components/scientist-badge";
import TraitExplorer from "./TraitExplorer";

export default async function Experiment({
  params,
}: {
  params: Promise<{ speciesId: string; experimentId: string }>;
}) {
  const { speciesId, experimentId } = await params;
  const experimentIdNum = Number(experimentId);

  const [user, experiment, traitNames] = await Promise.all([
    getUser(),
    getExperimentWithSpecies(experimentIdNum),
    getTraitNames(),
  ]);

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/traits/${speciesId}/${experimentId}`,
  });

  const experimentName = capitalizeFirstLetter(
    experiment?.name?.replaceAll("-", " ") ?? "",
  );
  const speciesName = experiment?.species?.common_name ?? "";

  if (!experiment) {
    return (
      <div>
        <Breadcrumbs
          speciesId={speciesId}
          speciesName={speciesName}
          experimentName={experimentName}
        />
        <div className="text-neutral-500 italic">Experiment not found.</div>
      </div>
    );
  }

  return (
    <div>
      <Breadcrumbs
        speciesId={speciesId}
        speciesName={speciesName}
        experimentName={experimentName}
      />

      {experiment.people && <ScientistBadge person={experiment.people} />}

      {traitNames.length === 0 ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 bg-stone-50 p-6 text-sm text-stone-500">
          No trait measurements are available yet for this experiment.
        </div>
      ) : (
        <TraitExplorer
          experimentId={experimentIdNum}
          traitNames={traitNames}
        />
      )}
    </div>
  );
}

function Breadcrumbs({
  speciesId,
  speciesName,
  experimentName,
}: {
  speciesId: string;
  speciesName: string;
  experimentName: string;
}) {
  return (
    <div className="text-xl mb-8 select-none">
      <span className="text-stone-400">
        <span className="hover:underline">
          <Link href="/app/traits">All species</Link>
        </span>
        &nbsp;▸&nbsp;
        <span className="hover:underline capitalize">
          <Link href={`/app/traits/${speciesId}`}>{speciesName}</Link>
        </span>
        &nbsp;▸&nbsp;
      </span>
      <span>{experimentName}</span>
    </div>
  );
}

function capitalizeFirstLetter(string: string): string {
  return string.charAt(0).toUpperCase() + string.slice(1);
}

async function getExperimentWithSpecies(experimentId: number) {
  const supabase = await createServerSupabaseClient();
  const { data } = await supabase
    .from("cyl_experiments")
    .select("*, species(*), people(*)")
    .eq("id", experimentId)
    .single();
  return data;
}

async function getTraitNames(): Promise<string[]> {
  const supabase = await createServerSupabaseClient();
  const { data } = await supabase
    .from("cyl_scan_trait_names")
    .select("name")
    .order("name");

  if (!data) return [];
  return (data as { name: string | null }[])
    .map((row) => row.name)
    .filter((name): name is string => Boolean(name));
}
