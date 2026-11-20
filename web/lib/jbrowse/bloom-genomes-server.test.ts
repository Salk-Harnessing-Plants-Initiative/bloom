import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BROWSABLE_SELECT,
  LINKED_VERSION_SELECT,
  SIGNABLE_BUILD_SELECT,
  REDIRECT_MAX_AGE_S,
  SIGNED_URL_TTL_S,
  geneLinkRedirect,
  loadBrowserConfig,
  placePicks,
  signBuildFile,
  whyNotOffered,
} from "./bloom-genomes-server";
import { browserConfig } from "./config";

const ROLES = [
  { role: "fasta", basename: "genome.fa.gz" },
  { role: "fasta_fai", basename: "genome.fa.gz.fai" },
  { role: "fasta_gzi", basename: "genome.fa.gz.gzi" },
  { role: "gff3", basename: "genes.gff3.gz" },
  { role: "gff3_tbi", basename: "genes.gff3.gz.tbi" },
];

const buildRow = (
  id: number,
  name: string,
  version: number,
  species: { genus: string | null; species: string | null } = { genus: "arabidopsis", species: "THALIANA" },
) => ({
  id,
  genome_reference_versions: {
    version,
    assembly: "TAIR10",
    annotation: null,
    genome_references: { name, species },
  },
});

/** A query builder that records its calls and resolves to `result`. */
function query(result: { data: unknown; error: unknown }) {
  const calls: unknown[][] = [];
  const builder: Record<string, unknown> = {
    calls,
    then: (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve),
  };
  for (const method of ["select", "eq", "in", "maybeSingle"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  return builder as typeof builder & { calls: unknown[][] };
}

function client({
  rows = { data: [] as unknown, error: null as unknown },
  roles = { data: ROLES as unknown, error: null as unknown },
  signed = { data: { signedUrl: "http://kong:8000/storage/v1/object/sign/x?token=t" } as unknown, error: null as unknown },
} = {}) {
  const table = query(rows);
  const createSignedUrl = vi.fn(async () => signed);
  const supabase = {
    from: vi.fn(() => table),
    rpc: vi.fn(async () => roles),
    storage: { from: vi.fn(() => ({ createSignedUrl })) },
  };
  return { supabase: supabase as never, table, createSignedUrl, raw: supabase };
}

describe("loadBrowserConfig", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads ready builds of ready versions, both statuses filtered through inner joins", async () => {
    const { supabase, raw, table } = client();
    await loadBrowserConfig(supabase);
    expect(raw.from).toHaveBeenCalledWith("genome_browser_builds");
    expect(table.calls).toContainEqual(["select", BROWSABLE_SELECT]);
    expect(BROWSABLE_SELECT).toContain("genome_reference_versions!inner(");
    expect(BROWSABLE_SELECT).toContain("genome_references!inner(");
    expect(table.calls).toContainEqual(["eq", "status", "ready"]);
    expect(table.calls).toContainEqual(["eq", "genome_reference_versions.status", "ready"]);
  });

  it("is the outside assemblies when nothing is browsable", async () => {
    const { supabase, raw } = client();
    expect(await loadBrowserConfig(supabase)).toEqual({
      config: browserConfig(),
      bloomGenomes: [],
      bloomBuilds: {},
      error: null,
    });
    expect(raw.storage.from).not.toHaveBeenCalled();
  });

  it("adds each browsable version by genome then numeric version, signing nothing", async () => {
    const { supabase, raw } = client({
      rows: {
        data: [buildRow(3, "zm.b73", 1), buildRow(5, "at.col0", 10), buildRow(4, "at.col0", 2)],
        error: null,
      },
    });
    const loaded = await loadBrowserConfig(supabase);

    expect(loaded.error).toBeNull();
    expect(loaded.bloomGenomes).toEqual(["at.col0.v2", "at.col0.v10", "zm.b73.v1"]);
    expect(loaded.bloomBuilds).toEqual({ "at.col0.v2": 4, "at.col0.v10": 5, "zm.b73.v1": 3 });
    expect(raw.storage.from).not.toHaveBeenCalled();
    const assembly = loaded.config.assemblies.find((a) => a.name === "at.col0.v2") as unknown as {
      displayName: string;
      sequence: { adapter: { fastaLocation: { uri: string } } };
    };
    // Species casing normalised as on the Species page.
    expect(assembly.displayName).toBe("at.col0 v2 — TAIR10 (Arabidopsis thaliana)");
    expect(assembly.sequence.adapter.fastaLocation.uri).toBe("/app/jbrowse/files/4/fasta");
    expect(loaded.config.assemblies).toHaveLength(browserConfig().assemblies.length + 3);
  });

  it("leaves the species out of the label when the row has neither part", async () => {
    const { supabase } = client({
      rows: { data: [buildRow(4, "at.col0", 2, { genus: null, species: null })], error: null },
    });
    const loaded = await loadBrowserConfig(supabase);
    const assembly = loaded.config.assemblies.at(-1) as unknown as { displayName: string };
    expect(assembly.displayName).toBe("at.col0 v2 — TAIR10");
  });

  it("keeps the outside assemblies with a short reason, and logs the detail, when the read fails", async () => {
    const { supabase } = client({ rows: { data: null, error: { message: "permission denied for table x" } } });
    const loaded = await loadBrowserConfig(supabase);
    expect(loaded).toEqual({
      config: browserConfig(),
      bloomGenomes: [],
      bloomBuilds: {},
      error: "Bloom's own genomes could not be read.",
    });
    expect(console.error).toHaveBeenCalledWith(
      "[genome browser]",
      new Error("reading browser builds: permission denied for table x"),
    );
  });
});

