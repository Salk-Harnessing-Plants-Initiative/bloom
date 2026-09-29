/** "Run this accession" submits every scan of every listed plant (add-cyl-pipeline-ui task 10.5). */

import { describe, expect, it } from "vitest";
import { accessionScanIds } from "./accession-scan-ids";

describe("accessionScanIds", () => {
  it("includes both same-day scans and a scan with no frame-1 image: 3 ids", () => {
    const plants = [
      {
        cyl_scans: [
          { id: 11, plant_age_days: 3, cyl_images: [{ id: 1 }] },
          { id: 12, plant_age_days: 3, cyl_images: [{ id: 2 }] },
          { id: 13, plant_age_days: 5, cyl_images: [] },
        ],
      },
    ];
    expect(accessionScanIds(plants)).toEqual([11, 12, 13]);
  });

  it("covers every plant and de-duplicates", () => {
    const plants = [{ cyl_scans: [{ id: 2 }, { id: 1 }] }, { cyl_scans: [{ id: 1 }, { id: 3 }] }, { cyl_scans: [] }];
    expect(accessionScanIds(plants)).toEqual([2, 1, 3]);
  });

  it("does not mutate its input", () => {
    const plants = [{ cyl_scans: [{ id: 5 }, { id: 4 }] }];
    const before = JSON.stringify(plants);
    const scans = plants[0].cyl_scans;
    accessionScanIds(plants);
    expect(JSON.stringify(plants)).toBe(before);
    expect(plants[0].cyl_scans).toBe(scans);
  });

  it("answers no ids for no plants", () => {
    expect(accessionScanIds([])).toEqual([]);
    expect(accessionScanIds(null)).toEqual([]);
  });
});
