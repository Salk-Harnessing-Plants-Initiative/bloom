"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { browserLink, searchGenes, type GenePlace } from "@/lib/jbrowse/gene-links";
import { createClientSupabaseClient } from "@/lib/supabase/client";

/** Most matches listed at once. */
const MAX_MATCHES = 20;
/** Pause after typing before searching, in ms. */
const TYPING_PAUSE_MS = 300;

/** Finds genes in Bloom's browsable genomes by part of an ID or name, each linked to the browser. */
export function GenomeGeneSearch() {
  const [text, setText] = useState("");
  const [matches, setMatches] = useState<GenePlace[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const term = text.trim();
    if (term.length < 2) {
      setMatches(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      searchGenes(createClientSupabaseClient(), term, MAX_MATCHES)
        .then((found) => {
          if (!live) return;
          setFailed(false);
          setMatches(found);
        })
        .catch((err) => {
          console.error("[gene search]", err);
          if (live) setFailed(true);
        });
    }, TYPING_PAUSE_MS);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [text]);

  return (
    <div className="px-4 text-sm">
      <input
        type="search"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Find a gene by ID or name, e.g. AT1G01010"
        aria-label="Find a gene in Bloom's genomes"
        className="w-80 rounded border border-stone-300 px-2 py-1"
      />
      {failed && <p className="mt-1 text-red-700">Genes could not be searched.</p>}
      {matches && !failed && (
        <ul className="mt-1 max-h-48 overflow-y-auto">
          {matches.length === 0 && <li className="text-stone-500">No gene in Bloom&apos;s genomes matches.</li>}
          {matches.map((m) => (
            <li key={`${m.assembly} ${m.gene}`}>
              <Link href={browserLink(m)} className="text-lime-700 hover:underline">
                {m.gene}
                {m.name && m.name !== m.gene ? ` (${m.name})` : ""}
              </Link>{" "}
              <span className="text-stone-500">
                {m.label}, {m.contig}:{m.start.toLocaleString()}..{m.end.toLocaleString()}
              </span>
            </li>
          ))}
          {matches.length === MAX_MATCHES && (
            <li className="text-stone-500">Showing the first {MAX_MATCHES}; type more to narrow it down.</li>
          )}
        </ul>
      )}
    </div>
  );
}
