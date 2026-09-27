/**
 * Each read hands its cancel signal to the query itself, so cancelling stops
 * the request on the wire rather than only discarding its answer.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJointArrays, fetchLabelCodes, fetchPoint } from "./joint-client";

const supabase = vi.hoisted(() => ({ rpc: vi.fn(), from: vi.fn() }));
vi.mock("../../lib/supabase/client", () => ({ createClientSupabaseClient: () => supabase }));

type Query = Record<string, ReturnType<typeof vi.fn>> & PromiseLike<unknown>;

/** A chainable, awaitable stand-in for a PostgREST query. */
function query(result: { data: unknown; error: unknown }): Query {
  const q = {} as Query;
  for (const method of ["select", "eq", "order", "range", "abortSignal"]) {
    q[method] = vi.fn(() => q);
  }
  (q as { then: PromiseLike<unknown>["then"] }).then = (resolve, reject) =>
    Promise.resolve(result).then(resolve, reject);
  return q;
}

beforeEach(() => {
  supabase.rpc.mockReset();
  supabase.from.mockReset();
});

describe("cancel signals reach the query", () => {
  it("fetchJointArrays", async () => {
    const q = query({ data: [{ x: [1], y: [2], member_ordinal: [0] }], error: null });
    supabase.rpc.mockReturnValue(q);
    const { signal } = new AbortController();

    expect(await fetchJointArrays(7, signal)).toEqual({ x: [1], y: [2], memberOrdinals: [0] });
    expect(q.abortSignal).toHaveBeenCalledWith(signal);
  });

  it("fetchLabelCodes", async () => {
    const q = query({ data: [{ levels: ["a"], codes: [0] }], error: null });
    supabase.rpc.mockReturnValue(q);
    const { signal } = new AbortController();

    expect(await fetchLabelCodes(7, "genotype", signal)).toEqual({ levels: ["a"], codes: [0] });
    expect(q.abortSignal).toHaveBeenCalledWith(signal);
  });

  it("fetchPoint", async () => {
    const q = query({ data: [{ barcode: "AAAC-1", dataset_id: 3, cell_id: 9 }], error: null });
    supabase.from.mockReturnValue(q);
    const { signal } = new AbortController();

    expect(await fetchPoint(7, 4, signal)).toEqual({ barcode: "AAAC-1", datasetId: 3, cellId: 9 });
    expect(q.abortSignal).toHaveBeenCalledWith(signal);
  });
});