describe("whyNotOffered", () => {
  it("looks the version up by genome name and number", async () => {
    const { supabase, raw, table } = client({
      rows: { data: { status: "ready", genome_browser_builds: [] }, error: null },
    });
    await expect(whyNotOffered(supabase, "at.col0.v2")).resolves.toBe("not built");
    expect(raw.from).toHaveBeenCalledWith("genome_reference_versions");
    expect(table.calls).toContainEqual(["select", LINKED_VERSION_SELECT]);
    expect(LINKED_VERSION_SELECT).toContain("genome_references!inner(name)");
    expect(table.calls).toContainEqual(["eq", "genome_references.name", "at.col0"]);
    expect(table.calls).toContainEqual(["eq", "version", 2]);
  });

  it("is unknown for a link that names no version, without a lookup", async () => {
    const { supabase, raw } = client();
    await expect(whyNotOffered(supabase, "at.col0")).resolves.toBe("unknown");
    expect(raw.from).not.toHaveBeenCalled();
  });

  it.each([
    [null, "unknown"],
    [{ status: "abandoned", genome_browser_builds: [] }, "abandoned"],
    [{ status: "withdrawn", genome_browser_builds: [{ status: "ready" }] }, "withdrawn"],
    [{ status: "ready", genome_browser_builds: [{ status: "withdrawn" }] }, "files withdrawn"],
    [{ status: "ready", genome_browser_builds: [{ status: "ready" }] }, null],
  ])("reads %j as %j", async (data, reason) => {
    const { supabase } = client({ rows: { data, error: null } });
    await expect(whyNotOffered(supabase, "at.col0.v2")).resolves.toBe(reason);
  });

  it("throws when the lookup fails", async () => {
    const { supabase } = client({ rows: { data: null, error: { message: "boom" } } });
    await expect(whyNotOffered(supabase, "at.col0.v2")).rejects.toThrow("reading genome version at.col0.v2: boom");
  });
});

describe("signBuildFile", () => {
  const ORIGINAL = process.env.NEXT_PUBLIC_SUPABASE_URL;
  beforeEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom.example/api";
  });
  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    else process.env.NEXT_PUBLIC_SUPABASE_URL = ORIGINAL;
  });

  const ready = { data: { folder: "at.col0/v2/browser/b1", genome_reference_versions: { status: "ready" } }, error: null };

  it("signs the role's file in the build's folder, for the TTL, on the public host", async () => {
    const { supabase, raw, table, createSignedUrl } = client({ rows: ready });
    await expect(signBuildFile(supabase, 4, "fasta_gzi")).resolves.toEqual({
      status: "signed",
      url: "https://bloom.example/api/storage/v1/object/sign/x?token=t",
    });
    expect(raw.from).toHaveBeenCalledWith("genome_browser_builds");
    expect(table.calls).toContainEqual(["select", SIGNABLE_BUILD_SELECT]);
    expect(SIGNABLE_BUILD_SELECT).toBe("folder, genome_reference_versions!inner(status)");
    expect(table.calls).toContainEqual(["eq", "id", 4]);
    expect(table.calls).toContainEqual(["eq", "status", "ready"]);
    expect(table.calls).toContainEqual(["eq", "genome_reference_versions.status", "ready"]);
    expect(raw.storage.from).toHaveBeenCalledWith("genome-references");
    expect(createSignedUrl).toHaveBeenCalledWith("at.col0/v2/browser/b1/genome.fa.gz.gzi", SIGNED_URL_TTL_S);
  });

  it("lets the browser reuse a redirect only while its signature still works", () => {
    expect(REDIRECT_MAX_AGE_S).toBeLessThan(SIGNED_URL_TTL_S);
  });

  it("is not found for an unknown role, without a lookup", async () => {
    const { supabase, raw } = client({ rows: ready });
    await expect(signBuildFile(supabase, 4, "genes.gtf")).resolves.toEqual({ status: "not found" });
    expect(raw.from).not.toHaveBeenCalled();
  });

  it("is not found when the build is not a ready build of a ready version", async () => {
    const { supabase, createSignedUrl } = client({ rows: { data: null, error: null } });
    await expect(signBuildFile(supabase, 4, "fasta")).resolves.toEqual({ status: "not found" });
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it.each([
    ["the build read fails", { rows: { data: null, error: { message: "boom" } } }, "reading build 4: boom"],
    ["the role names fail", { rows: ready, roles: { data: null, error: { message: "denied" } } }, "reading file names: denied"],
    ["the role has no file name", { rows: ready, roles: { data: [], error: null } }, "no file name for role fasta"],
    ["signing fails", { rows: ready, signed: { data: null, error: { message: "Object not found" } } }, "signing at.col0/v2/browser/b1/genome.fa.gz: Object not found"],
    ["storage returns no URL", { rows: ready, signed: { data: { signedUrl: null }, error: null } }, "no signed URL for at.col0/v2/browser/b1/genome.fa.gz"],
  ])("fails with a reason when %s", async (_case, setup, reason) => {
    const { supabase } = client(setup as never);
    await expect(signBuildFile(supabase, 4, "fasta")).resolves.toEqual({ status: "failed", reason });
  });
});

