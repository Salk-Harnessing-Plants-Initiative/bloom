import { describe, it, expect } from "vitest";
import {
  similarityRank,
  sortBestMatchRows,
  type BestMatchRow,
} from "./best-match-sort";

const rows: BestMatchRow[] = [
  { accession_id: 3, accession_name: "Col-0", uid: "3:g3", gene_id: "g_beta", similarity: 0.91, rank_in_accession: 1 },
  { accession_id: 1, accession_name: "Abd-0", uid: "1:g1", gene_id: "g_alpha", similarity: 0.97, rank_in_accession: 1 },
  { accession_id: 2, accession_name: "Zu-0", uid: "2:g2", gene_id: "g_gamma", similarity: 0.88, rank_in_accession: 1 },
];

describe("similarityRank", () => {
  it("ranks 1 = most similar, independent of input order", () => {
    const rank = similarityRank(rows);
    expect(rank.get("1:g1")).toBe(1); // 0.97
    expect(rank.get("3:g3")).toBe(2); // 0.91
    expect(rank.get("2:g2")).toBe(3); // 0.88
  });

  it("does not mutate the input array", () => {
    const copy = [...rows];
    similarityRank(rows);
    expect(rows).toEqual(copy);
  });
});

describe("sortBestMatchRows", () => {
  it("sorts by similarity descending (most similar first)", () => {
    const out = sortBestMatchRows(rows, "similarity", "desc");
    expect(out.map((r) => r.similarity)).toEqual([0.97, 0.91, 0.88]);
  });

  it("sorts by similarity ascending", () => {
    const out = sortBestMatchRows(rows, "similarity", "asc");
    expect(out.map((r) => r.similarity)).toEqual([0.88, 0.91, 0.97]);
  });

  it("sorts by accession name A→Z", () => {
    const out = sortBestMatchRows(rows, "accession_name", "asc");
    expect(out.map((r) => r.accession_name)).toEqual(["Abd-0", "Col-0", "Zu-0"]);
  });

  it("sorts by accession name Z→A", () => {
    const out = sortBestMatchRows(rows, "accession_name", "desc");
    expect(out.map((r) => r.accession_name)).toEqual(["Zu-0", "Col-0", "Abd-0"]);
  });

  it("sorts by gene id", () => {
    const out = sortBestMatchRows(rows, "gene_id", "asc");
    expect(out.map((r) => r.gene_id)).toEqual(["g_alpha", "g_beta", "g_gamma"]);
  });

  it("treats a null name/gene as an empty string (sorts first ascending)", () => {
    const withNull: BestMatchRow[] = [
      { accession_id: 5, accession_name: "Bur-0", uid: "5:g5", gene_id: "z", similarity: 0.5, rank_in_accession: 1 },
      { accession_id: 6, accession_name: null, uid: "6:g6", gene_id: "a", similarity: 0.4, rank_in_accession: 1 },
    ];
    const out = sortBestMatchRows(withNull, "accession_name", "asc");
    expect(out[0].accession_name).toBeNull();
  });

  it("tiebreaks same-accession rows by within-accession rank (closest first)", () => {
    const same: BestMatchRow[] = [
      { accession_id: 1, accession_name: "Abd-0", uid: "1:g2", gene_id: "g2", similarity: 0.7, rank_in_accession: 2 },
      { accession_id: 1, accession_name: "Abd-0", uid: "1:g1", gene_id: "g1", similarity: 0.9, rank_in_accession: 1 },
    ];
    const out = sortBestMatchRows(same, "accession_name", "asc");
    expect(out.map((r) => r.rank_in_accession)).toEqual([1, 2]);
  });

  it("does not mutate the input array", () => {
    const copy = [...rows];
    sortBestMatchRows(rows, "gene_id", "desc");
    expect(rows).toEqual(copy);
  });
});
