import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BROWSER_URL_TTL, loadBrowserConfig, whyNotOffered } from "./bloom-genomes-server";
import { browserConfig } from "./config";

const ROLES = [
  { role: "fasta", basename: "genome.fa.gz" },
  { role: "fasta_fai", basename: "genome.fa.gz.fai" },
  { role: "fasta_gzi", basename: "genome.fa.gz.gzi" },
  { role: "gff3", basename: "genes.gff3.gz" },
  { role: "gff3_tbi", basename: "genes.gff3.gz.tbi" },
];

const buildRow = (name: string, version: number) => ({
  folder: `${name}/v${version}/browser/b1`,
  genome_reference_versions: {
    version,
    genome_references: { name, species: { genus: "Arabidopsis", species: "thaliana" } },
  },
});

/** A query builder that records its filters and resolves to `result`. */
function query(result: { data: unknown; error: unknown }) {
  const calls: unknown[][] = [];
  const builder: Record<string, unknown> = {
    calls,
    then: (resolve: (r: unknown) => unknown) => Promise.resolve(result).then(resolve),
  };
  for (const method of ["select", "eq", "maybeSingle"]) {
    builder[method] = (...args: unknown[]) => {
      calls.push([method, ...args]);
      return builder;
    };
  }
  return builder as typeof builder & { calls: unknown[][] };
}

type Signed = { path: string; signedUrl: string | null; error: string | null };
type Sign = (paths: string[]) => { data: Signed[]; error: unknown };

function client({
  rows = { data: [] as unknown, error: null as unknown },
  roles = { data: ROLES as unknown, error: null as unknown },
  sign = ((paths: string[]) => ({
    data: paths.map((path) => ({ path, signedUrl: `http://kong:8000/storage/v1/object/sign/${path}?token=t`, error: null })),
    error: null as unknown,
  })) as Sign,
} = {}) {
  const table = query(rows);
  const createSignedUrls = vi.fn(async (paths: string[], _ttl: number) => sign(paths));
  const supabase = {
    from: vi.fn(() => table),
    rpc: vi.fn(async () => roles),
    storage: { from: vi.fn(() => ({ createSignedUrls })) },
  };
  return { supabase: supabase as never, table, createSignedUrls, raw: supabase };
}

