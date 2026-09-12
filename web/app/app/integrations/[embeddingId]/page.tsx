import Link from "next/link";
import { notFound } from "next/navigation";
import Mixpanel from "mixpanel";

import { createServerSupabaseClient, getUser } from "@/lib/supabase/server";
import { IntegrationView, type IntegrationMember } from "@/components/integration-view";
import {
  cellTypeLabels,
  GENOTYPE_KEY,
  memberNames,
} from "@/components/integration-lib/joint-map";

export default async function Integration({
  params,
}: {
  params: Promise<{ embeddingId: string }>;
}) {
  const { embeddingId } = await params;
  const id = Number(embeddingId);
  if (!Number.isInteger(id)) notFound();

  const user = await getUser();
  const mixpanel = process.env.MIXPANEL_TOKEN
    ? Mixpanel.init(process.env.MIXPANEL_TOKEN)
    : null;
  mixpanel?.track("Page view", {
    distinct_id: user?.email,
    url: `/app/integrations/${embeddingId}`,
  });

  const map = await getIntegration(id);
  const fmt = new Intl.NumberFormat("en-US");

  return (
    <div>
      <div className="mb-6 select-none border-b border-stone-200 pb-4 text-sm">
        <Link href="/app/integrations" className="text-stone-400 hover:underline">
          All integration maps
        </Link>
        {map && (
          <>
            <span className="text-stone-300">&nbsp;▸&nbsp;</span>
            <span className="text-stone-900">{map.title}</span>
          </>
        )}
      </div>

      {!map ? (
        <div className="text-sm italic text-stone-500">Map not found.</div>
      ) : (
        <>
          <div className="mb-6">
            <div className="mb-1 text-xs uppercase tracking-widest text-stone-500">
              {fmt.format(map.nPoints)} cells · {map.members.length} datasets
            </div>
            <h1 className="font-serif text-3xl italic">{map.title}</h1>
            {map.description && (
              <p className="mt-2 max-w-3xl text-sm text-stone-600">{map.description}</p>
            )}
          </div>
          <IntegrationView
            embeddingId={id}
            members={map.members}
            labelKeys={map.labelKeys}
            cellTypeLabels={map.cellTypeLabels}
          />
        </>
      )}
    </div>
  );
}

async function getIntegration(id: number) {
  const supabase = await createServerSupabaseClient();
  const [embedding, members, labels] = await Promise.all([
    supabase
      .from("scrna_embeddings")
      .select("title, description, n_points, params")
      .eq("id", id)
      .not("ingested_at", "is", null)
      .maybeSingle(),
    supabase
      .from("scrna_embedding_dataset_members")
      .select("ordinal, role, n_points, dataset_id, scrna_datasets(name, kind, species_id)")
      .eq("embedding_id", id)
      .order("ordinal"),
    supabase
      .from("scrna_embedding_labels")
      .select("key")
      .eq("embedding_id", id)
      .order("key"),
  ]);
  for (const { error } of [embedding, members, labels]) {
    if (error) throw new Error(`Could not load the map: ${error.message}`);
  }
  if (!embedding.data) return null;

  const names = memberNames(embedding.data.params);
  const memberRows: IntegrationMember[] = (members.data ?? []).map((m) => ({
    ordinal: m.ordinal,
    role: m.role,
    nPoints: m.n_points,
    datasetId: m.dataset_id,
    name: names.get(m.ordinal) ?? m.scrna_datasets?.name ?? `Dataset ${m.dataset_id}`,
    kind: m.scrna_datasets?.kind ?? "full",
    speciesId: m.scrna_datasets?.species_id ?? null,
  }));
  const labelKeys = (labels.data ?? []).map((l) => l.key);
  if (memberRows.some((m) => m.role === "query") && !labelKeys.includes(GENOTYPE_KEY)) {
    labelKeys.push(GENOTYPE_KEY);
  }
  labelKeys.sort();

  return {
    title: embedding.data.title,
    description: embedding.data.description,
    nPoints: embedding.data.n_points,
    members: memberRows,
    labelKeys,
    cellTypeLabels: cellTypeLabels(embedding.data.params),
  };
}
