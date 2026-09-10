/**
 * The per-gene object is the one thing the explorer reads that carries no cell
 * identifiers — the index is the identity, and everything is paired to the
 * cells by position. So what is pinned here is that it is read through the
 * storage client, because the bucket is private and a bare URL cannot see it;
 * that a sparse object expands to the right dense array, since a shift by one
 * puts every cell's value on its neighbour; and that an index outside the
 * dataset is refused rather than dropped, because it means the object was
 * written against a different set of cells.
 */

import { describe, expect, it, vi } from "vitest";

import { fetchGeneCounts } from "./scrna-client";

const downloaded: string[] = [];
let response: { data: Blob | null; error: { message: string } | null };
let storedPath: string | null = "counts/pennycress_data2_1_/g.json";

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
    from: () => ({
      select: () => ({
        eq: () => ({
          eq: () => ({
            maybeSingle: async () => ({
              data: storedPath === null
                ? null
                : { counts_object_path: storedPath },
              error: null,
            }),
          }),
        }),
      }),
    }),
    storage: {
      from: (bucket: string) => ({
        download: async (path: string) => {
          downloaded.push(`${bucket}/${path}`);
          return response;
        },
      }),
    },
  }),
}));

const stored = (o: Record<string, number>) =>
  ({ data: new Blob([JSON.stringify(o)]), error: null });

describe("fetchGeneCounts", () => {
  it("fetches the path stored in scrna_counts, not one built from the names", async () => {
    // Production paths are not derivable: the dataset named
    // "pennycress_data2.json" stores its genes under "pennycress_data2_1_".
    storedPath = "counts/pennycress_data2_1_/Tarv.1014.HPI3.1.g000010.json";
    response = stored({ "0": 1.5 });
    downloaded.length = 0;

    await fetchGeneCounts(1, "Tarv.1014.HPI3.1.g000010", 4);

    expect(downloaded).toEqual([
      "scrna/counts/pennycress_data2_1_/Tarv.1014.HPI3.1.g000010.json",
    ]);
  });

  it("says so when the gene has no object, which most datasets have many of", async () => {
    // Every dataset in the platform has genes registered whose object was
    // never written -- one has 49,178 genes and no objects at all.
    storedPath = null;
    await expect(fetchGeneCounts(1, "NEVER_LOADED", 10)).rejects.toThrow(
      /no stored expression/,
    );
    storedPath = "counts/d/g.json";
  });

  it("puts each value at its own cell, and zero everywhere else", async () => {
    response = stored({ "0": 1.5, "3": -2.25 });
    const got = await fetchGeneCounts(1, "g", 5);
    expect(Array.from(got)).toEqual([1.5, 0, 0, -2.25, 0]);
  });

  it("is 0-based, so index 0 is the first cell", async () => {
    response = stored({ "0": 9 });
    expect((await fetchGeneCounts(1, "g", 3))[0]).toBe(9);
  });

  it("fills the last cell when the last index is named", async () => {
    response = stored({ "8682": 7 });
    const got = await fetchGeneCounts(1, "g", 8683);
    expect(got[8682]).toBe(7);
    expect(got.length).toBe(8683);
  });

  it("returns one value per cell even when the gene is expressed nowhere", async () => {
    response = stored({});
    const got = await fetchGeneCounts(1, "g", 8683);
    expect(got.length).toBe(8683);
    expect(got.every((v) => v === 0)).toBe(true);
  });

  it("refuses an index past the end, rather than dropping it", async () => {
    response = stored({ "9000": 1 });
    await expect(fetchGeneCounts(1, "g", 8683)).rejects.toThrow(
      /names cell 9000.*holds 8683/,
    );
  });

  it("refuses a negative index", async () => {
    response = stored({ "-1": 1 });
    await expect(fetchGeneCounts(1, "g", 10)).rejects.toThrow(/names cell -1/);
  });

  it("says which gene failed rather than surfacing a bare storage error", async () => {
    response = { data: null, error: { message: "Object not found" } };
    await expect(fetchGeneCounts(1, "MISSING", 10)).rejects.toThrow(
      /MISSING.*Object not found/,
    );
  });

  it("fails when storage returns neither data nor an error", async () => {
    response = { data: null, error: null };
    await expect(fetchGeneCounts(1, "g", 10)).rejects.toThrow(/no data/);
  });
});
