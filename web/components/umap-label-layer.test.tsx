// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { UmapLabelLayer } from "./umap-label-layer";

afterEach(cleanup);

// A map 100 wide, positions already in pixels.
const project = (x: number, y: number) => ({ x, y });

describe("the label layer", () => {
  it("writes each label where it lands, and none that falls off the canvas", () => {
    render(
      <UmapLabelLayer
        labels={[{ text: "Cortex", x: 10, y: 20 }, { text: "Xylem", x: 500, y: 20 }]}
        project={project}
        width={100}
        height={100}
      />,
    );
    const labels = screen.getAllByTestId("umap-label");
    expect(labels.map((l) => l.textContent)).toEqual(["Cortex"]);
    expect(labels[0].style.left).toBe("10px");
    expect(labels[0].style.top).toBe("20px");
  });

  it("hides and shows the labels", () => {
    render(
      <UmapLabelLayer labels={[{ text: "Cortex", x: 10, y: 20 }]} project={project} width={100} height={100} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Hide labels" }));
    expect(screen.queryByTestId("umap-label")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show labels" }));
    expect(screen.getAllByTestId("umap-label")).toHaveLength(1);
  });

  it("draws nothing, not even the button, with no labels", () => {
    render(<UmapLabelLayer labels={[]} project={project} width={100} height={100} />);
    expect(screen.queryByRole("button")).toBeNull();
  });
});