describe("geneLinkRedirect", () => {
  const loaded = { bloomBuilds: { "at.col0.v2": 4 } };
  const gene = { gene_id: "AT1G01010", gene_name: "NAC001", contig: "Chr1", gene_start: 3631, gene_end: 5899 };

  it("sends a gene link to the gene's span in that build", async () => {
    const { supabase, table } = client({ rows: { data: [gene], error: null } });
    await expect(geneLinkRedirect(supabase, loaded, "at.col0.v2", "AT1G01010")).resolves.toBe(
      "/app/jbrowse?genome=at.col0.v2&loc=Chr1%3A3631..5899",
    );
    expect(table.calls).toContainEqual(["eq", "browser_build_id", 4]);
  });

  it("opens the whole genome, naming the gene, when the build does not have it", async () => {
    const { supabase } = client({ rows: { data: [], error: null } });
    await expect(geneLinkRedirect(supabase, loaded, "at.col0.v2", "NOPE1")).resolves.toBe(
      "/app/jbrowse?genome=at.col0.v2&missing=NOPE1",
    );
  });

  it.each([
    ["a region", "at.col0.v2", "Chr1:1..9"],
    ["no location", "at.col0.v2", ""],
    ["an outside assembly, which has its own gene index", "Athaliana.Col-0.HPIv02", "AT1G01010"],
  ])("needs no lookup for %s", async (_case, link, loc) => {
    const { supabase, raw } = client();
    await expect(geneLinkRedirect(supabase, loaded, link, loc)).resolves.toBeNull();
    expect(raw.from).not.toHaveBeenCalled();
  });
});

describe("placePicks", () => {
  const loaded = { bloomBuilds: { "at.col0.v2": 4 } };
  const row = (gene_id: string, gene_start: number, gene_end: number, contig = "Chr1") => ({
    gene_id,
    gene_name: null,
    contig,
    gene_start,
    gene_end,
  });

  it("needs nothing without picks, or for a genome that is not offered", async () => {
    const { supabase, raw } = client();
    await expect(placePicks(supabase, loaded, "at.col0.v2", "")).resolves.toBeNull();
    await expect(placePicks(supabase, loaded, "other.v1", "AT1G01010")).resolves.toBeNull();
    expect(raw.from).not.toHaveBeenCalled();
  });

  it("opens the picks' regions with each gene highlighted, from that build's genes", async () => {
    const { supabase, table } = client({
      rows: { data: [row("A", 100_001, 102_000), row("B", 300_001, 301_000), row("C", 10_001, 11_000, "Chr3")], error: null },
    });
    const plan = await placePicks(supabase, loaded, "at.col0.v2", "A,B,C");
    expect(plan?.loc).toBe("Chr1:79901..321100 Chr3:9901..11100");
    expect(plan?.highlight).toHaveLength(3);
    expect(plan?.notes).toEqual([]);
    expect(table.calls).toContainEqual(["eq", "browser_build_id", 4]);
  });

  it("names the picks the build does not hold and opens the rest", async () => {
    const { supabase } = client({ rows: { data: [row("A", 1_001, 2_000)], error: null } });
    const plan = await placePicks(supabase, loaded, "at.col0.v2", "A,NOPE1,NOPE2");
    expect(plan?.loc).toBe("Chr1:901..2100");
    expect(plan?.notes).toEqual(["NOPE1, NOPE2 are not among at.col0.v2's genes, so they were left out."]);
  });

  it("shows the whole genome when no pick is held", async () => {
    const { supabase } = client({ rows: { data: [], error: null } });
    const plan = await placePicks(supabase, loaded, "at.col0.v2", "NOPE1");
    expect(plan).toEqual({ loc: null, highlight: [], notes: ["NOPE1 is not among at.col0.v2's genes, so it was left out."] });
  });

  it("refuses more genes than the limit without reading any", async () => {
    const { supabase, raw } = client();
    const genes = Array.from({ length: 21 }, (_, i) => `G${i}`).join(",");
    await expect(placePicks(supabase, loaded, "at.col0.v2", genes)).resolves.toEqual({
      loc: null,
      highlight: [],
      notes: ["Open at most 20 genes at once; this link has 21."],
    });
    expect(raw.from).not.toHaveBeenCalled();
  });
});

