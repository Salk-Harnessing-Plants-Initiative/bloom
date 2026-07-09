import { describe, it, expect } from "vitest";
import { cosineDistance, euclideanDistance } from "./distance";

describe("cosineDistance", () => {
  it("returns 0 for identical vectors", () => {
    expect(cosineDistance([1, 2, 3], [1, 2, 3])).toBeCloseTo(0, 10);
  });

  it("returns 0 for proportional vectors (positive scaling)", () => {
    expect(cosineDistance([1, 2, 3], [2, 4, 6])).toBeCloseTo(0, 10);
  });

  it("returns 1 for orthogonal vectors", () => {
    expect(cosineDistance([1, 0], [0, 1])).toBeCloseTo(1, 10);
  });

  it("returns 2 for exactly opposite vectors", () => {
    expect(cosineDistance([1, 0], [-1, 0])).toBeCloseTo(2, 10);
  });

  it("returns a value strictly between 0 and 1 for partially aligned vectors", () => {
    const d = cosineDistance([1, 1], [1, 0]);
    expect(d).toBeGreaterThan(0);
    expect(d).toBeLessThan(1);
  });

  it("matches the known cosine for a 1280-dim vector (representative of ESM-2 scale)", () => {
    const a = new Array(1280).fill(0);
    const b = new Array(1280).fill(0);
    a[0] = 1.0;
    b[0] = 0.9;
    b[1] = Math.sqrt(1 - 0.81); // unit vector at angle arccos(0.9) from a
    expect(cosineDistance(a, b)).toBeCloseTo(0.1, 10);
  });

  it("treats a zero vector as maximally dissimilar (returns 1, not NaN)", () => {
    expect(cosineDistance([0, 0, 0], [1, 2, 3])).toBe(1);
    expect(cosineDistance([1, 2, 3], [0, 0, 0])).toBe(1);
    expect(cosineDistance([0, 0, 0], [0, 0, 0])).toBe(1);
  });

  it("does not mutate its inputs", () => {
    const a = [1, 2, 3];
    const b = [4, 5, 6];
    const aCopy = [...a];
    const bCopy = [...b];
    cosineDistance(a, b);
    expect(a).toEqual(aCopy);
    expect(b).toEqual(bCopy);
  });

  it("throws on length mismatch", () => {
    expect(() => cosineDistance([1, 2], [1, 2, 3])).toThrow(/length mismatch/);
  });

  it("throws on empty vectors", () => {
    expect(() => cosineDistance([], [])).toThrow(/non-empty/);
  });

  it("clamps small floating-point drift outside [-1, 1] (no negative distance)", () => {
    // Two unit vectors that are mathematically identical but constructed
    // differently can produce cos > 1 by ~1e-16. The clamp keeps distance >= 0.
    const v = [0.1, 0.2, 0.3, 0.4, 0.5];
    const d = cosineDistance(v, [...v]);
    expect(d).toBeGreaterThanOrEqual(0);
    expect(d).toBeCloseTo(0, 10);
  });
});

describe("euclideanDistance", () => {
  it("returns 0 for identical vectors", () => {
    expect(euclideanDistance([1, 2, 3], [1, 2, 3])).toBeCloseTo(0, 10);
  });

  it("returns the classical 3-4-5 distance", () => {
    expect(euclideanDistance([0, 0], [3, 4])).toBeCloseTo(5, 10);
  });

  it("is symmetric: d(a, b) === d(b, a)", () => {
    const a = [1.1, 2.2, 3.3];
    const b = [4.4, 5.5, 6.6];
    expect(euclideanDistance(a, b)).toBeCloseTo(euclideanDistance(b, a), 12);
  });

  it("returns a positive value for any pair of distinct vectors", () => {
    expect(euclideanDistance([0, 0], [0, 1])).toBeGreaterThan(0);
    expect(euclideanDistance([1, 2, 3], [3, 2, 1])).toBeGreaterThan(0);
  });

  it("handles negative components", () => {
    expect(euclideanDistance([-1, -1], [1, 1])).toBeCloseTo(
      Math.sqrt(8),
      10,
    );
  });

  it("does not mutate its inputs", () => {
    const a = [1, 2, 3];
    const b = [4, 5, 6];
    const aCopy = [...a];
    const bCopy = [...b];
    euclideanDistance(a, b);
    expect(a).toEqual(aCopy);
    expect(b).toEqual(bCopy);
  });

  it("throws on length mismatch", () => {
    expect(() => euclideanDistance([1], [1, 2])).toThrow(/length mismatch/);
  });

  it("throws on empty vectors", () => {
    expect(() => euclideanDistance([], [])).toThrow(/non-empty/);
  });

  it("scales correctly with vector dimension (1280-dim sanity)", () => {
    const a = new Array(1280).fill(0);
    const b = new Array(1280).fill(0);
    a[0] = 0;
    b[0] = 5;
    expect(euclideanDistance(a, b)).toBeCloseTo(5, 10);
  });

  it("matches sqrt of squared-component sum for a hand-checked example", () => {
    // (2-5)^2 + (4-7)^2 + (6-2)^2 = 9 + 9 + 16 = 34 -> sqrt(34)
    expect(euclideanDistance([2, 4, 6], [5, 7, 2])).toBeCloseTo(
      Math.sqrt(34),
      10,
    );
  });
});
