import { describe, expect, it } from "vitest";

import { labelAnchors, projectXY } from "./umap-labels";

/** Points in a tight square of side 0.02 around (cx, cy). */
function blob(cx: number, cy: number, count: number): number[] {
  const out: number[] = [];
  for (let i = 0; i < count; i++) out.push(cx + ((i % 5) - 2) * 0.004, cy + (Math.floor(i / 5) % 5 - 2) * 0.004);
  return out;
}

describe("labelAnchors", () => {
  it("names each group at the middle of its cluster", () => {
    const positions = new Float32Array([...blob(-0.5, -0.5, 25), ...blob(0.5, 0.5, 25)]);
    const codes = [...Array(25).fill(0), ...Array(25).fill(1)];
    const anchors = labelAnchors(positions, codes, 2, null, 20, 5);
    expect(anchors.map((a) => a.level)).toEqual([0, 1]);
    expect(anchors[0].x).toBeCloseTo(-0.5, 1);
    expect(anchors[0].y).toBeCloseTo(-0.5, 1);
    expect(anchors[1].x).toBeCloseTo(0.5, 1);
  });

  it("names a group split in two on its bigger patch, not in the gap between", () => {
    const positions = new Float32Array([...blob(-0.6, 0, 25), ...blob(0.6, 0, 10)]);
    const codes = Array(35).fill(0);
    const [anchor] = labelAnchors(positions, codes, 1, null, 20, 5);
    expect(anchor.x).toBeCloseTo(-0.6, 1);
  });

  it("leaves hidden points out", () => {
    const positions = new Float32Array([...blob(-0.6, 0, 25), ...blob(0.6, 0, 10)]);
    const codes = Array(35).fill(0);
    const visibility = new Float32Array(35).fill(1).fill(0, 0, 25);
    const [anchor] = labelAnchors(positions, codes, 1, visibility, 20, 5);
    expect(anchor.x).toBeCloseTo(0.6, 1);
  });

  it("does not name a group too small to find, or points with no group", () => {
    const positions = new Float32Array([...blob(0, 0, 3), ...blob(0.5, 0.5, 25)]);
    const codes = [0, 0, 0, ...Array(25).fill(-1)];
    expect(labelAnchors(positions, codes, 1, null, 20, 5)).toEqual([]);
  });

  it("ignores codes beyond the groups it was told about", () => {
    const positions = new Float32Array(blob(0, 0, 25));
    expect(labelAnchors(positions, Array(25).fill(255), 2, null, 20, 5)).toEqual([]);
  });
});

describe("projectXY", () => {
  it("places a map position on the canvas as the shader does", () => {
    const view = { zoom: 2, translate: [0.1, 0] as [number, number], width: 800, height: 400 };
    expect(projectXY(0, 0, view, [1, 1])).toEqual({ x: 400 + 0.1 * 2 * 400, y: 200 });
    expect(projectXY(0, 0.5, view, [0.5, 1]).y).toBeCloseTo(200 - 0.5 * 2 * 200, 9);
  });
});
