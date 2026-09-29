/**
 * Run → results links come only from the run's requested scans (design D6):
 * a "Scan images" link per scan, and up to 3 traits links per experiment for
 * the most common (wave, age) pairs, counted jointly.
 */

import { describe, expect, it } from "vitest";
import { scanImagesHref, traitsLinks } from "./run-links";
import type { ScanMeta } from "./scan-meta";

function meta(scan_id: number, overrides: Partial<ScanMeta> = {}): ScanMeta {
  return {
    scan_id,
    qr_code: `QR-${scan_id}`,
    wave_id: 11,
    wave_number: 1,
    plant_age_days: 14,
    species_id: 2,
    species_name: "pennycress",
    accession_id: 7,
    experiment_id: 5,
    experiment_name: "exp-five",
    ...overrides,
  };
}
const byId = (rows: ScanMeta[]) => new Map(rows.map((m) => [m.scan_id, m]));
const LABEL_TAIL = "traits (all scans in the experiment, latest result per scan)";

describe("scanImagesHref", () => {
  it("links to the scan's existing cylinder page", () => {
    expect(scanImagesHref(meta(577))).toBe("/app/phenotypes/2/5/11/7/577");
  });

  it("gives no link when metadata is missing", () => {
    expect(scanImagesHref(undefined)).toBeNull();
    for (const key of ["species_id", "experiment_id", "wave_id", "accession_id"] as const) {
      expect(scanImagesHref(meta(577, { [key]: null }))).toBeNull();
    }
  });
});

describe("traitsLinks", () => {
  it("gives a one-scan run one traits link at its wave and age", () => {
    expect(traitsLinks([577], byId([meta(577)]))).toEqual([
      {
        experimentId: 5,
        experimentName: "exp-five",
        speciesId: 2,
        links: [
          { wave: 1, age: 14, count: 1, href: "/app/traits/2/5?wave=1&age=14", label: `Wave 1 · day 14 ${LABEL_TAIL}` },
        ],
      },
    ]);
  });

  it("counts wave and age as a pair (the spec's example)", () => {
    const rows = [
      ...[1, 2, 3].map((id) => meta(id, { wave_number: 1, plant_age_days: 3 })),
      ...[4, 5].map((id) => meta(id, { wave_number: 2, plant_age_days: 5 })),
      ...[6, 7].map((id) => meta(id, { wave_number: 2, plant_age_days: 7 })),
    ];
    const [experiment] = traitsLinks(rows.map((r) => r.scan_id), byId(rows));
    expect(experiment.links.map((l) => l.href)).toEqual([
      "/app/traits/2/5?wave=1&age=3",
      "/app/traits/2/5?wave=2&age=5",
      "/app/traits/2/5?wave=2&age=7",
    ]);
    expect(experiment.links.map((l) => l.count)).toEqual([3, 2, 2]);
  });

  it("breaks ties by the lowest wave, then the lowest age", () => {
    const rows = [
      meta(1, { wave_number: 3, plant_age_days: 1 }),
      meta(2, { wave_number: 2, plant_age_days: 9 }),
      meta(3, { wave_number: 2, plant_age_days: 4 }),
    ];
    const [experiment] = traitsLinks([3, 2, 1], byId(rows));
    expect(experiment.links.map((l) => [l.wave, l.age])).toEqual([
      [2, 4],
      [2, 9],
      [3, 1],
    ]);
  });

  it("omits age for a null age, and ranks it after a numbered age on a tie", () => {
    const rows = [meta(1, { plant_age_days: null }), meta(2, { plant_age_days: 21 })];
    const [experiment] = traitsLinks([1, 2], byId(rows));
    expect(experiment.links.map((l) => l.href)).toEqual(["/app/traits/2/5?wave=1&age=21", "/app/traits/2/5?wave=1"]);
    expect(experiment.links[1].label).toBe(`Wave 1 ${LABEL_TAIL}`);
    expect(experiment.links[1].age).toBeNull();
  });

  it("links wave 0 and day 0 explicitly, which the traits page reads", () => {
    const [experiment] = traitsLinks([1], byId([meta(1, { wave_number: 0, plant_age_days: 0 })]));
    expect(experiment.links[0].href).toBe("/app/traits/2/5?wave=0&age=0");
    expect(experiment.links[0].label).toBe(`Wave 0 · day 0 ${LABEL_TAIL}`);
  });

  it("gives at most 3 links per experiment", () => {
    const rows = [1, 2, 3, 4, 5].map((id) => meta(id, { plant_age_days: id }));
    expect(traitsLinks([1, 2, 3, 4, 5], byId(rows))[0].links).toHaveLength(3);
  });

  it("gives each experiment the run touches its own links, the larger first", () => {
    const rows = [
      meta(1, { experiment_id: 6, experiment_name: "six", species_id: 3 }),
      meta(2),
      meta(3),
    ];
    const result = traitsLinks([1, 2, 3], byId(rows));
    expect(result.map((e) => [e.experimentId, e.speciesId, e.links[0].href])).toEqual([
      [5, 2, "/app/traits/2/5?wave=1&age=14"],
      [6, 3, "/app/traits/3/6?wave=1&age=14"],
    ]);
  });

  it("skips scans without metadata, an experiment, a species or a wave number", () => {
    const rows = [
      meta(2, { experiment_id: null }),
      meta(3, { species_id: null }),
      meta(4, { wave_number: null }),
      meta(5, { wave_number: 2 }),
    ];
    const result = traitsLinks([1, 2, 3, 4, 5], byId(rows));
    expect(result).toHaveLength(1);
    expect(result[0].links.map((l) => l.href)).toEqual(["/app/traits/2/5?wave=2&age=14"]);
  });

  it("gives nothing when no scan has metadata", () => {
    expect(traitsLinks([1, 2], new Map())).toEqual([]);
  });
});
