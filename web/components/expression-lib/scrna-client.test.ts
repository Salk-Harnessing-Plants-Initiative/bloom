/**
 * The per-gene vector is the one thing the explorer reads that carries no cell
 * identifiers — it is paired with the cells purely by position. So two things
 * are pinned here: that it is read through the storage client, because the
 * bucket is private and a bare URL fetch cannot see it; and that the bytes are
 * decoded as float32, because reading them at any other width silently shifts
 * every cell's value.
 */

import { describe, expect, it, vi } from "vitest";

const downloaded: string[] = [];
let response: { data: Blob | null; error: { message: string } | null };

vi.mock("@/lib/supabase/client", () => ({
  createClientSupabaseClient: () => ({
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

const { fetchGeneBin } = await import("./scrna-client");

describe("fetchGeneBin", () => {
  it("reads the gene's object from the private bucket and decodes it as float32", async () => {
    const values = new Float32Array([0, 1.5, -2.25, 1e-7]);
    response = { data: new Blob([values.buffer]), error: null };
    downloaded.length = 0;

    const got = await fetchGeneBin("MYB41 transgene", "AT4G28110.Fusion");

    expect(downloaded).toEqual([
      "scrna/counts/MYB41 transgene/AT4G28110.Fusion.bin",
    ]);
    expect(Array.from(got)).toEqual(Array.from(values));
  });

  it("keeps one value per cell, so the array lines up with the cells", async () => {
    const values = new Float32Array(8683).fill(0.5);
    response = { data: new Blob([values.buffer]), error: null };
    expect((await fetchGeneBin("d", "g")).length).toBe(8683);
  });

  it("says which gene failed rather than surfacing a bare storage error", async () => {
    response = { data: null, error: { message: "Object not found" } };
    await expect(fetchGeneBin("d", "MISSING")).rejects.toThrow(
      /MISSING.*Object not found/,
    );
  });

  it("fails when storage returns neither data nor an error", async () => {
    response = { data: null, error: null };
    await expect(fetchGeneBin("d", "g")).rejects.toThrow(/no data/);
  });
});
