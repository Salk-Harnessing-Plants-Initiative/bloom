import { describe, expect, it } from "vitest";

import { DEFAULT_PANEL, PANELS, panelFromParam, panelHref } from "./panels";

describe("panels", () => {
  it("lists the two scanner panels in order; RNA-seq runs have their own page", () => {
    expect(PANELS.map((p) => p.id)).toEqual(["cylinder", "plate"]);
    expect(DEFAULT_PANEL).toBe("cylinder");
  });

  it.each([
    ["rnaseq", "cylinder"],
    ["plate", "plate"],
    ["pipeline", "cylinder"],
    [undefined, "cylinder"],
    ["", "cylinder"],
    ["unknown", "cylinder"],
    [["plate", "cylinder"], "plate"],
  ])("reads ?panel=%j as %s", (value, panel) => {
    expect(panelFromParam(value as string | string[] | undefined)).toBe(panel);
  });

  it("links the default panel without a query", () => {
    expect(panelHref("cylinder")).toBe("/app/timeline");
    expect(panelHref("plate")).toBe("/app/timeline?panel=plate");
  });
});
