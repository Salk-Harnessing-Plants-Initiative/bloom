/** Classifying the confirm dialog's model groups against the production model windows (bloom#971). */

import { describe, expect, it } from "vitest";

import { PRODUCTION_CARDS } from "./__fixtures__/model-cards";
import { classifyModelGroups, type ModelCardEntry } from "./model-windows";
import type { ParamsGroup } from "./params-summary";

const group = (species: string, age: number, count = 1): ParamsGroup => ({ species, mode: "cylinder", age, count });

const synthetic = (selectors: [string, string, number, number][]): ModelCardEntry[] => [
  {
    root_type: "primary",
    registry_id: "org/registry/synthetic",
    version: "v0",
    selectors: selectors.map(([species, mode, age_min, age_max]) => ({ species, mode, age_min, age_max })),
  },
];

describe("classifyModelGroups on the production cards", () => {
  it.each([
    ["arabidopsis", 21, 14],
    ["arabidopsis", 28, 14],
    ["rice", 18, 10],
    ["soybean", 10, 8],
    ["canola", 14, 13],
    ["pennycress", 15, 14],
  ])("%s at day %i is past its window, with max %i", (species, age, max) => {
    const { pastWindow, noModel } = classifyModelGroups([group(species, age, 3)], PRODUCTION_CARDS);
    expect(pastWindow).toEqual([{ species, age, count: 3, max }]);
    expect(noModel).toEqual([]);
  });

  it.each([
    ["sorghum", 10],
    ["canola", 0],
    ["soybean", 1],
  ])("%s at day %i has no model", (species, age) => {
    const { pastWindow, noModel } = classifyModelGroups([group(species, age, 2)], PRODUCTION_CARDS);
    expect(noModel).toEqual([{ species, age, count: 2 }]);
    expect(pastWindow).toEqual([]);
  });

  it.each([
    ["arabidopsis", 14],
    ["rice", 8],
    ["rice", 3],
    ["canola", 2],
    ["rice", 6],
  ])("%s at day %i is covered", (species, age) => {
    expect(classifyModelGroups([group(species, age)], PRODUCTION_CARDS)).toEqual({ pastWindow: [], noModel: [] });
  });

  it("keeps the groups' order", () => {
    const groups = [group("arabidopsis", 28, 60), group("sorghum", 10, 40), group("arabidopsis", 21, 30), group("canola", 0, 5)];
    const { pastWindow, noModel } = classifyModelGroups(groups, PRODUCTION_CARDS);
    expect(pastWindow.map((g) => g.age)).toEqual([28, 21]);
    expect(noModel.map((g) => g.species)).toEqual(["sorghum", "canola"]);
  });
});

describe("classifyModelGroups edge cases", () => {
  it("scopes the window to the group's mode", () => {
    const cards = synthetic([
      ["canola", "cylinder", 2, 13],
      ["canola", "multiplant cylinder", 2, 20],
    ]);
    expect(classifyModelGroups([group("canola", 15)], cards).pastWindow).toEqual([
      { species: "canola", age: 15, count: 1, max: 13 },
    ]);
  });

  it("treats an age between disjoint windows as no model", () => {
    const cards = synthetic([
      ["canola", "cylinder", 2, 5],
      ["canola", "cylinder", 10, 13],
    ]);
    expect(classifyModelGroups([group("canola", 7)], cards)).toEqual({
      pastWindow: [],
      noModel: [{ species: "canola", age: 7, count: 1 }],
    });
  });

  it("gives no model when a species has cards only in another mode", () => {
    const cards = synthetic([["arabidopsis", "multiplant cylinder", 2, 14]]);
    expect(classifyModelGroups([group("arabidopsis", 7)], cards).noModel).toHaveLength(1);
  });

  it("treats every group as no model against an empty card list", () => {
    expect(classifyModelGroups([group("canola", 7)], []).noModel).toHaveLength(1);
  });

  it("returns empty lists for zero groups", () => {
    expect(classifyModelGroups([], PRODUCTION_CARDS)).toEqual({ pastWindow: [], noModel: [] });
  });
});

describe("cross-check against sleap-roots-predict at 79939ee (a hand-copied snapshot)", () => {
  // tests/test_model_selection.py `_PAST_WINDOW`: (species, age, window max) the
  // predictor clamps to. A later change upstream won't be detected here.
  const PREDICT_PAST_WINDOW: [string, number, number][] = [
    ["arabidopsis", 28, 14],
    ["rice", 18, 10],
    ["soybean", 10, 8],
    ["canola", 14, 13],
    ["pennycress", 15, 14],
  ];

  it.each(PREDICT_PAST_WINDOW)("%s at day %i clamps to %i, as predict does", (species, age, max) => {
    expect(classifyModelGroups([group(species, age)], PRODUCTION_CARDS).pastWindow[0]?.max).toBe(max);
  });

  it("uses the same selectors as predict's _PRODUCTION table (tests/card_builders.py)", () => {
    const flat = PRODUCTION_CARDS.flatMap((c) =>
      c.selectors.map((s) => `${c.root_type}:${s.species}/${s.mode}/${s.age_min}-${s.age_max}`),
    ).sort();
    expect(flat).toEqual(
      [
        "crown:rice/cylinder/6-10",
        "crown:rice/cylinder/2-5",
        "lateral:arabidopsis/cylinder/2-14",
        "lateral:arabidopsis/multiplant cylinder/2-14",
        "lateral:canola/cylinder/2-13",
        "lateral:pennycress/cylinder/2-14",
        "lateral:soybean/cylinder/2-8",
        "primary:arabidopsis/cylinder/2-14",
        "primary:arabidopsis/multiplant cylinder/2-14",
        "primary:canola/cylinder/2-13",
        "primary:pennycress/cylinder/2-14",
        "primary:rice/cylinder/2-5",
        "primary:soybean/cylinder/2-8",
      ].sort(),
    );
  });
});
