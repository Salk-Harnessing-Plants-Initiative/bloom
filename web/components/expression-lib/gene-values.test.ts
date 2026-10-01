import { describe, expect, it, vi } from "vitest";

import { createGeneReader, GENE_READS_AT_ONCE } from "./gene-values";
import { NoStoredExpressionError, type CellArraysRow } from "./scrna-client";

const CELLS = [{}, {}, {}] as CellArraysRow[];

function deps(overrides: Partial<Parameters<typeof createGeneReader>[1]> = {}) {
  return {
    fetchCells: vi.fn(async () => CELLS),
    fetchGeneCounts: vi.fn(async (_d: number, _g: string, n: number) => new Float32Array(n)),
    ...overrides,
  };
}

describe("createGeneReader", () => {
  it("reads the cells once however many genes are read", async () => {
    const d = deps();
    const reader = createGeneReader(1, d);
    await Promise.all(["a", "b", "c"].map(reader.gene));
    expect(d.fetchCells).toHaveBeenCalledTimes(1);
    expect(d.fetchGeneCounts).toHaveBeenCalledWith(1, "a", 3);
  });

  it("reads a gene once, however often it is asked for", async () => {
    const d = deps();
    const reader = createGeneReader(1, d);
    await reader.gene("a");
    await reader.gene("a");
    expect(d.fetchGeneCounts).toHaveBeenCalledTimes(1);
  });

  it(`reads at most ${GENE_READS_AT_ONCE} genes at once`, async () => {
    let running = 0;
    let most = 0;
    const d = deps({
      fetchGeneCounts: vi.fn(async (_d: number, _g: string, n: number) => {
        running++;
        most = Math.max(most, running);
        await new Promise((r) => setTimeout(r, 5));
        running--;
        return new Float32Array(n);
      }),
    });
    const reader = createGeneReader(1, d);
    const reads = await Promise.all(Array.from({ length: 14 }, (_, i) => reader.gene(`g${i}`)));
    expect(most).toBe(GENE_READS_AT_ONCE);
    expect(reads.every((r) => "values" in r)).toBe(true);
  });

  it("reports a gene the dataset stores no expression for, by name", async () => {
    const d = deps({
      fetchGeneCounts: vi.fn(async () => {
        throw new NoStoredExpressionError("NEVER_LOADED");
      }),
    });
    expect(await createGeneReader(1, d).gene("NEVER_LOADED")).toEqual({
      gene: "NEVER_LOADED",
      missing: true,
    });
  });

  it("reports any other failure, and tries again next time", async () => {
    const fetchGeneCounts = vi
      .fn()
      .mockRejectedValueOnce(new Error("storage is down"))
      .mockResolvedValueOnce(new Float32Array(3));
    const reader = createGeneReader(1, deps({ fetchGeneCounts }));
    expect(await reader.gene("a")).toEqual({ gene: "a", error: "storage is down" });
    expect("values" in (await reader.gene("a"))).toBe(true);
  });

  it("keeps each dataset's reads to itself", async () => {
    const d = deps();
    await createGeneReader(1, d).gene("a");
    await createGeneReader(2, d).gene("a");
    expect(d.fetchGeneCounts).toHaveBeenCalledTimes(2);
    expect(d.fetchCells).toHaveBeenCalledTimes(2);
  });
});
