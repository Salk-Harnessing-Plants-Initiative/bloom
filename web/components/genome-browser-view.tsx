"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

import { GenomeGeneSearch } from "@/components/genome-gene-search";
import JBrowseClient from "@/components/jbrowse-client";
import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import { goToGene, openAssemblies, openPicks, type BrowserSession } from "@/lib/jbrowse/browser-session";
import { browserLink, type GenePlace } from "@/lib/jbrowse/gene-links";
import { bloomTracks } from "@/lib/jbrowse/launch";
import { picksLink, type PicksPlan } from "@/lib/jbrowse/several-genes";

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** The gene search and JBrowse sharing JBrowse's session, so the search moves its views in place. */
export function GenomeBrowserView({ loaded, picks }: { loaded: LoadedBrowserConfig; picks: PicksPlan | null }) {
  const router = useRouter();
  const session = useRef<BrowserSession | null>(null);
  const [openGenomes, setOpenGenomes] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

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
      setProblem(null);
      if (!session.current) {
        router.push(browserLink(place));
        return;
      }
      goToGene(session.current, place, bloomTracks(place.assembly)).catch((err) => {
        console.error("[genome browser] going to a gene", err);
        setProblem(`${place.gene} could not be shown: ${reason(err)}`);
      });
    },
    [router],
  );
  const onOpenTogether = useCallback(
    async (assembly: string, places: GenePlace[]) => {
      setProblem(null);
      const link = picksLink(assembly, places.map((p) => p.gene));
      if (!session.current) {
        router.push(link);
        return;
      }
      try {
        await openPicks(session.current, assembly, places, bloomTracks(assembly));
        window.history.replaceState(null, "", link);
      } catch (err) {
        console.error("[genome browser] opening picks", err);
        setProblem(`The picked genes could not be opened: ${reason(err)}`);
      }
    },
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
      {problem && <p className="px-4 text-sm text-red-700">{problem}</p>}
      <JBrowseClient loaded={loaded} picks={picks} onSession={onSession} onChange={refresh} />
    </>
  );
}
