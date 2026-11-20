import Link from "next/link";

import { browserLink, placeGeneEverywhere, type GenePlace } from "@/lib/jbrowse/gene-links";
import { createServerSupabaseClient } from "@/lib/supabase/server";

/** Where the searched gene lies in Bloom's browsable genomes, linked to the genome browser. */
export async function BloomGenomesBar({ gene }: { gene: string }) {
  let places: GenePlace[];
  try {
    places = await placeGeneEverywhere(await createServerSupabaseClient(), gene);
  } catch (err) {
    console.error("[orthofinder]", err);
    return <p className="mb-3 px-4 text-sm text-stone-500">Bloom&apos;s genomes could not be searched for {gene}.</p>;
  }
  return (
    <p className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 px-4 text-sm text-stone-600">
      {places.length === 0 ? (
        <span>{gene} is not in any of Bloom&apos;s browsable genomes.</span>
      ) : (
        <>
          <span>{gene} in Bloom&apos;s genome browser:</span>
          {places.map((place) => (
            <Link key={place.assembly} href={browserLink(place)} className="text-lime-700 hover:underline">
              {place.label} ({place.contig}:{place.start.toLocaleString()}..{place.end.toLocaleString()})
            </Link>
          ))}
        </>
      )}
    </p>
  );
}