describe("loadBrowserConfig", () => {
  const ORIGINAL = process.env.NEXT_PUBLIC_SUPABASE_URL;
  beforeEach(() => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://bloom.example/api";
  });
  afterEach(() => {
    if (ORIGINAL === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    else process.env.NEXT_PUBLIC_SUPABASE_URL = ORIGINAL;
  });

  it("asks only for ready builds of ready versions", async () => {
    const { supabase, table } = client();
    await loadBrowserConfig(supabase);
    expect(table.calls).toContainEqual(["eq", "status", "ready"]);
    expect(table.calls).toContainEqual(["eq", "genome_reference_versions.status", "ready"]);
  });

  it("is the outside assemblies, with no URLs to refresh, when nothing is browsable", async () => {
    const { supabase, createSignedUrls } = client();
    const loaded = await loadBrowserConfig(supabase);
    expect(loaded).toEqual({ config: browserConfig(), bloomGenomes: [], validForMs: null, error: null });
    expect(createSignedUrls).not.toHaveBeenCalled();
  });

  it("adds each browsable version, its five files signed in one call", async () => {
    const { supabase, raw, createSignedUrls } = client({
      rows: { data: [buildRow("zm.b73", 1), buildRow("at.col0", 2)], error: null },
    });
    const loaded = await loadBrowserConfig(supabase);

    expect(loaded.error).toBeNull();
    expect(loaded.bloomGenomes).toEqual(["at.col0.v2", "zm.b73.v1"]);
    expect(loaded.validForMs).toBe(BROWSER_URL_TTL * 1000);
    expect(raw.storage.from).toHaveBeenCalledWith("genome-references");
    expect(createSignedUrls).toHaveBeenCalledTimes(1);
    expect(createSignedUrls.mock.calls[0][0]).toEqual([
      "at.col0/v2/browser/b1/genome.fa.gz",
      "at.col0/v2/browser/b1/genome.fa.gz.fai",
      "at.col0/v2/browser/b1/genome.fa.gz.gzi",
      "at.col0/v2/browser/b1/genes.gff3.gz",
      "at.col0/v2/browser/b1/genes.gff3.gz.tbi",
      "zm.b73/v1/browser/b1/genome.fa.gz",
      "zm.b73/v1/browser/b1/genome.fa.gz.fai",
      "zm.b73/v1/browser/b1/genome.fa.gz.gzi",
      "zm.b73/v1/browser/b1/genes.gff3.gz",
      "zm.b73/v1/browser/b1/genes.gff3.gz.tbi",
    ]);
    expect(createSignedUrls.mock.calls[0][1]).toBe(BROWSER_URL_TTL);

    const assembly = loaded.config.assemblies.find((a) => a.name === "at.col0.v2") as unknown as {
      displayName: string;
      sequence: { adapter: { fastaLocation: { uri: string } } };
    };
    expect(assembly.displayName).toBe("at.col0 v2 (Arabidopsis thaliana)");
    // Signed against the internal gateway, handed to the browser on the public host.
    expect(assembly.sequence.adapter.fastaLocation.uri).toBe(
      "https://bloom.example/api/storage/v1/object/sign/at.col0/v2/browser/b1/genome.fa.gz?token=t",
    );
    expect(loaded.config.assemblies).toHaveLength(browserConfig().assemblies.length + 2);
  });

  it("keeps the outside assemblies and says why when a file cannot be signed", async () => {
    const { supabase } = client({
      rows: { data: [buildRow("at.col0", 2)], error: null },
      sign: (paths) => ({
        data: paths.map((path) => ({
          path,
          signedUrl: path.endsWith(".tbi") ? null : `https://x/${path}`,
          error: path.endsWith(".tbi") ? "Object not found" : null,
        })),
        error: null,
      }),
    });
    const loaded = await loadBrowserConfig(supabase);
    expect(loaded.config).toEqual(browserConfig());
    expect(loaded.bloomGenomes).toEqual([]);
    expect(loaded.error).toBe("signing at.col0/v2/browser/b1/genes.gff3.gz.tbi: Object not found");
  });

  it("says why when the builds cannot be read", async () => {
    const { supabase } = client({ rows: { data: null, error: { message: "permission denied" } } });
    const loaded = await loadBrowserConfig(supabase);
    expect(loaded.config).toEqual(browserConfig());
    expect(loaded.error).toBe("reading browser builds: permission denied");
  });

  it("refuses when the database names a role differently", async () => {
    const { supabase } = client({
      rows: { data: [buildRow("at.col0", 2)], error: null },
      roles: { data: ROLES.filter((r) => r.role !== "fasta_gzi"), error: null },
    });
    expect((await loadBrowserConfig(supabase)).error).toBe("no file name for browser roles: fasta_gzi");
  });
});

describe("whyNotOffered", () => {
  it("looks the version up by genome name and number", async () => {
    const { supabase, table } = client({
      rows: { data: { status: "ready", genome_browser_builds: [] }, error: null },
    });
    await expect(whyNotOffered(supabase, "at.col0.v2")).resolves.toBe("not built");
    expect(table.calls).toContainEqual(["eq", "genome_references.name", "at.col0"]);
    expect(table.calls).toContainEqual(["eq", "version", 2]);
  });

  it("is unknown for a link that names no version, without a lookup", async () => {
    const { supabase, raw } = client();
    await expect(whyNotOffered(supabase, "at.col0")).resolves.toBe("unknown");
    expect(raw.from).not.toHaveBeenCalled();
  });

  it("is unknown when no such version exists", async () => {
    const { supabase } = client({ rows: { data: null, error: null } });
    await expect(whyNotOffered(supabase, "at.col0.v9")).resolves.toBe("unknown");
  });

  it("is withdrawn when the version's build was withdrawn", async () => {
    const { supabase } = client({
      rows: { data: { status: "ready", genome_browser_builds: [{ status: "withdrawn" }] }, error: null },
    });
    await expect(whyNotOffered(supabase, "at.col0.v2")).resolves.toBe("withdrawn");
  });
});
