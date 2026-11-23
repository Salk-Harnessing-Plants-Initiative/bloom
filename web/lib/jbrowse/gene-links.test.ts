import { describe, expect, it, vi } from "vitest";

import {
  GENE_BATCH,
  NO_PLACE_TEXT,
  browserLink,
  datasetGenome,
  geneInGenomeLink,
  genomeLink,
  isRegion,
  orthobrowserLink,
  placeGeneEverywhere,
  placeGenes,
  placeGenesEverywhere,
  readyBuild,
  searchGenes,
  type GenePlace,
} from "./gene-links";

/** A query builder that records its calls and resolves to the next queued result. */
function fakeClient(results: { data: unknown; error: unknown }[]) {
  const calls: { table: string; ops: unknown[][] }[] = [];
  const from = vi.fn((table: string) => {
    const entry = { table, ops: [] as unknown[][] };
    calls.push(entry);
    const result = results.shift() ?? { data: [], error: null };
    const builder: Record<string, unknown> = {
      then: (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve),
    };
    for (const op of ["select", "eq", "in", "or", "ilike", "limit", "order", "maybeSingle"]) {
      builder[op] = (...args: unknown[]) => {
        entry.ops.push([op, ...args]);
        return builder;
      };
    }
    return builder;
  });
  return { supabase: { from } as never, calls };
}

const place: GenePlace = {
  gene: "AT1G01010",
  name: "NAC001",
  assembly: "athaliana_col0.v2",
  label: "athaliana_col0 v2",
  contig: "Chr1",
  start: 3631,
  end: 5899,
};

describe("links", () => {
  it("opens the browser on a genome version at a region", () => {
    expect(genomeLink("a.v1", "Chr1:1..1000000")).toBe("/app/jbrowse?genome=a.v1&loc=Chr1%3A1..1000000");
  });

  it("opens the browser on the gene's genome version at its span", () => {
    expect(browserLink(place)).toBe("/app/jbrowse?genome=athaliana_col0.v2&loc=Chr1%3A3631..5899");
  });

  it("opens the OrthoBrowser searching the gene", () => {
    expect(orthobrowserLink("AT1G01010")).toBe("/app/orthofinder?search=AT1G01010");
    expect(orthobrowserLink("a b&c")).toBe("/app/orthofinder?search=a%20b%26c");
  });

  it("opens a genome version at a gene, for the browser page to place", () => {
    expect(geneInGenomeLink("athaliana_col0.v2", "AT1G01010")).toBe(
      "/app/jbrowse?genome=athaliana_col0.v2&loc=AT1G01010",
    );
  });

  it("tells a region from a gene", () => {
    expect(isRegion("Chr1:1..9")).toBe(true);
    expect(isRegion("AT1G01010")).toBe(false);
  });

  it("explains each reason a gene has no browser link", () => {
    expect(Object.keys(NO_PLACE_TEXT).sort()).toEqual(["no browser files", "no genome version", "unavailable"]);
  });
});

describe("datasetGenome", () => {
  it("is the dataset's genome version", async () => {
    const { supabase, calls } = fakeClient([{ data: { genome_version_id: 254 }, error: null }]);
    await expect(datasetGenome(supabase, 7)).resolves.toBe(254);
    expect(calls[0].table).toBe("scrna_datasets");
    expect(calls[0].ops).toContainEqual(["eq", "id", 7]);
  });

  it("is null when the dataset records none", async () => {
    const { supabase } = fakeClient([{ data: { genome_version_id: null }, error: null }]);
    await expect(datasetGenome(supabase, 7)).resolves.toBeNull();
  });
});

describe("readyBuild", () => {
  it("is the version's ready build of a ready version, named <genome>.v<N>", async () => {
    const { supabase, calls } = fakeClient([
      { data: { id: 3, genome_reference_versions: { version: 2, genome_references: { name: "athaliana_col0" } } }, error: null },
    ]);
    await expect(readyBuild(supabase, 254)).resolves.toEqual({
      buildId: 3,
      assembly: "athaliana_col0.v2",
      label: "athaliana_col0 v2",
    });
    expect(calls[0].table).toBe("genome_browser_builds");
    expect(calls[0].ops).toContainEqual(["eq", "version_id", 254]);
    expect(calls[0].ops).toContainEqual(["eq", "status", "ready"]);
    expect(calls[0].ops).toContainEqual(["eq", "genome_reference_versions.status", "ready"]);
  });

  it("is null when the version has no ready build", async () => {
    const { supabase } = fakeClient([{ data: null, error: null }]);
    await expect(readyBuild(supabase, 254)).resolves.toBeNull();
  });

  it("throws when the read fails", async () => {
    const { supabase } = fakeClient([{ data: null, error: { message: "boom" } }]);
    await expect(readyBuild(supabase, 254)).rejects.toThrow("boom");
  });
});

