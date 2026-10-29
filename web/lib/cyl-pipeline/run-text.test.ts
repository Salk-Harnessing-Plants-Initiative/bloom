/** Short run texts and the shared pluraliser (bloom#955). */

import { describe, expect, it } from "vitest";
import { plural, requesterText, targetText } from "./run-text";

describe("plural", () => {
  it.each([
    [0, "scan", "0 scans"],
    [1, "scan", "1 scan"],
    [2, "scan", "2 scans"],
    [1, "selected scan", "1 selected scan"],
  ])("plural(%i, %s)", (n, one, text) => {
    expect(plural(n, one)).toBe(text);
  });
});

describe("targetText", () => {
  it.each([
    [{ target_level: "scan_ids", target_id: null, scan_count: 1 }, "1 selected scan"],
    [{ target_level: "scan_ids", target_id: null, scan_count: 3 }, "3 selected scans"],
    [{ target_level: "scan", target_id: 577, scan_count: 1 }, "scan 577 · 1 scan"],
    [{ target_level: "experiment", target_id: 5, scan_count: 40 }, "experiment 5 · 40 scans"],
    [{ target_level: "experiment", target_id: null, scan_count: 40 }, "experiment · 40 scans"],
  ])("%o", (run, text) => {
    expect(targetText(run)).toBe(text);
  });
});

describe("requesterText", () => {
  it("names nobody when there is no requester", () => {
    expect(requesterText(null, "u1")).toBe("unknown requester");
  });

  it("says you for the current user", () => {
    expect(requesterText("0b7e2c91-aaaa", "0b7e2c91-aaaa")).toBe("you");
  });

  it("shows another member's first 8 characters", () => {
    expect(requesterText("0b7e2c91-aaaa", "u1")).toBe("another member · 0b7e2c91");
  });
});
