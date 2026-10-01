// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { UmapZoomBar } from "./umap-zoom-bar";
import {
  MAX_ZOOM,
  MIN_ZOOM,
  ZOOM_BUTTON_FACTOR,
  ZOOM_SLIDER_STEPS,
  zoomToSlider,
} from "./expression-lib/umap-zoom";

afterEach(cleanup);

function setup(zoom = 1) {
  const onZoomChange = vi.fn();
  const onReset = vi.fn();
  render(<UmapZoomBar zoom={zoom} onZoomChange={onZoomChange} onReset={onReset} />);
  return { onZoomChange, onReset };
}

const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("the zoom bar", () => {
  it("puts the slider where the current zoom sits", () => {
    setup(2);
    const slider = screen.getByRole("slider", { name: "Zoom" }) as HTMLInputElement;
    expect(slider.value).toBe(String(zoomToSlider(2)));
    expect(screen.getByText("2.0×")).toBeTruthy();
  });

  it("zooms all the way in when the slider is dragged to its end", () => {
    const { onZoomChange } = setup();
    fireEvent.change(screen.getByRole("slider", { name: "Zoom" }), {
      target: { value: String(ZOOM_SLIDER_STEPS) },
    });
    expect(onZoomChange).toHaveBeenCalledTimes(1);
    expect(onZoomChange.mock.calls[0][0]).toBeCloseTo(MAX_ZOOM, 6);
  });

  it("steps in and out by the button factor", () => {
    const { onZoomChange } = setup();
    fireEvent.click(button("Zoom in"));
    fireEvent.click(button("Zoom out"));
    expect(onZoomChange.mock.calls[0][0]).toBeCloseTo(ZOOM_BUTTON_FACTOR, 9);
    expect(onZoomChange.mock.calls[1][0]).toBeCloseTo(1 / ZOOM_BUTTON_FACTOR, 9);
  });

  it("disables the button that would go past a bound", () => {
    setup(MAX_ZOOM);
    expect(button("Zoom in").disabled).toBe(true);
    expect(button("Zoom out").disabled).toBe(false);
    cleanup();
    setup(MIN_ZOOM);
    expect(button("Zoom out").disabled).toBe(true);
    expect(button("Zoom in").disabled).toBe(false);
  });

  it("shows the whole map again on Fit", () => {
    const { onReset, onZoomChange } = setup(8);
    fireEvent.click(button("Fit the whole map"));
    expect(onReset).toHaveBeenCalledTimes(1);
    expect(onZoomChange).not.toHaveBeenCalled();
  });
});
