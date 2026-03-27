"use client";

import { useState } from "react";
import GeneSearch, { type KnnResult } from "./GeneSearch";
import KnnResults from "./KnnResults";
import SpeciesBreakdown from "./SpeciesBreakdown";
import KnnGraph from "./KnnGraph";
import GeneTree from "./GeneTree";

export default function EmbedTreeTab() {
  const [results, setResults] = useState<KnnResult[]>([]);
  const [queryUid, setQueryUid] = useState("");
  const [isSearching, setIsSearching] = useState(false);

  function handleSearch(newResults: KnnResult[], uid: string) {
    setResults(newResults);
    setQueryUid(uid);
  }

  return (
    <div className="flex flex-col overflow-hidden">
      <div className="px-4 pt-1 pb-2 border-b border-stone-200 bg-white">
        <div className="mb-2">
          <h2 className="text-lg font-semibold text-neutral-800 mb-0.5">
            Embedding Phylogenomics
          </h2>
          <p className="text-neutral-500 text-xs">
            Explore protein sequence relationships using ESM-2 embeddings across plant species.
          </p>
        </div>

        <GeneSearch
          onSearch={handleSearch}
          isSearching={isSearching}
          setIsSearching={setIsSearching}
        />
      </div>

      <div className="flex-grow overflow-y-auto p-4 space-y-5">
        {results.length > 0 && (
          <>
            <SpeciesBreakdown results={results} />

            <KnnGraph results={results} queryUid={queryUid} />

            <KnnResults
              results={results}
              queryUid={queryUid}
              showWithin={true}
              showAcross={true}
            />

            <GeneTree results={results} queryUid={queryUid} />
          </>
        )}
      </div>
    </div>
  );
}
