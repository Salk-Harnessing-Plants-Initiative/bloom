"use client";

import { useState } from "react";

import { ExpressionGeneSearch } from "@/components/expression-gene-search";

interface Props {
  datasetId: number;
  genes: string[];
  max: number;
  onAdd: (gene: string) => void;
  onRemove: (gene: string) => void;
}

/** The genes shown, as chips to remove, and a search to add another, up to `max`. */
export function ExpressionGenePicker({ datasetId, genes, max, onAdd, onRemove }: Props) {
  const [full, setFull] = useState(false);
  // A new search box after each pick, so the next search starts empty.
  const [searchKey, setSearchKey] = useState(0);

  const add = (gene: string | null) => {
    if (!gene || genes.includes(gene)) return;
    if (genes.length >= max) {
      setFull(true);
      return;
    }
    setFull(false);
    onAdd(gene);
    setSearchKey((k) => k + 1);
  };

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-64">
          <ExpressionGeneSearch key={searchKey} datasetId={datasetId} value={null} onChange={add} />
        </div>
        <span className="text-xs tabular-nums text-stone-500">
          {genes.length} of {max} genes
        </span>
      </div>
      {genes.length > 0 && (
        <ul aria-label="Genes shown" className="flex flex-wrap gap-1.5">
          {genes.map((gene) => (
            <li
              key={gene}
              className="inline-flex items-center gap-1 rounded-full border border-stone-300 bg-white py-0.5 pl-2.5 pr-1 font-mono text-xs text-stone-800"
            >
              {gene}
              <button
                type="button"
                aria-label={`Remove ${gene}`}
                onClick={() => {
                  setFull(false);
                  onRemove(gene);
                }}
                className="rounded-full px-1 text-stone-400 hover:bg-stone-100 hover:text-stone-700"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {full && (
        <p role="alert" className="text-xs text-amber-800">
          {max} genes is the most at once; remove one to add another.
        </p>
      )}
    </div>
  );
}

export default ExpressionGenePicker;
