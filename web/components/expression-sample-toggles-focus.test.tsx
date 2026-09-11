// @vitest-environment jsdom
/**
 * The focus button sits beside each value's show/hide toggle. It has to be its
 * own control: focusing on a value must not hide or show anything, and a row
 * that is not offered focusing shows no extra buttons at all.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { ExpressionSampleToggles } from "./expression-sample-toggles";

afterEach(cleanup);

const VALUES = [
  { name: "True", count: 232 },
  { name: "False", count: 8451 },
];

describe("ExpressionSampleToggles — focusing", () => {
  it("offers a focus button per value only when focusing is wired", () => {
    const { rerender } = render(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={() => {}} onShowAll={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: /^Focus on/ })).toBeNull();

    rerender(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={() => {}} onShowAll={() => {}}
                               focused={new Set()} onFocus={() => {}} />,
    );
    expect(screen.getByRole("button", { name: "Focus on True" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Focus on False" })).toBeTruthy();
  });

  it("focuses without hiding, and shows which values are focused on", () => {
    const onToggle = vi.fn();
    const onFocus = vi.fn();
    render(
      <ExpressionSampleToggles samples={VALUES} hidden={new Set()} unlabelledCount={0}
                               onToggle={onToggle} onShowAll={() => {}}
                               focused={new Set(["True"])} onFocus={onFocus} />,
    );
    expect(screen.getByRole("button", { name: "Focus on True" }).getAttribute("aria-pressed"))
      .toBe("true");
    expect(screen.getByRole("button", { name: "Focus on False" }).getAttribute("aria-pressed"))
      .toBe("false");

    fireEvent.click(screen.getByRole("button", { name: "Focus on False" }));
    expect(onFocus).toHaveBeenCalledWith("False");
    expect(onToggle).not.toHaveBeenCalled();
    // The show/hide toggle still answers for itself.
    fireEvent.click(screen.getByRole("button", { name: "True 232" }));
    expect(onToggle).toHaveBeenCalledWith("True");
  });
});
