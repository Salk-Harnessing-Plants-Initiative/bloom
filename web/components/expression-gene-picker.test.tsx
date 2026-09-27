// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/components/expression-gene-search", () => ({
  ExpressionGeneSearch: ({ onChange }: { onChange: (gene: string | null) => void }) => (
    <button type="button" onClick={() => onChange("AT1G01010")}>
      Pick AT1G01010
    </button>
  ),
}));

import { ExpressionGenePicker } from "./expression-gene-picker";

afterEach(cleanup);

function setup(genes: string[], max = 15) {
  const onAdd = vi.fn();
  const onRemove = vi.fn();
  render(<ExpressionGenePicker datasetId={1} genes={genes} max={max} onAdd={onAdd} onRemove={onRemove} />);
  return { onAdd, onRemove };
}

const pick = () => fireEvent.click(screen.getByRole("button", { name: "Pick AT1G01010" }));

describe("the gene picker", () => {
  it("adds a gene picked in the search", () => {
    const { onAdd } = setup([]);
    pick();
    expect(onAdd).toHaveBeenCalledWith("AT1G01010");
  });

  it("does not add a gene already shown", () => {
    const { onAdd } = setup(["AT1G01010"]);
    pick();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("removes a gene from its chip", () => {
    const { onRemove } = setup(["AT1G01010", "AT1G01040"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove AT1G01040" }));
    expect(onRemove).toHaveBeenCalledWith("AT1G01040");
  });

  it("refuses a gene past the cap, and says so", () => {
    const { onAdd } = setup(Array.from({ length: 15 }, (_, i) => `G${i}`));
    pick();
    expect(onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("15 genes is the most at once");
  });

  it("says how many of the cap are used", () => {
    setup(["A", "B"]);
    expect(screen.getByText("2 of 15 genes")).toBeTruthy();
  });
});
