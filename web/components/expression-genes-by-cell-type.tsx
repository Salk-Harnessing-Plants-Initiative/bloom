"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { ExpressionGeneDotplot } from "@/components/expression-gene-dotplot";
import { ExpressionGenePicker } from "@/components/expression-gene-picker";
import { ExpressionGeneViolin } from "@/components/expression-gene-violin";
import {
  cellTypes,
  csvText,
  groupCells,
  MAX_GENES,
  splitsOffered,
  tableRows,
  type Split,
} from "@/components/expression-lib/gene-stats";
import { createGeneReader, type GeneRead } from "@/components/expression-lib/gene-values";
import { loadStartingGenes } from "@/components/expression-lib/starting-genes";
import {
  fetchClusters,
  fetchDataset,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";
import type { Database } from "@/lib/database.types";

type Cluster = Database["public"]["Tables"]["scrna_clusters"]["Row"];
type Source = "markers" | "de" | "none";

/** Where the genes the tab opened on came from; `count` is how many it opened on. */
function sourceNote(source: Source, count: number): string {
  if (source === "markers") {
    return "By default, each cell type's top marker genes. Add or remove genes as you like.";
  }
  if (source === "de") {
    return `By default, ${count} ${count === 1 ? "gene" : "genes"} from the differential expression results (FDR < 0.05, |log2FC| > 0.5). Add or remove genes as you like.`;
  }
  return "Search for a gene to add it.";
}

const SPLITS: { key: Split; label: string }[] = [
  { key: "none", label: "Cell types" },
  { key: "genotype", label: "By genotype" },
  { key: "transgene", label: "By transgene" },
];

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** A dataset's genes against its cell types: a dot plot of the chosen genes,
 *  and one gene's distribution in each cell type, either split by genotype or
 *  transgene status. */
export function ExpressionGenesByCellType({ datasetId }: { datasetId: number }) {
  const reader = useMemo(() => createGeneReader(datasetId), [datasetId]);
  const activeReader = useRef(reader);
  const requested = useRef(new Set<string>());

  const [cells, setCells] = useState<CellArraysRow[] | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [unitsLabel, setUnitsLabel] = useState("log-normalized");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [genes, setGenes] = useState<string[]>([]);
  const [source, setSource] = useState<Source | null>(null);
  const [startCount, setStartCount] = useState(0);
  const [startError, setStartError] = useState<string | null>(null);
  const [reads, setReads] = useState<ReadonlyMap<string, GeneRead>>(new Map());
  const [split, setSplit] = useState<Split>("none");
  const [perGene, setPerGene] = useState(true);
  const [chosen, setChosen] = useState<string | null>(null);

  // The cells, the cell types and the genes to open on, once per dataset.
  useEffect(() => {
    let cancelled = false;
    activeReader.current = reader;
    requested.current = new Set();
    setCells(null);
    setClusters([]);
    setLoadError(null);
    setGenes([]);
    setSource(null);
    setStartError(null);
    setReads(new Map());
    setSplit("none");
    setChosen(null);
    (async () => {
      let catalogue: Cluster[];
      try {
        const [all, found, dataset] = await Promise.all([
          reader.cells(),
          fetchClusters(datasetId),
          fetchDataset(datasetId),
        ]);
        if (cancelled) return;
        catalogue = found;
        setCells(all);
        setClusters(found);
        if (dataset?.expression_units) setUnitsLabel(dataset.expression_units);
      } catch (err) {
        if (!cancelled) setLoadError(message(err));
        return;
      }
      try {
        const clusterIds = [...catalogue].sort((a, b) => a.ordinal - b.ordinal).map((c) => c.cluster_id);
        const start = await loadStartingGenes(datasetId, clusterIds);
        if (cancelled) return;
        setGenes(start.genes);
        setStartCount(start.genes.length);
        setSource(start.source);
      } catch (err) {
        if (cancelled) return;
        setSource("none");
        setStartError(`Could not choose genes to open on: ${message(err)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetId, reader]);

  // Each gene is read once; one that failed for a reason other than having no
  // stored expression is read again if it is added again.
  useEffect(() => {
    for (const gene of genes) {
      if (requested.current.has(gene)) continue;
      requested.current.add(gene);
      void reader.gene(gene).then((read) => {
        if (activeReader.current !== reader) return;
        if ("error" in read) requested.current.delete(gene);
        setReads((prev) => new Map(prev).set(gene, read));
      });
    }
  }, [genes, reader]);

  const types = useMemo(() => cellTypes(clusters), [clusters]);
  const offered = useMemo(
    () => (cells ? splitsOffered(cells) : { genotype: false, transgene: false }),
    [cells],
  );
  const groups = useMemo(() => (cells ? groupCells(cells, types, split) : []), [cells, types, split]);
  const values = useMemo(() => {
    const out = new Map<string, Float32Array>();
    for (const [gene, read] of reads) if ("values" in read) out.set(gene, read.values);
    return out;
  }, [reads]);

  if (loadError) {
    return (
      <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
        Could not load this dataset&apos;s cells: {loadError}
      </div>
    );
  }
  if (!cells) {
    return <div className="p-6 text-sm text-stone-500">Loading the cells…</div>;
  }

  const shown = genes.filter((g) => values.has(g));
  const missing = genes.filter((g) => {
    const read = reads.get(g);
    return read !== undefined && "missing" in read;
  });
  const failed = genes.flatMap((g) => {
    const read = reads.get(g);
    return read && "error" in read ? [`${g}: ${read.error}`] : [];
  });
  const loading = genes.filter((g) => !reads.has(g)).length;
  const current = chosen && values.has(chosen) ? chosen : shown[0] ?? null;
  const splitOptions = SPLITS.filter(
    (s) => s.key === "none" || (s.key === "genotype" ? offered.genotype : offered.transgene),
  );

  const download = () => {
    const text = csvText(tableRows(shown, values, groups));
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `genes-by-cell-type${split === "none" ? "" : `-by-${split}`}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div data-testid="genes-by-cell-type" className="flex flex-col gap-4">
      <section className="flex flex-col gap-3 rounded-lg border border-stone-200 bg-white p-4">
        <ExpressionGenePicker
          datasetId={datasetId}
          genes={genes}
          max={MAX_GENES}
          onAdd={(gene) => setGenes((prev) => [...prev, gene])}
          onRemove={(gene) => setGenes((prev) => prev.filter((g) => g !== gene))}
        />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {splitOptions.length > 1 && (
            <div
              role="group"
              aria-label="Split cell types"
              className="inline-flex rounded-md border border-stone-300 bg-white p-0.5"
            >
              {splitOptions.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  aria-pressed={split === option.key}
                  onClick={() => setSplit(option.key)}
                  className={`rounded px-3 py-1 text-sm transition-colors ${
                    split === option.key ? "bg-stone-800 text-white" : "text-stone-700 hover:bg-stone-100"
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
          <label className="flex items-center gap-2 text-xs text-stone-700">
            <input
              type="checkbox"
              role="switch"
              checked={perGene}
              onChange={(e) => setPerGene(e.target.checked)}
              className="accent-lime-700"
            />
            Scale each gene to its own highest mean
          </label>
          <button
            type="button"
            onClick={download}
            disabled={shown.length === 0}
            className="ml-auto rounded-md border border-stone-300 bg-white px-2.5 py-1 text-xs text-stone-700 hover:bg-stone-100 disabled:opacity-50"
          >
            Download CSV
          </button>
        </div>
        <div className="flex flex-col gap-1 text-xs text-stone-500" role="status">
          {source && <span>{sourceNote(source, startCount)}</span>}
          {loading > 0 && <span>Loading {loading} of {genes.length} genes…</span>}
          {missing.length > 0 && (
            <span className="text-amber-800">
              No stored expression in this dataset for: {missing.join(", ")}
            </span>
          )}
          {failed.length > 0 && <span className="text-rose-700">{failed.join(" · ")}</span>}
          {startError && <span className="text-rose-700">{startError}</span>}
        </div>
      </section>

      {shown.length > 0 && (
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h3 className="mb-1 text-sm font-medium text-stone-800">Expression across cell types</h3>
          <p className="mb-3 text-xs text-stone-500">
            Every cell counts: a cell with no stored value for a gene counts as 0. Click a gene
            for its expression in each cell type.
          </p>
          <ExpressionGeneDotplot
            genes={shown}
            groups={groups}
            values={values}
            perGene={perGene}
            chosen={current}
            onChoose={setChosen}
            unitsLabel={unitsLabel}
          />
        </section>
      )}

      {current && (
        <section className="rounded-lg border border-stone-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-medium text-stone-800">
            <span className="font-mono">{current}</span> expression by cell type
            {split === "genotype" ? " and genotype" : split === "transgene" ? " and transgene status" : ""}
          </h3>
          <ExpressionGeneViolin
            gene={current}
            groups={groups}
            values={values.get(current)!}
            unitsLabel={unitsLabel}
          />
        </section>
      )}
    </div>
  );
}

export default ExpressionGenesByCellType;
