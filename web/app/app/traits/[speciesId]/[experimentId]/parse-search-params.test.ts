/**
 * The traits page reads an optional `?wave=&age=` from its URL. Only a single, plain non-negative
 * integer counts; anything else is ignored, never coerced. Zero is real data: Bloom Desktop allows
 * wave 0, and nothing in the schema forbids a day-0 age.
 */

import { describe, expect, it } from "vitest";
import { parseWaveAge } from "./parse-search-params";

describe("parseWaveAge", () => {
  it("reads both values as numbers", () => {
    expect(parseWaveAge({ wave: "1", age: "14" })).toEqual({ wave: 1, age: 14 });
  });

  it("returns nothing when neither is present", () => {
    expect(parseWaveAge({})).toEqual({});
  });

  it("keeps a valid value when the other is missing or invalid", () => {
    expect(parseWaveAge({ wave: "2" })).toEqual({ wave: 2 });
    expect(parseWaveAge({ wave: "abc", age: "21" })).toEqual({ age: 21 });
  });

  it("reads wave 0 and day 0", () => {
    expect(parseWaveAge({ wave: "0", age: "0" })).toEqual({ wave: 0, age: 0 });
  });

  it.each(["00", "-0", "-1", "1.5", "07", "0x1", "1e2", "", " 7", "7 ", "+7", "9007199254740993"])(
    "ignores %j",
    (raw) => {
      expect(parseWaveAge({ wave: raw, age: raw })).toEqual({});
    },
  );

  it("ignores a repeated parameter rather than picking one", () => {
    expect(parseWaveAge({ wave: ["1", "2"], age: "14" })).toEqual({ age: 14 });
  });
});
