import { describe, expect, it } from "vitest";
import { datasetOrigin, datasetRunId, originFilter } from "./expression-dataset-origin";

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

describe("datasetOrigin with its run's origin", () => {
  const runs = new Map([[8, "public" as const], [9, "hpi" as const]]);

  it("uses the run's origin when the dataset has none recorded", () => {
    expect(datasetOrigin({ metadata: { rnaseq_run_id: 8 } }, runs)).toBe("public");
    expect(datasetOrigin({ metadata: { rnaseq_run_id: 9 } }, runs)).toBe("hpi");
  });

  it("prefers the origin recorded on the dataset", () => {
    expect(datasetOrigin({ metadata: { rnaseq_run_id: 8, source: { origin: "hpi" } } }, runs)).toBe("hpi");
  });

  it("is HPI for a run it doesn't know, or a dataset from no run", () => {
    expect(datasetOrigin({ metadata: { rnaseq_run_id: 10 } }, runs)).toBe("hpi");
    expect(datasetOrigin({ metadata: {} }, runs)).toBe("hpi");
  });
});

describe("datasetRunId", () => {
  it("reads a whole run id, and nothing else", () => {
    expect(datasetRunId({ metadata: { rnaseq_run_id: 8 } })).toBe(8);
    for (const metadata of [null, {}, { rnaseq_run_id: "8" }, { rnaseq_run_id: 8.5 }, []]) {
      expect(datasetRunId({ metadata })).toBeNull();
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
