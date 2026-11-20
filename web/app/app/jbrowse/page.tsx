import JBrowseClient from "@/components/jbrowse-client";
import {
  INVALID_LINK_MESSAGE,
  bloomGenomesNote,
  notOfferedMessage,
  parseGenomeVersion,
} from "@/lib/jbrowse/bloom-genomes";
import { loadBrowserConfig, whyNotOffered } from "@/lib/jbrowse/bloom-genomes-server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export default async function GenomeBrowser({
  searchParams,
}: {
  searchParams: Promise<{ genome?: string | string[] }>;
}) {
  const supabase = await createServerSupabaseClient();
  const loaded = await loadBrowserConfig(supabase);

  const { genome } = await searchParams;
  const link = (Array.isArray(genome) ? genome[0] : genome)?.trim() ?? "";
  let linkNotice: string | null = null;
  if (link && !loaded.error && !loaded.config.assemblies.some((a) => a.name === link)) {
    if (!parseGenomeVersion(link)) {
      linkNotice = INVALID_LINK_MESSAGE;
    } else {
      try {
        const reason = await whyNotOffered(supabase, link);
        linkNotice = reason ? notOfferedMessage(link, reason) : `${link} could not be opened.`;
      } catch (err) {
        console.error("[genome browser]", err);
        linkNotice = `${link} could not be checked.`;
      }
    }
  }

  return (
    <div className="space-y-2">
      {linkNotice && <p className="px-4 text-sm text-red-700">{linkNotice}</p>}
      <p className="px-4 text-sm text-stone-600">
        {bloomGenomesNote(loaded.bloomGenomes.length, loaded.error)}
      </p>
      <JBrowseClient loaded={loaded} />
    </div>
  );
}
