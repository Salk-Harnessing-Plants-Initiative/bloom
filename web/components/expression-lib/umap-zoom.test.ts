import { describe, expect, it } from "vitest";

import {
  clampZoom,
  formatZoom,
  MAX_ZOOM,
  MIN_ZOOM,
  sliderToZoom,
  stepZoom,
  ZOOM_BUTTON_FACTOR,
  ZOOM_SLIDER_STEPS,
  zoomToSlider,
} from "./umap-zoom";

describe("the zoom slider", () => {
  it("runs from the widest zoom at one end to the closest at the other", () => {
    expect(zoomToSlider(MIN_ZOOM)).toBe(0);
    expect(zoomToSlider(MAX_ZOOM)).toBe(ZOOM_SLIDER_STEPS);
    expect(sliderToZoom(0)).toBeCloseTo(MIN_ZOOM, 6);
    expect(sliderToZoom(ZOOM_SLIDER_STEPS)).toBeCloseTo(MAX_ZOOM, 6);
  });

  it("gives back the zoom it was set to, within a slider step", () => {
    for (const z of [MIN_ZOOM, 0.5, 1, 3.7, 20, MAX_ZOOM]) {
      expect(sliderToZoom(zoomToSlider(z)) / z).toBeCloseTo(1, 2);
    }
  });

  it("is logarithmic: equal moves are equal zoom ratios anywhere along it", () => {
    const low = sliderToZoom(300) / sliderToZoom(200);
    const high = sliderToZoom(900) / sliderToZoom(800);
    expect(low).toBeCloseTo(high, 6);
    expect(low).toBeGreaterThan(1);
  });

  it("keeps out-of-range values at the ends", () => {
    expect(zoomToSlider(1000)).toBe(ZOOM_SLIDER_STEPS);
    expect(zoomToSlider(0.001)).toBe(0);
    expect(sliderToZoom(-5)).toBe(MIN_ZOOM);
    expect(sliderToZoom(ZOOM_SLIDER_STEPS * 10)).toBe(MAX_ZOOM);
  });
});

describe("clampZoom", () => {
  it("holds a zoom inside the bounds, and turns a non-number into the whole map", () => {
    expect(clampZoom(0)).toBe(MIN_ZOOM);
    expect(clampZoom(1e6)).toBe(MAX_ZOOM);
    expect(clampZoom(2)).toBe(2);
    expect(clampZoom(Number.NaN)).toBe(1);
  });
});

describe("the + and − buttons", () => {
  it("multiply and divide by the button factor", () => {
    expect(stepZoom(1, 1)).toBeCloseTo(ZOOM_BUTTON_FACTOR, 9);
    expect(stepZoom(1, -1)).toBeCloseTo(1 / ZOOM_BUTTON_FACTOR, 9);
  });

  it("stop at the bounds", () => {
    expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
    expect(stepZoom(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
  });
});

describe("formatZoom", () => {
  it("shows one decimal below 10× and whole numbers above", () => {
    expect(formatZoom(1)).toBe("1.0×");
    expect(formatZoom(0.2)).toBe("0.2×");
    expect(formatZoom(12.4)).toBe("12×");
  });
});
