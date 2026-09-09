// @vitest-environment jsdom
/**
 * The samples come from the cells, so nothing here may assume which they are or
 * how many. A dataset that records none gets no control at all, rather than an
 * empty box; and hiding every one says so, because an empty map with no
 * explanation reads as a dataset that failed to load.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExpressionSampleToggles } from "./expression-sample-toggles";

afterEach(cleanup);

const SAMPLES = [
  { name: "Col-0", count: 2442 },
  { name: "pFACT", count: 3304 },
  { name: "pHORST", count: 2937 },
];

describe("ExpressionSampleToggles", () => {
  it("shows one toggle per sample with its cell count", () => {
    render(
      <ExpressionSampleToggles
        samples={SAMPLES}
        hidden={new Set()}
        onToggle={() => {}}
        onShowAll={() => {}}
      />,
    );
    for (const { name } of SAMPLES) {
      expect(screen.getByRole("button", { name: new RegExp(name) })).toBeTruthy();
    }
    expect(screen.getByText("2,442")).toBeTruthy();
    expect(screen.getByText("3,304")).toBeTruthy();
  });

  it("renders nothing for a dataset whose cells carry no sample", () => {
    const { container } = render(
      <ExpressionSampleToggles
        samples={[]}
        hidden={new Set()}
        onToggle={() => {}}
        onShowAll={() => {}}
      />,
    );
    expect(container.textContent).toBe("");
  });

  it("says which samples are showing, for a screen reader too", () => {
    render(
      <ExpressionSampleToggles
        samples={SAMPLES}
        hidden={new Set(["pFACT"])}
        onToggle={() => {}}
        onShowAll={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Col-0/ }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: /pFACT/ }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("toggles the sample that was clicked and no other", () => {
    const onToggle = vi.fn();
    render(
      <ExpressionSampleToggles
        samples={SAMPLES}
        hidden={new Set()}
        onToggle={onToggle}
        onShowAll={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /pHORST/ }));
    expect(onToggle.mock.calls).toEqual([["pHORST"]]);
  });

  it("explains the empty map when every sample is hidden, and offers a way back", () => {
    const onShowAll = vi.fn();
    render(
      <ExpressionSampleToggles
        samples={SAMPLES}
        hidden={new Set(SAMPLES.map((s) => s.name))}
        onToggle={() => {}}
        onShowAll={onShowAll}
      />,
    );
    expect(screen.getByText(/Everything in this row is hidden/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(onShowAll).toHaveBeenCalledOnce();
  });

  it("says nothing about an empty map while any sample is showing", () => {
    render(
      <ExpressionSampleToggles
        samples={SAMPLES}
        hidden={new Set(["Col-0", "pFACT"])}
        onToggle={() => {}}
        onShowAll={() => {}}
      />,
    );
    expect(screen.queryByText(/Everything in this row is hidden/)).toBeNull();
  });
});
