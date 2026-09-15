// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ExpressionGeneViolin } from "./expression-gene-violin";
import type { CellGroup } from "./expression-lib/gene-stats";

afterEach(cleanup);

// A has four distinct values, three above zero; B has only two values.
const GROUPS: CellGroup[] = [
  { key: "A", cellType: "A", color: "#ff0000", part: null, cells: Int32Array.from([0, 1, 2, 3]) },
  { key: "B", cellType: "B", color: null, part: null, cells: Int32Array.from([4, 5]) },
];
const VALUES = Float32Array.from([0, 1, 2, 3, 4, 5]);

describe("the violin view", () => {
  it("draws a group for each cell type, with its cell count and share expressing", () => {
    render(<ExpressionGeneViolin gene="g1" groups={GROUPS} values={VALUES} unitsLabel="log1p" />);
    expect(screen.getAllByTestId("violin-group")).toHaveLength(2);
    expect(screen.getByText("n 4")).toBeTruthy();
    expect(screen.getByText("75% expr.")).toBeTruthy();
    expect(screen.getByText("100% expr.")).toBeTruthy();
  });

  it("leaves the violin out where there are too few distinct values, keeping the box", () => {
    render(<ExpressionGeneViolin gene="g1" groups={GROUPS} values={VALUES} unitsLabel="log1p" />);
    const [a, b] = screen.getAllByTestId("violin-group");
    expect(a.querySelector('[data-testid="violin-shape"]')).not.toBeNull();
    expect(b.querySelector('[data-testid="violin-shape"]')).toBeNull();
    expect(b.querySelector("rect")).not.toBeNull();
  });
});
