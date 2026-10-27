/** Compact elapsed time for run rows and the drill-down header. */

import { describe, expect, it } from "vitest";
import { formatElapsed } from "./elapsed";

const start = "2026-09-28T10:00:00.123456+00:00";
const now = (ms: number) => Date.parse("2026-09-28T10:00:00.123Z") + ms;
const MIN = 60_000;

describe("formatElapsed", () => {
  it.each([
    [0, "under 1 min"],
    [59_000, "under 1 min"],
    [MIN, "1 min"],
    [12 * MIN, "12 min"],
    [60 * MIN, "1 h"],
    [65 * MIN, "1 h 5 min"],
    [24 * 60 * MIN, "1 d"],
    [(2 * 24 * 60 + 4 * 60 + 30) * MIN, "2 d 4 h"],
  ])("%d ms is %s", (ms, text) => {
    expect(formatElapsed(start, now(ms))).toBe(text);
  });

  it("treats a start in the future as just started", () => {
    expect(formatElapsed(start, now(-5 * MIN))).toBe("under 1 min");
  });

  it("gives an empty string for a missing or unparsable start", () => {
    expect(formatElapsed(null, now(0))).toBe("");
    expect(formatElapsed("garbage", now(0))).toBe("");
  });
});
