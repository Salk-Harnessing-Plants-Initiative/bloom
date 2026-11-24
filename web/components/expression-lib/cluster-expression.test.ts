/** A gene's expression per cluster for the genome browser's panel: the same numbers as the
 * dataset's own view and bloommcp, checked on a fixture bloommcp's tests read too. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { clusterExpression } from "./cluster-expression";
import { readGeneCounts } from "./gene-counts";

const fixture = JSON.parse(readFileSync(join(__dirname, "fixtures", "cluster-expression.json"), "utf8"));

const client = (body: string) =>
  ({
    from: () => ({
      select: () => ({ eq: () => ({ eq: () => ({ maybeSingle: async () => ({ data: { counts_object_path: "p" }, error: null }) }) }) }),
    }),
    storage: { from: () => ({ download: async () => ({ data: new Blob([body]), error: null }) }) },
  }) as never;

describe("clusterExpression", () => {
  it.each(["object", "list"])("gives the shared fixture's numbers from the %s count file", async (shape) => {
    const values = await readGeneCounts(client(fixture.count_files[shape]), 1, "G1", fixture.cell_count);
    expect(clusterExpression(values, fixture.cell_ordinals, fixture.clusters)).toEqual(fixture.expected);
  });

  it("labels each row with its own cluster when an empty one shares its name", () => {
    const rows = clusterExpression(new Float32Array([2, 0]), [1, 1], fixture.clusters);
    expect(rows).toEqual([{ ordinal: 1, cluster_id: "1", name: "Cortex", cells: 2, expressing: 1, share: 0.5, mean: 1 }]);
  });

  it("leaves out empty clusters", () => {
    const rows = clusterExpression(new Float32Array([1]), [0], fixture.clusters);
    expect(rows.map((r) => r.ordinal)).toEqual([0]);
  });
});
