"use client";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useRef, useState } from "react";

import { GenomeGeneSearch } from "@/components/genome-gene-search";
import { GenomePicker } from "@/components/genome-picker";
import JBrowseClient from "@/components/jbrowse-client";
import type { LoadedBrowserConfig } from "@/lib/jbrowse/bloom-genomes";
import {
  contigLengths,
  goToGene,
  openAssemblies,
  openPicks,
  replaceViews,
  searchedGenomes,
  type BrowserSession,
} from "@/lib/jbrowse/browser-session";
import { browserLink, genomeLink, type GenePlace } from "@/lib/jbrowse/gene-links";
import { genomeChoices, viewsForGenome } from "@/lib/jbrowse/genome-choice";
import { bloomTracks } from "@/lib/jbrowse/launch";
import { picksLink, type PicksPlan } from "@/lib/jbrowse/several-genes";

const reason = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** The gene search and JBrowse sharing JBrowse's session, so the search moves its views in place. */
export function GenomeBrowserView({ loaded, picks }: { loaded: LoadedBrowserConfig; picks: PicksPlan | null }) {
  const router = useRouter();
  const session = useRef<BrowserSession | null>(null);
  const [openGenomes, setOpenGenomes] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [defaultGenome, setDefaultGenome] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const choices = useMemo(() => genomeChoices(loaded), [loaded]);

  const refresh = useCallback(() => {
    const next = openAssemblies(session.current);
    setOpenGenomes((now) => (now.join("\n") === next.join("\n") ? now : next));
  }, []);
  const onSession = useCallback(
    (s: BrowserSession) => {
      session.current = s;
      setReady(true);
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

  const onChooseGenome = useCallback(
    async (assembly: string) => {
      setProblem(null);
      const s = session.current;
      if (!s) return;
      try {
        const views = viewsForGenome(loaded, assembly, await contigLengths(s, assembly));
        if (!views) throw new Error("its chromosomes could not be read.");
        replaceViews(s, assembly, views);
        setDefaultGenome(null);
        window.history.replaceState(null, "", genomeLink(assembly, views[views.length - 1].loc));
      } catch (err) {
        console.error("[genome browser] choosing a genome", err);
        setProblem(`${assembly} could not be opened: ${reason(err)}`);
      }
    },
    [loaded],
  );

  return (
    <>
      <div className="flex flex-wrap items-start gap-3 px-4">
        <GenomePicker
          choices={choices}
          value={openGenomes.length === 1 ? openGenomes[0] : ""}
          disabled={!ready}
          onChoose={onChooseGenome}
        />
        {loaded.bloomGenomes.length > 0 && (
          <GenomeGeneSearch
            bloomBuilds={loaded.bloomBuilds}
            openGenomes={searchedGenomes(openGenomes, defaultGenome)}
            onGoTo={onGoTo}
            onOpenTogether={onOpenTogether}
          />
        )}
      </div>
      {problem && <p className="px-4 text-sm text-red-700">{problem}</p>}
      <JBrowseClient
        loaded={loaded}
        picks={picks}
        onSession={onSession}
        onChange={refresh}
        onDefaultOpened={setDefaultGenome}
      />
    </>
  );
}
