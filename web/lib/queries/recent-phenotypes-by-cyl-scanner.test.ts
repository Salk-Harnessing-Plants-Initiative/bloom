/**
 * Unit tests for getRecentPhenotypesByCylScanner.
 *
 * These tests exercise the helper's grouping + error-handling logic against
 * a mocked Supabase client. The view's SQL is verified manually against a
 * seeded local DB and again by the Phase 5 E2E test that drives the home
 * page from a real browser.
 */

import { describe, it, expect, vi } from "vitest";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/database.types";
import {
  getRecentPhenotypesByCylScanner,
  type CylScanRow,
} from "@/lib/queries/recent-phenotypes-by-cyl-scanner";

// ─── Test helpers ────────────────────────────────────────────────────────────

/**
 * Build a mock Supabase client that returns the given rows (or error) when
 * `from(...).select(...).order(...).order(...)` is called. The query builder
 * is itself thenable so `await chain` resolves to `{ data, error }`.
 */
function makeMockSupabase(
  rows: CylScanRow[] | null,
  error: { message: string } | null = null,
): SupabaseClient<Database> {
  const result = Promise.resolve({ data: rows, error });

  const builder = {
    select: vi.fn(),
    order: vi.fn(),
    then: result.then.bind(result),
    catch: result.catch.bind(result),
  };
  // Make every chain method return the same builder so any chain length works.
  builder.select.mockReturnValue(builder);
  builder.order.mockReturnValue(builder);

  return {
    from: vi.fn().mockReturnValue(builder),
  } as unknown as SupabaseClient<Database>;
}

/**
 * Build a minimal row with sensible defaults; overrides patch individual
 * fields per test.
 */
function makeRow(overrides: Partial<CylScanRow> = {}): CylScanRow {
  return {
    scanner_id: 1,
    scanner_name: "Cyl-01",
    experiment_id: 100,
    experiment_name: "Arabidopsis Clock Mutants",
    species_id: 7,
    species_common_name: "Arabidopsis",
    wave_id: 200,
    wave_number: 3,
    wave_name: null,
    plant_age_days: 14,
    phenotyper_first_name: "Alice",
    phenotyper_last_name: "Smith",
    latest_upload_on_this_scanner_at: "2026-05-28T08:04:00Z",
    rank_on_scanner: 1,
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("getRecentPhenotypesByCylScanner", () => {
  it("returns an empty array when the view returns no rows", async () => {
    const supabase = makeMockSupabase([]);
    const result = await getRecentPhenotypesByCylScanner(supabase);
    expect(result).toEqual([]);
  });

  it("returns an empty array when the view returns null data", async () => {
    const supabase = makeMockSupabase(null);
    const result = await getRecentPhenotypesByCylScanner(supabase);
    expect(result).toEqual([]);
  });

  it("groups a single scanner's rows into one section", async () => {
    const rows = [
      makeRow({
        scanner_id: 1,
        scanner_name: "Cyl-01",
        experiment_id: 100,
        rank_on_scanner: 1,
      }),
      makeRow({
        scanner_id: 1,
        scanner_name: "Cyl-01",
        experiment_id: 101,
        rank_on_scanner: 2,
      }),
    ];

    const result = await getRecentPhenotypesByCylScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      scanner_id: 1,
      scanner_name: "Cyl-01",
    });
    expect(result[0].cards).toHaveLength(2);
    expect(result[0].cards[0].experiment_id).toBe(100);
    expect(result[0].cards[1].experiment_id).toBe(101);
  });

  it("creates a separate section per scanner_id", async () => {
    const rows = [
      makeRow({ scanner_id: 1, scanner_name: "Cyl-01", experiment_id: 100 }),
      makeRow({ scanner_id: 2, scanner_name: "Cyl-02", experiment_id: 200 }),
      makeRow({ scanner_id: 2, scanner_name: "Cyl-02", experiment_id: 201 }),
      makeRow({ scanner_id: 3, scanner_name: "Cyl-03", experiment_id: 300 }),
    ];

    const result = await getRecentPhenotypesByCylScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(3);
    expect(result.map((s) => s.scanner_id)).toEqual([1, 2, 3]);
    expect(result[0].cards).toHaveLength(1);
    expect(result[1].cards).toHaveLength(2);
    expect(result[2].cards).toHaveLength(1);
  });

  it("preserves the row order within each scanner section", async () => {
    // The view orders rows by rank_on_scanner asc; the helper should pass
    // that ordering through to each scanner's cards array.
    const rows = [
      makeRow({
        scanner_id: 1,
        scanner_name: "Cyl-01",
        experiment_id: 100,
        rank_on_scanner: 1,
        latest_upload_on_this_scanner_at: "2026-05-28T10:00:00Z",
      }),
      makeRow({
        scanner_id: 1,
        scanner_name: "Cyl-01",
        experiment_id: 101,
        rank_on_scanner: 2,
        latest_upload_on_this_scanner_at: "2026-05-28T09:00:00Z",
      }),
    ];

    const result = await getRecentPhenotypesByCylScanner(makeMockSupabase(rows));

    expect(result[0].cards[0].rank_on_scanner).toBe(1);
    expect(result[0].cards[1].rank_on_scanner).toBe(2);
    expect(result[0].cards[0].latest_upload_on_this_scanner_at).toBe(
      "2026-05-28T10:00:00Z",
    );
  });

  it("skips rows with null scanner_name", async () => {
    const rows = [
      makeRow({ scanner_id: 1, scanner_name: null }),
      makeRow({ scanner_id: 2, scanner_name: "Cyl-02" }),
    ];

    const result = await getRecentPhenotypesByCylScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(1);
    expect(result[0].scanner_id).toBe(2);
  });

  it("throws with a descriptive message when supabase returns an error", async () => {
    const supabase = makeMockSupabase(null, {
      message: "relation does not exist",
    });

    await expect(getRecentPhenotypesByCylScanner(supabase)).rejects.toThrow(
      /Failed to fetch recent phenotypes by cyl scanner.*relation does not exist/,
    );
  });

  it("queries the recent_experiments_by_cyl_scanner view", async () => {
    const supabase = makeMockSupabase([]);
    await getRecentPhenotypesByCylScanner(supabase);
    expect(supabase.from).toHaveBeenCalledWith(
      "recent_experiments_by_cyl_scanner",
    );
  });
});
