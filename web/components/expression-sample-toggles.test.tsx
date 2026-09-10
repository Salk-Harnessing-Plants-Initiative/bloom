// @vitest-environment jsdom
/**
 * The samples come from the cells, so nothing here may assume which they are or
 * how many. A dataset that records none gets no control at all, rather than an
 * empty box; and hiding every one says what is left on the map, because a wrong
 * or missing explanation reads as a dataset that failed to load.
 *
 * Buttons are looked up by their whole accessible name -- "Col-0 2,442" -- so
 * every assertion also pins that a count sits inside its own sample's button.
 * A free-floating getByText("2,442") passes just as well when the counts are
 * rendered against the wrong names.
 */

import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExpressionSampleToggles } from "./expression-sample-toggles";

afterEach(cleanup);

const SAMPLES = [
  { name: "Col-0", count: 2442 },
  { name: "pFACT", count: 3304 },
  { name: "pHORST", count: 2937 },
];

/** Every sample's button, named as a reader sees it. */
const NAMED = ["Col-0 2,442", "pFACT 3,304", "pHORST 2,937"];

const ALL_HIDDEN = new Set(SAMPLES.map((s) => s.name));

type Props = ComponentProps<typeof ExpressionSampleToggles>;

/** One place for the defaults, so a new required prop lands once. */
function renderToggles(overrides: Partial<Props> = {}) {
  return render(
    <ExpressionSampleToggles
      samples={SAMPLES}
      hidden={new Set()}
      unlabelledCount={0}
      onToggle={() => {}}
      onShowAll={() => {}}
      {...overrides}
    />,
  );
}

describe("ExpressionSampleToggles", () => {
  it("shows one toggle per sample carrying that sample's own count", () => {
    renderToggles();
    for (const name of NAMED) {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    }
    expect(screen.getAllByRole("button")).toHaveLength(SAMPLES.length);
  });

  it("renders nothing for a dataset whose cells carry no sample", () => {
    const { container } = renderToggles({ samples: [] });
    expect(container.textContent).toBe("");
  });

  it("says which samples are showing, for a screen reader too", () => {
    renderToggles({ hidden: new Set(["pFACT"]) });
    expect(
      screen.getByRole("button", { name: "Col-0 2,442" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "pFACT 3,304" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("toggles the sample that was clicked and no other", () => {
    const onToggle = vi.fn();
    renderToggles({ onToggle });
    fireEvent.click(screen.getByRole("button", { name: "pHORST 2,937" }));
    expect(onToggle.mock.calls).toEqual([["pHORST"]]);
  });

  it("explains the empty map when every sample is hidden, and offers a way back", () => {
    const onShowAll = vi.fn();
    renderToggles({ hidden: ALL_HIDDEN, onShowAll });
    expect(screen.getByText(/Every sample is hidden, so the map is empty/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show all samples" }));
    expect(onShowAll).toHaveBeenCalledOnce();
  });

  it("does not claim an empty map when cells no toggle can hide are still drawn", () => {
    renderToggles({ hidden: ALL_HIDDEN, unlabelledCount: 412 });
    expect(screen.queryByText(/so the map is empty/)).toBeNull();
    expect(
      screen.getByText(/412 cells record no sample and stay on the map/),
    ).toBeTruthy();
  });

  it("says it in the singular for a single unlabelled cell", () => {
    renderToggles({ hidden: ALL_HIDDEN, unlabelledCount: 1 });
    expect(
      screen.getByText(/1 cell records no sample and stays on the map/),
    ).toBeTruthy();
  });

  it("names the button for its own control, not just 'Show all'", () => {
    // The cluster sidebar has a "Show all" of its own on the same screen, and
    // the two do different things.
    renderToggles({ hidden: ALL_HIDDEN });
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
  });

  it("says nothing about an empty map while any sample is showing", () => {
    renderToggles({ hidden: new Set(["Col-0", "pFACT"]) });
    expect(screen.queryByText(/Every sample is hidden/)).toBeNull();
  });
});
