import { describe, it, expect } from "vitest";
import { accessionColor, NOISE_EPSILON } from "./constants";

describe("accessionColor", () => {
  it("returns the neutral color for null or undefined", () => {
    expect(accessionColor(null)).toBe("#9ca3af");
    expect(accessionColor(undefined)).toBe("#9ca3af");
  });

  it("is deterministic for the same id", () => {
    expect(accessionColor(7)).toBe(accessionColor(7));
  });

  it("returns an hsl() string for a real id", () => {
    expect(accessionColor(1)).toMatch(/^hsl\([\d.]+, 62%, 52%\)$/);
  });

  it("gives adjacent ids distinct hues (golden-angle spacing)", () => {
    expect(accessionColor(1)).not.toBe(accessionColor(2));
    expect(accessionColor(2)).not.toBe(accessionColor(3));
  });
});

describe("NOISE_EPSILON", () => {
  it("is a small positive cosine band", () => {
    expect(NOISE_EPSILON).toBeGreaterThan(0);
    expect(NOISE_EPSILON).toBeLessThan(0.1);
  });
});
