"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type ReactNode } from "react";

import {
  NO_PLACE_TEXT,
  browserLink,
  datasetGenome,
  geneInGenomeLink,
  orthobrowserLink,
  placeGenesEverywhere,
  readyBuild,
  type GenePlace,
  type NoPlaceReason,
  type ReadyBuild,
} from "@/lib/jbrowse/gene-links";
import { createClientSupabaseClient } from "@/lib/supabase/client";

const LINK = "text-lime-700 hover:underline";

/** A dataset's browsable genome, or why it has none; asked once per dataset per page. */
const datasetBuilds = new Map<number, Promise<ReadyBuild | NoPlaceReason>>();

export function datasetBuild(datasetId: number): Promise<ReadyBuild | NoPlaceReason> {
  let found = datasetBuilds.get(datasetId);
  if (!found) {
    const supabase = createClientSupabaseClient();
    found = datasetGenome(supabase, datasetId)
      .then(async (versionId) => {
        if (versionId === null) return "no genome version" as const;
        return (await readyBuild(supabase, versionId)) ?? ("no browser files" as const);
      })
      .catch(() => {
        datasetBuilds.delete(datasetId);
        return "unavailable" as const;
      });
    datasetBuilds.set(datasetId, found);
  }
  return found;
}

/** A gene's "Genome browser" and "Orthologs" links; the first is greyed with the reason on hover
 * when the gene's genome cannot be browsed. `build` undefined means still looking it up. */
export function GeneLinks({
  gene,
  build,
}: {
  gene: string;
  build: ReadyBuild | NoPlaceReason | undefined;
}) {
  return (
    <span className="ml-2 inline-flex gap-2 whitespace-nowrap font-sans text-[10px] font-normal">
      {build && typeof build !== "string" ? (
        <Link href={geneInGenomeLink(build.assembly, gene)} className={LINK} title={`Open ${gene} in ${build.label}`}>
          Genome browser
        </Link>
      ) : (
        <span className="cursor-help text-stone-300" title={build ? NO_PLACE_TEXT[build] : "Looking up the genome…"}>
          Genome browser
        </span>
      )}
      <Link href={orthobrowserLink(gene)} className={LINK} title={`Find ${gene}'s orthologs`}>
        Orthologs
      </Link>
    </span>
  );
}

/** Renders the links for any gene of a single-cell dataset. */
export function useDatasetGeneLinks(datasetId: number): (gene: string) => ReactNode {
  const [build, setBuild] = useState<ReadyBuild | NoPlaceReason>();
  useEffect(() => {
    let live = true;
    setBuild(undefined);
    datasetBuild(datasetId).then((found) => live && setBuild(found));
    return () => {
      live = false;
    };
  }, [datasetId]);
  return useCallback((gene: string) => <GeneLinks gene={gene} build={build} />, [build]);
}

/** Links to each Bloom genome version holding a gene, then its orthologs. */
export function BloomGenomeLinks({ gene, places }: { gene: string; places: GenePlace[] }) {
  return (
    <span className="ml-2 inline-flex flex-wrap gap-2 whitespace-nowrap font-sans text-[10px] font-normal">
      {places.map((place) => (
        <Link
          key={place.assembly}
          href={browserLink(place)}
          className={LINK}
          title={`Open ${place.gene} in ${place.label} at ${place.contig}:${place.start}..${place.end}`}
        >
          {place.label}
        </Link>
      ))}
      <Link href={orthobrowserLink(gene)} className={LINK} title={`Find ${gene}'s orthologs`}>
        Orthologs
      </Link>
    </span>
  );
}

/** Where each of `genes` lies across Bloom's browsable genomes; empty until read or if it fails. */
export function useGenesEverywhere(genes: string[]): Map<string, GenePlace[]> {
  const key = [...new Set(genes.filter(Boolean))].sort().join("\n");
  const [found, setFound] = useState<Map<string, GenePlace[]>>(new Map());
  useEffect(() => {
    if (!key) return;
    let live = true;
    placeGenesEverywhere(createClientSupabaseClient(), key.split("\n"))
      .then((places) => live && setFound(places))
      .catch((err) => console.error("[gene links]", err));
    return () => {
      live = false;
    };
  }, [key]);
  return found;
}
