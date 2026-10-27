// @vitest-environment jsdom
/** The free name/value rows: editing one, adding one, and removing one. */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import ScrnaOtherDetails from "./scrna-other-details";
import type { AttributeRow } from "@/lib/scrna-jobs";

afterEach(cleanup);

function renderRows(rows: AttributeRow[]) {
  const onChange = vi.fn<(rows: AttributeRow[]) => void>();
  render(<ScrnaOtherDetails rows={rows} onChange={onChange} fieldClass="" labelClass="" />);
  return onChange;
}

describe("other details", () => {
  it("edits the name and value of one row", () => {
    const onChange = renderRows([{ key: "tissue", value: "" }, { key: "", value: "" }]);
    fireEvent.change(screen.getByLabelText("Field 1 value"), { target: { value: "root" } });
    expect(onChange).toHaveBeenLastCalledWith([
      { key: "tissue", value: "root" },
      { key: "", value: "" },
    ]);
    fireEvent.change(screen.getByLabelText("Field 2 name"), { target: { value: "replicate" } });
    expect(onChange).toHaveBeenLastCalledWith([
      { key: "tissue", value: "" },
      { key: "replicate", value: "" },
    ]);
  });

  it("adds an empty row", () => {
    const onChange = renderRows([{ key: "tissue", value: "root" }]);
    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    expect(onChange).toHaveBeenLastCalledWith([
      { key: "tissue", value: "root" },
      { key: "", value: "" },
    ]);
  });

  it("removes a row", () => {
    const onChange = renderRows([
      { key: "tissue", value: "root" },
      { key: "replicate", value: "2" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
    expect(onChange).toHaveBeenLastCalledWith([{ key: "replicate", value: "2" }]);
  });

  it("empties the last row instead of removing it, so there's always one to type in", () => {
    const onChange = renderRows([{ key: "tissue", value: "root" }]);
    fireEvent.click(screen.getByRole("button", { name: "Remove field 1" }));
    expect(onChange).toHaveBeenLastCalledWith([{ key: "", value: "" }]);
  });
});
