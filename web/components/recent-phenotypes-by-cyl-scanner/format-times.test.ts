/**
 * Unit tests for the relative + absolute timestamp formatting helpers used by
 * <ExperimentCard />. The React rendering is exercised by Phase 5's Playwright
 * E2E; these tests just lock the pure-string output.
 */

import { describe, it, expect } from "vitest";
import {
  formatRelative,
  formatAbsolute,
  formatRelativeAndAbsolute,
} from "./format-times";

const NOW = new Date("2026-05-28T12:00:00Z");

describe("formatRelative", () => {
  it("formats minutes ago", () => {
    const result = formatRelative("2026-05-28T11:55:00Z", NOW, "en-US");
    expect(result).toMatch(/5 minutes ago/);
  });

  it("formats hours ago", () => {
    const result = formatRelative("2026-05-28T08:00:00Z", NOW, "en-US");
    expect(result).toMatch(/4 hours ago/);
  });

  it("formats days ago", () => {
    const result = formatRelative("2026-05-25T12:00:00Z", NOW, "en-US");
    expect(result).toMatch(/3 days ago/);
  });

  it("formats future times", () => {
    const result = formatRelative("2026-05-28T14:00:00Z", NOW, "en-US");
    expect(result).toMatch(/in 2 hours/);
  });

  it("returns empty string for invalid input", () => {
    expect(formatRelative("not-a-date", NOW)).toBe("");
  });
});

describe("formatAbsolute", () => {
  it("formats a known date in en-US", () => {
    const result = formatAbsolute("2026-05-28T08:04:00Z", "en-US");
    // The exact form depends on the locale data; we just sanity-check that
    // the result contains the month + year + a clock-like fragment.
    expect(result).toMatch(/May/);
    expect(result).toMatch(/2026/);
    expect(result).toMatch(/:/);
  });

  it("returns empty string for invalid input", () => {
    expect(formatAbsolute("garbage")).toBe("");
  });
});

describe("formatRelativeAndAbsolute", () => {
  it("joins relative + absolute with a bullet", () => {
    const result = formatRelativeAndAbsolute(
      "2026-05-28T08:00:00Z",
      NOW,
      "en-US",
    );
    expect(result).toContain("•");
    expect(result).toMatch(/hours ago/);
    expect(result).toMatch(/May/);
  });

  it("skips the bullet when only one half is present", () => {
    // An obviously bogus input should produce empty halves; helper returns
    // an empty string rather than a stray bullet.
    expect(formatRelativeAndAbsolute("nonsense")).toBe("");
  });
});
