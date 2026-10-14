import { describe, expect, it } from "vitest";

import { when } from "./run-labels";

describe("when", () => {
  it("shows a time in Pacific time, whatever the machine's zone", () => {
    expect(when("2026-09-30T00:03:54Z")).toBe("Sep 29, 5:03 PM");
  });

  it("shows a dash for no time", () => {
    expect(when(null)).toBe("—");
  });
});
