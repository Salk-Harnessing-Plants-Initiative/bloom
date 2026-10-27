import Mixpanel from "mixpanel";
import SpeciesCatalog from "@/components/species-catalog";
import InfoTip from "@/components/info-tip";
import { speciesImage } from "@/components/illustration";
import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { sortedSpeciesList } from "@/lib/species-options";

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
    .select("id, common_name, genus, species, created_by, illustration_path")
    .is("deleted_at", null);
  if (error) console.error("[species] Couldn't load the species:", error.message);

  // Each species' image, or the placeholder until someone adds one.
  const rows = data ?? [];
  const images = new Map(
    await Promise.all(
      rows.map(async (r) => [r.id, await speciesImage(r.illustration_path, r.common_name)] as const)
    )
  );
  const withImages = sortedSpeciesList(rows).map((s) => ({ ...s, image: images.get(s.id) }));

  return (
    <div>
      <div className="mb-1 text-sm uppercase tracking-widest text-stone-500">Resources</div>
      <h1 className="text-3xl font-serif italic mb-2 select-none">Species</h1>
      <p className="mb-6 max-w-2xl text-sm text-stone-500">
        Lists every species in Bloom.
        <InfoTip label="When to add a species">
          <span className="block font-medium text-stone-800">
            Add a species here before you use it in:
          </span>
          <span className="mt-1 block">• Bloom Desktop, for cylinder scans</span>
          <span className="block">• an Expression analysis (its form can add one too)</span>
        </InfoTip>
      </p>
      {error ? (
        <p role="alert" className="text-sm text-red-700">
          Couldn&apos;t load the species. Reload the page to try again.
        </p>
      ) : (
        <SpeciesCatalog species={withImages} currentUserId={user?.id ?? null} />
      )}
    </div>
  );
}
