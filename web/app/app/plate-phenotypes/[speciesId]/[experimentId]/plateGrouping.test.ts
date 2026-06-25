/**
 * Unit tests for the wave/plate grouping on the plate-phenotypes experiment
 * page. The React rendering (tabs, plate cards) is exercised separately; these
 * lock the pure grouping/sorting — most importantly that plates which reuse a
 * plate_id across waves are NOT merged.
 */

import { describe, it, expect } from "vitest";
import {
  groupByWave,
  scanWave,
  plateSortKey,
  waveLabel,
  waveKey,
  parseWaveKey,
  waveScanDateRange,
  type ScanRow,
} from "./plateGrouping";

function scan(overrides: Partial<ScanRow> & { plate_id: string }): ScanRow {
  return {
    id: Math.floor(Math.random() * 1e9),
    plate_index: null,
    wave_number: null,
    capture_date: "2026-05-01T00:00:00Z",
    gravi_images: null,
    gravi_scan_metadata_accession: null,
    ...overrides,
  };
}

describe("groupByWave", () => {
  it("splits plates by wave instead of mixing them into one list", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: 1 }),
      scan({ plate_id: "Plate_2", wave_number: 1 }),
      scan({ plate_id: "Plate_3", wave_number: 2 }),
    ]);

    expect(waves.map((w) => w.waveNumber)).toEqual([1, 2]);
    expect(waves[0].plates.map((p) => p.plate_id)).toEqual([
      "Plate_1",
      "Plate_2",
    ]);
    expect(waves[1].plates.map((p) => p.plate_id)).toEqual(["Plate_3"]);
  });

  it("does NOT collapse the same plate_id across different waves (the bug)", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-01T00:00:00Z" }),
      scan({ plate_id: "Plate_1", wave_number: 2, capture_date: "2026-05-08T00:00:00Z" }),
    ]);

    expect(waves).toHaveLength(2);
    const wave1 = waves.find((w) => w.waveNumber === 1)!;
    const wave2 = waves.find((w) => w.waveNumber === 2)!;
    expect(wave1.plates).toHaveLength(1);
    expect(wave2.plates).toHaveLength(1);
    // Each wave keeps its own Plate_1 — neither is lost to the other.
    expect(wave1.plates[0].plate_id).toBe("Plate_1");
    expect(wave2.plates[0].plate_id).toBe("Plate_1");
  });

  it("collects all timepoints of one plate and picks the latest scan", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-01T00:00:00Z" }),
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-05T00:00:00Z" }),
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-03T00:00:00Z" }),
    ]);

    const plate = waves[0].plates[0];
    expect(plate.scans).toHaveLength(3);
    expect(plate.latestScan.capture_date).toBe("2026-05-05T00:00:00Z");
  });

  it("natural-sorts plates within a wave (Plate_2 before Plate_10)", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_10", wave_number: 1 }),
      scan({ plate_id: "Plate_2", wave_number: 1 }),
      scan({ plate_id: "Plate_1", wave_number: 1 }),
    ]);
    expect(waves[0].plates.map((p) => p.plate_id)).toEqual([
      "Plate_1",
      "Plate_2",
      "Plate_10",
    ]);
  });

  it("sorts waves ascending and puts 'no wave' (null) last", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: null }),
      scan({ plate_id: "Plate_2", wave_number: 2 }),
      scan({ plate_id: "Plate_3", wave_number: 1 }),
    ]);
    expect(waves.map((w) => w.waveNumber)).toEqual([1, 2, null]);
  });

  it("ignores scans with no plate_id", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: 1 }),
      { ...scan({ plate_id: "x" }), plate_id: null },
    ]);
    expect(waves).toHaveLength(1);
    expect(waves[0].plates).toHaveLength(1);
  });
});

describe("scanWave", () => {
  it("prefers the metadata wave over the scan's own wave_number", () => {
    const s = scan({
      plate_id: "Plate_1",
      wave_number: 9,
      gravi_scan_metadata_accession: {
        plate_id: "Plate_1",
        accession_name: "acc",
        wave_number: 3,
        gravi_scan_metadata_sections: [],
      },
    });
    expect(scanWave(s)).toBe(3);
  });

  it("falls back to the scan wave_number, then null", () => {
    expect(scanWave(scan({ plate_id: "Plate_1", wave_number: 4 }))).toBe(4);
    expect(scanWave(scan({ plate_id: "Plate_1" }))).toBeNull();
  });
});

describe("plateSortKey / waveLabel", () => {
  it("extracts the trailing integer", () => {
    expect(plateSortKey("Plate_7")).toBe(7);
    expect(plateSortKey("no-digits")).toBe(Number.POSITIVE_INFINITY);
  });

  it("labels waves, with null as 'No wave'", () => {
    expect(waveLabel(2)).toBe("Wave 2");
    expect(waveLabel(null)).toBe("No wave");
  });
});

describe("waveKey / parseWaveKey", () => {
  it("round-trips a wave number and the null sentinel", () => {
    expect(waveKey(3)).toBe("3");
    expect(waveKey(null)).toBe("none");
    expect(parseWaveKey("3")).toBe(3);
    expect(parseWaveKey("none")).toBeNull();
  });
});

describe("waveScanDateRange", () => {
  it("returns earliest and latest capture_date across a wave's plates", () => {
    const waves = groupByWave([
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-05T00:00:00Z" }),
      scan({ plate_id: "Plate_1", wave_number: 1, capture_date: "2026-05-01T00:00:00Z" }),
      scan({ plate_id: "Plate_2", wave_number: 1, capture_date: "2026-05-08T00:00:00Z" }),
    ]);
    expect(waveScanDateRange(waves[0].plates)).toEqual({
      first: "2026-05-01T00:00:00Z",
      last: "2026-05-08T00:00:00Z",
    });
  });

  it("returns nulls when there are no plates", () => {
    expect(waveScanDateRange([])).toEqual({ first: null, last: null });
  });
});
