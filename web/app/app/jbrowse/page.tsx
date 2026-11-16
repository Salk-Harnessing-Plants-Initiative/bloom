import JBrowseClient from "@/components/jbrowse-client";
import { notOfferedMessage } from "@/lib/jbrowse/bloom-genomes";
import { loadBrowserConfig, whyNotOffered } from "@/lib/jbrowse/bloom-genomes-server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** What the page says about Bloom's own genomes above the browser. */
function bloomGenomesNote(count: number, error: string | null): string {
  if (error) {
    return `Bloom's own genomes could not be loaded (${error}). The reference genomes below still work.`;
  }
  if (count === 0) {
    return "Bloom has no browsable genome versions yet; a version becomes browsable once its browser files are built. The reference genomes below are served from outside Bloom.";
  }
  return `${count} Bloom genome version${count === 1 ? "" : "s"} listed with the reference genomes; a version appears once its browser files are built.`;
}

export default async function GenomeBrowser({
  searchParams,
}: {
  searchParams: Promise<{ genome?: string | string[] }>;
}) {
  const supabase = await createServerSupabaseClient();
  const loaded = await loadBrowserConfig(supabase);

  const { genome } = await searchParams;
  const link = typeof genome === "string" ? genome.trim() : "";
  let linkNotice: string | null = null;
  if (link && !loaded.error && !loaded.config.assemblies.some((a) => a.name === link)) {
    try {
      const reason = await whyNotOffered(supabase, link);
      linkNotice = reason ? notOfferedMessage(link, reason) : `${link} could not be opened.`;
    } catch (err) {
      linkNotice = `${link} could not be opened: ${err instanceof Error ? err.message : String(err)}`;
    }
  }

  return (
    <div className="space-y-2">
      {linkNotice && <p className="px-4 text-sm text-red-700">{linkNotice}</p>}
      <p className="px-4 text-sm text-stone-600">
        {bloomGenomesNote(loaded.bloomGenomes.length, loaded.error)}
      </p>
      <JBrowseClient initial={loaded} />
    </div>
  );
}