describe("placeGenes", () => {
  const build = { buildId: 3, assembly: "athaliana_col0.v2", label: "athaliana_col0 v2" };
  const row = (gene_id: string, gene_name: string | null, start = 1) => ({
    gene_id,
    gene_name,
    contig: "Chr1",
    gene_start: start,
    gene_end: start + 9,
  });

  it("places genes by ID, and by an unambiguous name, in that build only", async () => {
    const { supabase, calls } = fakeClient([
      { data: [row("AT1G01010", "NAC001", 100)], error: null },
      { data: [row("AT1G01020", "ARV1", 200), row("AT1G99999", "DUP"), row("AT1G99998", "DUP")], error: null },
    ]);
    const places = await placeGenes(supabase, build, ["AT1G01010", "ARV1", "DUP", "MISSING"]);

    expect(places.get("AT1G01010")).toMatchObject({ gene: "AT1G01010", start: 100, assembly: "athaliana_col0.v2" });
    expect(places.get("ARV1")).toMatchObject({ gene: "AT1G01020", name: "ARV1", start: 200 });
    expect(places.has("DUP")).toBe(false);
    expect(places.has("MISSING")).toBe(false);
    expect(calls.every((c) => c.table === "genome_browser_genes")).toBe(true);
    expect(calls[0].ops).toContainEqual(["eq", "browser_build_id", 3]);
    expect(calls[0].ops).toContainEqual(["in", "gene_id", ["AT1G01010", "ARV1", "DUP", "MISSING"]]);
    expect(calls[1].ops).toContainEqual(["in", "gene_name", ["ARV1", "DUP", "MISSING"]]);
  });

  it("asks in batches", async () => {
    const genes = Array.from({ length: GENE_BATCH + 1 }, (_, i) => `G${i}`);
    const { supabase, calls } = fakeClient([]);
    await placeGenes(supabase, build, genes);
    const idBatches = calls.filter((c) => c.ops.some((op) => op[0] === "in" && op[1] === "gene_id"));
    expect(idBatches).toHaveLength(2);
  });

  it("asks nothing for no genes", async () => {
    const { supabase, calls } = fakeClient([]);
    expect((await placeGenes(supabase, build, [])).size).toBe(0);
    expect(calls).toHaveLength(0);
  });
});

describe("placeGeneEverywhere / searchGenes", () => {
  const hit = (gene_id: string, name: string, version: number, buildStatus = "ready", versionStatus = "ready") => ({
    gene_id,
    gene_name: "NAC001",
    contig: "Chr1",
    gene_start: 3631,
    gene_end: 5899,
    genome_browser_builds: {
      status: buildStatus,
      genome_reference_versions: { version, status: versionStatus, genome_references: { name } },
    },
  });

  it("lists every browsable genome version holding the gene, by genome and version", async () => {
    const { supabase, calls } = fakeClient([
      { data: [hit("AT1G01010", "zz", 1), hit("AT1G01010", "athaliana_col0", 2)], error: null },
    ]);
    const places = await placeGeneEverywhere(supabase, "AT1G01010");
    expect(places.map((p) => p.assembly)).toEqual(["athaliana_col0.v2", "zz.v1"]);
    expect(calls[0].ops).toContainEqual(["eq", "gene_id", "AT1G01010"]);
    expect(calls[0].ops).toContainEqual(["eq", "genome_browser_builds.status", "ready"]);
    expect(calls[0].ops).toContainEqual(["eq", "genome_browser_builds.genome_reference_versions.status", "ready"]);
  });

  it("groups each gene's browsable versions, asking for all genes at once", async () => {
    const { supabase, calls } = fakeClient([
      { data: [hit("AT1G01010", "athaliana_col0", 2), hit("AT1G01010", "athaliana_col0", 3), hit("AT1G01020", "athaliana_col0", 2)], error: null },
    ]);
    const places = await placeGenesEverywhere(supabase, ["AT1G01010", "AT1G01020", "AT1G01010", ""]);
    expect(places.get("AT1G01010")?.map((p) => p.assembly)).toEqual(["athaliana_col0.v2", "athaliana_col0.v3"]);
    expect(places.get("AT1G01020")).toHaveLength(1);
    expect(calls).toHaveLength(1);
    expect(calls[0].ops).toContainEqual(["in", "gene_id", ["AT1G01010", "AT1G01020"]]);
  });

  it("searches IDs and names by part, case-insensitively, in a build, by gene ID", async () => {
    const { supabase, calls } = fakeClient([{ data: [hit("AT1G01010", "athaliana_col0", 2)], error: null }]);
    const found = await searchGenes(supabase, "nac0", 25, [4]);
    expect(found).toHaveLength(1);
    expect(calls[0].ops).toContainEqual(["or", "gene_id.ilike.*nac0*,gene_name.ilike.*nac0*"]);
    expect(calls[0].ops).toContainEqual(["eq", "browser_build_id", 4]);
    expect(calls[0].ops).toContainEqual(["order", "gene_id"]);
    expect(calls[0].ops).toContainEqual(["limit", 25]);
  });

  it("searches each build on its own, so one genome's matches cannot crowd out another's", async () => {
    const { supabase, calls } = fakeClient([
      { data: [hit("AT1G01010", "a", 1)], error: null },
      { data: [hit("AT1G01010", "b", 1)], error: null },
    ]);
    const found = await searchGenes(supabase, "nac0", 50, [4, 7]);
    expect(calls.map((c) => c.ops.find((op) => op[0] === "eq" && op[1] === "browser_build_id"))).toEqual([
      ["eq", "browser_build_id", 4],
      ["eq", "browser_build_id", 7],
    ]);
    expect(calls.every((c) => c.ops.some((op) => op[0] === "limit" && op[1] === 50))).toBe(true);
    expect(found.map((p) => p.assembly)).toEqual(["a.v1", "b.v1"]);
  });

  it("searches nothing without builds", async () => {
    const { supabase, calls } = fakeClient([]);
    await expect(searchGenes(supabase, "nac0", 25, [])).resolves.toEqual([]);
    expect(calls).toHaveLength(0);
  });

  it.each(["", " ", "a", "x,y", "a(b", "a*b"])("searches nothing for %j", async (text) => {
    const { supabase, calls } = fakeClient([]);
    await expect(searchGenes(supabase, text, 25, [4])).resolves.toEqual([]);
    expect(calls).toHaveLength(0);
  });
});
