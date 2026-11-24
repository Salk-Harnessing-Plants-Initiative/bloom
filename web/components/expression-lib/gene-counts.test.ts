/** Reading one gene's count file with a given Supabase client, so the server can read it too, and
 * refusing a file that can't be trusted with an error naming the gene. */

import { describe, expect, it } from "vitest";

import { MAX_COUNT_FILE_BYTES, NoStoredExpressionError, readGeneCounts } from "./gene-counts";

function client(body: string | Blob | null, path: string | null = "counts/d_1_/G1.json") {
  return {
    from: () => ({
      select: () => ({
        eq: () => ({
          eq: () => ({
            maybeSingle: async () => ({ data: path === null ? null : { counts_object_path: path }, error: null }),
          }),
        }),
      }),
    }),
    storage: {
      from: () => ({
        download: async () => ({ data: typeof body === "string" ? new Blob([body]) : body, error: null }),
      }),
    },
  } as never;
}

const read = (body: string | Blob | null, cells = 4) => readGeneCounts(client(body), 7, "G1", cells);

describe("readGeneCounts", () => {
  it("reads both file shapes with the client it is given", async () => {
    expect([...(await read('{"1": 1.5, "3": 2}'))]).toEqual([0, 1.5, 0, 2]);
    expect([...(await read('[{"1": 1.5}, {"3": 2}]'))]).toEqual([0, 1.5, 0, 2]);
  });

  it("says when the dataset stores no expression for the gene", async () => {
    await expect(readGeneCounts(client("{}", null), 7, "G1", 4)).rejects.toBeInstanceOf(NoStoredExpressionError);
  });

  it.each([
    ['{"1": "high"}', "G1's count file holds a value that is not a number, for cell 1."],
    ['{"1": null}', "G1's count file holds a value that is not a number, for cell 1."],
    ['{"1": 1e999}', "G1's count file holds a value that is not a number, for cell 1."],
  ])("refuses the value in %s", async (body, message) => {
    await expect(read(body)).rejects.toThrow(message);
  });

  it("refuses a cell at or past the dataset's cell count", async () => {
    await expect(read('{"4": 1}')).rejects.toThrow("G1 names cell 4, but the dataset holds 4 cells");
  });

  it("refuses a file that is not valid JSON", async () => {
    await expect(read('{"1": 1.5')).rejects.toThrow("G1's count file is not valid JSON.");
  });

  it("refuses a file over 16 MB without reading it", async () => {
    expect(MAX_COUNT_FILE_BYTES).toBe(16 * 1024 * 1024);
    const big = new Blob(["x"]);
    Object.defineProperty(big, "size", { value: MAX_COUNT_FILE_BYTES + 1 });
    await expect(read(big)).rejects.toThrow("G1's count file is over 16 MB.");
  });

  it("refuses a file that is neither shape", async () => {
    await expect(read("42")).rejects.toThrow("G1's count file is not a map of cells to values.");
  });
});
