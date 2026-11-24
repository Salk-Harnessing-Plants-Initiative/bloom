/** Reading one gene's count file with a given Supabase client, so the server can read it too, and
 * refusing a file that can't be trusted, as bloommcp's expression_counts.py refuses it. */

import { describe, expect, it } from "vitest";

import { MAX_COUNT_FILE_BYTES, NoStoredExpressionError, readGeneCounts } from "./gene-counts";

interface Stored {
  body?: string | null;
  path?: string | null;
  size?: number;
  rowError?: string;
  infoError?: { message: string; statusCode?: string };
  downloadError?: { message: string; statusCode?: string };
}

function client(stored: Stored) {
  const calls: string[] = [];
  const path = stored.path === undefined ? "counts/d_1_/G1.json" : stored.path;
  const body = stored.body ?? "{}";
  const supabase = {
    from: () => ({
      select: () => ({
        eq: () => ({
          eq: () => ({
            maybeSingle: async () =>
              stored.rowError
                ? { data: null, error: { message: stored.rowError } }
                : { data: path === null ? null : { counts_object_path: path }, error: null },
          }),
        }),
      }),
    }),
    storage: {
      from: (bucket: string) => ({
        info: async (p: string) => {
          calls.push(`info ${bucket}/${p}`);
          return stored.infoError
            ? { data: null, error: stored.infoError }
            : { data: { size: stored.size ?? new Blob([body]).size }, error: null };
        },
        download: async (p: string) => {
          calls.push(`download ${bucket}/${p}`);
          return stored.downloadError ? { data: null, error: stored.downloadError } : { data: new Blob([body]), error: null };
        },
      }),
    },
  };
  return { supabase: supabase as never, calls };
}

const read = (stored: Stored, cells = 4) => readGeneCounts(client(stored).supabase, 7, "G1", cells);
const values = async (body: string, cells = 4) => [...(await read({ body }, cells))];

describe("readGeneCounts", () => {
  it("reads the file its scrna_counts row names, checking its size first", async () => {
    const { supabase, calls } = client({ body: '{"1": 1.5}' });
    await readGeneCounts(supabase, 7, "G1", 4);
    expect(calls).toEqual(["info scrna/counts/d_1_/G1.json", "download scrna/counts/d_1_/G1.json"]);
  });

  it("puts each value at its 0-based cell and zero elsewhere, in both file shapes", async () => {
    expect(await values('{"0": 1.5, "3": -2.25}')).toEqual([1.5, 0, 0, -2.25]);
    expect(await values('[{"0": 1.5}, {"3": -2.25}]')).toEqual([1.5, 0, 0, -2.25]);
    expect(await values("{}")).toEqual([0, 0, 0, 0]);
    expect(await values("[]")).toEqual([0, 0, 0, 0]);
  });

  it("merges the older list shape before checking it, later entries winning, as bloommcp does", async () => {
    expect(await values('[{"1": "x"}, {"1": 2}]')).toEqual([0, 2, 0, 0]);
  });

  it("reads leading zeros as the same cell, as bloommcp does", async () => {
    expect(await values('{"01": 2}')).toEqual([0, 2, 0, 0]);
  });

  it("says when the dataset stores no expression for the gene", async () => {
    await expect(read({ path: null })).rejects.toBeInstanceOf(NoStoredExpressionError);
    await expect(read({ path: null })).rejects.toThrow("G1 has no stored expression in this dataset.");
  });

  it.each(["", "-1", "1.0", " 1", "+1", "1e0", "0x1", "-0", "4", "99999999999999999999"])(
    "refuses the cell key %j, which bloommcp refuses too",
    async (key) => {
      await expect(read({ body: JSON.stringify({ [key]: 1 }) })).rejects.toThrow(
        `Its count file names cell ${JSON.stringify(key)}, but the dataset holds 4 cells.`,
      );
    },
  );

  it("shows at most 20 characters of a damaged key", async () => {
    const key = "x".repeat(50);
    await expect(read({ body: JSON.stringify({ [key]: 1 }) })).rejects.toThrow(
      `Its count file names cell "${"x".repeat(20)}…", but the dataset holds 4 cells.`,
    );
  });

  it.each([
    ['{"1": "1.62"}', "1"],
    ['{"1": null}', "1"],
    ['{"1": true}', "1"],
    ['{"1": 1e999}', "1"],
    ['{"1": 1e39}', "1"],
  ])("refuses %s, whose value is not a number a cell can hold", async (body, cell) => {
    await expect(read({ body })).rejects.toThrow(`Its count file's value for cell ${cell} is not a finite number.`);
  });

  it.each(["42", "null", '"text"', "[1, 2]", "[[1]]"])("refuses %s, which is neither file shape", async (body) => {
    await expect(read({ body })).rejects.toThrow("Its count file is not a map of cells to values.");
  });

  it.each(['{"1": 1.5', ""])("refuses %j, which is not valid JSON", async (body) => {
    await expect(read({ body })).rejects.toThrow("Its count file is not valid JSON.");
  });

  it("refuses a file over 16 MB before downloading it, and reads one of exactly 16 MB", async () => {
    expect(MAX_COUNT_FILE_BYTES).toBe(16 * 1024 * 1024);
    const big = client({ body: '{"1": 1}', size: MAX_COUNT_FILE_BYTES + 1 });
    await expect(readGeneCounts(big.supabase, 7, "G1", 4)).rejects.toThrow(
      "Its count file is 16 MB, over the 16 MB limit.",
    );
    expect(big.calls).toEqual(["info scrna/counts/d_1_/G1.json"]);
    await expect(read({ body: '{"1": 1}', size: MAX_COUNT_FILE_BYTES })).resolves.toHaveLength(4);
  });

  it.each([
    ["/counts/G1.json", "Its stored file path has a character that could leave the bucket, so it was not read."],
    ["counts/../other/G1.json", "Its stored file path has an empty, '.' or '..' folder, so it was not read."],
    ["counts//G1.json", "Its stored file path has an empty, '.' or '..' folder, so it was not read."],
    ["counts/G1.json?x", "Its stored file path has a character that could leave the bucket, so it was not read."],
    ["counts/%2e%2e/G1.json", "Its stored file path has a character that could leave the bucket, so it was not read."],
    [" counts/G1.json", "Its stored file path is empty or has spaces at an end, so it was not read."],
    ["counts/G\u0001.json", "Its stored file path has a control character, so it was not read."],
  ])("refuses the stored path %j without asking storage, as bloommcp does", async (path, message) => {
    const { supabase, calls } = client({ path });
    await expect(readGeneCounts(supabase, 7, "G1", 4)).rejects.toThrow(message);
    expect(calls).toEqual([]);
  });

  it("says a missing file is missing, and other storage failures could not be read", async () => {
    await expect(read({ infoError: { message: "Object not found", statusCode: "404" } })).rejects.toThrow(
      "Its count file is missing.",
    );
    await expect(read({ downloadError: { message: "Object not found", statusCode: "404" } })).rejects.toThrow(
      "Its count file is missing.",
    );
    await expect(read({ downloadError: { message: "upstream timeout" } })).rejects.toThrow(
      "Its count file could not be read; try again.",
    );
  });

  it("says when the gene's count record could not be read", async () => {
    await expect(read({ rowError: "connection reset" })).rejects.toThrow(
      "Its count record could not be read; try again.",
    );
  });
});
