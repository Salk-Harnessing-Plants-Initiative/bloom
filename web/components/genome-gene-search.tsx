"use client";

import { useEffect, useMemo, useState } from "react";

import { parseGenomeVersion } from "@/lib/jbrowse/bloom-genomes";
import { groupByGenome, searchScope } from "@/lib/jbrowse/browser-session";
import { searchGenes, type GenePlace } from "@/lib/jbrowse/gene-links";
import { MAX_PICKS } from "@/lib/jbrowse/several-genes";
import { createClientSupabaseClient } from "@/lib/supabase/client";

/** Most matches listed at once, shared between the genomes searched. */
const MAX_MATCHES = 100;
/** Pause after typing before searching, in ms. */
const TYPING_PAUSE_MS = 300;

interface Props {
  /** Each Bloom genome version's browser build, by assembly name. */
  bloomBuilds: Record<string, number>;
  /** The genomes open in the browser's views. */
  openGenomes: string[];
  /** Moves the browser to one gene. */
  onGoTo: (place: GenePlace) => void;
  /** Opens several genes of one genome together. */
  onOpenTogether: (assembly: string, genes: GenePlace[]) => Promise<void>;
}

/** A Bloom genome version as people read it, e.g. "tair10 v1". */
const genomeLabel = (assembly: string) => {
  const v = parseGenomeVersion(assembly);
  return v ? `${v.genome} v${v.version}` : assembly;
};

const same = (a: GenePlace, b: GenePlace) => a.assembly === b.assembly && a.gene === b.gene;

/** Finds genes in Bloom's genomes by part of an ID or name: in the genomes open in the browser,
 * or all of them when none is; picks several from one genome to open together. */
export function GenomeGeneSearch({ bloomBuilds, openGenomes, onGoTo, onOpenTogether }: Props) {
  const [text, setText] = useState("");
  const [matches, setMatches] = useState<GenePlace[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [picks, setPicks] = useState<GenePlace[]>([]);
  const [opening, setOpening] = useState(false);

  const picked = picks[0] ?? null;
  const scope = useMemo(
    () => searchScope(openGenomes, picked?.assembly ?? null, bloomBuilds),
    [openGenomes, picked?.assembly, bloomBuilds],
  );
  const scopeKey = scope.join(",");
  const perGenome = Math.ceil(MAX_MATCHES / Math.max(1, scope.length));
  const searchedGenomes = openGenomes.filter((a) => bloomBuilds[a] !== undefined).map(genomeLabel);
  const searchedText = picked
    ? picked.label
    : searchedGenomes.length
      ? searchedGenomes.join(", ")
      : "Bloom's genomes";

  useEffect(() => {
    const term = text.trim();
    if (term.length < 2) {
      setMatches(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      setFailed(false);
      searchGenes(createClientSupabaseClient(), term, perGenome, scope)
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
    // scopeKey stands for scope's contents.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, scopeKey]);

  const isPicked = (p: GenePlace) => picks.some((q) => same(p, q));
  const toggle = (p: GenePlace) => setPicks((now) => (now.some((q) => same(p, q)) ? now.filter((q) => !same(p, q)) : [...now, p]));
  const full = picks.length >= MAX_PICKS;
  const shown = (matches ?? []).filter((m) => !picked || m.assembly === picked.assembly);
  const groups = groupByGenome(shown);
  const openTogether = () => {
    setOpening(true);
    onOpenTogether(picks[0].assembly, picks).finally(() => setOpening(false));
  };

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
      {picked ? (
        <p className="mt-1 text-stone-500">Picking from {picked.label}; genes are picked from one genome at a time.</p>
      ) : (
        searchedGenomes.length > 0 && <p className="mt-1 text-stone-500">Searching {searchedText}.</p>
      )}
      {picks.length > 0 && (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <span className="text-stone-700">
            {picks.length} picked: {picks.map((p) => p.gene).join(", ")}
          </span>
          <button
            type="button"
            onClick={openTogether}
            disabled={opening}
            className="rounded bg-lime-700 px-2 py-0.5 text-white hover:bg-lime-800 disabled:opacity-60"
          >
            {opening ? "Opening…" : "Open together"}
          </button>
          <button type="button" onClick={() => setPicks([])} className="text-lime-700 hover:underline">
            Clear
          </button>
        </div>
      )}
      {full && <p className="mt-1 text-stone-500">At most {MAX_PICKS} genes can be opened together.</p>}
      {failed && <p className="mt-1 text-red-700">Genes could not be searched.</p>}
      {matches && !failed && (
        <div className="mt-1 max-h-72 overflow-y-auto">
          {shown.length === 0 && <p className="text-stone-500">No gene in {searchedText} matches.</p>}
          {groups.map((group) => (
            <div key={group.assembly}>
              <p className="mt-1 font-medium text-stone-700">{group.label}</p>
              <ul>
                {group.places.map((m) => (
                  <li key={`${m.assembly} ${m.gene}`} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={isPicked(m)}
                      disabled={full && !isPicked(m)}
                      onChange={() => toggle(m)}
                      aria-label={`Pick ${m.gene} in ${m.label}`}
                    />
                    <button type="button" onClick={() => onGoTo(m)} className="text-lime-700 hover:underline">
                      {m.gene}
                    </button>
                    <span className="text-stone-500">
                      {m.name && m.name !== m.gene ? `${m.name}, ` : ""}
                      {m.contig}:{m.start.toLocaleString()}..{m.end.toLocaleString()}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {groups.some((g) => g.places.length >= perGenome) && (
            <p className="text-stone-500">Showing the first {perGenome} per genome; type more to narrow it down.</p>
          )}
        </div>
      )}
    </div>
  );
}
