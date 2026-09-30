import { describe, expect, it } from "vitest";

import { DEFAULT_PANEL, PANELS, panelFromParam, panelHref } from "./panels";

describe("panels", () => {
  it("lists the three panels in order", () => {
    expect(PANELS.map((p) => p.id)).toEqual(["cylinder", "plate", "rnaseq"]);
    expect(DEFAULT_PANEL).toBe("cylinder");
  });

  it.each([
    ["rnaseq", "rnaseq"],
    ["plate", "plate"],
    ["pipeline", "cylinder"],
    [undefined, "cylinder"],
    ["", "cylinder"],
    ["unknown", "cylinder"],
    [["plate", "rnaseq"], "plate"],
  ])("reads ?panel=%j as %s", (value, panel) => {
    expect(panelFromParam(value as string | string[] | undefined)).toBe(panel);
  });

  it("links the default panel without a query", () => {
    expect(panelHref("cylinder")).toBe("/app/timeline");
    expect(panelHref("rnaseq")).toBe("/app/timeline?panel=rnaseq");
  });
});
