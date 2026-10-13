import { describe, expect, it } from "vitest";
import { datasetOrigin, originFilter } from "./expression-dataset-origin";

describe("datasetOrigin", () => {
  it("is public only when the dataset's run marked it public", () => {
    expect(datasetOrigin({ metadata: { source: { origin: "public" } } })).toBe("public");
    expect(datasetOrigin({ metadata: { source: { origin: "hpi" } } })).toBe("hpi");
  });

  it("counts a dataset with no recorded origin as HPI's own", () => {
    for (const metadata of [null, {}, { source: {} }, { source: null }, { source: "public" }, [], "public"]) {
      expect(datasetOrigin({ metadata })).toBe("hpi");
    }
  });
});

describe("originFilter", () => {
  it("reads hpi or public, and anything else as all", () => {
    expect(originFilter("hpi")).toBe("hpi");
    expect(originFilter("public")).toBe("public");
    for (const value of [undefined, "", "HPI", "other", ["public"]]) {
      expect(originFilter(value)).toBeNull();
    }
  });
});
