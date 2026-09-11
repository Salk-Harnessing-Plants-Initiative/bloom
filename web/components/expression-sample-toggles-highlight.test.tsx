// @vitest-environment jsdom
/**
 * The highlight button sits beside each value's show/hide toggle. It has to be
 * its own control: highlighting a value must not hide or show anything, and a
 * row that is not offered highlighting shows no extra buttons at all.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExpressionSampleToggles } from "./expression-sample-toggles";

afterEach(cleanup);

const VALUES = [
  { name: "True", count: 232 },
  { name: "False", count: 8451 },
];

describe("ExpressionSampleToggles — highlighting", () => {
  it("offers a highlight button per value only when highlighting is wired", () => {
    const { rerender } = render(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={() => {}} onShowAll={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: /^Highlight/ })).toBeNull();

    rerender(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={() => {}} onShowAll={() => {}}
                               highlighted={new Set()} onHighlight={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Highlight True" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Highlight False" })).toBeTruthy();
  });

  it("highlights without hiding, and shows which values are highlighted", () => {
    const onToggle = vi.fn();
    const onHighlight = vi.fn();
    render(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={onToggle} onShowAll={() => {}}
                               highlighted={new Set(["True"])} onHighlight={onHighlight} />,
    );
    const on = screen.getByRole("button", { name: "Highlight True" });
    expect(on.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Highlight False" }).getAttribute("aria-pressed"))
      .toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Highlight False" }));
    expect(onHighlight).toHaveBeenCalledWith("False");
    expect(onToggle).not.toHaveBeenCalled();
    // The show/hide toggle still answers for itself.
    fireEvent.click(screen.getByRole("button", { name: "True 232" }));
    expect(onToggle).toHaveBeenCalledWith("True");
  });
});
