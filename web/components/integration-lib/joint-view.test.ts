import { describe, expect, it } from "vitest";

import {
  FILL_BOTTOM_GAP,
  fillHeightFor,
  fitFor,
  mapHeightFor,
  MAX_MAP_HEIGHT,
  MIN_MAP_HEIGHT,
  panBy,
  pickPoint,
  projectPoint,
  VIEWPORT_MARGIN,
  type JointView,
} from "./joint-view";

const WIDE: JointView = { zoom: 1, translate: [0, 0], width: 1000, height: 500 };
const P = new Float32Array([0, 0, 0.5, 0, 0, 0.5, -0.5, -0.5]);

describe("fitFor", () => {
  it("shrinks the longer side so the map fits the shorter one", () => {
    expect(fitFor(1000, 500)).toEqual([0.5, 1]);
    expect(fitFor(500, 1000)).toEqual([1, 0.5]);
    expect(fitFor(700, 700)).toEqual([1, 1]);
  });

  it("leaves a canvas with no size alone", () => {
    expect(fitFor(0, 500)).toEqual([1, 1]);
  });
});

describe("projectPoint", () => {
  it("puts the middle of the map in the middle of the canvas", () => {
    expect(projectPoint(P, 0, WIDE)).toEqual({ x: 500, y: 250 });
  });

  it("keeps a unit as long across as it is up on a wide canvas, so the map is not stretched", () => {
    const origin = projectPoint(P, 0, WIDE);
    const right = projectPoint(P, 1, WIDE);
    const up = projectPoint(P, 2, WIDE);
    expect(right.x - origin.x).toBeCloseTo(origin.y - up.y, 9);
    expect(right.x - origin.x).toBeCloseTo(125, 9);
  });

  it("follows the zoom and the pan", () => {
    const view = { ...WIDE, zoom: 2, translate: [0.25, 0] as [number, number] };
    expect(projectPoint(P, 0, view).x).toBeCloseTo(500 + 0.25 * 2 * 0.5 * 500, 9);
  });
});

describe("pickPoint", () => {
  it("finds the point under the cursor, where projectPoint put it", () => {
    for (let i = 0; i < 4; i++) {
      const at = projectPoint(P, i, WIDE);
      expect(pickPoint(P, null, WIDE, at)).toBe(i);
    }
  });

  it("finds nothing farther than the radius", () => {
    const at = projectPoint(P, 1, WIDE);
    expect(pickPoint(P, null, WIDE, { x: at.x + 20, y: at.y }, 8)).toBeNull();
    expect(pickPoint(P, null, WIDE, { x: at.x + 5, y: at.y }, 8)).toBe(1);
  });

  it("skips a hidden point", () => {
    const at = projectPoint(P, 1, WIDE);
    expect(pickPoint(P, new Float32Array([1, 0, 1, 1]), WIDE, at)).toBeNull();
  });

  it("finds nothing on a canvas with no size", () => {
    expect(pickPoint(P, null, { ...WIDE, width: 0 }, { x: 0, y: 0 })).toBeNull();
  });
});

describe("panBy", () => {
  it("moves the map exactly as far as the pointer was dragged", () => {
    const view = { ...WIDE, zoom: 3, translate: [0.1, -0.2] as [number, number] };
    const before = projectPoint(P, 3, view);
    const [dx, dy] = panBy(30, -20, view);
    const after = projectPoint(P, 3, {
      ...view,
      translate: [view.translate[0] + dx, view.translate[1] + dy],
    });
    expect(after.x - before.x).toBeCloseTo(30, 6);
    expect(after.y - before.y).toBeCloseTo(-20, 6);
  });
});

describe("fillHeightFor", () => {
  it("runs from the canvas's top to just above the window's bottom", () => {
    expect(fillHeightFor(300, 1400)).toBe(1400 - 300 - FILL_BOTTOM_GAP);
  });

  it("never falls below the smallest map", () => {
    expect(fillHeightFor(900, 1000)).toBe(MIN_MAP_HEIGHT);
  });
});

describe("mapHeightFor", () => {
  it("is square to the width when the window is tall enough", () => {
    expect(mapHeightFor(900, 900 + VIEWPORT_MARGIN + 50)).toBe(900);
  });

  it("stops short of the bottom of a shorter window", () => {
    expect(mapHeightFor(1400, 900)).toBe(900 - VIEWPORT_MARGIN);
  });

  it("stays within its bounds", () => {
    expect(mapHeightFor(300, 600)).toBe(MIN_MAP_HEIGHT);
    expect(mapHeightFor(3000, 3000)).toBe(MAX_MAP_HEIGHT);
  });
});
