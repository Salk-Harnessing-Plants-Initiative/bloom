// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { IntegrationLegend } from "./integration-legend";

afterEach(cleanup);

function setup(overrides: Partial<Parameters<typeof IntegrationLegend>[0]> = {}) {
  const handlers = {
    onToggle: vi.fn(),
    onFocus: vi.fn(),
    onShowAll: vi.fn(),
    onHideAll: vi.fn(),
  };
  render(
    <IntegrationLegend
      title="shahan_cell_type"
      levels={["Cortex", "Xylem"]}
      colours={["rgb(1, 2, 3)", "rgb(4, 5, 6)"]}
      counts={[1234, 5]}
      noValueCount={0}
      hidden={new Set()}
      focused={new Set()}
      {...handlers}
      {...overrides}
    />,
  );
  return handlers;
}

const button = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("the legend", () => {
  it("lists every value with its count", () => {
    setup();
    expect(screen.getByText("Cortex")).toBeTruthy();
    expect(screen.getByText("1,234")).toBeTruthy();
    expect(screen.getByText("Xylem")).toBeTruthy();
  });

  it("hides or shows a value by its index", () => {
    const { onToggle } = setup({ hidden: new Set([1]) });
    fireEvent.click(button("Hide Cortex"));
    fireEvent.click(button("Show Xylem"));
    expect(onToggle.mock.calls).toEqual([[0], [1]]);
  });

  it("focuses on a value, and marks the ones focused on", () => {
    const { onFocus } = setup({ focused: new Set([1]) });
    fireEvent.click(button("Focus on Cortex"));
    expect(onFocus).toHaveBeenCalledWith(0);
    expect(button("Focus on Xylem").getAttribute("aria-pressed")).toBe("true");
    expect(button("Focus on Cortex").getAttribute("aria-pressed")).toBe("false");
  });

  it("shows or hides every value at once", () => {
    const { onShowAll, onHideAll } = setup();
    fireEvent.click(button("Show all"));
    fireEvent.click(button("Hide all"));
    expect(onShowAll).toHaveBeenCalledTimes(1);
    expect(onHideAll).toHaveBeenCalledTimes(1);
  });

  it("shows a green badge on the values that have one", () => {
    setup({ badges: ["82 transgene+", null] });
    expect(screen.getByText("82 transgene+")).toBeTruthy();
    expect(screen.getAllByText(/transgene\+/)).toHaveLength(1);
  });

  it("says how many points have no value, and only when some do", () => {
    setup({ noValueCount: 19755 });
    expect(screen.getByText(/19,755 points have no shahan_cell_type/)).toBeTruthy();
    cleanup();
    setup();
    expect(screen.queryByText(/have no/)).toBeNull();
  });
});
