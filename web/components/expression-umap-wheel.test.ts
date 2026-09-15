// @vitest-environment jsdom
/**
 * Scrolling over the map zoomed it and scrolled the page underneath at the same
 * time. The old handler did call preventDefault — but React binds its wheel
 * listener to the root passively, and preventDefault inside a passive listener
 * does nothing at all, so the page moved anyway.
 *
 * What these check is the part that was broken: that the event is actually
 * cancelled. A test that only checked the zoom maths would have passed
 * throughout the bug.
 */

import { afterEach, describe, expect, it } from "vitest";

import { attachWheelZoom, MAX_ZOOM, MIN_ZOOM } from "./expression-umap";

let detach: (() => void) | null = null;

afterEach(() => {
  detach?.();
  detach = null;
});

function mounted() {
  const canvas = document.createElement("canvas");
  document.body.appendChild(canvas);
  let zoom = 1;
  detach = attachWheelZoom(canvas, (update) => {
    zoom = update(zoom);
  });
  return { canvas, read: () => zoom };
}

function wheel(target: Element, init: WheelEventInit) {
  const event = new WheelEvent("wheel", { bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

describe("scrolling over the map", () => {
  it("cancels the event, so the page does not scroll with it", () => {
    const { canvas } = mounted();
    expect(wheel(canvas, { deltaY: -120 }).defaultPrevented).toBe(true);
  });

  it("cancels a trackpad pinch, so the browser does not zoom the page", () => {
    const { canvas } = mounted();
    expect(wheel(canvas, { deltaY: -40, ctrlKey: true }).defaultPrevented).toBe(true);
  });

  it("zooms in on one direction and out on the other", () => {
    const { canvas, read } = mounted();
    wheel(canvas, { deltaY: -120 });
    const zoomedIn = read();
    expect(zoomedIn).toBeGreaterThan(1);
    wheel(canvas, { deltaY: 120 });
    expect(read()).toBeLessThan(zoomedIn);
  });

  it("moves a pinch more gently than a scroll of the same size", () => {
    const scrolled = mounted();
    wheel(scrolled.canvas, { deltaY: -100 });
    const byScroll = scrolled.read();
    detach?.();

    const pinched = mounted();
    wheel(pinched.canvas, { deltaY: -100, ctrlKey: true });
    expect(pinched.read()).toBeLessThan(byScroll);
    expect(pinched.read()).toBeGreaterThan(1);
  });

  it("stops at the zoom bounds however far it is scrolled", () => {
    const { canvas, read } = mounted();
    for (let i = 0; i < 200; i++) wheel(canvas, { deltaY: -500 });
    expect(read()).toBe(MAX_ZOOM);
    for (let i = 0; i < 400; i++) wheel(canvas, { deltaY: 500 });
    expect(read()).toBe(MIN_ZOOM);
  });

  it("stops listening once detached", () => {
    const { canvas } = mounted();
    detach?.();
    detach = null;
    expect(wheel(canvas, { deltaY: -120 }).defaultPrevented).toBe(false);
  });
});
