import Mixpanel from "mixpanel";
import SpeciesCatalog from "@/components/species-catalog";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { sortedSpeciesOptions } from "@/lib/species-options";

/** Every species in Bloom, and a form to add one (e.g. before uploading its scans). */
export default async function SpeciesPage() {
  const user = await getUser();

  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;

  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/species",
  });

  const supabase = await createServerSupabaseClient();
  const { data, error } = await supabase
    .from("species")
    .select("id, common_name, genus, species")
    .is("deleted_at", null);

  return (
    <div>
      <div className="mb-1 text-sm uppercase tracking-widest text-stone-500">Resources</div>
      <h1 className="text-3xl font-serif italic mb-2 select-none">Species</h1>
      <p className="mb-6 max-w-2xl text-sm text-stone-500">
        Every species in Bloom. Add one here before uploading its scans or data: uploads
        are matched to a species by its common name.
      </p>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          Couldn&apos;t load the species: {error.message}
        </p>
      ) : (
        <SpeciesCatalog species={sortedSpeciesOptions(data ?? [])} />
      )}
    </div>
  );
}
