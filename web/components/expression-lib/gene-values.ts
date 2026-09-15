/** Reading a dataset's cells once and its genes a few at a time, each kept once read. */

import {
  fetchCells,
  fetchGeneCounts,
  NoStoredExpressionError,
  type CellArraysRow,
} from "@/components/expression-lib/scrna-client";

/** Genes read at once; each is a request and a download. */
export const GENE_READS_AT_ONCE = 6;

export type GeneRead =
  | { gene: string; values: Float32Array }
  | { gene: string; missing: true }
  | { gene: string; error: string };

export interface GeneReaderDeps {
  fetchCells: (datasetId: number) => Promise<CellArraysRow[]>;
  fetchGeneCounts: (datasetId: number, gene: string, cellCount: number) => Promise<Float32Array>;
}

/** One reader per dataset: its cells are read once, and each gene once, with
 *  at most GENE_READS_AT_ONCE gene reads in flight. A read that failed for any
 *  reason but a missing gene is tried again when asked for again. */
export function createGeneReader(
  datasetId: number,
  deps: GeneReaderDeps = { fetchCells, fetchGeneCounts },
) {
  let cellsRead: Promise<CellArraysRow[]> | null = null;
  const reads = new Map<string, Promise<GeneRead>>();
  let running = 0;
  const waiting: (() => void)[] = [];

  async function inTurn<T>(work: () => Promise<T>): Promise<T> {
    while (running >= GENE_READS_AT_ONCE) await new Promise<void>((go) => waiting.push(go));
    running++;
    try {
      return await work();
    } finally {
      running--;
      waiting.shift()?.();
    }
  }

  const cells = () => (cellsRead ??= deps.fetchCells(datasetId));

  const gene = (name: string): Promise<GeneRead> => {
    const known = reads.get(name);
    if (known) return known;
    const read = cells()
      .then((all) => inTurn(() => deps.fetchGeneCounts(datasetId, name, all.length)))
      .then(
        (values): GeneRead => ({ gene: name, values }),
        (err): GeneRead => {
          if (err instanceof NoStoredExpressionError) return { gene: name, missing: true };
          reads.delete(name);
          return { gene: name, error: err instanceof Error ? err.message : String(err) };
        },
      );
    reads.set(name, read);
    return read;
  };

  return { cells, gene };
}

export type GeneReader = ReturnType<typeof createGeneReader>;
