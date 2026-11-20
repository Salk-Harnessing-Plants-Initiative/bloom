"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { GenomeGeneSearch } from "@/components/genome-gene-search";
import JBrowseClient from "@/components/jbrowse-client";
import { geneTrackId, type LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import { goToGene, openAssemblies, type BrowserSession } from "@/lib/jbrowse/browser-session";
import { browserLink, type GenePlace } from "@/lib/jbrowse/gene-links";
import { picksLink, type PicksPlan } from "@/lib/jbrowse/several-genes";

/** The gene search and JBrowse, sharing JBrowse's session: the search covers the genomes open
 * in it, and a gene clicked in the search moves only that genome's view. */
export function GenomeBrowserView({ loaded, picks }: { loaded: LoadedBrowserConfig; picks: PicksPlan | null }) {
  const router = useRouter();
  const session = useRef<BrowserSession | null>(null);
  const [openGenomes, setOpenGenomes] = useState<string[]>([]);

  const refresh = useCallback(() => {
    const next = openAssemblies(session.current);
    setOpenGenomes((now) => (now.join("\n") === next.join("\n") ? now : next));
  }, []);
  const onSession = useCallback(
    (s: BrowserSession) => {
      session.current = s;
      refresh();
    },
    [refresh],
  );
  const onGoTo = useCallback(
    (place: GenePlace) => {
      if (!session.current) {
        router.push(browserLink(place));
        return;
      }
      goToGene(session.current, place, geneTrackId(place.assembly)).catch((err) =>
        console.error("[genome browser] going to a gene", err),
      );
    },
    [router],
  );
  const onOpenTogether = useCallback(
    (assembly: string, genes: string[]) => router.push(picksLink(assembly, genes)),
    [router],
  );

  return (
    <>
      {loaded.bloomGenomes.length > 0 && (
        <GenomeGeneSearch
          bloomBuilds={loaded.bloomBuilds}
          openGenomes={openGenomes}
          onGoTo={onGoTo}
          onOpenTogether={onOpenTogether}
        />
      )}
      <JBrowseClient loaded={loaded} picks={picks} onSession={onSession} onChange={refresh} />
    </>
  );
}
