import Link from "next/link";
import Mixpanel from "mixpanel";

import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { memberNames } from "@/components/integration-lib/joint-map";

export default async function Integrations() {
  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: "/app/integrations",
  });

  const maps = await getIntegrations();
  const fmt = new Intl.NumberFormat("en-US");

  return (
    <div>
      <div className="mb-1 text-sm uppercase tracking-widest text-stone-500">
        Multi-dataset integration
      </div>
      <div className="mb-2 select-none font-serif text-3xl italic">
        Multi-dataset single-cell integration
      </div>
      <p className="mb-8 max-w-2xl text-sm text-stone-500">
        Maps that place the cells of several single-cell datasets in one shared
        space based on ESM protein embeddings, such as a new experiment beside
        the reference atlases it was compared with. Open a map to colour its
        cells by dataset or label, filter them, and inspect any cell.
      </p>

      {maps.length === 0 ? (
        <div className="text-sm italic text-stone-500">No integration maps yet.</div>
      ) : (
        <ul className="divide-y divide-stone-200 border-y border-stone-200">
          {maps.map((map) => (
            <li key={map.id}>
              <Link
                href={`/app/integrations/${map.id}`}
                className="group -mx-4 flex items-center gap-6 rounded-sm px-4 py-6 transition-colors hover:bg-stone-50"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-baseline gap-3">
                    <span className="text-xl text-lime-700 underline-offset-4 group-hover:underline">
                      {map.title}
                    </span>
                    <span className="text-sm text-stone-500">
                      {fmt.format(map.n_points)} cells · {map.members.length} datasets
                    </span>
                  </div>
                  {map.description && (
                    <div className="mt-1 max-w-3xl text-sm text-stone-600">
                      {map.description}
                    </div>
                  )}
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500">
                    {map.members.map((member) => (
                      <span key={member.ordinal}>
                        <span className="text-stone-700">{member.name}</span> ·{" "}
                        {member.role} · {fmt.format(member.nPoints)}
                      </span>
                    ))}
                  </div>
                </div>
                <span className="shrink-0 whitespace-nowrap rounded-full border border-stone-200 bg-stone-100 px-3 py-1 text-xs text-stone-600 transition-colors group-hover:border-lime-700 group-hover:text-lime-700">
                  Open
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

async function getIntegrations() {
  const supabase = await createServerSupabaseClient();
  const { data: maps, error } = await supabase
    .from("scrna_embeddings")
    .select("id, title, description, n_points, params")
    .not("ingested_at", "is", null)
    .order("created_at", { ascending: false });
  if (error) throw new Error(`Could not list the integration maps: ${error.message}`);
  if (!maps?.length) return [];

  const { data: members, error: membersError } = await supabase
    .from("scrna_embedding_dataset_members")
    .select("embedding_id, ordinal, role, n_points, scrna_datasets(name)")
    .in("embedding_id", maps.map((m) => m.id))
    .order("ordinal");
  if (membersError) {
    throw new Error(`Could not list the maps' datasets: ${membersError.message}`);
  }

  return maps.map((map) => {
    const names = memberNames(map.params);
    return {
      id: map.id,
      title: map.title,
      description: map.description,
      n_points: map.n_points,
      members: (members ?? [])
        .filter((m) => m.embedding_id === map.id)
        .map((m) => ({
          ordinal: m.ordinal,
          role: m.role,
          nPoints: m.n_points,
          name: names.get(m.ordinal) ?? m.scrna_datasets?.name ?? `Dataset ${m.ordinal}`,
        })),
    };
  });
}
