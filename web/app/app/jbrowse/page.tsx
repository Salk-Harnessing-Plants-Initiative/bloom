import { redirect } from "next/navigation";

import { GenomeGeneSearch } from "@/components/genome-gene-search";
import JBrowseClient from "@/components/jbrowse-client";
import {
  INVALID_LINK_MESSAGE,
  notOfferedMessage,
  pageNotes,
  parseGenomeVersion,
} from "@/lib/jbrowse/bloom-genomes";
import { geneLinkRedirect, loadBrowserConfig, whyNotOffered } from "@/lib/jbrowse/bloom-genomes-server";
import { createServerSupabaseClient } from "@/lib/supabase/server";

export default async function GenomeBrowser({
  searchParams,
}: {
  searchParams: Promise<Record<"genome" | "loc" | "missing", string | string[] | undefined>>;
}) {
  const supabase = await createServerSupabaseClient();
  const loaded = await loadBrowserConfig(supabase);

  const params = await searchParams;
  const first = (key: "genome" | "loc" | "missing") =>
    ((Array.isArray(params[key]) ? params[key][0] : params[key]) ?? "").trim();
  const link = first("genome");
  const missing = first("missing");
  let linkNotice: string | null = missing && link ? `${missing} is not among ${link}'s genes; showing the whole genome.` : null;

  // A Bloom genome has no gene index inside JBrowse: a gene in `loc` is placed here instead.
  let geneRedirect: string | null = null;
  try {
    geneRedirect = await geneLinkRedirect(supabase, loaded, link, first("loc"));
  } catch (err) {
    console.error("[genome browser]", err);
    linkNotice = `${first("loc")} could not be looked up in ${link}.`;
  }
  if (geneRedirect) redirect(geneRedirect);

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
      {pageNotes(loaded.error, linkNotice).map((note) => (
        <p key={note} className="px-4 text-sm text-red-700">
          {note}
        </p>
      ))}
      {loaded.bloomGenomes.length > 0 && <GenomeGeneSearch />}
      <JBrowseClient loaded={loaded} />
    </div>
  );
}
