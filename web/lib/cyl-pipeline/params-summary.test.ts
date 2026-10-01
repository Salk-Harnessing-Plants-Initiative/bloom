/** The confirm dialog's resolved-params groups and stage-in count (add-cyl-pipeline-ui task 10.1). */

import { beforeEach, describe, expect, it, vi } from "vitest";

const stageIn = vi.hoisted(() => ({ calls: 0 }));
vi.mock("./stage-in", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./stage-in")>();
  return {
    ...actual,
    stageInProblems: (...args: Parameters<typeof actual.stageInProblems>) => {
      stageIn.calls += 1;
      return actual.stageInProblems(...args);
    },
  };
});

import { paramsSummary } from "./params-summary";

const scans = (n: number, species_name: string | null, plant_age_days: number | null) =>
  Array.from({ length: n }, () => ({ species_name, plant_age_days }));

beforeEach(() => {
  stageIn.calls = 0;
});

describe("paramsSummary", () => {
  it("groups the spec scenario by trimmed, lowercased species, cylinder mode and age, and flags the null ages", () => {
    const summary = paramsSummary([...scans(30, " Pennycress ", 14), ...scans(8, "Pennycress", 21), ...scans(2, "Pennycress", null)]);
    expect(summary).toEqual({
      groups: [
        { species: "pennycress", mode: "cylinder", age: 14, count: 30 },
        { species: "pennycress", mode: "cylinder", age: 21, count: 8 },
      ],
      stageInCount: 2,
    });
  });

  it("leaves every flagged scan out of the groups", () => {
    const summary = paramsSummary([
      ...scans(3, "soybean", 7),
      ...scans(1, "   ", 7),
      ...scans(1, null, 7),
      ...scans(1, "soybean", 7.5),
      ...scans(1, "soybean", null),
    ]);
    expect(summary.groups).toEqual([{ species: "soybean", mode: "cylinder", age: 7, count: 3 }]);
    expect(summary.stageInCount).toBe(4);
  });

  it("decides what is flagged with stageInProblems(), once per scan", () => {
    paramsSummary([...scans(2, "soybean", 7), ...scans(1, null, 7)]);
    expect(stageIn.calls).toBe(3);
  });

  it("orders groups by count, then species, then age", () => {
    const summary = paramsSummary([...scans(2, "b", 5), ...scans(2, "a", 9), ...scans(2, "a", 3), ...scans(5, "c", 1)]);
    expect(summary.groups.map((g) => [g.species, g.age, g.count])).toEqual([
      ["c", 1, 5],
      ["a", 3, 2],
      ["a", 9, 2],
      ["b", 5, 2],
    ]);
  });

  it("answers exactly groups and stageInCount, and nothing that looks like a hash", () => {
    const summary = paramsSummary(scans(1, "soybean", 7));
    expect(Object.keys(summary).sort()).toEqual(["groups", "stageInCount"]);
    expect(Object.keys(summary.groups[0]).sort()).toEqual(["age", "count", "mode", "species"]);
  });

  it("answers no groups for no scans", () => {
    expect(paramsSummary([])).toEqual({ groups: [], stageInCount: 0 });
  });
});
