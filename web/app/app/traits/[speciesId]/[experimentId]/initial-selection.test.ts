/**
 * Which wave and plant age the traits page opens on.
 *
 * The page's defaults are the last wave and the oldest plant age. A link from a pipeline run asks
 * for a specific wave and age instead (`?wave=&age=`); after that, changing trait keeps whatever
 * the user is looking at if the new trait has data there. When the wanted pair has no data, the
 * page falls back to the defaults and says so.
 */

import { describe, expect, it } from "vitest";
import { resolveSelection, type SelectionRow } from "./initial-selection";

const rows = (...pairs: [number, number][]): SelectionRow[] =>
  pairs.map(([wave_number, plant_age_days]) => ({ wave_number, plant_age_days }));

// Waves 1-3; wave 1 has ages 7 and 14, wave 2 has 14 and 21, wave 3 has 21.
const DATA = rows([1, 7], [1, 14], [2, 14], [2, 21], [3, 21]);

describe("resolveSelection", () => {
  it("uses the defaults, last wave and oldest age, when nothing is asked for", () => {
    expect(resolveSelection(DATA, null)).toEqual({
      selection: { wave: 3, age: 21 },
      note: null,
    });
  });

  it("selects an available wave and age pair", () => {
    expect(resolveSelection(DATA, { wave: 1, age: 14 })).toEqual({
      selection: { wave: 1, age: 14 },
      note: null,
    });
  });

  it("falls back to the defaults, with a note, when the wave does not exist", () => {
    expect(resolveSelection(DATA, { wave: 9, age: 14 })).toEqual({
      selection: { wave: 3, age: 21 },
      note: "Wave 9 · day 14 has no data for this trait; showing wave 3 · day 21.",
    });
  });

  it("falls back when the age exists, but not in the chosen wave", () => {
    const { selection, note } = resolveSelection(DATA, { wave: 1, age: 21 });
    expect(selection).toEqual({ wave: 3, age: 21 });
    expect(note).toBe("Wave 1 · day 21 has no data for this trait; showing wave 3 · day 21.");
  });

  it("keeps a selection that is still available", () => {
    // The same call serves a trait change: the current selection is the preference.
    expect(resolveSelection(DATA, { wave: 2, age: 14 }).selection).toEqual({ wave: 2, age: 14 });
  });

  it("fills in the oldest age within the wave when only a wave is asked for", () => {
    expect(resolveSelection(DATA, { wave: 1 })).toEqual({
      selection: { wave: 1, age: 14 },
      note: null,
    });
  });

  it("fills in the last wave that has the age when only an age is asked for", () => {
    expect(resolveSelection(DATA, { age: 14 })).toEqual({
      selection: { wave: 2, age: 14 },
      note: null,
    });
  });

  it("names only the part that was asked for when a lone wave has no data", () => {
    expect(resolveSelection(DATA, { wave: 9 }).note).toBe(
      "Wave 9 has no data for this trait; showing wave 3 · day 21.",
    );
  });

  it("has no selection and no note when the trait has no data at all", () => {
    expect(resolveSelection([], { wave: 1, age: 14 })).toEqual({ selection: null, note: null });
  });

  it("treats an empty preference like no preference", () => {
    expect(resolveSelection(DATA, {})).toEqual({ selection: { wave: 3, age: 21 }, note: null });
  });
});
