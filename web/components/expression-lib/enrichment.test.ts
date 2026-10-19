/**
 * A dataset's GO and pathway results are one gzipped JSON file per enrichment run, recorded
 * in scrna_enrichment_runs. What is pinned here: the newest ready run is the one read, through
 * the storage client (the bucket is private); the file is checked on load, so a file of another
 * format or dataset is refused rather than drawn; and the terms of a comparison, direction and
 * kind come back most significant first.
 */

import { gzipSync, strToU8 } from "fflate";
import { describe, expect, it, vi } from "vitest";

import {
  clustersOf,
  comparisonsOf,
  decodeEnrichment,
  EnrichmentFileError,
  fetchLatestEnrichment,
  geneRatio,
  goTermUrl,
  keggMapUrl,
  termsFor,
  type EnrichmentFile,
} from "./enrichment";

const TERM = {
  comparison: "2_vs_rest", direction: "up", kind: "GO_BP", term: "GO:0006412",
  name: "translation", list_genes_in_term: 30, list_size: 120, term_size: 300,
  background_size: 8000, p: 1e-12, fdr: 1e-10, genes: ["G1", "G2"],
};

function file(results: object[] = [TERM], extra: object = {}): object {
  return {
    format: "bloom-enrichment/1", dataset_id: 25, de_run_id: 5,
    annotation: "sbicolor.btx623.hpiv3.v1/eggnog-b1",
    params: { top_n: 300, fdr_max: 0.05 }, background_genes: 21957, annotated_genes: 11427,
    results, ...extra,
  };
}

const gz = (o: object) => gzipSync(strToU8(JSON.stringify(o)));

// --------------------------------------------------------------------------- //
// Decoding and checking a file
// --------------------------------------------------------------------------- //

describe("decodeEnrichment", () => {
  it("reads a gzipped file", () => {
    expect(decodeEnrichment(gz(file()), 25).results[0].name).toBe("translation");
  });

  it("reads one the server already unzipped", () => {
    expect(decodeEnrichment(strToU8(JSON.stringify(file())), 25).annotation)
      .toBe("sbicolor.btx623.hpiv3.v1/eggnog-b1");
  });

  it("refuses another format", () => {
    expect(() => decodeEnrichment(gz(file([], { format: "bloom-enrichment/0" })), 25))
      .toThrow(EnrichmentFileError);
  });

  it("refuses a file of another dataset", () => {
    expect(() => decodeEnrichment(gz(file()), 26)).toThrow(/dataset 25/);
  });

  it.each([
    ["an unknown direction", { direction: "sideways" }],
    ["an unknown kind", { kind: "REACTOME" }],
    ["a p-value that isn't a number", { p: "small" }],
    ["genes that aren't a list", { genes: "G1" }],
    ["more list genes in the term than in the list", { list_genes_in_term: 121 }],
  ])("refuses a term with %s", (_, change) => {
    expect(() => decodeEnrichment(gz(file([{ ...TERM, ...change }])), 25))
      .toThrow(EnrichmentFileError);
  });
});

// --------------------------------------------------------------------------- //
// Reading the newest ready run
// --------------------------------------------------------------------------- //

function client(rows: object[], stored: Uint8Array | null) {
  const asked: Record<string, unknown> = {};
  const downloaded: string[] = [];
  const query = {
    select: () => query,
    eq: (column: string, value: unknown) => { asked[column] = value; return query; },
    order: (column: string, options: object) => { asked.order = [column, options]; return query; },
    limit: async () => ({ data: rows, error: null }),
  };
  return {
    asked,
    downloaded,
    from: (table: string) => { asked.table = table; return query; },
    storage: {
      from: (bucket: string) => ({
        download: async (path: string) => {
          downloaded.push(`${bucket}/${path}`);
          return stored === null
            ? { data: null, error: { message: "Object not found" } }
            : { data: new Blob([new Uint8Array(stored)]), error: null };
        },
      }),
    },
  };
}

const RUN = { id: 9, de_run_id: 5, annotation_build_id: 3, file_path: "enrichment/25/run-9.json.gz",
              ready_at: "2026-10-19T20:00:00Z" };

describe("fetchLatestEnrichment", () => {
  it("reads the newest ready run's file through the storage client", async () => {
    const c = client([RUN], gz(file()));
    const found = await fetchLatestEnrichment(c as never, 25);
    expect(c.asked).toMatchObject({ table: "scrna_enrichment_runs", dataset_id: 25,
                                     status: "ready" });
    expect(c.asked.order).toEqual(["ready_at", { ascending: false }]);
    expect(c.downloaded).toEqual(["scrna/enrichment/25/run-9.json.gz"]);
    expect(found?.run.id).toBe(9);
    expect(found?.file.results).toHaveLength(1);
  });

  it("is nothing when the dataset has no ready run", async () => {
    expect(await fetchLatestEnrichment(client([], null) as never, 25)).toBeNull();
  });

  it("says when the run's file can't be read", async () => {
    await expect(fetchLatestEnrichment(client([RUN], null) as never, 25))
      .rejects.toThrow(/Object not found/);
  });
});

