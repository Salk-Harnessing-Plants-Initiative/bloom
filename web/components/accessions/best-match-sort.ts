// Pure sort/rank helpers for the best-match-per-accession table, split out so
// they are unit-testable without rendering the client component.

export type BestMatchRow = {
  accession_id: number;
  accession_name: string | null;
  uid: string;
  gene_id: string | null;
  similarity: number;
};

export type SortKey = "accession_name" | "gene_id" | "similarity";
export type SortDir = "asc" | "desc";

// Similarity rank (1 = most similar), keyed by uid. Stable regardless of the
// column the table is currently sorted by.
export function similarityRank(rows: BestMatchRow[]): Map<string, number> {
  const m = new Map<string, number>();
  [...rows]
    .sort((a, b) => b.similarity - a.similarity)
    .forEach((r, i) => m.set(r.uid, i + 1));
  return m;
}

// A copy of rows ordered by the chosen column/direction. Text columns compare
// case/locale-aware; null names/genes sort as empty strings.
export function sortBestMatchRows(
  rows: BestMatchRow[],
  sortKey: SortKey,
  sortDir: SortDir,
): BestMatchRow[] {
  const dir = sortDir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    let cmp: number;
    if (sortKey === "similarity") {
      cmp = a.similarity - b.similarity;
    } else {
      const av = (sortKey === "accession_name" ? a.accession_name : a.gene_id) ?? "";
      const bv = (sortKey === "accession_name" ? b.accession_name : b.gene_id) ?? "";
      cmp = av.localeCompare(bv);
    }
    return cmp * dir;
  });
}
