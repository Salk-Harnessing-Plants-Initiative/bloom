// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { colourCss, ExpressionGeneDotplot } from "./expression-gene-dotplot";
import type { CellGroup } from "./expression-lib/gene-stats";

afterEach(cleanup);

const GROUPS: CellGroup[] = [
  { key: "Cortex", cellType: "Cortex", color: "#ff0000", part: null, cells: Int32Array.from([0, 2]) },
  { key: "Xylem", cellType: "Xylem", color: "#0000ff", part: null, cells: Int32Array.from([1]) },
];
const VALUES = new Map([
  ["g1", Float32Array.from([0, 6, 2])],
  ["g2", Float32Array.from([1, 1, 1])],
]);

function draw(perGene = true, onChoose = vi.fn()) {
  render(
    <ExpressionGeneDotplot
      genes={["g1", "g2", "not-read"]}
      groups={GROUPS}
      values={VALUES}
      perGene={perGene}
      chosen={null}
      onChoose={onChoose}
      unitsLabel="log1p"
    />,
  );
  return { onChoose, dots: screen.getAllByTestId("dotplot-dot") };
}

describe("the dot plot", () => {
  it("has a row per gene read and a column per group", () => {
    draw();
    expect(screen.getAllByTestId("dotplot-row")).toHaveLength(2);
    expect(screen.getAllByTestId("dotplot-column")).toHaveLength(2);
  });

  it("gives each dot's cells, cells expressing, share and mean, zeros included", () => {
    const { dots } = draw();
    expect(dots[0].querySelector("title")?.textContent).toBe(
      "g1 in Cortex: 2 cells, 1 expressing (50%), mean 1.000",
    );
    expect(dots[1].querySelector("title")?.textContent).toBe(
      "g1 in Xylem: 1 cell, 1 expressing (100%), mean 6.000",
    );
  });

  it("sizes a dot by the share expressing", () => {
    const { dots } = draw();
    expect(Number(dots[1].getAttribute("r"))).toBeGreaterThan(Number(dots[0].getAttribute("r")));
  });

  it("colours each gene on its own scale, or all on one", () => {
    const own = draw(true).dots[2].getAttribute("fill");
    cleanup();
    const shared = draw(false).dots[2].getAttribute("fill");
    expect(own).toBe(colourCss(1));
    expect(shared).toBe(colourCss(1 / 6));
  });

  it("chooses a gene when its name is clicked", () => {
    const { onChoose } = draw();
    fireEvent.click(screen.getByRole("button", { name: "Show g2 by cell type" }));
    expect(onChoose).toHaveBeenCalledWith("g2");
  });
});