// --------------------------------------------------------------------------- //
// Choosing terms
// --------------------------------------------------------------------------- //

describe("comparisonsOf and termsFor", () => {
  const terms = [
    { ...TERM, comparison: "10_vs_rest" },
    { ...TERM, comparison: "2_vs_rest", term: "GO:2", fdr: 1e-3 },
    { ...TERM, comparison: "2_vs_rest", term: "GO:1", fdr: 1e-9 },
    { ...TERM, comparison: "2_vs_rest", direction: "down" },
    { ...TERM, comparison: "2_vs_rest", kind: "KEGG", term: "map03010" },
  ];
  const parsed = decodeEnrichment(gz(file(terms)), 25) as EnrichmentFile;

  it("lists comparisons in cluster order, 2 before 10", () => {
    expect(comparisonsOf(parsed)).toEqual(["2_vs_rest", "10_vs_rest"]);
  });

  it("gives one comparison, direction and kind, most significant first", () => {
    expect(termsFor(parsed, "2_vs_rest", "up", "GO_BP").map((t) => t.term))
      .toEqual(["GO:1", "GO:2"]);
  });

  it("gives the share of the list in the term", () => {
    expect(geneRatio(TERM as never)).toBeCloseTo(0.25);
  });
});

describe("clustersOf", () => {
  const terms = [
    { ...TERM, comparison: "10_vs_rest" },
    { ...TERM, comparison: "2_vs_rest" },
    { ...TERM, comparison: "2_vs_5" },
    { ...TERM, comparison: "orphan" },
  ];
  const parsed = decodeEnrichment(gz(file(terms)), 25);
  const rows = [
    { cluster_id: "2", contrast: "2_vs_rest" },
    { cluster_id: "2", contrast: "2_vs_5" },
    { cluster_id: "10", contrast: "10_vs_rest" },
  ];

  it("groups comparisons under their DE row's cluster, in cluster order", () => {
    expect(clustersOf(parsed, rows)).toEqual([
      { cluster: "2", comparisons: [{ comparison: "2_vs_5", label: "cluster 5" },
                                    { comparison: "2_vs_rest", label: "all other cells" }] },
      { cluster: "10", comparisons: [{ comparison: "10_vs_rest", label: "all other cells" }] },
      { cluster: "orphan", comparisons: [{ comparison: "orphan", label: "all other cells" }] },
    ]);
  });

  it("names an older one-vs-rest row, which has no contrast, by its cluster", () => {
    const old = decodeEnrichment(gz(file([{ ...TERM, comparison: "7" }])), 25);
    expect(clustersOf(old, [{ cluster_id: "7", contrast: null }])).toEqual([
      { cluster: "7", comparisons: [{ comparison: "7", label: "all other cells" }] },
    ]);
  });
});

describe("keggMapUrl", () => {
  const kegg = { ...TERM, kind: "KEGG", term: "map03010", name: "Ribosome",
                 kos: ["K02866", "K02872"] };

  it("colours the list genes' orthologs on KEGG's map: red for up", () => {
    const url = new URL(keggMapUrl(kegg as never)!);
    expect(url.origin + url.pathname).toBe("https://www.kegg.jp/kegg-bin/show_pathway");
    expect(url.searchParams.get("map")).toBe("map03010");
    expect(url.searchParams.get("multi_query")).toBe("K02866 #d73027\nK02872 #d73027");
  });

  it("blue for down", () => {
    const url = new URL(keggMapUrl({ ...kegg, direction: "down" } as never)!);
    expect(url.searchParams.get("multi_query")).toContain("K02866 #4575b4");
  });

  it("is nothing for a GO term, or a pathway without orthologs", () => {
    expect(keggMapUrl(TERM as never)).toBeNull();
    expect(keggMapUrl({ ...kegg, kos: [] } as never)).toBeNull();
    expect(keggMapUrl({ ...kegg, kos: undefined } as never)).toBeNull();
  });

  it("refuses a file whose orthologs aren't a list of K numbers", () => {
    expect(() => decodeEnrichment(gz(file([{ ...kegg, kos: ["not-a-ko"] }])), 25))
      .toThrow(EnrichmentFileError);
  });
});

describe("goTermUrl", () => {
  it("opens a GO term's QuickGO page", () => {
    expect(goTermUrl(TERM as never)).toBe("https://www.ebi.ac.uk/QuickGO/term/GO%3A0006412");
  });

  it("is nothing for a KEGG pathway", () => {
    expect(goTermUrl({ ...TERM, kind: "KEGG", term: "map03010" } as never)).toBeNull();
  });
});
