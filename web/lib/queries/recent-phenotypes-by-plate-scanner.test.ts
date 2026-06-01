/**
 * Unit tests for getRecentPhenotypesByPlateScanner.
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
  getRecentPhenotypesByPlateScanner,
  type PlateScanRow,
} from "@/lib/queries/recent-phenotypes-by-plate-scanner";

// ─── Test helpers ────────────────────────────────────────────────────────────

function makeMockSupabase(
  rows: PlateScanRow[] | null,
  error: { message: string } | null = null,
): SupabaseClient<Database> {
  const result = Promise.resolve({ data: rows, error });

  const builder = {
    select: vi.fn(),
    order: vi.fn(),
    then: result.then.bind(result),
    catch: result.catch.bind(result),
  };
  builder.select.mockReturnValue(builder);
  builder.order.mockReturnValue(builder);

  return {
    from: vi.fn().mockReturnValue(builder),
  } as unknown as SupabaseClient<Database>;
}

function makeRow(overrides: Partial<PlateScanRow> = {}): PlateScanRow {
  return {
    scanner_id: 1,
    scanner_name: "gravi-scanner-01",
    experiment_id: 100,
    experiment_name: "Root gravitropism",
    species_id: 1,
    species_common_name: "Arabidopsis",
    wave_number: 1,
    scan_mode: "single",
    plate_id: "PLATE-A1",
    phenotyper_first_name: "Sarah",
    phenotyper_last_name: "Lee",
    latest_upload_on_this_scanner_at: "2026-05-29T13:45:00Z",
    rank_on_scanner: 1,
    ...overrides,
  };
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("getRecentPhenotypesByPlateScanner", () => {
  it("returns an empty array when the view returns no rows", async () => {
    const supabase = makeMockSupabase([]);
    const result = await getRecentPhenotypesByPlateScanner(supabase);
    expect(result).toEqual([]);
  });

  it("returns an empty array when the view returns null data", async () => {
    const supabase = makeMockSupabase(null);
    const result = await getRecentPhenotypesByPlateScanner(supabase);
    expect(result).toEqual([]);
  });

  it("groups a single scanner's rows into one section", async () => {
    const rows = [
      makeRow({
        scanner_id: 1,
        scanner_name: "gravi-scanner-01",
        experiment_id: 100,
        rank_on_scanner: 1,
      }),
      makeRow({
        scanner_id: 1,
        scanner_name: "gravi-scanner-01",
        experiment_id: 101,
        rank_on_scanner: 2,
      }),
    ];

    const result = await getRecentPhenotypesByPlateScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      scanner_id: 1,
      scanner_name: "gravi-scanner-01",
    });
    expect(result[0].cards).toHaveLength(2);
    expect(result[0].cards[0].experiment_id).toBe(100);
    expect(result[0].cards[1].experiment_id).toBe(101);
  });

  it("creates a separate section per scanner_id", async () => {
    const rows = [
      makeRow({ scanner_id: 1, scanner_name: "gravi-01", experiment_id: 100 }),
      makeRow({ scanner_id: 2, scanner_name: "gravi-02", experiment_id: 200 }),
      makeRow({ scanner_id: 2, scanner_name: "gravi-02", experiment_id: 201 }),
      makeRow({ scanner_id: 3, scanner_name: "gravi-03", experiment_id: 300 }),
    ];

    const result = await getRecentPhenotypesByPlateScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(3);
    expect(result.map((s) => s.scanner_id)).toEqual([1, 2, 3]);
    expect(result[0].cards).toHaveLength(1);
    expect(result[1].cards).toHaveLength(2);
    expect(result[2].cards).toHaveLength(1);
  });

  it("preserves the row order within each scanner section", async () => {
    const rows = [
      makeRow({
        scanner_id: 1,
        scanner_name: "gravi-01",
        rank_on_scanner: 1,
        latest_upload_on_this_scanner_at: "2026-05-29T13:45:00Z",
      }),
      makeRow({
        scanner_id: 1,
        scanner_name: "gravi-01",
        rank_on_scanner: 2,
        latest_upload_on_this_scanner_at: "2026-05-28T08:15:00Z",
      }),
    ];

    const result = await getRecentPhenotypesByPlateScanner(makeMockSupabase(rows));

    expect(result[0].cards[0].rank_on_scanner).toBe(1);
    expect(result[0].cards[1].rank_on_scanner).toBe(2);
  });

  it("skips rows with null scanner_name", async () => {
    const rows = [
      makeRow({ scanner_id: 1, scanner_name: null }),
      makeRow({ scanner_id: 2, scanner_name: "gravi-02" }),
    ];

    const result = await getRecentPhenotypesByPlateScanner(makeMockSupabase(rows));

    expect(result).toHaveLength(1);
    expect(result[0].scanner_id).toBe(2);
  });

  it("throws with a descriptive message when supabase returns an error", async () => {
    const supabase = makeMockSupabase(null, {
      message: "relation does not exist",
    });

    await expect(getRecentPhenotypesByPlateScanner(supabase)).rejects.toThrow(
      /Failed to fetch recent phenotypes by plate scanner.*relation does not exist/,
    );
  });

  it("queries the recent_phenotypes_by_plate_scanner view", async () => {
    const supabase = makeMockSupabase([]);
    await getRecentPhenotypesByPlateScanner(supabase);
    expect(supabase.from).toHaveBeenCalledWith(
      "recent_phenotypes_by_plate_scanner",
    );
  });

  it("preserves gravi-specific fields (scan_mode, plate_id, wave_number)", async () => {
    const rows = [
      makeRow({
        scanner_id: 1,
        scanner_name: "gravi-01",
        scan_mode: "continuous",
        plate_id: "PLATE-X9",
        wave_number: 3,
      }),
    ];

    const result = await getRecentPhenotypesByPlateScanner(makeMockSupabase(rows));

    expect(result[0].cards[0]).toMatchObject({
      scan_mode: "continuous",
      plate_id: "PLATE-X9",
      wave_number: 3,
    });
  });
});
