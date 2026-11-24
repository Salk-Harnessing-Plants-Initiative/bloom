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

import { fetchGeneCounts, NoStoredExpressionError } from "./scrna-client";

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
        info: async () => ({ data: { size: response.data?.size ?? 0 }, error: null }),
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
    // Its own kind of error, so a reader can tell a missing gene from a failed read.
    await expect(fetchGeneCounts(1, "NEVER_LOADED", 10)).rejects.toBeInstanceOf(
      NoStoredExpressionError,
    );
    storedPath = "counts/d/g.json";
  });

  it("reads through the shared reader, with the browser's client", async () => {
    // Parsing and refusals are tested with readGeneCounts (gene-counts.test.ts).
    response = stored({ "0": 1.5, "3": -2.25 });
    expect(Array.from(await fetchGeneCounts(1, "g", 5))).toEqual([1.5, 0, 0, -2.25, 0]);
  });
});
